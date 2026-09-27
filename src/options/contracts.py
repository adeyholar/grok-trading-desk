"""Alpaca options contracts client — paper host locked.

GET /v2/options/contracts against https://paper-api.alpaca.markets only.
Credentials prefer ALPACA_PAPER_API_KEY / ALPACA_PAPER_API_SECRET over yaml.
"""

from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from typing import Any

import httpx

log = logging.getLogger(__name__)

PAPER_BASE_URL = "https://paper-api.alpaca.markets"


def resolve_paper_credentials(config: dict[str, Any] | None = None) -> tuple[str, str]:
    """Env vars win; yaml alpaca.api_key/api_secret is the fallback (paper:true expected)."""
    cfg = (config or {}).get("alpaca", {}) or {}
    key = (os.environ.get("ALPACA_PAPER_API_KEY") or cfg.get("api_key") or "").strip()
    secret = (os.environ.get("ALPACA_PAPER_API_SECRET") or cfg.get("api_secret") or "").strip()
    return key, secret


def assert_paper_base_url(base_url: str) -> str:
    """Refuse anything that is not the Alpaca paper trading host."""
    cleaned = (base_url or "").strip().rstrip("/")
    if not cleaned:
        return PAPER_BASE_URL
    lower = cleaned.lower()
    if "paper-api.alpaca.markets" not in lower:
        raise ValueError(
            f"options client refuses non-paper base URL {cleaned!r}; "
            f"locked to {PAPER_BASE_URL}"
        )
    # Explicit live host (even if somehow combined) is never allowed.
    if "://api.alpaca.markets" in lower and "paper-api" not in lower:
        raise ValueError(f"options client refuses live host {cleaned!r}")
    return cleaned


class OptionsContractsClient:
    """Thin REST wrapper for GET /v2/options/contracts (paper only)."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        base_url: str | None = None,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
    ):
        self.config = config or {}
        opts = self.config.get("options", {}) or {}
        configured = base_url or opts.get("base_url") or PAPER_BASE_URL
        self.base_url = assert_paper_base_url(str(configured))
        self.api_key, self.api_secret = resolve_paper_credentials(self.config)
        self._client = client
        self._timeout = timeout
        self._owns_client = client is None

        alpaca = self.config.get("alpaca", {}) or {}
        if alpaca.get("paper") is False:
            log.warning(
                "alpaca.paper is false in config; options path still locks to paper host %s",
                self.base_url,
            )

    def _headers(self) -> dict[str, str]:
        if not self.api_key or not self.api_secret:
            raise RuntimeError(
                "missing Alpaca paper credentials "
                "(set ALPACA_PAPER_API_KEY / ALPACA_PAPER_API_SECRET or alpaca.api_key/api_secret)"
            )
        return {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
        }

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.base_url,
                headers=self._headers(),
                timeout=self._timeout,
            )
            self._owns_client = True
        return self._client

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "OptionsContractsClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def list_contracts(
        self,
        underlying_symbols: str | list[str],
        *,
        option_type: str | None = None,
        expiration_date_gte: str | date | None = None,
        expiration_date_lte: str | date | None = None,
        status: str = "active",
        limit: int = 1000,
        strike_price_gte: float | None = None,
        strike_price_lte: float | None = None,
        page_token: str | None = None,
    ) -> dict[str, Any]:
        """Fetch one page of option contracts. Returns the raw JSON body."""
        if isinstance(underlying_symbols, (list, tuple, set)):
            underlyings = ",".join(str(s).upper() for s in underlying_symbols)
        else:
            underlyings = str(underlying_symbols).upper()

        params: dict[str, Any] = {
            "underlying_symbols": underlyings,
            "status": status,
            "limit": min(int(limit), 10000),
        }
        if option_type:
            params["type"] = str(option_type).lower()
        if expiration_date_gte is not None:
            params["expiration_date_gte"] = str(expiration_date_gte)
        if expiration_date_lte is not None:
            params["expiration_date_lte"] = str(expiration_date_lte)
        if strike_price_gte is not None:
            params["strike_price_gte"] = strike_price_gte
        if strike_price_lte is not None:
            params["strike_price_lte"] = strike_price_lte
        if page_token:
            params["page_token"] = page_token

        url = f"{self.base_url}/v2/options/contracts"
        response = self.client.get(url, params=params, headers=self._headers())
        response.raise_for_status()
        return response.json()

    def iter_contracts(
        self,
        underlying_symbols: str | list[str],
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        """Page through contracts until exhausted (or a safety cap)."""
        collected: list[dict[str, Any]] = []
        token: str | None = None
        for _ in range(20):  # hard cap on pages
            payload = self.list_contracts(
                underlying_symbols, page_token=token, **kwargs
            )
            batch = payload.get("option_contracts") or payload.get("contracts") or []
            if isinstance(batch, dict):
                batch = list(batch.values())
            collected.extend(batch)
            token = payload.get("next_page_token")
            if not token:
                break
        return collected

    def contracts_for_dte_window(
        self,
        underlying: str,
        *,
        dte_min: int = 14,
        dte_max: int = 42,
        option_type: str | None = None,
        as_of: date | None = None,
    ) -> list[dict[str, Any]]:
        """Contracts whose expiration falls in [dte_min, dte_max] calendar days."""
        today = as_of or date.today()
        gte = today + timedelta(days=int(dte_min))
        lte = today + timedelta(days=int(dte_max))
        return self.iter_contracts(
            underlying,
            option_type=option_type,
            expiration_date_gte=gte.isoformat(),
            expiration_date_lte=lte.isoformat(),
            status="active",
        )
