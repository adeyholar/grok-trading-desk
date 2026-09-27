"""Alpaca paper-host lock — shared by options, stock day, and crypto day.

Live trading host (api.alpaca.markets) is always refused.
Credentials prefer ALPACA_PAPER_API_KEY / ALPACA_PAPER_API_SECRET over yaml.
"""

from __future__ import annotations

import os
from typing import Any

PAPER_BASE_URL = "https://paper-api.alpaca.markets"


def resolve_paper_credentials(config: dict[str, Any] | None = None) -> tuple[str, str]:
    """Env vars win; yaml alpaca.api_key/api_secret is the fallback."""
    cfg = (config or {}).get("alpaca", {}) or {}
    key = (os.environ.get("ALPACA_PAPER_API_KEY") or cfg.get("api_key") or "").strip()
    secret = (
        os.environ.get("ALPACA_PAPER_API_SECRET") or cfg.get("api_secret") or ""
    ).strip()
    return key, secret


def assert_paper_base_url(base_url: str) -> str:
    """Refuse anything that is not the Alpaca paper trading host."""
    cleaned = (base_url or "").strip().rstrip("/")
    if not cleaned:
        return PAPER_BASE_URL
    lower = cleaned.lower()
    if "paper-api.alpaca.markets" not in lower:
        raise ValueError(
            f"refuses non-paper base URL {cleaned!r}; locked to {PAPER_BASE_URL}"
        )
    if "://api.alpaca.markets" in lower and "paper-api" not in lower:
        raise ValueError(f"refuses live host {cleaned!r}")
    return cleaned
