"""Orchestrates all three bots on their own cadence. Dry-run by default —
flip dry_run=False per bot only after watching paper decisions in the
dashboard and agreeing they look right. That switch is a conscious,
separate step, not a config default.
"""
from __future__ import annotations
import time

from .alpaca_client import AlpacaClient, AlpacaNotConfigured
from .risk import RiskEngine, RiskLimits
from .bots.orb import OpeningRangeBreakout
from .bots.insider_filings import InsiderFilingsBot
from .bots.futures_reversal import FuturesReversal
from . import state


def build_bots(dry_run: bool = True):
    client = AlpacaClient()  # raises AlpacaNotConfigured with a clear message if keys are missing
    risk = RiskEngine(RiskLimits())
    return [
        OpeningRangeBreakout(client=client, risk=risk, dry_run=dry_run),
        InsiderFilingsBot(client=client, risk=risk, dry_run=dry_run),
        FuturesReversal(client=client, risk=risk, dry_run=dry_run),
    ]


CADENCE_SECONDS = {
    "orb": 60,                 # check every minute during the opening window
    "insider_filings": 300,    # SEC feed doesn't need sub-minute polling
    "futures_reversal": 60,
}


def main(dry_run: bool = True, max_cycles: int | None = None):
    try:
        bots = build_bots(dry_run=dry_run)
    except AlpacaNotConfigured as e:
        print(f"Cannot start: {e}")
        return

    last_run = {b.name: 0.0 for b in bots}
    cycles = 0
    while True:
        now = time.time()
        acct = bots[0].client.get_account()
        state.record_equity(acct.equity, acct.cash)

        for b in bots:
            if now - last_run[b.name] >= CADENCE_SECONDS[b.name]:
                result = b.run_once()
                print(f"[{b.name}] {result}")
                last_run[b.name] = now

        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            break
        time.sleep(2)  # dashboard polls every 2s; keep the loop in step with it


if __name__ == "__main__":
    main()
