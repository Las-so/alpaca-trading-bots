"""Central config: reads env vars, reports what's configured without crashing.
Mirrors the pattern used in freelance-radar-mcp's adzuna source — a missing
key is a clear, structured "not configured" state, never a silent guess.
"""
from __future__ import annotations
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class AlpacaConfig:
    api_key: str | None
    secret_key: str | None
    base_url: str

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.secret_key)

    @property
    def is_paper(self) -> bool:
        return "paper" in self.base_url


@dataclass(frozen=True)
class JevConfig:
    api_key: str | None

    @property
    def configured(self) -> bool:
        return bool(self.api_key)


def load_alpaca_config() -> AlpacaConfig:
    return AlpacaConfig(
        api_key=os.environ.get("ALPACA_API_KEY") or None,
        secret_key=os.environ.get("ALPACA_SECRET_KEY") or None,
        base_url=os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
    )


def load_jev_config() -> JevConfig:
    return JevConfig(api_key=os.environ.get("TYPESAFE_API_KEY") or None)


def source_status() -> dict:
    """What a smoke test / dashboard boot screen prints: never claim configured
    without actually having the values, never claim tested without having run it."""
    a = load_alpaca_config()
    j = load_jev_config()
    return {
        "alpaca": {
            "configured": a.configured,
            "mode": "paper" if a.is_paper else "LIVE — base_url is not a paper endpoint",
            "base_url": a.base_url,
            "signup_url": "https://app.alpaca.markets/signup",
            "keys_url": "https://app.alpaca.markets/paper/dashboard/overview",
        },
        "jev": {
            "configured": j.configured,
            "note": "reads the same TYPESAFE_API_KEY already live on this machine (see jev-cli)",
        },
    }
