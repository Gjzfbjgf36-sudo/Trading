"""Venue adapters.

No real exchange or DEX adapter exists. Writing one requires reading the
venue's *current* official documentation and recording its API version, auth
model, rate limits, WebSocket semantics, order semantics, fee schedule and
withdrawal constraints — none of which may be guessed. See docs/assumptions.md.

What exists here is the interface every adapter must satisfy, plus a synthetic
venue used for paper research and chaos testing.
"""
