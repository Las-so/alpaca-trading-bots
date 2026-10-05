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

    def get_bars(self, symbol: str, timeframe: str = "1Min", limit: int = 60):
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        tf_map = {"1Min": TimeFrame.Minute, "1Day": TimeFrame.Day}
        req = StockBarsRequest(
            symbol_or_symbols=symbol, timeframe=tf_map.get(timeframe, TimeFrame.Minute), limit=limit
        )
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

    def get_futures_bars(self, symbol: str, timeframe: str = "1Min", limit: int = 60):
        """Alpaca added futures trading access in 2025; the exact client class
        name has moved around in alpaca-py releases. This tries the documented
        current path and falls back to a clear error rather than a silent
        wrong result — verify against https://docs.alpaca.markets the first
        time this runs against a funded futures-enabled paper account."""
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
                "This installed alpaca-py version doesn't expose a futures "
                "historical data client under the expected path. Run "
                "`uv pip install -U alpaca-py` and check "
                "https://docs.alpaca.markets/docs/futures-trading for the "
                "current class name, then update this method."
            ) from e
