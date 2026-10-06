"""Bot 4 — DCA Drip (dollar-cost averaging / accumulation).

Strategy (code): on a fixed cadence (runner.py's CADENCE_SECONDS, e.g.
weekly), buy a fixed DOLLAR amount of the symbol, regardless of price.
This is deliberately not reactive to a setup the way the other three bots
are — the signal is "it's time", not "the chart says so". That's the whole
point of a drip: discipline over timing.

This is the one place in the fleet where sizing is NOT the generic
risk-sized max (which would scale this up to 10% of equity on every
cycle and defeat the purpose) — see Bot.desired_dollar_amount() / the
desired_dollar_amount param on RiskEngine.check_new_entry.

Jev's job here is narrow and asymmetric: not "is this a good buy" (a drip
buys regardless), but "is there an acute reason to skip THIS cycle" — e.g.
an ongoing flash-crash-style move so extreme that even a drip strategy
would rather wait a cycle. Default leans toward buying; Jev has to be
fairly confident something is wrong to block it, which is why the
threshold is lower than the other bots' 0.55.
"""
from __future__ import annotations

from .base import Bot, Signal
from .. import jev_client


class DCADripBot(Bot):
    name = "dca_drip"

    def __init__(self, *args, symbol: str = "SPY", dollar_amount: float = 50.0, **kwargs):
        super().__init__(*args, **kwargs)
        self.symbol = symbol
        self.dollar_amount = dollar_amount

    def desired_dollar_amount(self) -> float | None:
        return self.dollar_amount

    def uses_notional_order(self) -> bool:
        return True  # fractional-share dollar buy, no bracket — see base.py

    def compute_signal(self) -> Signal:
        bars = self.client.get_bars(self.symbol, timeframe="1Day", limit=2)
        if not bars:
            return Signal(self.symbol, "none", 0.0, "no bars available to price this cycle's buy")
        latest = bars[-1]
        return Signal(
            self.symbol, "buy", latest.c,
            f"scheduled drip buy — ${self.dollar_amount:.2f} of {self.symbol} at {latest.c}",
        )

    def ask_jev(self, signal: Signal):
        return jev_client.ask_noul(
            instructions=(
                "This is a scheduled dollar-cost-average buy that executes on a "
                "fixed cadence regardless of price — the default is to proceed. "
                "Only say no if there is an acute, extreme reason to skip this one "
                "cycle (e.g. an ongoing flash-crash-magnitude move), not ordinary "
                "day-to-day price movement or a normal pullback."
            ),
            state={
                "symbol": signal.symbol,
                "price": signal.price,
                "setup": signal.description,
            },
            threshold=0.4,
        )
