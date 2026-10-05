"""Bot 3 — Futures Reversal.

Strategy (code): rolling z-score of price vs its own recent mean on a
continuous futures contract. A large negative z-score (price stretched
far below its recent average) is a candidate long reversal; a large
positive z-score is a candidate short. Pure statistics, no model.

Jev's job: judge whether this stretch looks like an exhausted move likely
to mean-revert, or the start of a genuine trend continuation — given the
magnitude of the move and how long price has been stretched.

NOTE: get_futures_bars() depends on alpaca-py's futures data client, which
is newer and has moved between releases — see the comment on that method.
Verify it against a real funded futures-enabled paper account before the
first live run; don't assume the class path without checking.
"""
from __future__ import annotations
import statistics
from dataclasses import dataclass

from .base import Bot, Signal
from .. import jev_client


@dataclass
class FuturesReversal(Bot):
    name: str = "futures_reversal"
    symbol: str = "ESZ5"  # front-month E-mini S&P continuous contract; update per active contract
    lookback: int = 30
    z_entry: float = 2.0

    def compute_signal(self) -> Signal:
        bars = self.client.get_futures_bars(self.symbol, timeframe="1Min", limit=self.lookback + 1)
        if len(bars) < self.lookback + 1:
            return Signal(self.symbol, "none", 0.0, "not enough bars for the lookback window")

        closes = [b.c for b in bars[:-1]]
        latest = bars[-1]
        mean = statistics.mean(closes)
        stdev = statistics.pstdev(closes) or 1e-9
        z = (latest.c - mean) / stdev

        if z <= -self.z_entry:
            return Signal(self.symbol, "buy", latest.c, f"z-score {z:.2f}, stretched below {self.lookback}-bar mean {mean:.2f}")
        if z >= self.z_entry:
            return Signal(self.symbol, "sell", latest.c, f"z-score {z:.2f}, stretched above {self.lookback}-bar mean {mean:.2f}")
        return Signal(self.symbol, "none", latest.c, f"z-score {z:.2f}, inside normal range")

    def ask_jev(self, signal: Signal):
        return jev_client.ask_noul(
            instructions=(
                "Given this statistical stretch from the recent mean, does this "
                "look like an exhausted move likely to revert, rather than the "
                "start of a genuine trend continuation that would keep running?"
            ),
            state={
                "symbol": signal.symbol,
                "side": signal.side,
                "price": signal.price,
                "setup": signal.description,
            },
            threshold=0.55,
        )
