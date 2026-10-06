"""Shared bot lifecycle: fetch data -> compute signal (code) -> ask Jev
(judgment) -> risk check (code, hard veto) -> submit order (code) -> log.

This ordering is the whole point of the architecture: Claude's code owns
every step that is mechanical or safety-critical; Jev is consulted exactly
once per candidate trade, for the one thing code can't do well — weighing
ambiguous, context-dependent conviction.
"""
from __future__ import annotations
from dataclasses import dataclass
from abc import ABC, abstractmethod

from ..alpaca_client import AlpacaClient, AlpacaNotConfigured
from ..risk import RiskEngine, AccountSnapshot
from ..jev_client import JevNotConfigured, JevError
from .. import state


@dataclass
class Signal:
    symbol: str
    side: str          # "buy" | "sell" | "none"
    price: float
    description: str


class Bot(ABC):
    name: str = "base"

    def __init__(self, client: AlpacaClient | None = None, risk: RiskEngine | None = None, dry_run: bool = True):
        self.client = client
        self.risk = risk or RiskEngine()
        self.dry_run = dry_run  # True until Larry explicitly flips it after watching paper results

    @abstractmethod
    def compute_signal(self) -> Signal:
        """Pure, testable: pulls bars, returns a Signal. No side effects."""

    @abstractmethod
    def ask_jev(self, signal: Signal):
        """Returns a JevVerdict. The only place this bot calls Jev."""

    def desired_dollar_amount(self) -> float | None:
        """Override to cap this bot's position at a fixed dollar amount per
        trade (e.g. a DCA/drip bot) instead of the generic risk-sized max.
        None (default) keeps the existing risk-sized behavior."""
        return None

    def uses_notional_order(self) -> bool:
        """Override True for a bot that should submit a fractional/dollar
        market order (no bracket) instead of the default whole-share
        bracket order — for an accumulation strategy with no stop/target,
        where flooring to whole shares could round a small buy to 0."""
        return False

    def run_once(self) -> dict:
        try:
            signal = self.compute_signal()
        except Exception as e:
            state.record_decision(state.DecisionLogEntry(
                ts=_now(), bot=self.name, symbol="?", signal="error",
                jev_verdict="n/a", jev_probability=None, action="error",
                rationale=f"compute_signal failed: {e}",
            ))
            return {"action": "error", "reason": str(e)}

        if signal.side == "none":
            state.record_decision(state.DecisionLogEntry(
                ts=_now(), bot=self.name, symbol=signal.symbol, signal="none",
                jev_verdict="n/a", jev_probability=None, action="skipped",
                rationale=signal.description,
            ))
            return {"action": "skipped", "reason": signal.description}

        try:
            verdict = self.ask_jev(signal)
        except (JevNotConfigured, JevError) as e:
            state.record_decision(state.DecisionLogEntry(
                ts=_now(), bot=self.name, symbol=signal.symbol, signal=signal.side,
                jev_verdict="unavailable", jev_probability=None, action="error",
                rationale=f"Jev call failed: {e}",
            ))
            return {"action": "error", "reason": str(e)}

        if not verdict.proceed:
            state.record_decision(state.DecisionLogEntry(
                ts=_now(), bot=self.name, symbol=signal.symbol, signal=signal.side,
                jev_verdict=verdict.rationale_hint, jev_probability=verdict.probability,
                action="skipped", rationale="Jev declined",
            ))
            return {"action": "skipped", "reason": "jev_declined"}

        # Code has the final word: risk check cannot be overridden by Jev.
        try:
            acct = self.client.get_account() if self.client else None
        except AlpacaNotConfigured as e:
            state.record_decision(state.DecisionLogEntry(
                ts=_now(), bot=self.name, symbol=signal.symbol, signal=signal.side,
                jev_verdict=verdict.rationale_hint, jev_probability=verdict.probability,
                action="error", rationale=f"Alpaca not configured: {e}",
            ))
            return {"action": "error", "reason": str(e)}

        if acct is None:
            return {"action": "error", "reason": "no alpaca client"}

        snapshot = AccountSnapshot(
            equity=acct.equity, starting_equity_today=acct.equity,
            open_position_value=0.0, open_position_count=len(self.client.list_open_positions()),
        )
        if self.uses_notional_order():
            decision = self.risk.check_new_notional_entry(snapshot, signal.price, self.desired_dollar_amount() or 0.0)
        else:
            decision = self.risk.check_new_entry(snapshot, signal.price, desired_dollar_amount=self.desired_dollar_amount())

        if not decision.allowed:
            state.record_decision(state.DecisionLogEntry(
                ts=_now(), bot=self.name, symbol=signal.symbol, signal=signal.side,
                jev_verdict=verdict.rationale_hint, jev_probability=verdict.probability,
                action="risk_blocked", rationale=decision.reason,
            ))
            return {"action": "risk_blocked", "reason": decision.reason}

        if self.uses_notional_order():
            if self.dry_run:
                state.record_decision(state.DecisionLogEntry(
                    ts=_now(), bot=self.name, symbol=signal.symbol, signal=signal.side,
                    jev_verdict=verdict.rationale_hint, jev_probability=verdict.probability,
                    action="skipped", rationale=f"DRY RUN — would submit \${decision.notional_amount:.2f} notional @ {signal.price}",
                ))
                return {"action": "dry_run", "notional": decision.notional_amount}

            order = self.client.submit_notional_order(signal.symbol, decision.notional_amount, signal.side)
            state.record_decision(state.DecisionLogEntry(
                ts=_now(), bot=self.name, symbol=signal.symbol, signal=signal.side,
                jev_verdict=verdict.rationale_hint, jev_probability=verdict.probability,
                action="entered", rationale=f"\${decision.notional_amount:.2f} notional @ {signal.price}",
            ))
            return {"action": "entered", "order": order}

        if self.dry_run:
            state.record_decision(state.DecisionLogEntry(
                ts=_now(), bot=self.name, symbol=signal.symbol, signal=signal.side,
                jev_verdict=verdict.rationale_hint, jev_probability=verdict.probability,
                action="skipped", rationale=f"DRY RUN — would submit {decision.max_shares_or_contracts} @ {signal.price}",
            ))
            return {"action": "dry_run", "qty": decision.max_shares_or_contracts}

        stop = self.risk.stop_price(signal.price, signal.side)
        target = round(signal.price + (signal.price - stop) * 2, 2) if signal.side == "buy" else round(signal.price - (stop - signal.price) * 2, 2)
        order = self.client.submit_bracket_order(
            signal.symbol, decision.max_shares_or_contracts, signal.side, target, stop,
        )
        state.record_decision(state.DecisionLogEntry(
            ts=_now(), bot=self.name, symbol=signal.symbol, signal=signal.side,
            jev_verdict=verdict.rationale_hint, jev_probability=verdict.probability,
            action="entered", rationale=f"{decision.max_shares_or_contracts} @ {signal.price}, stop {stop}, target {target}",
        ))
        return {"action": "entered", "order": order}


def _now() -> float:
    import time
    return time.time()
