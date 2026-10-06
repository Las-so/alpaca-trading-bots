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

## Bot 4 — DCA Drip (`bots/dca_drip.py`), added Oct 6 2026

A fixed-dollar accumulation bot, not a reactive one — buys a set dollar
amount of a symbol on a fixed cadence regardless of price. This needed two
real architecture additions, not just a new bot file:

- **Fixed-dollar sizing** (`RiskEngine.check_new_entry`'s new
  `desired_dollar_amount` param, `Bot.desired_dollar_amount()` hook): without
  this, the generic risk-sized max would scale a drip up to 10% of equity
  per buy, which defeats the entire point of a drip. Caught before shipping
  by actually computing the sizing, not just reading the code.
- **Notional (fractional-share) orders** (`AlpacaClient.submit_notional_order`,
  `RiskEngine.check_new_notional_entry`, `Bot.uses_notional_order()` hook):
  a $50 buy at a $764 share price floors to 0 whole shares under the other
  three bots' integer-qty bracket-order path — which would have made this
  bot a permanent no-op forever. Alpaca's bracket orders (take-profit +
  stop-loss legs) require whole shares, but a drip has no stop/target
  anyway, so it uses a plain notional market order instead. Caught the same
  way: by actually running `run_once()` against the real account and
  getting qty=0, not by reading the code and assuming it would work.

Both bugs were caught on the FIRST real run against live data, which is
the entire reason this account's standing rule is "run it for real before
calling it done" rather than "it imports clean and the logic reads right."

## Backtest harness (`scripts/backtest.py`), added Oct 6 2026

Runs bots against real historical Alpaca daily bars (via `AlpacaClient.get_bars`'s
new `start`/`end` support) instead of live data, so a strategy's real
track record is known before paper (let alone live) money rides on it.

**What it can and can't backtest, verified directly, not assumed:**
- `orb`, `dca_drip`: yes — both read ordinary equity bars.
- `insider_filings`: no — its only data source is SEC EDGAR's LIVE
  "getcurrent" filings feed. There is no historical equivalent wired in;
  backtesting it for real needs a different SEC data source (e.g. the
  full-text search historical index) and a rewrite of its fetch path.
- `futures_reversal`: no — **real finding, not a guess**: alpaca-py has no
  futures historical data client at all as of its latest release (0.44.0,
  checked Oct 6 2026). Confirmed two independent ways: introspecting the
  installed package directly (no `futures` module anywhere in it) and
  reading Alpaca's own SDK docs site (Market Data Reference lists stock,
  crypto, options — no futures). The ORIGINAL code comment on this bot said
  the futures client's "exact class name has moved between releases" —
  that was an unverified assumption, written without ever actually checking,
  and it was wrong: the capability doesn't exist yet, period. Lesson: a
  third-party SDK's capability claim gets checked by introspecting the
  installed package and reading its official docs before writing anything
  about it in a comment, never stated as "probably moved" from a guess.

**Known simplification, stated plainly:** the ORB backtest uses a daily-bar
rolling-range proxy, not the live bot's first-15-minutes-of-the-day logic
(1Min bars this far back are a separate, heavier historical pull). This
is good enough to compare relative performance across bots, not a claim
that it replays the exact live strategy bar-for-bar — don't read its
numbers as "what ORB would have actually earned live."

**Real result from the first live run (180 days, SPY, $50/week drip):**
orb: 0 trades, 0% return (one signal fired, Jev declined it). dca_drip:
26 buys, $1,300 invested, 8.01% return. This is one run over one window on
one symbol — not a verdict on either strategy, just the first real data
point. Re-run with different windows/symbols before drawing conclusions,
and don't add a 5th bot before these two have more backtest history behind
them (see the standing "don't start more things before finishing the
current one" pattern).

## What "done" looks like for this repo

Per the standing rule for this account: nothing is handed off as finished
until `scripts/e2e_smoke_test.py` has actually run against real Alpaca
paper keys and a real Jev call, and a second, independent review pass has
looked at the risk engine specifically (that file is the one place a bug
has real consequences).
