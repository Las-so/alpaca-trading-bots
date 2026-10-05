"""Thin, typed Jev client — ported from ~/dev/jev-cli/jev_ask.py so the bots
use the exact same tested call shape instead of a second implementation.
Jev is the DECISION layer only: it never sees code, never places an order.
It answers one typed judgment at a time; code (risk.py) has the final veto.
"""
from __future__ import annotations
import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Any

from .config import load_jev_config

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
RETRY_CODES = (429, 500, 502, 503, 529)


class JevNotConfigured(RuntimeError):
    pass


class JevError(RuntimeError):
    pass


@dataclass
class JevVerdict:
    proceed: bool
    probability: float | None
    confidence: float | None
    rationale_hint: str
    raw: dict


def _call(state: Any, questions: dict, retries: int = 4, timeout: int = 45) -> dict:
    cfg = load_jev_config()
    if not cfg.configured:
        raise JevNotConfigured(
            "TYPESAFE_API_KEY is not set in this process's environment. "
            "On this machine it's a Windows user env var managed by jev-cli "
            "(`jev status` / `jev on`) — restart the terminal/service after "
            "turning it on so the new env var is inherited."
        )
    body = json.dumps({"model": MODEL, "state": state, "questions": questions}).encode()
    headers = {"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"}
    delay = 1.0
    for attempt in range(retries):
        req = urllib.request.Request(API_URL, data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:300]
            if e.code == 401:
                raise JevError("401 — TYPESAFE_API_KEY was rejected") from e
            if e.code == 422:
                raise JevError(f"422 — question shape rejected: {detail}") from e
            if e.code in RETRY_CODES and attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise JevError(f"HTTP {e.code}: {detail}") from e
        except urllib.error.URLError as e:
            if attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise JevError(f"network error calling Jev: {e}") from e
    raise JevError("exhausted retries calling Jev")


def ask_noul(instructions: str, state: Any, threshold: float = 0.5) -> JevVerdict:
    """Yes/no judgment with a probability. Used as a go/no-go gate."""
    r = _call(state, {"q": {"type": "noul", "instructions": instructions}})
    ans = r["answers"]["q"]
    p = ans.get("noul")
    return JevVerdict(
        proceed=(p is not None and p >= threshold),
        probability=p,
        confidence=None,
        rationale_hint=instructions,
        raw=r,
    )


def ask_choice(instructions: str, state: Any, options: dict[str, str]) -> JevVerdict:
    """Pick one labeled option; used when a strategy needs a discrete regime
    read (e.g. 'trend' vs 'chop') rather than a yes/no."""
    r = _call(state, {"q": {"type": "choice", "instructions": instructions, "criteria": options}})
    ans = r["answers"]["q"]
    return JevVerdict(
        proceed=True,  # caller interprets .raw["answers"]["q"]["choice"] itself
        probability=None,
        confidence=ans.get("confidence"),
        rationale_hint=str(ans.get("choice")),
        raw=r,
    )


def ask_score(instructions: str, state: Any, levels: list[str], proceed_level_index: int) -> JevVerdict:
    """Graded judgment (e.g. conviction level); proceed if the scored level
    index is at or above the caller's threshold level."""
    r = _call(state, {"q": {"type": "score", "instructions": instructions, "criteria": levels}})
    ans = r["answers"]["q"]
    score = ans.get("score")
    idx = levels.index(score) if score in levels else -1
    return JevVerdict(
        proceed=(idx >= proceed_level_index),
        probability=None,
        confidence=ans.get("confidence"),
        rationale_hint=str(score),
        raw=r,
    )
