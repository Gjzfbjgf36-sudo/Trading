# Configuration and installation

## Install

```bash
git clone <repo> && cd Trading
python3 -m venv .venv && source .venv/bin/activate
make install
make check
```

Requires Python 3.11+. The only runtime dependency is PyYAML.

## Runtime posture

Set via environment variables (see `.env.example`). `.env` is git-ignored.

| Variable | Values | Default |
|---|---|---|
| `ARBCORE_ENV` | `development` `testing` `paper` `canary` `production` | `development` |
| `ARBCORE_TRADING_MODE` | `research` `paper` `canary` `live` | `research` |
| `ARBCORE_LIVE_TRADING_ENABLED` | `true`/`false` | `false` |
| `ARBCORE_MANUAL_APPROVAL_REF` | free text | empty |
| `ARBCORE_READY_*` (8 flags) | `true`/`false` | `false` |

Flag parsing is strict: only `1`, `true`, `yes`, `on` (case-insensitive) read as
true. Anything else — including a typo — reads as false, so a mistyped flag
fails safe.

`profile_from_env({})` on an empty environment yields development + research +
live-off. Missing configuration produces the least-privileged profile, not an
error and not a permissive default.

## Risk limits

`config/risk_limits.paper.yaml`. One file per environment; a production file
must never be loaded by a development process, and vice versa.

Loading validates:

* every key present, no unknown keys (a typo is an error, not a silent default);
* fractions within `[0, 1]`, sizes strictly positive, integers positive;
* `cost_safety_factor ≥ 1`;
* `max_trade_size ≤ max_total_exposure`, and each concentration ceiling
  `≤ max_total_exposure`.

The shipped values are conservative starting points for paper research, **not
recommendations**. Every change must be logged in `docs/model-governance.md`.

## Whitelists

`config/universe.py` builds the Phase 1 whitelist: BTC, ETH, SOL, USDC
(Ethereum + Solana), USDT (Ethereum); Coinbase, Kraken and Binance as
*candidates* with no adapters. No DEX or bridge protocol is whitelisted —
admission requires verified contract addresses, which Phase 4 supplies from
official documentation.

Stablecoin mint/freeze/transfer-restriction findings are recorded as `UNKNOWN`.
That is the honest status today and it is visible in code rather than assumed
away.

## Environment separation

Development, testing, paper, canary and production are distinct environments
with distinct credentials and distinct limit files. `RuntimeProfile` refuses
combinations that would let a lower environment run a real-money mode.
