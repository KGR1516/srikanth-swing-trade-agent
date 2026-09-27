"""Fundamental snapshot per stock (Yahoo Finance `info`), cached daily.

Values are returned raw (as Yahoo reports them); unit conversion happens in
src.analysis.fundamental so the provider can be swapped easily.
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

FIELDS = {
    "marketCap": "market_cap",
    "trailingPE": "pe",
    "priceToBook": "pb",
    "returnOnEquity": "roe",
    "debtToEquity": "debt_to_equity_pct",
    "revenueGrowth": "revenue_growth",
    "earningsGrowth": "earnings_growth",
    "earningsQuarterlyGrowth": "earnings_quarterly_growth",
    "profitMargins": "profit_margin",
    "trailingEps": "trailing_eps",
    "sector": "yf_sector",
    "industry": "yf_industry",
}


def _fetch_one(ticker: str) -> dict:
    import yfinance as yf

    try:
        info = yf.Ticker(ticker).info or {}
    except Exception as exc:
        log.debug("info failed for %s: %s", ticker, exc)
        info = {}
    return {out: info.get(src) for src, out in FIELDS.items()}


def fetch_fundamentals(
    tickers: list[str], cache_dir: Path | None = None, max_workers: int = 8
) -> dict[str, dict]:
    cache_path = cache_dir / f"fundamentals_{date.today():%Y%m%d}.json" if cache_dir else None
    cache: dict[str, dict] = {}
    if cache_path and cache_path.exists():
        cache = json.loads(cache_path.read_text())

    todo = [t for t in tickers if t not in cache]
    if todo:
        log.info("Fetching fundamentals for %d tickers", len(todo))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for t, data in zip(todo, pool.map(_fetch_one, todo)):
                cache[t] = data
        if cache_path:
            cache_path.write_text(json.dumps(cache, indent=1, default=str))

    return {t: cache.get(t, {}) for t in tickers}
