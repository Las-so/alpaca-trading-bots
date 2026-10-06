"""Thin wrapper around alpaca-py so bots never touch the SDK directly.
Always paper (base_url is config-driven; config.AlpacaConfig.is_paper
is checked at startup by runner.py and refuses to run live without an
explicit --i-know-this-is-live flag that does not exist yet on purpose).
"""
from __future__ import annotations
from dataclasses import dataclass

from .config import load_alpaca_config, AlpacaConfig


class AlpacaNotConfigured(RuntimeError):
    pass


@dataclass
class Bar:
    t: str
    o: float
    h: float
    l: float
    c: float
    v: float


@dataclass
class Account:
    equity: float
    cash: float
    buying_power: float


def _require_config() -> AlpacaConfig:
    cfg = load_alpaca_config()
    if not cfg.configured:
        raise AlpacaNotConfigured(
            "ALPACA_API_KEY / ALPACA_SECRET_KEY are not set. Create a free "
            "account at https://app.alpaca.markets/signup, then generate "
            "paper-trading keys at https://app.alpaca.markets/paper/dashboard/overview "
            "and put them in .env."
        )
    return cfg


class AlpacaClient:
    """Lazy-imports alpaca-py so `import alpaca_bots` works even before the
    dependency (or the account) exists — same pattern as the Adzuna source
    in freelance-radar-mcp: module loads clean, calls fail loud and clear."""

    def __init__(self):
        self.cfg = _require_config()
        from alpaca.trading.client import TradingClient
        from alpaca.data.historical.stock import StockHistoricalDataClient
        from alpaca.data.historical.crypto import CryptoHistoricalDataClient

        self.trading = TradingClient(
            self.cfg.api_key, self.cfg.secret_key, paper=self.cfg.is_paper
        )
        self.stock_data = StockHistoricalDataClient(self.cfg.api_key, self.cfg.secret_key)
        self.crypto_data = CryptoHistoricalDataClient(self.cfg.api_key, self.cfg.secret_key)

    def get_account(self) -> Account:
        a = self.trading.get_account()
        return Account(equity=float(a.equity), cash=float(a.cash), buying_power=float(a.buying_power))

    def list_open_positions(self):
        return self.trading.get_all_positions()

    def get_bars(self, symbol: str, timeframe: str = "1Min", limit: int = 60, start=None, end=None):
        """start/end (datetime, UTC) select a real historical window — used by
        scripts/backtest.py. Omit both for the original behavior: the most
        recent `limit` bars from now, used by the live bots.

        VERIFIED (not assumed): on this paper account's IEX feed, a Day-bar
        request with ONLY `limit` (no start/end) comes back empty — tested
        directly, confirmed by the same request working once start/end are
        added. So for 1Day with no explicit window, synthesize one ourselves
        rather than pass limit alone and silently get nothing back.
        """
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        from datetime import datetime, timedelta, timezone

        tf_map = {"1Min": TimeFrame.Minute, "1Day": TimeFrame.Day}
        kwargs = dict(symbol_or_symbols=symbol, timeframe=tf_map.get(timeframe, TimeFrame.Minute))
        if start is not None or end is not None:
            if start is not None:
                kwargs["start"] = start
            if end is not None:
                kwargs["end"] = end
            kwargs["limit"] = limit
        elif timeframe == "1Day":
            kwargs["end"] = datetime.now(timezone.utc) - timedelta(minutes=20)
            kwargs["start"] = kwargs["end"] - timedelta(days=max(limit * 3, 10))
            kwargs["limit"] = limit
        else:
            kwargs["limit"] = limit
        req = StockBarsRequest(**kwargs)
        bars = self.stock_data.get_stock_bars(req)
        return [
            Bar(t=str(b.timestamp), o=float(b.open), h=float(b.high), l=float(b.low), c=float(b.close), v=float(b.volume))
            for b in bars[symbol]
        ]

    def submit_bracket_order(self, symbol: str, qty: int, side: str, take_profit: float, stop_loss: float):
        from alpaca.trading.requests import MarketOrderRequest, TakeProfitRequest, StopLossRequest
        from alpaca.trading.enums import OrderSide, TimeInForce, OrderClass

        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=take_profit),
            stop_loss=StopLossRequest(stop_price=stop_loss),
        )
        return self.trading.submit_order(req)

    def submit_notional_order(self, symbol: str, notional: float, side: str):
        """Fractional/dollar-amount market order — no bracket (take-profit/
        stop-loss legs require whole-share qty on Alpaca and don't apply to
        an accumulation strategy anyway). Used by DCADripBot, not the other
        three bots."""
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce

        req = MarketOrderRequest(
            symbol=symbol,
            notional=round(notional, 2),
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        return self.trading.submit_order(req)

    def get_futures_bars(self, symbol: str, timeframe: str = "1Min", limit: int = 60):
        """VERIFIED Oct 6 2026, two independent checks, not an assumption:
        (1) direct introspection of the installed alpaca-py 0.44.0 package —
        no 'futures' module anywhere in it; (2) Alpaca's own official SDK docs
        site (alpaca.markets/sdks/python) — its Market Data Reference lists
        stock, crypto, and options historical clients, no futures client.
        alpaca-py genuinely does not expose futures historical data yet, full
        stop — this is not an import path that "moved", which is what an
        earlier version of this comment assumed without checking. Re-verify
        both of those before assuming this still holds."""
        try:
            from alpaca.data.historical.futures import FuturesHistoricalDataClient
            from alpaca.data.requests import FuturesBarsRequest
            from alpaca.data.timeframe import TimeFrame

            client = FuturesHistoricalDataClient(self.cfg.api_key, self.cfg.secret_key)
            req = FuturesBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Minute, limit=limit)
            bars = client.get_futures_bars(req)
            return [
                Bar(t=str(b.timestamp), o=float(b.open), h=float(b.high), l=float(b.low), c=float(b.close), v=float(b.volume))
                for b in bars[symbol]
            ]
        except ImportError as e:
            raise RuntimeError(
                "alpaca-py has no futures historical data client as of the "
                "latest release (0.44.0, checked Oct 2026) — this isn't a "
                "class-name/import-path issue to fix here, it's a real gap "
                "in Alpaca's own SDK. Before trying anything else: re-check "
                "pypi.org/project/alpaca-py for a newer release that adds "
                "futures, and alpaca.markets/sdks/python for an updated "
                "Market Data Reference. If still missing, futures data for "
                "this bot needs a different provider entirely (or Alpaca's "
                "raw REST API directly, bypassing alpaca-py, if their REST "
                "surface supports futures ahead of the SDK — unverified, "
                "check before relying on it)."
            ) from e
