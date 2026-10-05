# Architecture notes

## Why this split (code vs Jev)

The video's own framing, confirmed from the real transcript (not a
secondary summary — see the Oct 5 2026 mistake writeup in Claude's
memory on this): Claude writes the strategy code and the risk-rule
enforcement; Jev makes the live per-trade judgment call; the pipeline is
backtest -> Alpaca paper trading ($10k sim capital per bot) -> live
capital, deployed with a dashboard that updates every ~2 seconds.

This repo mirrors that: `risk.py` has zero model calls in it on purpose.
A drawdown circuit breaker, a position-size cap, a stop-loss — these are
things code must enforce unconditionally, because a judgment model can be
wrong, can be prompted into a bad answer, or can simply have an off day.
Jev's one job per candidate trade is the part code is bad at: "does this
specific setup, in this specific context, look worth taking."

## Open design decisions (flag these back to Larry before going live, not after)

- **Position sizing defaults** (`RiskLimits` in `risk.py`): 10% max per
  position, 50% max total exposure, 3% daily drawdown circuit breaker, 2%
  stop-loss. These are reasonable starting defaults, not validated against
  Larry's existing framework (Aristotle Investments / Trading Bondsman —
  see `/areas/trading-desk.md`). Confirm before paper trading starts.
- **Futures contract roll**: `futures_reversal.py` hardcodes `ESZ5` as the
  front-month symbol. Continuous-contract roll handling isn't built yet —
  needs a real roll calendar before this runs unattended for more than a
  few weeks.
- **Insider Filings polling interval** (currently 300s): SEC's own feed
  updates on filing cadence, not a fixed schedule — this could be tuned
  down once real filing volume is observed.
- **Jev threshold tuning** (`threshold=0.55` in each bot's `ask_jev`):
  picked as a reasonable starting gate, not back-tested. Once paper trades
  accumulate, re-validate against outcomes per the standing "RE-RUN JEV
  AFTER RESULTS" rule.

## What "done" looks like for this repo

Per the standing rule for this account: nothing is handed off as finished
until `scripts/e2e_smoke_test.py` has actually run against real Alpaca
paper keys and a real Jev call, and a second, independent review pass has
looked at the risk engine specifically (that file is the one place a bug
has real consequences).
