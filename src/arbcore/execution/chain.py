"""On-chain execution.

Different failure semantics from an exchange order, and the differences are
what the module exists to encode:

* **Simulate first.** A transaction is simulated against current state before
  it is signed. A failing simulation is a rejection, never a "try anyway".
* **Atomic or nothing.** Both swaps sit in one transaction. If the second leg
  cannot deliver the minimum output, the whole transaction reverts and the
  position is unchanged — there is no half-executed arbitrage.
* **We pay for failure.** A reverted transaction still consumes gas. That is
  the cost of being wrong, and it is charged.
* **Inclusion is not guaranteed.** A transaction may never land. Nothing is
  spent on chain when that happens, but the opportunity is gone.
"""

from __future__ import annotations

import enum
import random
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from ..costs.gas import GasModel, InclusionModel
from ..domain.types import ZERO, to_decimal
from ..pricing.amm import Pool, PoolUnusable, quote_swap

_QUANT = Decimal("0.00000001")


class TxState(enum.StrEnum):
    #: Simulated locally, not submitted.
    SIMULATED = "SIMULATED"
    #: Simulation said it would fail. Never submitted.
    SIMULATION_FAILED = "SIMULATION_FAILED"
    SUBMITTED = "SUBMITTED"
    #: Included and succeeded.
    CONFIRMED = "CONFIRMED"
    #: Included but reverted. Gas spent, position unchanged.
    REVERTED = "REVERTED"
    #: Never included. Nothing spent on chain.
    DROPPED = "DROPPED"
    #: Submitted, outcome unestablished. Requires reconciliation; never resent.
    UNKNOWN = "UNKNOWN"


TERMINAL_TX_STATES: frozenset[TxState] = frozenset(
    {TxState.CONFIRMED, TxState.REVERTED, TxState.DROPPED, TxState.SIMULATION_FAILED}
)


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """What the node says would happen if we submitted this."""

    succeeds: bool
    expected_output: Decimal
    reason: str = ""


@dataclass(slots=True)
class ArbitrageTx:
    """One atomic two-swap transaction."""

    #: Deterministic idempotency key. A restarted process recomputes it and
    #: recognises its own in-flight transaction rather than sending a second.
    idempotency_key: str
    opportunity_id: str
    input_amount: Decimal
    #: Enforced on chain. If the round trip cannot return this, it reverts.
    min_output: Decimal
    state: TxState = TxState.SIMULATED
    gas_paid: Decimal = ZERO
    realised_output: Decimal = ZERO
    detail: str = ""
    submitted_at: datetime | None = None
    status_queries: int = 0

    def __post_init__(self) -> None:
        for name in ("input_amount", "min_output"):
            setattr(self, name, to_decimal(getattr(self, name)))
        if self.input_amount <= ZERO:
            raise ValueError("transaction input must be > 0")

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_TX_STATES

    @property
    def profit(self) -> Decimal:
        """Realised profit net of gas. Negative on a revert, by construction."""
        if self.state is TxState.CONFIRMED:
            return (self.realised_output - self.input_amount - self.gas_paid).quantize(_QUANT)
        return -self.gas_paid


def simulate(
    buy_pool: Pool,
    sell_pool: Pool,
    quote_asset: object,
    base_asset: object,
    input_amount: Decimal,
    min_output: Decimal,
) -> SimulationResult:
    """Dry-run the round trip against current reserves.

    Mirrors what the transaction would do on chain, including the minimum-output
    check, so a simulation failure is the same condition as an on-chain revert.
    """
    try:
        first = quote_swap(buy_pool, quote_asset, input_amount)  # type: ignore[arg-type]
        second = quote_swap(sell_pool, base_asset, first.output_amount)  # type: ignore[arg-type]
    except PoolUnusable as exc:
        return SimulationResult(False, ZERO, f"pool unusable: {exc}")
    if second.output_amount < min_output:
        return SimulationResult(
            False,
            second.output_amount,
            f"output {second.output_amount} below minimum {min_output}",
        )
    return SimulationResult(True, second.output_amount)


class PaperChain:
    """A simulated chain: submission, competition, inclusion and reverts.

    Deterministic under a seed. Pessimistic by construction — where the model is
    uncertain it errs against us.
    """

    def __init__(
        self,
        gas: GasModel,
        inclusion: InclusionModel,
        seed: int,
        *,
        unknown_probability: float = 0.005,
    ) -> None:
        self._gas = gas
        self._inclusion = inclusion
        self._rng = random.Random(seed)
        self._unknown_probability = unknown_probability
        self._forced: str | None = None
        self.transactions: dict[str, ArbitrageTx] = {}

    def inject_failure(self, kind: str | None) -> None:
        """Force the next submission to fail specifically. For chaos tests."""
        if kind not in (None, "revert", "dropped", "unknown", "congestion"):
            raise ValueError(f"unknown injected failure: {kind!r}")
        self._forced = kind

    def submit(
        self,
        tx: ArbitrageTx,
        buy_pool: Pool,
        sell_pool: Pool,
        quote_asset: object,
        base_asset: object,
        *,
        now: datetime,
    ) -> ArbitrageTx:
        """Submit a transaction, applying competition, inclusion and reverts.

        Duplicate submissions are refused by idempotency key: this is what makes
        a retry after an ambiguous response safe.
        """
        existing = self.transactions.get(tx.idempotency_key)
        if existing is not None:
            existing.detail = "duplicate submission ignored"
            return existing
        self.transactions[tx.idempotency_key] = tx
        tx.state = TxState.SUBMITTED
        tx.submitted_at = now

        forced, self._forced = self._forced, None
        gas = self._gas.with_congestion(Decimal(4)) if forced == "congestion" else self._gas

        if forced == "unknown" or (
            forced is None and self._rng.random() < self._unknown_probability
        ):
            # Submitted, but we never learned the outcome. Nothing may be
            # assumed and nothing may be resent.
            tx.state = TxState.UNKNOWN
            tx.detail = "no receipt; outcome unestablished"
            return tx

        if forced == "dropped" or (
            forced is None and self._rng.random() > float(self._inclusion.inclusion_probability)
        ):
            # Never included: no gas spent, but the opportunity is gone.
            tx.state = TxState.DROPPED
            tx.detail = "not included in a timely block"
            return tx

        if forced == "revert" or (
            forced is None and self._rng.random() < float(self._inclusion.revert_probability)
        ):
            # Someone was ordered ahead of us and moved the pools. We pay for
            # the attempt regardless.
            tx.state = TxState.REVERTED
            tx.gas_paid = (gas.total_cost * self._inclusion.revert_gas_fraction).quantize(
                _QUANT
            )
            tx.detail = "reverted: state moved before inclusion"
            return tx

        result = simulate(
            buy_pool, sell_pool, quote_asset, base_asset, tx.input_amount, tx.min_output
        )
        if not result.succeeds:
            tx.state = TxState.REVERTED
            tx.gas_paid = (gas.total_cost * self._inclusion.revert_gas_fraction).quantize(
                _QUANT
            )
            tx.detail = f"reverted on chain: {result.reason}"
            return tx

        tx.state = TxState.CONFIRMED
        tx.gas_paid = gas.total_cost
        tx.realised_output = result.expected_output
        return tx

    def receipt(self, key: str) -> ArbitrageTx | None:
        """Authoritative status query — the only way to resolve ``UNKNOWN``."""
        tx = self.transactions.get(key)
        if tx is not None:
            tx.status_queries += 1
        return tx


@dataclass(slots=True)
class ChainStats:
    """Outcome counts, used to derive measured execution probabilities."""

    confirmed: int = 0
    reverted: int = 0
    dropped: int = 0
    unknown: int = 0
    simulation_failures: int = 0
    gas_spent: Decimal = ZERO
    history: list[TxState] = field(default_factory=list)

    def record(self, tx: ArbitrageTx) -> None:
        self.history.append(tx.state)
        self.gas_spent += tx.gas_paid
        if tx.state is TxState.CONFIRMED:
            self.confirmed += 1
        elif tx.state is TxState.REVERTED:
            self.reverted += 1
        elif tx.state is TxState.DROPPED:
            self.dropped += 1
        elif tx.state is TxState.UNKNOWN:
            self.unknown += 1
        elif tx.state is TxState.SIMULATION_FAILED:
            self.simulation_failures += 1

    @property
    def attempts(self) -> int:
        return self.confirmed + self.reverted + self.dropped + self.unknown

    def summary(self) -> dict[str, str]:
        return {
            "attempts": str(self.attempts),
            "confirmed": str(self.confirmed),
            "reverted": str(self.reverted),
            "dropped": str(self.dropped),
            "unknown": str(self.unknown),
            "simulation_failures": str(self.simulation_failures),
            "gas_spent": str(self.gas_spent),
        }
