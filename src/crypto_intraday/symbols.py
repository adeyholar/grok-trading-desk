"""Alpaca paper crypto symbols allowlist — fail-closed.

Only BTC/USD and ETH/USD (and slash/no-slash variants Alpaca accepts).
NOT pump.fun / Solana memecoins. Unknown symbols refused.
"""

from __future__ import annotations

from typing import Iterable

# Canonical forms used in tickets / orders.
ALLOWED_CRYPTO_SYMBOLS: frozenset[str] = frozenset({"BTC/USD", "ETH/USD"})

# Accepted aliases → canonical.
_ALIASES: dict[str, str] = {
    "BTC/USD": "BTC/USD",
    "BTCUSD": "BTC/USD",
    "BTC": "BTC/USD",
    "ETH/USD": "ETH/USD",
    "ETHUSD": "ETH/USD",
    "ETH": "ETH/USD",
}


def normalize_crypto_symbol(symbol: str) -> str:
    raw = str(symbol or "").strip().upper().replace(" ", "")
    if not raw:
        return ""
    if raw in _ALIASES:
        return _ALIASES[raw]
    # BTC-USD style
    raw2 = raw.replace("-", "/")
    return _ALIASES.get(raw2, "")


def is_allowed_crypto_symbol(symbol: str) -> bool:
    return normalize_crypto_symbol(symbol) in ALLOWED_CRYPTO_SYMBOLS


def filter_crypto_symbols(symbols: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in symbols:
        canon = normalize_crypto_symbol(item)
        if canon and canon not in seen:
            seen.add(canon)
            out.append(canon)
    return out
