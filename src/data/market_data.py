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


def _chunks(items: list[str], n: int):
    for i in range(0, len(items), n):
        yield items[i : i + n]


def download(
    tickers: list[str],
    batch_size: int = 50,
    retries: int = 2,
    pause: float = 0.5,
    **yf_kwargs,
) -> dict[str, pd.DataFrame]:
    """Batched yf.download with retries.

    Yahoo throttles large bursts from cloud IPs; a throttled batch comes back empty rather
    than raising. Missing tickers are retried with smaller batches and a growing pause.
    """
    import time

    import yfinance as yf  # lazy: tests run offline

    result: dict[str, pd.DataFrame] = {}
    pending = list(dict.fromkeys(tickers))
    for attempt in range(retries + 1):
        if not pending:
            break
        if attempt:
            wait = 5 * attempt * attempt
            batch_size = max(10, batch_size // 2)
            log.info("Retry %d/%d for %d tickers in %ds (batch %d)", attempt, retries, len(pending), wait, batch_size)
            time.sleep(wait)
        for batch in _chunks(pending, batch_size):
            try:
                raw = yf.download(batch, interval="1d", auto_adjust=True, group_by="ticker",
                                  threads=True, progress=False, **yf_kwargs)
            except Exception as exc:
                log.warning("Download failed for batch starting %s: %s", batch[0], exc)
                continue
            result.update(_split_download(raw, batch))
            if pause:
                time.sleep(pause)
        pending = [t for t in pending if t not in result]
    if pending:
        log.warning("No data for %d tickers after %d retries: %s", len(pending), retries,
                    ", ".join(pending[:15]))
    return result


def fetch_ohlcv(
    tickers: list[str],
    lookback_days: int = 420,
    cache_dir: Path | None = None,
    batch_size: int = 50,
    retries: int = 2,
) -> tuple[dict[str, pd.DataFrame], set[str]]:
    """Return ({yahoo_ticker: OHLCV}, tickers served from today's cache)."""
    result: dict[str, pd.DataFrame] = {}
    pending: list[str] = []
    for t in tickers:
        cached = _read_cache(_cache_file(cache_dir, t)) if cache_dir else None
        if cached is not None:
            result[t] = cached
        else:
            pending.append(t)
    from_cache = set(result)

    if pending:
        start = date.today() - timedelta(days=lookback_days)
        log.info("Downloading %d tickers from Yahoo (cached: %d)", len(pending), len(result))
        fresh = download(pending, batch_size, retries, start=start.isoformat())
        for t, df in fresh.items():
            result[t] = df
            if cache_dir:
                df.to_csv(_cache_file(cache_dir, t))
    return result, from_cache


def merge_recent(base: pd.DataFrame, recent: pd.DataFrame) -> pd.DataFrame:
    """Replace the tail of a cached history with freshly downloaded recent bars."""
    if recent is None or recent.empty:
        return base
    if base is None or base.empty:
        return recent
    return pd.concat([base[base.index < recent.index.min()], recent]).sort_index()


def refresh_recent(
    prices: dict[str, pd.DataFrame],
    symbols: list[str],
    days: int = 5,
    suffix: str = ".NS",
    batch_size: int = 50,
    retries: int = 1,
) -> int:
    """Re-download only the last `days` bars for `symbols` and merge them in place."""
    if not symbols:
        return 0
    tickers = [to_yahoo(s, suffix) for s in symbols]
    fresh = download(tickers, batch_size, retries, pause=0.2, period=f"{days}d")
    n = 0
    for t, df in fresh.items():
        sym = from_yahoo(t, suffix)
        prices[sym] = merge_recent(prices.get(sym), df)
        n += 1
    return n


def load_prices(
    symbols: list[str],
    benchmark: str,
    lookback_days: int,
    cache_dir: Path | None = None,
    suffix: str = ".NS",
    batch_size: int = 50,
    retries: int = 2,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame, int]:
    """Return ({NSE symbol: OHLCV}, benchmark OHLCV, number of symbols served from cache)."""
    tickers = [to_yahoo(s, suffix) for s in symbols]
    data, cached = fetch_ohlcv(tickers + [benchmark], lookback_days, cache_dir, batch_size, retries)
    bench = data.pop(benchmark, pd.DataFrame(columns=OHLCV))
    return {from_yahoo(t, suffix): df for t, df in data.items()}, bench, len(cached - {benchmark})
