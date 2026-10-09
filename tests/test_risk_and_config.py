"""Risk-engine and config tests. No network, no API keys, no market data.

The risk layer is the part that must never be wrong, so it is the part that
gets tested. Every case here is a veto the bots are required to honour.
"""
import os
import pytest
from alpaca_bots.risk import RiskEngine, RiskLimits, AccountSnapshot
from alpaca_bots import config


def acct(equity=10000.0, start=10000.0, open_value=0.0, count=0):
    return AccountSnapshot(
        equity=equity,
        starting_equity_today=start,
        open_position_value=open_value,
        open_position_count=count,
    )


class TestCircuitBreaker:
    def test_flat_day_is_not_tripped(self):
        assert RiskEngine().circuit_breaker_tripped(acct()) is False

    def test_exactly_at_limit_trips(self):
        # 3% down on a 3% limit must trip: the limit is inclusive.
        assert RiskEngine().circuit_breaker_tripped(acct(equity=9700.0)) is True

    def test_just_under_limit_does_not_trip(self):
        assert RiskEngine().circuit_breaker_tripped(acct(equity=9710.0)) is False

    def test_zero_starting_equity_does_not_divide_by_zero(self):
        assert RiskEngine().daily_drawdown_pct(acct(equity=0.0, start=0.0)) == 0.0

    def test_profitable_day_reports_negative_drawdown_not_a_trip(self):
        e = RiskEngine()
        assert e.daily_drawdown_pct(acct(equity=11000.0)) < 0
        assert e.circuit_breaker_tripped(acct(equity=11000.0)) is False


class TestNewEntryVeto:
    def test_blocked_once_drawdown_limit_is_hit(self):
        d = RiskEngine().check_new_entry(acct(equity=9600.0), price=100.0)
        assert d.allowed is False
        assert "drawdown" in d.reason

    def test_blocked_at_max_open_positions(self):
        d = RiskEngine().check_new_entry(acct(count=5), price=100.0)
        assert d.allowed is False
        assert "max_open_positions" in d.reason

    def test_allowed_on_a_clean_account(self):
        assert RiskEngine().check_new_entry(acct(), price=100.0).allowed is True

    def test_position_never_exceeds_max_pct_of_equity(self):
        # 10% of 10000 = 1000, so at $100 a share that is 10 shares, never more.
        d = RiskEngine().check_new_entry(acct(), price=100.0)
        assert d.max_shares_or_contracts <= 10

    def test_share_sizing_vetoes_a_buy_smaller_than_one_share(self):
        # Correct behaviour, locked in: check_new_entry sizes in WHOLE shares,
        # so $50 at a $100 share price rounds to 0 and is refused rather than
        # silently submitted. A dollar-sized bot must use the notional path.
        d = RiskEngine().check_new_entry(acct(), price=100.0, desired_dollar_amount=50.0)
        assert d.allowed is False
        assert d.max_shares_or_contracts == 0


class TestNotionalEntry:
    def test_dollar_target_is_honoured_and_not_scaled_up(self):
        # A drip bot asking for $50 must get $50, not risk-sized up to the
        # generic 10%-of-equity max.
        d = RiskEngine().check_new_notional_entry(acct(), price=100.0, desired_dollar_amount=50.0)
        assert d.allowed is True
        assert d.notional_amount == 50.0

    def test_notional_is_capped_by_max_position_pct(self):
        # Asking for $9000 on 10k equity is capped to the 10% position limit.
        d = RiskEngine().check_new_notional_entry(acct(), price=100.0, desired_dollar_amount=9000.0)
        assert d.allowed is True
        assert d.notional_amount <= 1000.0

    def test_notional_still_obeys_the_circuit_breaker(self):
        d = RiskEngine().check_new_notional_entry(acct(equity=9600.0), price=100.0, desired_dollar_amount=50.0)
        assert d.allowed is False

    def test_notional_refused_when_no_exposure_room_left(self):
        d = RiskEngine().check_new_notional_entry(acct(open_value=5000.0, count=1), price=100.0, desired_dollar_amount=50.0)
        assert d.allowed is False


class TestStopPrice:
    def test_long_stop_sits_below_entry(self):
        assert RiskEngine().stop_price(100.0, "buy") == 98.0

    def test_short_stop_sits_above_entry(self):
        assert RiskEngine().stop_price(100.0, "sell") == 102.0

    def test_exposure_ceiling_is_respected(self):
        # 50% cap on 10000 = 5000 already deployed means no room left.
        d = RiskEngine().check_new_entry(acct(open_value=5000.0, count=1), price=100.0)
        assert d.allowed is False or d.max_shares_or_contracts == 0


class TestCustomLimits:
    def test_limits_are_overridable(self):
        e = RiskEngine(RiskLimits(max_daily_drawdown_pct=0.10))
        assert e.circuit_breaker_tripped(acct(equity=9700.0)) is False


class TestConfig:
    def test_missing_keys_report_not_configured_rather_than_crashing(self, monkeypatch):
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        assert config.load_alpaca_config().configured is False

    def test_paper_endpoint_is_detected(self, monkeypatch):
        monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
        assert config.load_alpaca_config().is_paper is True

    def test_live_endpoint_is_detected_as_not_paper(self, monkeypatch):
        monkeypatch.setenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
        assert config.load_alpaca_config().is_paper is False

    def test_source_status_never_claims_configured_without_values(self, monkeypatch):
        monkeypatch.delenv("ALPACA_API_KEY", raising=False)
        monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
        s = config.source_status()
        assert s["alpaca"]["configured"] is False
        assert "signup_url" in s["alpaca"]