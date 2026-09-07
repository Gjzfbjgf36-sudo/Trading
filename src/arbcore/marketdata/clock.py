"""Clock tracking.

We hold three clocks: ours, the venue's, and (where relevant) the chain's. They
disagree, and the disagreement is a tradeable-or-not signal rather than a
nuisance. Drift is signed — being ahead of a venue and behind it are different
failures — and the magnitude is what invalidates time-sensitive opportunities.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(slots=True)
class ClockTracker:
    """Rolling estimate of our offset from one venue's clock.

    ``drift_ms`` is ``local − venue``: positive means our clock runs ahead.
    The median of the window is used rather than the last sample, so one
    delayed message does not look like a clock problem.
    """

    venue: str
    window: int = 64
    samples: deque[int] = field(default_factory=lambda: deque(maxlen=64))

    def __post_init__(self) -> None:
        if self.window < 1:
            raise ValueError("window must be >= 1")
        self.samples = deque(self.samples, maxlen=self.window)

    def observe(self, local: datetime, venue_time: datetime) -> int:
        delta_ms = int((local - venue_time).total_seconds() * 1000)
        self.samples.append(delta_ms)
        return delta_ms

    @property
    def drift_ms(self) -> int:
        """Median signed drift. Zero when we have no samples at all.

        A caller must not read "no samples" as "no drift"; use
        :attr:`has_samples` to distinguish, since an unmeasured clock is an
        unknown clock.
        """
        if not self.samples:
            return 0
        ordered = sorted(self.samples)
        mid = len(ordered) // 2
        if len(ordered) % 2 == 1:
            return ordered[mid]
        return (ordered[mid - 1] + ordered[mid]) // 2

    @property
    def has_samples(self) -> bool:
        return bool(self.samples)
