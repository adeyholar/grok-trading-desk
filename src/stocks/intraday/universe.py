"""Intraday equity universe helpers — mega-cap names + liquid ETFs.

Same-session intraday universe (mega-cap + liquid ETFs). Network-free.
"""

from __future__ import annotations

from typing import Any, Iterable

# Core mega-caps commonly traded on liquid intraday books.
DEFAULT_MEGA_CAPS: frozenset[str] = frozenset(
    {
        "AAPL",
        "MSFT",
        "AMZN",
        "GOOGL",
        "GOOG",
        "META",
        "NVDA",
        "TSLA",
        "BRK.B",
        "BRKB",
        "JPM",
        "V",
        "MA",
        "UNH",
        "XOM",
        "JNJ",
        "WMT",
        "PG",
        "HD",
        "COST",
        "AVGO",
        "NFLX",
        "AMD",
        "CRM",
        "ORCL",
    }
)

# Highly liquid broad-market / sector ETFs suitable for same-session worker-managed exits.
DEFAULT_INTRADAY_ETFS: frozenset[str] = frozenset(
    {
        "SPY",
        "QQQ",
        "IWM",
        "DIA",
        "VOO",
        "IVV",
        "XLK",
        "XLF",
        "XLE",
        "XLV",
        "SMH",
        "TQQQ",
        "SQQQ",
        "HYG",
        "TLT",
        "GLD",
    }
)

DEFAULT_INTRADAY_UNIVERSE: frozenset[str] = DEFAULT_MEGA_CAPS | DEFAULT_INTRADAY_ETFS


def normalize_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper().replace(" ", "")


def is_mega_cap(symbol: str, allowlist: Iterable[str] | None = None) -> bool:
    pool = frozenset(normalize_symbol(s) for s in (allowlist or DEFAULT_MEGA_CAPS))
    return normalize_symbol(symbol) in pool


def is_intraday_etf(symbol: str, allowlist: Iterable[str] | None = None) -> bool:
    pool = frozenset(normalize_symbol(s) for s in (allowlist or DEFAULT_INTRADAY_ETFS))
    return normalize_symbol(symbol) in pool


def in_intraday_universe(
    symbol: str,
    *,
    mega_caps: Iterable[str] | None = None,
    etfs: Iterable[str] | None = None,
    extra: Iterable[str] | None = None,
) -> bool:
    """True when symbol is a known mega-cap, liquid ETF, or caller extra allowlist."""
    sym = normalize_symbol(symbol)
    if not sym:
        return False
    if is_mega_cap(sym, mega_caps):
        return True
    if is_intraday_etf(sym, etfs):
        return True
    if extra is not None and sym in {normalize_symbol(s) for s in extra}:
        return True
    return False


def filter_intraday_universe(
    symbols: Iterable[str | dict[str, Any]],
    *,
    mega_caps: Iterable[str] | None = None,
    etfs: Iterable[str] | None = None,
    extra: Iterable[str] | None = None,
) -> list[str]:
    """Return normalized symbols that pass the mega-cap + ETF intraday filter."""
    out: list[str] = []
    seen: set[str] = set()
    for item in symbols:
        if isinstance(item, dict):
            raw = str(item.get("symbol") or item.get("ticker") or "")
        else:
            raw = str(item)
        sym = normalize_symbol(raw)
        if not sym or sym in seen:
            continue
        if in_intraday_universe(sym, mega_caps=mega_caps, etfs=etfs, extra=extra):
            seen.add(sym)
            out.append(sym)
    return out
