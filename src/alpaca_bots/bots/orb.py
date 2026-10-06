"""Bot 1 — Opening Range Breakout (equities).

Strategy (code): take the high/low of the first N minutes after the open
for each watched symbol; if price breaks above the range high on rising
volume, that's a candidate long. Below the range low on rising volume is
a candidate short. This part is pure arithmetic — no model needed.

Jev's job: judge whether THIS breakout looks like a real move or a
false-breakout trap, given the day's broader context (volatility regime,
how far through the session we are, how clean the range was). Jev is
asked a single `noul` — "is this breakout worth taking" — with the real
numbers in state, not a vibe.
"""
from __future__ import annotations

from .base import Bot, Signal
from .. import jev_client


class OpeningRangeBreakout(Bot):
    name = "orb"

    def __init__(self, *args, symbol: str = "SPY", range_minutes: int = 15, volume_confirm_mult: float = 1.5, **kwargs):
        super().__init__(*args, **kwargs)
        self.symbol = symbol
        self.range_minutes = range_minutes
        self.volume_confirm_mult = volume_confirm_mult

    def compute_signal(self) -> Signal:
        bars = self.client.get_bars(self.symbol, timeframe="1Min", limit=self.range_minutes + 5)
        if len(bars) < self.range_minutes + 1:
            return Signal(self.symbol, "none", 0.0, "not enough bars yet for the opening range")

        opening = bars[: self.range_minutes]
        range_high = max(b.h for b in opening)
        range_low = min(b.l for b in opening)
        avg_vol = sum(b.v for b in opening) / len(opening)

        latest = bars[-1]
        if latest.c > range_high and latest.v > avg_vol * self.volume_confirm_mult:
            return Signal(self.symbol, "buy", latest.c, f"broke above {range_high} on {latest.v}/{avg_vol:.0f} avg vol")
        if latest.c < range_low and latest.v > avg_vol * self.volume_confirm_mult:
            return Signal(self.symbol, "sell", latest.c, f"broke below {range_low} on {latest.v}/{avg_vol:.0f} avg vol")
        return Signal(self.symbol, "none", latest.c, f"inside range [{range_low}, {range_high}]")

    def ask_jev(self, signal: Signal):
        return jev_client.ask_noul(
            instructions=(
                "Given this opening-range breakout setup, is this a real breakout "
                "worth taking rather than a false-breakout trap? Weigh how clean the "
                "range was, the volume confirmation, and how early in the session this is."
            ),
            state={
                "symbol": signal.symbol,
                "side": signal.side,
                "price": signal.price,
                "setup": signal.description,
            },
            threshold=0.55,
        )
