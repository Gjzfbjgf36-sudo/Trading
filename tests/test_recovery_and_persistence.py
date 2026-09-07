"""Reconciliation, crash recovery and durable state."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from arbcore.config.universe import BTC, COINBASE, USDC_ETHEREUM
from arbcore.domain.decision import CheckResult, RejectReason, decide
from arbcore.domain.types import Side
from arbcore.execution.order import Fill, Order, OrderState
from arbcore.inventory.manager import InventoryManager
from arbcore.persistence.store import Store
from arbcore.recovery.reconciliation import MismatchKind, Reconciler, resolve_unknown_order
from arbcore.safety.safe_mode import SafeMode

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def an_order(state: OrderState = OrderState.ACKNOWLEDGED, client_id: str = "cid-1") -> Order:
    order = Order(
        client_id=client_id,
        opportunity_id="OPP-1",
        venue=COINBASE,
        asset=BTC,
        side=Side.BUY,
        quantity=Decimal("1"),
        decision_price=Decimal("100"),
        limit_price=Decimal("101"),
        created_at=NOW,
    )
    order.state = state
    return order


def inventory_with(amount: str) -> InventoryManager:
    inv = InventoryManager()
    inv.set_balance(COINBASE, BTC, Decimal(amount))
    return inv


# --- reconciliation ------------------------------------------------------


def test_matching_state_reconciles_clean():
    inv = inventory_with("1")
    result = Reconciler().reconcile(
        inv, {(COINBASE, BTC): Decimal("1")}, {}, {}, now=NOW
    )
    assert result.clean


def test_balance_mismatch_is_detected():
    inv = inventory_with("1")
    result = Reconciler().reconcile(
        inv, {(COINBASE, BTC): Decimal("0.9")}, {}, {}, now=NOW
    )
    assert not result.clean
    assert result.mismatches[0].kind is MismatchKind.BALANCE


def test_an_order_the_venue_has_but_we_do_not_is_detected():
    """The worst case: exposure we do not know about."""
    result = Reconciler().reconcile(
        inventory_with("1"),
        {(COINBASE, BTC): Decimal("1")},
        {},
        {"cid-1": an_order()},
        now=NOW,
    )
    assert any(m.kind is MismatchKind.ORDER_MISSING_LOCALLY for m in result.mismatches)


def test_a_live_local_order_absent_at_the_venue_is_detected():
    result = Reconciler().reconcile(
        inventory_with("1"),
        {(COINBASE, BTC): Decimal("1")},
        {"cid-1": an_order()},
        {},
        now=NOW,
    )
    assert any(m.kind is MismatchKind.ORDER_MISSING_AT_VENUE for m in result.mismatches)


def test_order_state_disagreement_is_detected():
    local = an_order(OrderState.ACKNOWLEDGED)
    remote = an_order(OrderState.FILLED)
    result = Reconciler().reconcile(
        inventory_with("1"),
        {(COINBASE, BTC): Decimal("1")},
        {"cid-1": local},
        {"cid-1": remote},
        now=NOW,
    )
    assert any(m.kind is MismatchKind.ORDER_STATE for m in result.mismatches)


def test_any_mismatch_engages_safe_mode():
    safe = SafeMode()
    safe.clear("operator")
    reconciler = Reconciler()
    result = reconciler.reconcile(
        inventory_with("1"), {(COINBASE, BTC): Decimal("0.5")}, {}, {}, now=NOW
    )
    assert not reconciler.enforce(result, safe)
    assert safe.active


def test_clean_reconciliation_leaves_trading_open():
    safe = SafeMode()
    safe.clear("operator")
    reconciler = Reconciler()
    result = reconciler.reconcile(
        inventory_with("1"), {(COINBASE, BTC): Decimal("1")}, {}, {}, now=NOW
    )
    assert reconciler.enforce(result, safe)
    assert not safe.active


def test_unknown_order_resolves_only_from_venue_truth():
    local = an_order(OrderState.UNKNOWN)
    remote = an_order(OrderState.FILLED)
    remote.fills = [Fill(Decimal("1"), Decimal("100"), Decimal("0.1"), NOW)]
    resolved = resolve_unknown_order(local, remote)
    assert resolved.state is OrderState.FILLED
    assert resolved.filled_quantity == Decimal("1")


def test_unknown_stays_unknown_when_the_venue_cannot_say_either():
    """A genuinely unresolvable state is reported, never assumed flat."""
    local = an_order(OrderState.UNKNOWN)
    assert resolve_unknown_order(local, None).state is OrderState.UNKNOWN
    assert resolve_unknown_order(local, an_order(OrderState.UNKNOWN)).state is OrderState.UNKNOWN


# --- persistence ---------------------------------------------------------


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.sqlite")
    s.start_session(
        "sess-1",
        started_at=NOW,
        environment="paper",
        trading_mode="paper",
        config={"seed": 1},
    )
    yield s
    s.close()


def test_decisions_are_persisted_with_their_reasons(store):
    store.record_decision(
        decide("OPP-1", [CheckResult.fail("slippage", RejectReason.SLIPPAGE_EXCEEDED)]),
        session_id="sess-1",
    )
    store.commit()
    assert store.rejection_histogram("sess-1") == {"SLIPPAGE_EXCEEDED": 1}


def test_orders_and_fills_survive_and_do_not_duplicate(store):
    order = an_order()
    order.record_fill(Fill(Decimal("0.5"), Decimal("100"), Decimal("0.05"), NOW))
    store.record_order(order, at=NOW, session_id="sess-1")
    store.record_order(order, at=NOW, session_id="sess-1")  # re-record
    store.commit()
    assert store.counts("sess-1")["orders"] == 1
    assert store.counts("sess-1")["fills"] == 1


def test_open_orders_are_what_recovery_must_chase(store):
    store.record_order(an_order(OrderState.UNKNOWN), at=NOW, session_id="sess-1")
    store.record_order(
        an_order(OrderState.FILLED, client_id="cid-2"), at=NOW, session_id="sess-1"
    )
    store.commit()
    open_rows = store.open_orders()
    assert [r.client_id for r in open_rows] == ["cid-1"]


def test_state_survives_reopening_the_database(tmp_path):
    """RAM is not a system of record."""
    path = tmp_path / "durable.sqlite"
    first = Store(path)
    first.start_session(
        "s", started_at=NOW, environment="paper", trading_mode="paper", config={}
    )
    first.record_order(an_order(OrderState.UNKNOWN), at=NOW, session_id="s")
    first.commit()
    first.close()

    second = Store(path)
    assert len(second.open_orders()) == 1
    assert second.integrity_check()
    second.close()


def test_persisted_records_contain_no_credentials(store):
    store.record_decision(
        decide("OPP-1", [CheckResult.ok("a"), CheckResult.ok("b")]), session_id="sess-1"
    )
    store.record_event("TEST", "INFO", "nothing sensitive", at=NOW, session_id="sess-1")
    store.record_balances(
        [(str(COINBASE), str(USDC_ETHEREUM), Decimal("1"), Decimal("0"))],
        at=NOW,
        session_id="sess-1",
    )
    store.commit()
    import sqlite3

    conn = sqlite3.connect(store.path)
    blob = "".join(str(row) for row in conn.execute("SELECT * FROM decisions")).lower()
    for forbidden in ("secret", "api_key", "private_key", "password"):
        assert forbidden not in blob
    conn.close()
