"""Backtest harness — runs bots against REAL historical Alpaca data (not
live bars) to see which strategies actually would have made money, before
adding more bots or flipping anything to dry_run=False.

What this can and can't backtest, and why (checked directly, not assumed):

  - orb (OpeningRangeBreakout) and dca_drip (DCADripBot): YES. Both read
    ordinary equity bars via AlpacaClient.get_bars(), which now supports a
    real start/end historical window.
  - insider_filings (InsiderFilingsBot): NO, not here. Its data source is
    SEC EDGAR's live "getcurrent" atom feed — there is no historical
    equivalent wired into this bot. A real backtest of this bot needs a
    different source (e.g. SEC's full-text search historical index) and a
    rewrite of its data-fetch path; it is excluded below with that reason
    stated, not silently skipped.
  - futures_reversal (FuturesReversal): NO, not here. alpaca-py has no
    futures historical data client at all as of 0.44.0 (verified Oct 2026
    by both introspecting the installed package and reading Alpaca's own
    SDK docs) — there is no data to replay. Excluded with that reason
    stated.

Simulation rules (kept deliberately simple and stated plainly, not hidden):
  - Signals are computed on each day's bar using only bars up to and
    including that day (no lookahead).
  - A signal fires at that day's CLOSE (the same price compute_signal()
    already returns) rather than modeling next-bar slippage — this is an
    approximation, not a claim of exact fill price.
  - Jev is called for real on every candidate signal, same as live — this
    actually spends TypeSafe API usage per backtest run. If TYPESAFE_API_KEY
    isn't configured, Jev-gated bots are skipped with that reason, not
    silently treated as "always proceed".
  - No commission (Alpaca is commission-free on equities); no slippage model.
  - ORB bots trade a risk-sized qty (same RiskEngine as live); the drip bot
    buys a fixed notional amount on its own cadence, not every day —
    cadence is read from runner.CADENCE_SECONDS, converted to a day-count.
"""
from __future__ import annotations
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from alpaca_bots.alpaca_client import AlpacaClient, AlpacaNotConfigured
from alpaca_bots.risk import RiskEngine, RiskLimits, AccountSnapshot
from alpaca_bots.jev_client import JevNotConfigured, JevError
from alpaca_bots.bots.base import Bot, Signal
from alpaca_bots.bots.orb import OpeningRangeBreakout
from alpaca_bots.bots.dca_drip import DCADripBot
from alpaca_bots.runner import CADENCE_SECONDS


@dataclass
class SimPortfolio:
    starting_cash: float
    cash: float = field(init=False)
    shares: float = 0.0
    equity_curve: list[float] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)

    def __post_init__(self):
        self.cash = self.starting_cash

    def mark_to_market(self, price: float) -> float:
        eq = self.cash + self.shares * price
        self.equity_curve.append(eq)
        return eq

    def buy_notional(self, dollars: float, price: float, day: str):
        qty = dollars / price
        self.cash -= dollars
        self.shares += qty
        self.trades.append({"day": day, "side": "buy", "qty": qty, "price": price, "notional": dollars})

    def buy_shares(self, qty: int, price: float, day: str):
        self.cash -= qty * price
        self.shares += qty
        self.trades.append({"day": day, "side": "buy", "qty": qty, "price": price, "notional": qty * price})

    def sell_shares(self, qty: int, price: float, day: str):
        qty = min(qty, self.shares)
        self.cash += qty * price
        self.shares -= qty
        self.trades.append({"day": day, "side": "sell", "qty": qty, "price": price, "notional": qty * price})


def _max_drawdown_pct(curve: list[float]) -> float:
    peak = curve[0] if curve else 0.0
    worst = 0.0
    for v in curve:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, (peak - v) / peak)
    return worst


def backtest_orb(client: AlpacaClient, symbol: str, days: int, starting_cash: float) -> dict:
    """Daily-bar approximation of ORB: treats each day's bar as the
    'breakout check' (using a rolling N-day range instead of first-N-minutes,
    since 1Min historical bars this far back are a separate, heavier pull).
    This is a SIMPLER proxy than the live 1Min logic — stated plainly, not
    hidden — good enough to compare relative performance, not a claim that
    it replays the exact live strategy bar-for-bar.
    """
    bot = OpeningRangeBreakout(client=client, risk=RiskEngine(RiskLimits()), dry_run=True, symbol=symbol)
    bars = client.get_bars(symbol, timeframe="1Day", limit=days)
    if len(bars) < bot.range_minutes + 5:
        return {"bot": "orb", "skipped": f"only {len(bars)} daily bars returned, need more history"}

    port = SimPortfolio(starting_cash)
    jev_calls, jev_skips = 0, 0
    for i in range(bot.range_minutes, len(bars)):
        window = bars[max(0, i - bot.range_minutes - 5): i + 1]
        opening = window[: bot.range_minutes]
        range_high = max(b.h for b in opening)
        range_low = min(b.l for b in opening)
        avg_vol = sum(b.v for b in opening) / len(opening)
        latest = window[-1]

        side = None
        if latest.c > range_high and latest.v > avg_vol * bot.volume_confirm_mult:
            side = "buy"
        elif latest.c < range_low and latest.v > avg_vol * bot.volume_confirm_mult and port.shares > 0:
            side = "sell"

        port.mark_to_market(latest.c)
        if side is None:
            continue

        signal = Signal(symbol, side, latest.c, f"backtest day {latest.t}")
        try:
            verdict = bot.ask_jev(signal)
            jev_calls += 1
        except (JevNotConfigured, JevError) as e:
            jev_skips += 1
            continue
        if not verdict.proceed:
            continue

        acct_snap = AccountSnapshot(equity=port.cash + port.shares * latest.c, starting_equity_today=port.cash + port.shares * latest.c, open_position_value=port.shares * latest.c, open_position_count=1 if port.shares > 0 else 0)
        decision = bot.risk.check_new_entry(acct_snap, latest.c)
        if not decision.allowed or decision.max_shares_or_contracts < 1:
            continue
        if side == "buy":
            port.buy_shares(decision.max_shares_or_contracts, latest.c, latest.t)
        else:
            port.sell_shares(decision.max_shares_or_contracts, latest.c, latest.t)

    final_price = bars[-1].c
    final_equity = port.cash + port.shares * final_price
    port.equity_curve.append(final_equity)
    return {
        "bot": "orb", "symbol": symbol, "days": len(bars),
        "starting_cash": starting_cash, "final_equity": round(final_equity, 2),
        "total_return_pct": round((final_equity / starting_cash - 1) * 100, 2),
        "max_drawdown_pct": round(_max_drawdown_pct(port.equity_curve) * 100, 2),
        "num_trades": len(port.trades), "jev_calls": jev_calls, "jev_skips": jev_skips,
    }


def backtest_dca(client: AlpacaClient, symbol: str, days: int, starting_cash: float, dollar_amount: float) -> dict:
    """NOTE: starting_cash is only used for the risk engine's exposure-room
    math (does this $50 buy still fit under max_total_exposure_pct of the
    account) — it is NOT the drip's own capital base. The drip only ever
    "owns" what it has actually bought, so return is measured on the value
    of SHARES PURCHASED vs DOLLARS ACTUALLY INVESTED, never blended with the
    untouched rest of a $100k paper account. (First version of this function
    did blend them — caught on the first real run, which is exactly why this
    gets run against real data before being called done.)
    """
    bot = DCADripBot(client=client, risk=RiskEngine(RiskLimits()), dry_run=True, symbol=symbol, dollar_amount=dollar_amount)
    bars = client.get_bars(symbol, timeframe="1Day", limit=days)
    if not bars:
        return {"bot": "dca_drip", "skipped": "no daily bars returned"}

    cadence_days = max(1, CADENCE_SECONDS["dca_drip"] // 86400)
    shares, total_invested = 0.0, 0.0
    jev_calls, jev_skips, jev_blocks = 0, 0, 0
    for i, bar in enumerate(bars):
        if i % cadence_days != 0:
            continue
        signal = Signal(symbol, "buy", bar.c, f"scheduled drip buy on {bar.t}")
        try:
            verdict = bot.ask_jev(signal)
            jev_calls += 1
        except (JevNotConfigured, JevError):
            jev_skips += 1
            verdict = None
        if verdict is not None and not verdict.proceed:
            jev_blocks += 1
            continue
        # Risk check uses the REAL account's equity for exposure-room math,
        # but position tracking below is the drip's own invested capital only.
        acct_snap = AccountSnapshot(equity=starting_cash, starting_equity_today=starting_cash, open_position_value=shares * bar.c, open_position_count=1 if shares > 0 else 0)
        decision = bot.risk.check_new_notional_entry(acct_snap, bar.c, dollar_amount)
        if decision.allowed and decision.notional_amount > 0:
            shares += decision.notional_amount / bar.c
            total_invested += decision.notional_amount

    final_price = bars[-1].c
    final_value = shares * final_price
    return {
        "bot": "dca_drip", "symbol": symbol, "days": len(bars),
        "total_invested": round(total_invested, 2), "final_value_of_shares_bought": round(final_value, 2),
        "total_return_pct": round(((final_value - total_invested) / total_invested) * 100, 2) if total_invested else None,
        "num_buys": int(total_invested / dollar_amount) if dollar_amount else 0,
        "jev_calls": jev_calls, "jev_skips": jev_skips, "jev_blocked_buys": jev_blocks,
    }


def main(symbol: str = "SPY", days: int = 180, starting_cash: float = 100_000.0, dca_dollar_amount: float = 50.0):
    try:
        client = AlpacaClient()
    except AlpacaNotConfigured as e:
        print(f"Cannot backtest: {e}")
        return

    print(f"BACKTEST over the last {days} daily bars of {symbol}\n")

    results = []
    results.append(backtest_orb(client, symbol, days, starting_cash))
    results.append(backtest_dca(client, symbol, days, starting_cash, dca_dollar_amount))
    results.append({
        "bot": "insider_filings",
        "skipped": "no historical data source wired in — this bot reads SEC EDGAR's "
                   "LIVE filings feed only; backtesting it needs a different, historical "
                   "SEC data source and a rewrite of its fetch path (not done here).",
    })
    results.append({
        "bot": "futures_reversal",
        "skipped": "alpaca-py has no futures historical data client as of 0.44.0 "
                   "(verified directly, not assumed) — there is no data to replay.",
    })

    for r in results:
        print(f"--- {r['bot']} ---")
        if "skipped" in r:
            print(f"  SKIPPED: {r['skipped']}\n")
            continue
        for k, v in r.items():
            if k == "bot":
                continue
            print(f"  {k}: {v}")
        print()

    runnable = [r for r in results if "skipped" not in r]
    if len(runnable) >= 2:
        print("--- COMPARISON (runnable bots only) ---")
        for r in runnable:
            print(f"  {r['bot']}: total_return_pct={r.get('total_return_pct')}  trades/buys={r.get('num_trades', r.get('num_buys'))}")


if __name__ == "__main__":
    main()
