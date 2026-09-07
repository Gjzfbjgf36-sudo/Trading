"""Paper execution.

This is not a convenience simulator that fills everything at the quoted price.
It models the ways real execution disappoints us: latency, adverse drift during
that latency, walking the book, depth limits, fees, partial fills, outright
rejection, and the venue going silent so we never learn the outcome.

Determinism is a design requirement, not a convenience: every draw comes from a
seeded generator so a surprising session can be replayed exactly.

The output of a paper fill must never be more optimistic than the decision that
authorised it. Where the model is uncertain it errs against us.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from ..domain.types import ZERO, Side, to_decimal
from ..marketdata.book import OrderBook
from ..pricing.executable import NotExecutable, walk_book
from .order import Fill, Order, OrderState

_QUANT = Decimal("0.00000001")


@dataclass(frozen=True, slots=True)
class PaperMarketModel:
    """Parameters of the simulated execution environment.

    Defaults are pessimistic on purpose. A simulator tuned to be kind produces
    a strategy that only works in the simulator.
    """

    #: Round-trip order latency, uniform over [min, max].
    latency_min_ms: int = 40
    latency_max_ms: int = 250
    #: Probability the venue rejects the order outright.
    reject_probability: float = 0.01
    #: Probability the venue never confirms (→ UNKNOWN, requires reconciliation).
    timeout_probability: float = 0.005
    #: Probability only part of the size fills.
    partial_probability: float = 0.08
    #: When partial, the fraction that fills, uniform over [min, 1).
    partial_min_fraction: float = 0.25
    #: Adverse price drift per 100 ms of latency, as a fraction.
    decay_per_100ms: Decimal = Decimal("0.00004")
    #: Taker fee applied to filled notional.
    taker_fee_rate: Decimal = Decimal("0.0010")
    #: Probability that faster participants took the liquidity we aimed at.
    #: Without this the simulator fills every order at the book we *saw*, which
    #: produced a 98% win rate and a profit factor of 672 in run 05 — a number
    #: that says the model is wrong, not that the strategy is good. A visible
    #: spread is precisely the spread everyone else can also see.
    competition_probability: float = 0.35
    #: Extra adverse move when we lose the race, as a fraction.
    competition_adverse_min: Decimal = Decimal("0.0002")
    competition_adverse_max: Decimal = Decimal("0.0020")
    #: Fraction of our size that still fills when we lose the race.
    competition_fill_min: float = 0.0
    competition_fill_max: float = 0.7

    def __post_init__(self) -> None:
        if self.latency_min_ms < 0 or self.latency_max_ms < self.latency_min_ms:
            raise ValueError("invalid latency range")
        for name in (
            "reject_probability",
            "timeout_probability",
            "partial_probability",
            "competition_probability",
        ):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be a probability")
        if not 0.0 < self.partial_min_fraction < 1.0:
            raise ValueError("partial_min_fraction must be within (0, 1)")


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    """What the simulated venue did, and why."""

    order: Order
    latency_ms: int
    #: Set when nothing filled, for the audit record.
    note: str = ""

    @property
    def filled(self) -> bool:
        return self.order.filled_quantity > ZERO


class PaperVenue:
    """A simulated venue for one exchange.

    ``inject_failure`` lets chaos tests force a specific pathology without
    fighting the random generator, which keeps those tests deterministic and
    readable.
    """

    def __init__(self, model: PaperMarketModel, seed: int) -> None:
        self._model = model
        self._rng = random.Random(seed)
        self._forced: str | None = None
        self.orders: dict[str, Order] = {}

    def inject_failure(self, kind: str | None) -> None:
        """Force the next execution to fail in a specific way.

        Accepts ``"reject"``, ``"timeout"``, ``"partial"``, or ``None`` to clear.
        """
        if kind not in (None, "reject", "timeout", "partial"):
            raise ValueError(f"unknown injected failure: {kind!r}")
        self._forced = kind

    def submit(self, order: Order, book: OrderBook, *, now: datetime) -> ExecutionOutcome:
        """Execute one order against the book as it stands, plus latency effects.

        Duplicate submissions are refused by client id: this is the idempotency
        guarantee that makes a retry after an ambiguous response safe.
        """
        existing = self.orders.get(order.client_id)
        if existing is not None:
            return ExecutionOutcome(
                order=existing,
                latency_ms=0,
                note="duplicate submission ignored; returning the existing order",
            )
        self.orders[order.client_id] = order
        order.transition(OrderState.SENT)

        latency_ms = self._rng.randint(self._model.latency_min_ms, self._model.latency_max_ms)
        forced, self._forced = self._forced, None

        if forced == "reject" or (forced is None and self._draw() < self._model.reject_probability):
            order.transition(OrderState.REJECTED, detail="venue rejected the order")
            return ExecutionOutcome(order, latency_ms, "rejected by venue")

        if forced == "timeout" or (
            forced is None and self._draw() < self._model.timeout_probability
        ):
            # The venue may or may not have the order. We do not know, so we do
            # not guess, and above all we do not resend.
            order.transition(OrderState.UNKNOWN)
            return ExecutionOutcome(order, latency_ms, "no confirmation; state unknown")

        order.transition(OrderState.ACKNOWLEDGED)

        if not book.usable:
            order.transition(OrderState.CANCELLED)
            return ExecutionOutcome(order, latency_ms, "book unusable at execution time")

        size = order.quantity
        if forced == "partial" or (
            forced is None and self._draw() < self._model.partial_probability
        ):
            fraction = Decimal(
                str(round(self._rng.uniform(self._model.partial_min_fraction, 0.999), 6))
            )
            size = (order.quantity * fraction).quantize(_QUANT)

        # Adverse selection: with some probability the opportunity was taken
        # before we arrived. We then get a worse price, a smaller fill, or
        # nothing at all — which is what competing for a visible spread costs.
        competition_drift = ZERO
        if self._draw() < self._model.competition_probability:
            competition_drift = _uniform_decimal(
                self._rng,
                self._model.competition_adverse_min,
                self._model.competition_adverse_max,
            )
            fill_fraction = Decimal(
                str(
                    round(
                        self._rng.uniform(
                            self._model.competition_fill_min,
                            self._model.competition_fill_max,
                        ),
                        6,
                    )
                )
            )
            size = (size * fill_fraction).quantize(_QUANT)
            if size <= ZERO:
                order.transition(OrderState.CANCELLED)
                return ExecutionOutcome(
                    order, latency_ms, "opportunity taken by a faster participant"
                )

        try:
            quote = walk_book(book, order.side, size)
        except NotExecutable as exc:
            order.transition(OrderState.CANCELLED)
            return ExecutionOutcome(order, latency_ms, f"not executable: {exc}")

        # Adverse drift over the latency window. Always against us: modelling it
        # as symmetric noise would hand back half the cost as free profit.
        drift = (
            self._model.decay_per_100ms * Decimal(latency_ms) / Decimal(100)
        ) + competition_drift
        multiplier = Decimal(1) + drift if order.side is Side.BUY else Decimal(1) - drift
        effective_price = (quote.vwap * multiplier).quantize(_QUANT)

        worse_than_limit = (
            effective_price > order.limit_price
            if order.side is Side.BUY
            else effective_price < order.limit_price
        )
        if worse_than_limit:
            order.transition(OrderState.CANCELLED)
            return ExecutionOutcome(
                order,
                latency_ms,
                f"price moved through the limit: {effective_price} vs {order.limit_price}",
            )

        filled = quote.filled_size
        if filled <= ZERO:
            order.transition(OrderState.CANCELLED)
            return ExecutionOutcome(order, latency_ms, "no liquidity")

        fee = (effective_price * filled * self._model.taker_fee_rate).quantize(_QUANT)
        order.record_fill(
            Fill(
                quantity=filled,
                price=effective_price,
                fee=fee,
                at=now + timedelta(milliseconds=latency_ms),
            )
        )
        if order.state is OrderState.PARTIALLY_FILLED:
            # The venue is done with it; the remainder will not fill later.
            order.transition(OrderState.CANCELLED)
        return ExecutionOutcome(order, latency_ms)

    def query(self, client_id: str) -> Order | None:
        """Authoritative state query — how an ``UNKNOWN`` order is resolved."""
        order = self.orders.get(client_id)
        if order is not None:
            order.status_queries += 1
        return order

    def _draw(self) -> float:
        return self._rng.random()


def _uniform_decimal(rng: random.Random, low: Decimal, high: Decimal) -> Decimal:
    """Uniform draw between two Decimals, without routing through float money."""
    span = high - low
    fraction = Decimal(str(round(rng.random(), 6)))
    return (low + span * fraction).quantize(Decimal("0.00000001"))


def limit_price_for(
    decision_price: Decimal, side: Side, max_slippage: Decimal
) -> Decimal:
    """The worst price we will accept, derived from the slippage limit.

    Expressing the limit in price terms means the venue enforces our slippage
    budget even if our own post-trade checks are wrong.
    """
    reference = to_decimal(decision_price)
    tolerance = reference * to_decimal(max_slippage)
    return (
        reference + tolerance if side is Side.BUY else reference - tolerance
    ).quantize(_QUANT)
