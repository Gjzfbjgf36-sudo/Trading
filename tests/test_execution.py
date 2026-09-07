"""Order lifecycle, idempotency and paper execution."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from arbcore.config.universe import BTC, COINBASE, KRAKEN
from arbcore.domain.types import Side
from arbcore.execution.order import (
    Fill,
    IllegalOrderTransition,
    Order,
    OrderState,
    client_order_id,
)
from arbcore.execution.paper import PaperMarketModel, PaperVenue, limit_price_for
from arbcore.marketdata.book import BookSnapshot, Level, OrderBook

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def deep_book() -> OrderBook:
    book = OrderBook(venue=COINBASE, asset=BTC)
    book.apply_snapshot(
        BookSnapshot(
            venue=COINBASE,
            asset=BTC,
            sequence=1,
            venue_time=NOW,
            received_at=NOW,
            bids=tuple(Level(str(100 - i), "50") for i in range(10)),
            asks=tuple(Level(str(101 + i), "50") for i in range(10)),
        )
    )
    return book


def an_order(**overrides) -> Order:
    kwargs = dict(
        client_id="cid-1",
        opportunity_id="OPP-1",
        venue=COINBASE,
        asset=BTC,
        side=Side.BUY,
        quantity=Decimal("1"),
        decision_price=Decimal("101"),
        limit_price=Decimal("110"),
        created_at=NOW,
    )
    kwargs.update(overrides)
    return Order(**kwargs)


# --- idempotency ---------------------------------------------------------


def test_client_order_id_is_deterministic():
    """A restarted process must compute the same id, not place a second order."""
    first = client_order_id("OPP-1", "buy", COINBASE)
    second = client_order_id("OPP-1", "buy", COINBASE)
    assert first == second


def test_client_order_id_separates_legs_and_venues():
    ids = {
        client_order_id("OPP-1", "buy", COINBASE),
        client_order_id("OPP-1", "sell", COINBASE),
        client_order_id("OPP-1", "buy", KRAKEN),
        client_order_id("OPP-2", "buy", COINBASE),
    }
    assert len(ids) == 4


def test_duplicate_submission_does_not_create_a_second_order():
    venue = PaperVenue(PaperMarketModel(competition_probability=0.0), seed=1)
    order = an_order()
    venue.submit(order, deep_book(), now=NOW)
    again = venue.submit(order, deep_book(), now=NOW)
    assert again.order is order
    assert "duplicate" in again.note
    assert len(venue.orders) == 1


# --- order state ---------------------------------------------------------


def test_order_sent_is_not_order_filled():
    order = an_order()
    order.transition(OrderState.SENT)
    assert order.filled_quantity == Decimal(0)
    assert order.average_price is None  # not zero, which would read as free


def test_illegal_order_transitions_are_refused():
    order = an_order()
    with pytest.raises(IllegalOrderTransition):
        order.transition(OrderState.FILLED)


def test_fills_cannot_exceed_the_order_quantity():
    order = an_order()
    order.transition(OrderState.SENT)
    order.transition(OrderState.ACKNOWLEDGED)
    order.record_fill(Fill(Decimal("0.6"), Decimal("101"), Decimal("0.1"), NOW))
    with pytest.raises(ValueError):
        order.record_fill(Fill(Decimal("0.6"), Decimal("101"), Decimal("0.1"), NOW))


def test_partial_then_full_fill_advances_state():
    order = an_order()
    order.transition(OrderState.SENT)
    order.transition(OrderState.ACKNOWLEDGED)
    order.record_fill(Fill(Decimal("0.5"), Decimal("101"), Decimal("0.05"), NOW))
    assert order.state is OrderState.PARTIALLY_FILLED
    order.record_fill(Fill(Decimal("0.5"), Decimal("102"), Decimal("0.05"), NOW))
    assert order.state is OrderState.FILLED
    assert order.average_price == Decimal("101.5")


def test_unknown_state_requires_reconciliation():
    order = an_order()
    order.transition(OrderState.SENT)
    order.transition(OrderState.UNKNOWN)
    assert order.needs_reconciliation
    assert not order.is_terminal


# --- paper execution -----------------------------------------------------


def test_normal_fill_costs_more_than_the_decision_price():
    """Latency drift is always against us; a kind simulator teaches nothing."""
    venue = PaperVenue(
        PaperMarketModel(competition_probability=0.0, partial_probability=0.0), seed=3
    )
    order = an_order()
    outcome = venue.submit(order, deep_book(), now=NOW)
    assert outcome.filled
    assert order.average_price >= order.decision_price


def test_limit_price_caps_the_damage():
    venue = PaperVenue(
        PaperMarketModel(competition_probability=0.0, partial_probability=0.0), seed=3
    )
    order = an_order(limit_price=Decimal("100"))  # below the best ask
    venue.submit(order, deep_book(), now=NOW)
    assert order.state is OrderState.CANCELLED
    assert order.filled_quantity == Decimal(0)


def test_injected_rejection():
    venue = PaperVenue(PaperMarketModel(), seed=3)
    venue.inject_failure("reject")
    order = an_order()
    venue.submit(order, deep_book(), now=NOW)
    assert order.state is OrderState.REJECTED


def test_injected_timeout_produces_unknown_not_a_retry():
    venue = PaperVenue(PaperMarketModel(), seed=3)
    venue.inject_failure("timeout")
    order = an_order()
    venue.submit(order, deep_book(), now=NOW)
    assert order.state is OrderState.UNKNOWN
    # The only permitted next step is asking the venue.
    assert venue.query(order.client_id) is order
    assert order.status_queries == 1


def test_injected_partial_fill():
    venue = PaperVenue(PaperMarketModel(competition_probability=0.0), seed=3)
    venue.inject_failure("partial")
    order = an_order()
    venue.submit(order, deep_book(), now=NOW)
    assert Decimal(0) < order.filled_quantity < order.quantity


def test_unusable_book_cancels_rather_than_inventing_a_price():
    venue = PaperVenue(PaperMarketModel(), seed=3)
    order = an_order()
    venue.submit(order, OrderBook(venue=COINBASE, asset=BTC), now=NOW)
    assert order.filled_quantity == Decimal(0)


def test_competition_can_take_the_whole_opportunity():
    """A visible spread is the spread everyone else can also see."""
    venue = PaperVenue(
        PaperMarketModel(competition_probability=1.0, competition_fill_max=0.0), seed=3
    )
    order = an_order()
    outcome = venue.submit(order, deep_book(), now=NOW)
    assert not outcome.filled
    assert "faster participant" in outcome.note


def test_execution_is_deterministic_for_a_given_seed():
    results = []
    for _ in range(2):
        venue = PaperVenue(PaperMarketModel(), seed=99)
        order = an_order()
        venue.submit(order, deep_book(), now=NOW)
        results.append((str(order.state), str(order.filled_quantity)))
    assert results[0] == results[1]


def test_limit_price_helper_moves_against_us_on_both_sides():
    assert limit_price_for(Decimal("100"), Side.BUY, Decimal("0.01")) == Decimal("101")
    assert limit_price_for(Decimal("100"), Side.SELL, Decimal("0.01")) == Decimal("99")
