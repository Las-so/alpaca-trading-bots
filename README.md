# alpaca-trading-bots

Three Alpaca paper-trading bots, cloned from the architecture in "I Turned
JEV AI Into A 24/7 Stock Trader" (youtube.com/watch?v=87q_P6iq9mk):

1. **Opening Range Breakout** (`src/alpaca_bots/bots/orb.py`) — equities
2. **Insider Filings** (`src/alpaca_bots/bots/insider_filings.py`) — equities, live SEC EDGAR Form 4 feed
3. **Futures Reversal** (`src/alpaca_bots/bots/futures_reversal.py`) — futures, mean-reversion

## Architecture (matches the video)

Claude's code owns every mechanical and safety-critical step. Jev is
consulted exactly once per candidate trade, for the one judgment code
can't make well: is this specific setup worth taking. Jev never sees
code and never places an order — it answers a typed question and code
decides what to do with the answer.

```
fetch bars (code) -> compute signal (code, pure math)
    -> ask Jev: "is this worth taking?" (judgment, one call)
    -> risk check (code, HARD VETO — Jev cannot override this)
    -> submit bracket order to Alpaca paper account (code)
    -> log decision + equity to state.json (code)
    -> dashboard polls state.json every 2s (matches the video's cadence)
```

Risk rules (`src/alpaca_bots/risk.py`) are enforced in code, not asked of
Jev: max position size as % of equity, max total exposure, a daily
drawdown circuit breaker that halts new entries, and a stop-loss on every
position. These are hard limits a model cannot talk its way around.

## Setup

1. **Alpaca paper account** (you do this part — I can't create third-party
   accounts on your behalf): sign up free at
   https://app.alpaca.markets/signup, then generate paper-trading keys at
   https://app.alpaca.markets/paper/dashboard/overview. Takes about 2
   minutes, no funding required for paper trading.
2. Copy `.env.example` to `.env` and paste in `ALPACA_API_KEY` /
   `ALPACA_SECRET_KEY`. Leave `TYPESAFE_API_KEY` blank — it already lives
   as a Windows user env var on this machine (see `../jev-cli`); make sure
   it's on (`jev status` from any terminal).
3. Install:
   ```
   uv sync
   ```
4. Live-test before trusting anything here:
   ```
   uv run python scripts/e2e_smoke_test.py
   ```
   This isn't a unit test — it makes real calls to the real Alpaca paper
   API and the real Jev API and prints PASS/FAIL per check. Nothing in
   this repo is "done" until this passes.
5. Run the dashboard:
   ```
   uv run uvicorn alpaca_bots.dashboard:app --app-dir src --reload --port 8800
   ```
   Open http://localhost:8800 — equity curve, live decision log, refreshes every 2s.
6. Run the bots (dry-run by default — logs decisions, places no orders):
   ```
   uv run python -m alpaca_bots.runner
   ```
   Flip `dry_run=False` in `runner.build_bots()` only after watching a
   session of dry-run decisions in the dashboard and agreeing with what
   it would have done.

## Status

- [x] Repo, risk engine, Jev client, Alpaca client, all three bots, dashboard, smoke test — written, import-clean, and construction-tested (every bot built with real client/risk args, not just imported).
- [x] `uv sync` run on this machine and dependency versions confirmed.
- [x] Alpaca paper account created and keys wired into `.env`.
- [x] `e2e_smoke_test.py` run against real keys and real live calls — Alpaca account call, Jev noul call, and ORB dry-run cycle all PASS. This is the actual "live test before handoff" gate, and it is met.
- [ ] Futures data client path verified against a funded futures-enabled paper account (alpaca-py's futures API moved between recent releases — see the comment in `alpaca_client.get_futures_bars`). Still open — needs a futures-enabled account, which paper equities accounts don't include by default.
- [ ] A session of dry-run decisions reviewed before any bot goes live (even in paper). Still open — this is your call, not a code task.

See `docs/ARCHITECTURE.md` for the full design notes and open questions.
