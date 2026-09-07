"""Feed health and reconnection.

A feed that has stopped delivering looks exactly like a quiet market until you
check. Heartbeats make the difference observable, and the reconnect policy is
deliberately unhurried: reconnect storms get an account rate-limited or banned,
which turns a transient outage into a long one.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..domain.types import VenueId


class FeedState(enum.StrEnum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    LIVE = "LIVE"
    #: Connected but not delivering within the heartbeat budget.
    STALLED = "STALLED"


@dataclass(slots=True)
class FeedHealth:
    """Tracks one venue feed's liveness and reconnection backoff."""

    venue: VenueId
    heartbeat_timeout_ms: int = 5_000
    state: FeedState = FeedState.DISCONNECTED
    last_message_at: datetime | None = None
    connect_attempts: int = 0
    disconnects: int = 0
    stalls: int = 0
    #: Backoff schedule in milliseconds; the last value repeats indefinitely.
    backoff_schedule_ms: tuple[int, ...] = (1_000, 2_000, 5_000, 10_000, 30_000)
    messages: int = 0

    def __post_init__(self) -> None:
        if self.heartbeat_timeout_ms <= 0:
            raise ValueError("heartbeat_timeout_ms must be > 0")
        if not self.backoff_schedule_ms:
            raise ValueError("a backoff schedule is required")

    def connecting(self) -> None:
        self.state = FeedState.CONNECTING
        self.connect_attempts += 1

    def connected(self, now: datetime) -> None:
        self.state = FeedState.LIVE
        self.last_message_at = now

    def disconnected(self) -> None:
        if self.state is not FeedState.DISCONNECTED:
            self.disconnects += 1
        self.state = FeedState.DISCONNECTED
        self.last_message_at = None

    def message(self, now: datetime) -> None:
        self.messages += 1
        self.last_message_at = now
        if self.state is FeedState.STALLED:
            self.state = FeedState.LIVE

    def check(self, now: datetime) -> FeedState:
        """Re-evaluate liveness against the heartbeat budget."""
        if self.state in (FeedState.DISCONNECTED, FeedState.CONNECTING):
            return self.state
        if self.last_message_at is None:
            self.state = FeedState.STALLED
            return self.state
        overdue = now - self.last_message_at > timedelta(milliseconds=self.heartbeat_timeout_ms)
        if overdue and self.state is not FeedState.STALLED:
            self.stalls += 1
            self.state = FeedState.STALLED
        return self.state

    @property
    def healthy(self) -> bool:
        return self.state is FeedState.LIVE

    def next_backoff_ms(self) -> int:
        """Backoff for the *next* attempt, saturating at the final step."""
        index = min(self.connect_attempts, len(self.backoff_schedule_ms) - 1)
        return self.backoff_schedule_ms[index]

    def reset_backoff(self) -> None:
        self.connect_attempts = 0


@dataclass(slots=True)
class FeedRegistry:
    """All feeds. Absence of a feed is a failure, not a neutral state."""

    feeds: dict[VenueId, FeedHealth] = field(default_factory=dict)

    def add(self, feed: FeedHealth) -> None:
        if feed.venue in self.feeds:
            raise ValueError(f"duplicate feed for {feed.venue}")
        self.feeds[feed.venue] = feed

    def get(self, venue: VenueId) -> FeedHealth:
        try:
            return self.feeds[venue]
        except KeyError:
            raise KeyError(f"no feed registered for {venue}") from None

    def check_all(self, now: datetime) -> tuple[FeedHealth, ...]:
        """Returns the feeds that are *not* healthy."""
        return tuple(f for f in self.feeds.values() if f.check(now) is not FeedState.LIVE)
