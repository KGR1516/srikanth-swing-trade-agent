"""② Market data — daily OHLCV from Yahoo Finance (split/dividend adjusted),
cached per day under data/raw so re-runs don't re-download.

Swap this module for a broker / vendor feed without touching the analysis layer:
the contract is just {symbol: DataFrame[Open, High, Low, Close, Volume]}.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from src.data.nse import from_yahoo, to_yahoo

log = logging.getLogger(__name__)

OHLCV = ["Open", "High", "Low", "Close", "Volume"]


def _cache_file(cache_dir: Path, ticker: str) -> Path:
    safe = ticker.replace("^", "IDX_").replace("&", "_AND_")
    return cache_dir / f"{safe}_{date.today():%Y%m%d}.csv"


def _read_cache(path: Path) -> pd.DataFrame | None:
    if path.exists():
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        return df if not df.empty else None
    return None


def _normalise(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).title() for c in df.columns]
    df = df[[c for c in OHLCV if c in df.columns]].dropna(how="all")
    idx = pd.to_datetime(df.index)
    df.index = idx.tz_localize(None) if idx.tz is not None else idx
    df.index.name = "Date"
    return df.sort_index()


def _split_download(raw: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return out
    if isinstance(raw.columns, pd.MultiIndex):
        level0 = set(raw.columns.get_level_values(0))
        for t in tickers:
            if t in level0:
                df = _normalise(raw[t])
                if not df.empty:
                    out[t] = df
    elif len(tickers) == 1:
        df = _normalise(raw)
        if not df.empty:
            out[tickers[0]] = df
    return out


def fetch_ohlcv(
    tickers: list[str],
    lookback_days: int = 420,
    cache_dir: Path | None = None,
    batch_size: int = 50,
) -> dict[str, pd.DataFrame]:
    """Return {yahoo_ticker: OHLCV DataFrame}."""
    import yfinance as yf  # lazy: tests run offline

    result: dict[str, pd.DataFrame] = {}
    pending: list[str] = []
    for t in tickers:
        cached = _read_cache(_cache_file(cache_dir, t)) if cache_dir else None
        if cached is not None:
            result[t] = cached
        else:
            pending.append(t)

    if pending:
        start = date.today() - timedelta(days=lookback_days)
        log.info("Downloading %d tickers from Yahoo (cached: %d)", len(pending), len(result))
        for i in range(0, len(pending), batch_size):
            batch = pending[i : i + batch_size]
            try:
                raw = yf.download(
                    batch,
                    start=start.isoformat(),
                    interval="1d",
                    auto_adjust=True,
                    group_by="ticker",
                    threads=True,
                    progress=False,
                )
            except Exception as exc:
                log.error("Download failed for batch starting %s: %s", batch[0], exc)
                continue
            for t, df in _split_download(raw, batch).items():
                result[t] = df
                if cache_dir:
                    df.to_csv(_cache_file(cache_dir, t))

    missing = [t for t in tickers if t not in result]
    if missing:
        log.warning("No data for %d tickers: %s", len(missing), ", ".join(missing[:15]))
    return result


def load_prices(
    symbols: list[str],
    benchmark: str,
    lookback_days: int,
    cache_dir: Path | None = None,
    suffix: str = ".NS",
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Return ({NSE symbol: OHLCV}, benchmark OHLCV)."""
    tickers = [to_yahoo(s, suffix) for s in symbols]
    data = fetch_ohlcv(tickers + [benchmark], lookback_days, cache_dir)
    bench = data.pop(benchmark, pd.DataFrame(columns=OHLCV))
    return {from_yahoo(t, suffix): df for t, df in data.items()}, bench
