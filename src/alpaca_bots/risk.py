"""Risk enforcement — pure code, no model in this file, on purpose.

In the video's architecture, Jev judges the OPPORTUNITY; code enforces the
RISK RULES as a hard veto Jev cannot override. This file is that veto layer.
Every bot MUST pass every proposed order through RiskEngine.check() before
calling alpaca_client.submit_order(), and MUST re-check open positions every
cycle against drawdown + stop rules — not just at entry.
"""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class RiskLimits:
    max_position_pct_of_equity: float = 0.10   # no single position > 10% of equity
    max_total_exposure_pct: float = 0.50        # all open positions combined <= 50% of equity
    max_daily_drawdown_pct: float = 0.03        # halt new entries if the account is down 3% today
    stop_loss_pct: float = 0.02                 # per-position stop, 2% from entry
    max_open_positions: int = 5


@dataclass
class RiskDecision:
    allowed: bool
    reason: str
    max_shares_or_contracts: int = 0


@dataclass
class AccountSnapshot:
    equity: float
    starting_equity_today: float
    open_position_value: float
    open_position_count: int


class RiskEngine:
    def __init__(self, limits: RiskLimits | None = None):
        self.limits = limits or RiskLimits()

    def daily_drawdown_pct(self, acct: AccountSnapshot) -> float:
        if acct.starting_equity_today <= 0:
            return 0.0
        return (acct.starting_equity_today - acct.equity) / acct.starting_equity_today

    def circuit_breaker_tripped(self, acct: AccountSnapshot) -> bool:
        return self.daily_drawdown_pct(acct) >= self.limits.max_daily_drawdown_pct

    def check_new_entry(self, acct: AccountSnapshot, price: float) -> RiskDecision:
        if self.circuit_breaker_tripped(acct):
            return RiskDecision(
                allowed=False,
                reason=(
                    f"daily drawdown {self.daily_drawdown_pct(acct):.2%} >= "
                    f"limit {self.limits.max_daily_drawdown_pct:.2%} — no new entries today"
                ),
            )
        if acct.open_position_count >= self.limits.max_open_positions:
            return RiskDecision(
                allowed=False,
                reason=f"already at max_open_positions ({self.limits.max_open_positions})",
            )
        max_position_value = acct.equity * self.limits.max_position_pct_of_equity
        room_left = (acct.equity * self.limits.max_total_exposure_pct) - acct.open_position_value
        position_value = min(max_position_value, max(room_left, 0.0))
        if position_value <= 0 or price <= 0:
            return RiskDecision(
                allowed=False,
                reason="no exposure room left under max_total_exposure_pct",
            )
        qty = int(position_value // price)
        if qty < 1:
            return RiskDecision(allowed=False, reason="sized position rounds to 0 shares/contracts")
        return RiskDecision(allowed=True, reason="within limits", max_shares_or_contracts=qty)

    def stop_price(self, entry_price: float, side: str) -> float:
        if side == "buy":
            return round(entry_price * (1 - self.limits.stop_loss_pct), 2)
        return round(entry_price * (1 + self.limits.stop_loss_pct), 2)
