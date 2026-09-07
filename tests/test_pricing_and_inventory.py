"""Executable pricing and inventory accounting."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from arbcore.config.universe import BTC, COINBASE, KRAKEN, USDC_ETHEREUM
from arbcore.domain.types import Side
from arbcore.inventory.manager import InsufficientBalance, InventoryManager
from arbcore.marketdata.book import BookSnapshot, Level, OrderBook
from arbcore.pricing.executable import (
    NotExecutable,
    depth_at_price,
    expected_slippage,
    walk_book,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def book_with(bids, asks) -> OrderBook:
    book = OrderBook(venue=COINBASE, asset=BTC)
    book.apply_snapshot(
        BookSnapshot(
            venue=COINBASE,
            asset=BTC,
            sequence=1,
            venue_time=NOW,
            received_at=NOW,
            bids=tuple(Level(p, s) for p, s in bids),
            asks=tuple(Level(p, s) for p, s in asks),
        )
    )
    return book


def standard_book() -> OrderBook:
    return book_with(
        [("100", "1"), ("99", "2"), ("98", "5")],
        [("101", "1"), ("102", "2"), ("103", "5")],
    )


def test_small_size_fills_at_top_of_book():
    quote = walk_book(standard_book(), Side.BUY, Decimal("0.5"))
    assert quote.vwap == Decimal("101")
    assert quote.complete


def test_large_size_walks_the_book_and_pays_more():
    """The headline number: best ask is not the price for our size."""
    quote = walk_book(standard_book(), Side.BUY, Decimal("3"))
    # 1@101 + 2@102 = 305 / 3
    assert quote.vwap == Decimal("101.66666667")
    assert quote.vwap > Decimal("101")
    assert quote.levels_consumed == 2


def test_price_impact_is_measured_against_top_of_book():
    quote = walk_book(standard_book(), Side.BUY, Decimal("3"))
    assert quote.price_impact > Decimal("0.006")


def test_selling_walks_down_the_bids():
    quote = walk_book(standard_book(), Side.SELL, Decimal("3"))
    assert quote.vwap == Decimal("99.33333333")
    assert quote.price_impact > Decimal(0)


def test_incomplete_fill_is_reported_not_hidden():
    quote = walk_book(standard_book(), Side.BUY, Decimal("100"))
    assert not quote.complete
    assert quote.filled_size == Decimal("8")


def test_unusable_book_has_no_price_at_all():
    book = OrderBook(venue=COINBASE, asset=BTC)
    with pytest.raises(NotExecutable):
        walk_book(book, Side.BUY, Decimal("1"))


def test_zero_size_is_refused():
    with pytest.raises(NotExecutable):
        walk_book(standard_book(), Side.BUY, Decimal("0"))


def test_slippage_floors_favourable_moves_at_zero():
    """An unexpectedly good fill is not a budget to spend elsewhere."""
    quote = walk_book(standard_book(), Side.BUY, Decimal("1"))
    assert expected_slippage(quote, Decimal("200")) == Decimal(0)
    assert expected_slippage(quote, Decimal("100")) > Decimal(0)


def test_depth_at_price_answers_the_exit_liquidity_question():
    assert depth_at_price(standard_book(), Side.BUY, Decimal("102")) == Decimal("3")
    assert depth_at_price(standard_book(), Side.SELL, Decimal("99")) == Decimal("3")


# --- inventory -----------------------------------------------------------


def manager() -> InventoryManager:
    inv = InventoryManager()
    inv.set_balance(COINBASE, BTC, Decimal("1"))
    inv.set_balance(COINBASE, USDC_ETHEREUM, Decimal("1000"))
    inv.set_balance(KRAKEN, BTC, Decimal("2"))
    return inv


def test_reservation_moves_free_to_reserved():
    inv = manager()
    inv.reserve("key-1", COINBASE, BTC, Decimal("0.4"))
    position = inv.position(COINBASE, BTC)
    assert position.free == Decimal("0.6")
    assert position.reserved == Decimal("0.4")
    assert position.total == Decimal("1")


def test_reserving_the_same_key_twice_is_idempotent():
    """This is what makes a retried request safe."""
    inv = manager()
    inv.reserve("key-1", COINBASE, BTC, Decimal("0.4"))
    inv.reserve("key-1", COINBASE, BTC, Decimal("0.4"))
    assert inv.position(COINBASE, BTC).reserved == Decimal("0.4")


def test_over_reservation_is_refused():
    inv = manager()
    with pytest.raises(InsufficientBalance):
        inv.reserve("key-1", COINBASE, BTC, Decimal("5"))


def test_minimum_reserve_is_withheld_from_spending():
    inv = manager()
    inv.min_reserve = {USDC_ETHEREUM: Decimal("900")}
    assert inv.spendable(COINBASE, USDC_ETHEREUM) == Decimal("100")
    with pytest.raises(InsufficientBalance):
        inv.reserve("key-1", COINBASE, USDC_ETHEREUM, Decimal("500"))


def test_release_returns_the_reservation():
    inv = manager()
    inv.reserve("key-1", COINBASE, BTC, Decimal("0.4"))
    inv.release("key-1")
    assert inv.position(COINBASE, BTC).free == Decimal("1")


def test_release_of_an_unknown_key_is_a_no_op():
    assert manager().release("never-existed") == Decimal(0)


def test_partial_settlement_returns_the_unspent_remainder():
    inv = manager()
    inv.reserve("key-1", COINBASE, USDC_ETHEREUM, Decimal("500"))
    inv.settle(
        "key-1",
        spent=Decimal("300"),
        received_venue=COINBASE,
        received_asset=BTC,
        received=Decimal("3"),
    )
    assert inv.position(COINBASE, USDC_ETHEREUM).free == Decimal("700")
    assert inv.position(COINBASE, USDC_ETHEREUM).reserved == Decimal("0")
    assert inv.position(COINBASE, BTC).free == Decimal("4")


def test_settling_more_than_reserved_is_refused():
    """The bug that a real venue reports as insufficient funds."""
    inv = manager()
    inv.reserve("key-1", COINBASE, USDC_ETHEREUM, Decimal("500"))
    with pytest.raises(ValueError):
        inv.settle(
            "key-1",
            spent=Decimal("501"),
            received_venue=COINBASE,
            received_asset=BTC,
            received=Decimal("1"),
        )


def test_settling_an_unknown_reservation_is_refused():
    with pytest.raises(KeyError):
        manager().settle(
            "ghost",
            spent=Decimal("1"),
            received_venue=COINBASE,
            received_asset=BTC,
            received=Decimal("1"),
        )


def test_untracked_position_is_zero_not_unlimited():
    inv = manager()
    assert inv.free(KRAKEN, USDC_ETHEREUM) == Decimal(0)
    with pytest.raises(InsufficientBalance):
        inv.reserve("k", KRAKEN, USDC_ETHEREUM, Decimal("1"))


def test_imbalance_reports_the_worst_venue_not_the_average():
    inv = manager()
    worst = inv.imbalance(BTC, {COINBASE: Decimal("1.5"), KRAKEN: Decimal("1.5")})
    assert worst == Decimal("0.5")
