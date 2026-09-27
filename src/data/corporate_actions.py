"""Upcoming corporate events: results dates and ex-dividend dates."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

import pandas as pd

log = logging.getLogger(__name__)


def _to_date(val) -> date | None:
    if val is None:
        return None
    if isinstance(val, (list, tuple)):
        dates = [d for d in (_to_date(v) for v in val) if d]
        return min(dates) if dates else None
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    try:
        ts = pd.to_datetime(val)
        return None if pd.isna(ts) else ts.date()
    except Exception:
        return None


def _fetch_one(ticker: str) -> dict:
    import yfinance as yf

    out = {"next_earnings": None, "ex_dividend": None}
    try:
        cal = yf.Ticker(ticker).calendar
        if isinstance(cal, pd.DataFrame):  # older yfinance
            cal = cal.iloc[:, 0].to_dict() if not cal.empty else {}
        cal = cal or {}
        out["next_earnings"] = _to_date(cal.get("Earnings Date"))
        out["ex_dividend"] = _to_date(cal.get("Ex-Dividend Date"))
    except Exception as exc:
        log.debug("calendar failed for %s: %s", ticker, exc)
    return out


def fetch_events(tickers: list[str], max_workers: int = 8) -> dict[str, dict]:
    log.info("Fetching corporate events for %d tickers", len(tickers))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        return dict(zip(tickers, pool.map(_fetch_one, tickers)))
