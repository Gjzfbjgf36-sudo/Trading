"""arbcore — multi-market crypto arbitrage research platform.

Operating posture (see docs/architecture.md):
  * RESEARCH and PAPER modes only. Live execution is gated and disabled by default.
  * Fail-closed: absent, stale or contradictory information rejects the trade.
"""

__version__ = "0.1.0"
