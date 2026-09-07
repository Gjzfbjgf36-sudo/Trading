"""Orders and their state.

Two rules are structural here rather than procedural:

* **ORDER SENT ≠ ORDER FILLED.** State only advances on venue-confirmed
  information; nothing infers a fill from a successful HTTP call.
* **A timeout never creates a second order.** It produces `UNKNOWN`, which is
  resolved by querying the venue, never by resending.
"""

from __future__ import annotations

import enum
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from ..domain.types import ZERO, AssetId, Side, VenueId, to_decimal


class OrderState(enum.StrEnum):
    #: Created locally, not yet sent. Balance is already reserved.
    PENDING = "PENDING"
    SENT = "SENT"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    #: We do not know whether the venue has this order. Never retried blindly.
    UNKNOWN = "UNKNOWN"


TERMINAL_ORDER_STATES: frozenset[OrderState] = frozenset(
    {OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED}
)

_ORDER_TRANSITIONS: dict[OrderState, frozenset[OrderState]] = {
    OrderState.PENDING: frozenset({OrderState.SENT, OrderState.REJECTED}),
    OrderState.SENT: frozenset(
        {
            OrderState.ACKNOWLEDGED,
            OrderState.REJECTED,
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.UNKNOWN,
        }
    ),
    OrderState.ACKNOWLEDGED: frozenset(
        {
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.UNKNOWN,
        }
    ),
    OrderState.PARTIALLY_FILLED: frozenset(
        {OrderState.PARTIALLY_FILLED, OrderState.FILLED, OrderState.CANCELLED, OrderState.UNKNOWN}
    ),
    # UNKNOWN resolves only by querying the venue, into whatever is true.
    OrderState.UNKNOWN: frozenset(
        {
            OrderState.ACKNOWLEDGED,
            OrderState.PARTIALLY_FILLED,
            OrderState.FILLED,
            OrderState.CANCELLED,
            OrderState.REJECTED,
        }
    ),
    OrderState.FILLED: frozenset(),
    OrderState.CANCELLED: frozenset(),
    OrderState.REJECTED: frozenset(),
}


class IllegalOrderTransition(RuntimeError):
    pass


def client_order_id(opportunity_id: str, leg: str, venue: VenueId) -> str:
    """Deterministic idempotency key.

    Derived from the opportunity, leg and venue rather than randomly generated,
    so that a process which crashes and restarts computes the *same* key and
    recognises its own in-flight order instead of placing a second one.
    """
    material = f"{opportunity_id}|{leg}|{venue}".encode()
    return "arb-" + hashlib.sha256(material).hexdigest()[:24]


@dataclass(frozen=True, slots=True)
class Fill:
    quantity: Decimal
    price: Decimal
    fee: Decimal
    at: datetime

    def __post_init__(self) -> None:
        for name in ("quantity", "price", "fee"):
            value = to_decimal(getattr(self, name))
            object.__setattr__(self, name, value)
        if self.quantity <= ZERO or self.price <= ZERO or self.fee < ZERO:
            raise ValueError("implausible fill")

    @property
    def notional(self) -> Decimal:
        return self.quantity * self.price


@dataclass(slots=True)
class Order:
    """One leg of an opportunity."""

    client_id: str
    opportunity_id: str
    venue: VenueId
    asset: AssetId
    side: Side
    quantity: Decimal
    #: Price we decided at; used to measure slippage after the fact.
    decision_price: Decimal
    #: Worst acceptable average price. Fills beyond it are refused.
    limit_price: Decimal
    state: OrderState = OrderState.PENDING
    fills: list[Fill] = field(default_factory=list)
    venue_order_id: str | None = None
    reject_reason: str = ""
    created_at: datetime | None = None
    #: Incremented on every *state query*, never used to gate a resend.
    status_queries: int = 0

    def __post_init__(self) -> None:
        for name in ("quantity", "decision_price", "limit_price"):
            setattr(self, name, to_decimal(getattr(self, name)))
        if self.quantity <= ZERO:
            raise ValueError("order quantity must be > 0")

    def transition(self, target: OrderState, *, detail: str = "") -> None:
        if target not in _ORDER_TRANSITIONS[self.state]:
            raise IllegalOrderTransition(
                f"{self.client_id}: {self.state} -> {target} is not permitted"
            )
        if target is OrderState.REJECTED and detail:
            self.reject_reason = detail
        self.state = target

    def record_fill(self, fill: Fill) -> None:
        """Record a venue-confirmed fill and advance state accordingly."""
        if self.filled_quantity + fill.quantity > self.quantity:
            raise ValueError(
                f"{self.client_id}: fills ({self.filled_quantity + fill.quantity}) "
                f"would exceed order quantity ({self.quantity})"
            )
        self.fills.append(fill)
        target = (
            OrderState.FILLED
            if self.filled_quantity >= self.quantity
            else OrderState.PARTIALLY_FILLED
        )
        if target in _ORDER_TRANSITIONS[self.state]:
            self.state = target

    @property
    def filled_quantity(self) -> Decimal:
        return sum((f.quantity for f in self.fills), start=ZERO)

    @property
    def unfilled_quantity(self) -> Decimal:
        return self.quantity - self.filled_quantity

    @property
    def average_price(self) -> Decimal | None:
        """``None`` when nothing filled — never zero, which would read as free."""
        filled = self.filled_quantity
        if filled <= ZERO:
            return None
        return sum((f.notional for f in self.fills), start=ZERO) / filled

    @property
    def fees_paid(self) -> Decimal:
        return sum((f.fee for f in self.fills), start=ZERO)

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_ORDER_STATES

    @property
    def needs_reconciliation(self) -> bool:
        return self.state is OrderState.UNKNOWN
