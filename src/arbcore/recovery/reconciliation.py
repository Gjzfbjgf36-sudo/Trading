"""Reconciliation.

Our database is a *claim* about the world. The venue and the chain are the
world. When they disagree, the world wins and we stop trading until a human
has looked.

Reconciliation is deliberately not "repair": it detects and halts. Automatic
repair of a discrepancy we do not understand is how a reporting bug becomes a
position.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from ..domain.types import ZERO, AssetId, VenueId, to_decimal
from ..execution.order import Order, OrderState
from ..inventory.manager import InventoryManager
from ..safety.safe_mode import SafeMode, SafeModeTrigger


class MismatchKind(enum.StrEnum):
    BALANCE = "BALANCE"
    ORDER_STATE = "ORDER_STATE"
    ORDER_MISSING_LOCALLY = "ORDER_MISSING_LOCALLY"
    ORDER_MISSING_AT_VENUE = "ORDER_MISSING_AT_VENUE"
    UNRESOLVED_ORDER = "UNRESOLVED_ORDER"


@dataclass(frozen=True, slots=True)
class Mismatch:
    kind: MismatchKind
    detail: str
    internal: str
    external: str


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    at: datetime
    mismatches: tuple[Mismatch, ...]
    balances_checked: int
    orders_checked: int

    @property
    def clean(self) -> bool:
        return not self.mismatches

    def as_dict(self) -> dict[str, object]:
        return {
            "at": self.at.isoformat(),
            "clean": self.clean,
            "balances_checked": self.balances_checked,
            "orders_checked": self.orders_checked,
            "mismatches": [
                {
                    "kind": str(m.kind),
                    "detail": m.detail,
                    "internal": m.internal,
                    "external": m.external,
                }
                for m in self.mismatches
            ],
        }


@dataclass(slots=True)
class Reconciler:
    """Compares internal state against venue-reported truth.

    ``tolerance`` exists because venues round; it is an equality tolerance, not
    a licence to ignore a drift. Anything outside it is a mismatch, full stop.
    """

    tolerance: Decimal = Decimal("0.00000001")
    history: list[ReconciliationResult] = field(default_factory=list)

    def reconcile(
        self,
        inventory: InventoryManager,
        venue_balances: dict[tuple[VenueId, AssetId], Decimal],
        local_orders: dict[str, Order],
        venue_orders: dict[str, Order],
        *,
        now: datetime,
    ) -> ReconciliationResult:
        mismatches: list[Mismatch] = []

        # --- balances --------------------------------------------------
        checked_keys = set(venue_balances) | set(inventory.positions)
        for key in sorted(checked_keys, key=lambda k: (str(k[0]), str(k[1]))):
            venue, asset = key
            internal = (
                inventory.positions[key].total if key in inventory.positions else ZERO
            )
            external = to_decimal(venue_balances.get(key, ZERO))
            if abs(internal - external) > self.tolerance:
                mismatches.append(
                    Mismatch(
                        kind=MismatchKind.BALANCE,
                        detail=f"{venue}/{asset} balance disagrees",
                        internal=str(internal),
                        external=str(external),
                    )
                )

        # --- orders ----------------------------------------------------
        for client_id, local in sorted(local_orders.items()):
            remote = venue_orders.get(client_id)
            if remote is None:
                if not local.is_terminal:
                    mismatches.append(
                        Mismatch(
                            kind=MismatchKind.ORDER_MISSING_AT_VENUE,
                            detail=f"{client_id} is live locally but unknown at the venue",
                            internal=str(local.state),
                            external="ABSENT",
                        )
                    )
                continue
            if remote.state is not local.state:
                mismatches.append(
                    Mismatch(
                        kind=MismatchKind.ORDER_STATE,
                        detail=f"{client_id} state disagrees",
                        internal=str(local.state),
                        external=str(remote.state),
                    )
                )
            if abs(remote.filled_quantity - local.filled_quantity) > self.tolerance:
                mismatches.append(
                    Mismatch(
                        kind=MismatchKind.ORDER_STATE,
                        detail=f"{client_id} filled quantity disagrees",
                        internal=str(local.filled_quantity),
                        external=str(remote.filled_quantity),
                    )
                )
            if local.state is OrderState.UNKNOWN:
                mismatches.append(
                    Mismatch(
                        kind=MismatchKind.UNRESOLVED_ORDER,
                        detail=f"{client_id} is in UNKNOWN state and needs resolution",
                        internal="UNKNOWN",
                        external=str(remote.state),
                    )
                )

        for client_id in sorted(set(venue_orders) - set(local_orders)):
            # An order at the venue we have no record of is the worst case: we
            # may be exposed without knowing it.
            mismatches.append(
                Mismatch(
                    kind=MismatchKind.ORDER_MISSING_LOCALLY,
                    detail=f"{client_id} exists at the venue but not locally",
                    internal="ABSENT",
                    external=str(venue_orders[client_id].state),
                )
            )

        result = ReconciliationResult(
            at=now,
            mismatches=tuple(mismatches),
            balances_checked=len(checked_keys),
            orders_checked=len(set(local_orders) | set(venue_orders)),
        )
        self.history.append(result)
        return result

    def enforce(self, result: ReconciliationResult, safe_mode: SafeMode) -> bool:
        """Engage SAFE MODE on any mismatch. Returns True if trading may continue."""
        if result.clean:
            return True
        kinds = ", ".join(sorted({str(m.kind) for m in result.mismatches}))
        safe_mode.engage(
            SafeModeTrigger.RECONCILIATION_MISMATCH,
            f"{len(result.mismatches)} mismatch(es): {kinds}",
            now=result.at,
        )
        return False


def resolve_unknown_order(local: Order, venue_view: Order | None) -> Order:
    """Resolve an ``UNKNOWN`` order from authoritative venue state.

    This is the *only* permitted way out of UNKNOWN. If the venue has no view
    either, the order stays UNKNOWN — a genuinely unresolvable state is
    reported as such rather than assumed flat.
    """
    if local.state is not OrderState.UNKNOWN:
        return local
    if venue_view is None:
        return local
    local.status_queries += 1
    if venue_view.state is OrderState.UNKNOWN:
        return local
    local.fills = list(venue_view.fills)
    local.state = venue_view.state
    local.venue_order_id = venue_view.venue_order_id
    return local
