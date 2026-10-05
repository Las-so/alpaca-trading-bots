"""Shared state file the dashboard polls every ~2s, per the video's cadence.
Every bot appends a decision log line and updates the equity snapshot here;
nothing else talks to the dashboard. One small JSON file, atomic writes.
"""
from __future__ import annotations
import json
import os
import tempfile
import time
from dataclasses import dataclass, asdict, field

STATE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "state")
STATE_FILE = os.path.join(STATE_DIR, "state.json")


@dataclass
class DecisionLogEntry:
    ts: float
    bot: str
    symbol: str
    signal: str
    jev_verdict: str
    jev_probability: float | None
    action: str          # "entered" | "skipped" | "risk_blocked" | "error"
    rationale: str


def _ensure_dir():
    os.makedirs(STATE_DIR, exist_ok=True)


def load() -> dict:
    _ensure_dir()
    if not os.path.exists(STATE_FILE):
        return {"equity_curve": [], "decisions": [], "updated_at": None}
    with open(STATE_FILE, "r") as f:
        return json.load(f)


def _atomic_write(data: dict):
    _ensure_dir()
    fd, tmp = tempfile.mkstemp(dir=STATE_DIR)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, STATE_FILE)


def record_equity(equity: float, cash: float):
    data = load()
    data["equity_curve"].append({"ts": time.time(), "equity": equity, "cash": cash})
    data["equity_curve"] = data["equity_curve"][-2000:]
    data["updated_at"] = time.time()
    _atomic_write(data)


def record_decision(entry: DecisionLogEntry):
    data = load()
    data["decisions"].append(asdict(entry))
    data["decisions"] = data["decisions"][-500:]
    data["updated_at"] = time.time()
    _atomic_write(data)
