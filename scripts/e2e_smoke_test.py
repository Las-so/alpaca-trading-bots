"""The live-test script. This is what "LIVE TEST BEFORE HANDOFF" means for
this repo: it does not pass until it has actually called the real Alpaca
paper API and the real Jev API and gotten real answers back — a clean
import is not a pass.

Run: uv run python scripts/e2e_smoke_test.py
"""
from __future__ import annotations
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from alpaca_bots.config import source_status
from alpaca_bots.alpaca_client import AlpacaClient, AlpacaNotConfigured
from alpaca_bots import jev_client
from alpaca_bots.jev_client import JevNotConfigured, JevError
from alpaca_bots.bots.orb import OpeningRangeBreakout


def main() -> int:
    status = source_status()
    print("SOURCE STATUS:")
    import json
    print(json.dumps(status, indent=2))

    ok = True

    print("\n--- Alpaca: account call ---")
    try:
        client = AlpacaClient()
        acct = client.get_account()
        print(f"PASS — equity=${acct.equity:,.2f} cash=${acct.cash:,.2f} buying_power=${acct.buying_power:,.2f}")
    except AlpacaNotConfigured as e:
        print(f"SKIPPED — {e}")
        ok = False
    except Exception as e:
        print(f"FAIL — {e}")
        ok = False

    print("\n--- Jev: one real noul call ---")
    try:
        v = jev_client.ask_noul(
            "Is this a reasonable smoke-test question to answer yes to?",
            {"context": "alpaca-trading-bots e2e_smoke_test.py"},
        )
        print(f"PASS — proceed={v.proceed} probability={v.probability}")
    except JevNotConfigured as e:
        print(f"SKIPPED — {e}")
        ok = False
    except JevError as e:
        print(f"FAIL — {e}")
        ok = False

    print("\n--- ORB bot: one real dry-run cycle against live bars ---")
    if status["alpaca"]["configured"]:
        try:
            bot = OpeningRangeBreakout(client=AlpacaClient(), dry_run=True)
            result = bot.run_once()
            print(f"PASS — {result}")
        except Exception as e:
            print(f"FAIL — {e}")
            ok = False
    else:
        print("SKIPPED — needs Alpaca keys first")
        ok = False

    print("\n" + ("ALL LIVE CHECKS PASSED" if ok else "NOT FULLY LIVE-TESTED YET — see SKIPPED/FAIL above"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
