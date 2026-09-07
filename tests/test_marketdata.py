"""Market-data integrity.

The book must be *unusable* after any integrity failure, not approximately
right. Each test drives one failure mode and asserts that trading stops.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from arbcore.config.universe import BTC, COINBASE
from arbcore.marketdata.book import (
    BookInvalidation,
    BookSnapshot,
    BookStatus,
    BookUpdate,
    Level,
    OrderBook,
)
from arbcore.marketdata.clock import ClockTracker
from arbcore.marketdata.feed import FeedHealth, FeedRegistry, FeedState
from arbcore.marketdata.quality import score_book

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def snapshot(sequence: int = 10, *, at: datetime = NOW) -> BookSnapshot:
    return BookSnapshot(
        venue=COINBASE,
        asset=BTC,
        sequence=sequence,
        venue_time=at,
        received_at=at,
        bids=(Level("100", "1"), Level("99", "2")),
        asks=(Level("101", "1"), Level("102", "2")),
    )


def update(sequence: int, *, bids=(), asks=(), at: datetime = NOW) -> BookUpdate:
    return BookUpdate(
        venue=COINBASE,
        asset=BTC,
        sequence=sequence,
        venue_time=at,
        received_at=at,
        bids=bids,
        asks=asks,
    )


def fresh_book() -> OrderBook:
    book = OrderBook(venue=COINBASE, asset=BTC)
    book.apply_snapshot(snapshot())
    return book


def test_uninitialised_book_is_unusable():
    book = OrderBook(venue=COINBASE, asset=BTC)
    assert book.status is BookStatus.UNINITIALISED
    assert not book.usable
    # An update against a book we never snapshotted is refused outright.
    assert not book.apply_update(update(1))


def test_snapshot_makes_a_book_usable():
    book = fresh_book()
    assert book.usable
    assert book.best_bid.price == Decimal("100")
    assert book.best_ask.price == Decimal("101")


def test_sequence_gap_invalidates():
    book = fresh_book()
    assert not book.apply_update(update(13, bids=(Level("100", "3"),)))
    assert book.invalidation is BookInvalidation.SEQUENCE_GAP
    assert not book.usable
    assert book.gaps_detected == 1


def test_duplicate_is_dropped_without_invalidating():
    """A replayed message describes state we already have."""
    book = fresh_book()
    assert book.apply_update(update(10))
    assert book.usable
    assert book.duplicates_ignored == 1


def test_out_of_order_is_dropped_without_invalidating():
    book = fresh_book()
    assert book.apply_update(update(4))
    assert book.usable
    assert book.out_of_order_ignored == 1


def test_timestamp_regression_invalidates():
    book = fresh_book()
    stale = NOW - timedelta(hours=1)
    assert not book.apply_update(update(11, bids=(Level("100", "1"),), at=stale))
    assert book.invalidation is BookInvalidation.TIMESTAMP_REGRESSION


def test_crossed_book_invalidates():
    book = fresh_book()
    # A bid above the best ask cannot be real.
    assert not book.apply_update(update(11, bids=(Level("105", "1"),)))
    assert book.invalidation is BookInvalidation.CROSSED_BOOK


def test_emptying_a_side_invalidates():
    book = fresh_book()
    assert not book.apply_update(
        update(11, asks=(Level("101", "0"), Level("102", "0")))
    )
    assert book.invalidation is BookInvalidation.EMPTY_SIDE


def test_only_a_snapshot_recovers_an_invalid_book():
    book = fresh_book()
    book.apply_update(update(20))  # gap
    assert not book.usable
    assert not book.apply_update(update(21))
    book.apply_snapshot(snapshot(sequence=30))
    assert book.usable
    assert book.resyncs == 1


def test_zero_size_removes_a_level():
    book = fresh_book()
    book.apply_update(update(11, bids=(Level("99", "0"),)))
    assert [level.price for level in book.bids] == [Decimal("100")]


def test_message_for_the_wrong_instrument_is_an_error():
    from arbcore.config.universe import KRAKEN

    book = fresh_book()
    with pytest.raises(ValueError):
        book.apply_update(
            BookUpdate(venue=KRAKEN, asset=BTC, sequence=11, venue_time=NOW, received_at=NOW)
        )


def test_uninitialised_book_is_infinitely_old():
    book = OrderBook(venue=COINBASE, asset=BTC)
    assert book.age_ms(NOW) > 10**9


# --- feeds ---------------------------------------------------------------


def test_feed_stalls_when_the_heartbeat_lapses():
    feed = FeedHealth(venue=COINBASE, heartbeat_timeout_ms=1000)
    feed.connecting()
    feed.connected(NOW)
    assert feed.check(NOW) is FeedState.LIVE
    assert feed.check(NOW + timedelta(seconds=5)) is FeedState.STALLED
    assert feed.stalls == 1


def test_feed_recovers_on_the_next_message():
    feed = FeedHealth(venue=COINBASE, heartbeat_timeout_ms=1000)
    feed.connecting()
    feed.connected(NOW)
    feed.check(NOW + timedelta(seconds=5))
    feed.message(NOW + timedelta(seconds=6))
    assert feed.healthy


def test_backoff_grows_and_saturates():
    feed = FeedHealth(venue=COINBASE)
    seen = []
    for _ in range(8):
        seen.append(feed.next_backoff_ms())
        feed.connecting()
    assert seen[0] < seen[3]
    assert seen[-1] == feed.backoff_schedule_ms[-1]


def test_unregistered_feed_is_an_error():
    registry = FeedRegistry()
    with pytest.raises(KeyError):
        registry.get(COINBASE)


# --- clock ---------------------------------------------------------------


def test_clock_drift_is_signed_and_median_based():
    clock = ClockTracker(venue="coinbase")
    for offset in (100, 120, 110, 5000, 105):  # one outlier
        clock.observe(NOW + timedelta(milliseconds=offset), NOW)
    assert 100 <= clock.drift_ms <= 120  # the outlier does not dominate


def test_negative_drift_is_preserved():
    clock = ClockTracker(venue="coinbase")
    clock.observe(NOW, NOW + timedelta(milliseconds=300))
    assert clock.drift_ms == -300


def test_unmeasured_clock_is_flagged_rather_than_assumed_good():
    clock = ClockTracker(venue="coinbase")
    assert not clock.has_samples
    assert clock.drift_ms == 0  # but has_samples says not to trust it


# --- quality -------------------------------------------------------------


def _live_feed() -> FeedHealth:
    feed = FeedHealth(venue=COINBASE, heartbeat_timeout_ms=10_000)
    feed.connecting()
    feed.connected(NOW)
    return feed


def _measured_clock() -> ClockTracker:
    clock = ClockTracker(venue="coinbase")
    clock.observe(NOW, NOW)
    return clock


def test_quality_is_high_when_everything_is_healthy():
    factors = score_book(
        fresh_book(),
        _live_feed(),
        _measured_clock(),
        now=NOW,
        max_quote_age_ms=1000,
        max_clock_drift_ms=500,
    )
    assert factors.score > Decimal("0.95")


def test_invalid_book_zeroes_the_whole_score():
    """One disqualifying input must not be outvoted by healthy ones."""
    book = fresh_book()
    book.apply_update(update(99))  # gap
    factors = score_book(
        book,
        _live_feed(),
        _measured_clock(),
        now=NOW,
        max_quote_age_ms=1000,
        max_clock_drift_ms=500,
    )
    assert factors.score == Decimal(0)
    assert "book_integrity" in factors.zero_factors()


def test_unmeasured_clock_zeroes_the_score():
    factors = score_book(
        fresh_book(),
        _live_feed(),
        ClockTracker(venue="coinbase"),
        now=NOW,
        max_quote_age_ms=1000,
        max_clock_drift_ms=500,
    )
    assert factors.score == Decimal(0)


def test_stale_quote_decays_the_score_to_zero():
    factors = score_book(
        fresh_book(),
        _live_feed(),
        _measured_clock(),
        now=NOW + timedelta(seconds=5),
        max_quote_age_ms=1000,
        max_clock_drift_ms=500,
    )
    assert factors.quote_freshness == Decimal(0)
    assert factors.score == Decimal(0)
