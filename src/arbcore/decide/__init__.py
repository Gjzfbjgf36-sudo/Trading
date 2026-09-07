"""Decision support.

This package deliberately contains **no order path**. It answers one question —
"may this trade proceed, at what size, and if not, why not" — and records what
you committed to beforehand so that your own hit rate becomes measurable.

The signal comes from a rule you wrote and can backtest. Nothing here produces
a market view of its own.
"""
