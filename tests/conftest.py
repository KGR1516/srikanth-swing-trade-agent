"""Synthetic OHLCV fixtures so the test-suite never touches the network."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from src.config import load_config

AS_OF = date(2026, 9, 25)  # a Friday


def make_ohlcv(n: int = 300, start: float = 100.0, drift: float = 0.0015, vol: float = 0.01,
               seed: int = 0, volume: float = 2_000_000, end: date = AS_OF) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = start * np.exp(np.cumsum(drift + vol * rng.standard_normal(n)))
    high = close * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.006, n)))
    open_ = np.clip(np.r_[close[0], close[:-1]], low, high)
    vols = volume * (0.8 + 0.4 * rng.random(n))
    idx = pd.bdate_range(end=pd.Timestamp(end), periods=n)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vols}, index=idx)


def add_breakout(df: pd.DataFrame, pct_above: float = 2.0, vol_mult: float = 3.0) -> pd.DataFrame:
    """Force the last bar to close above the prior 20-day high on heavy volume."""
    df = df.copy()
    prior_high = df["High"].iloc[-21:-1].max()
    c = prior_high * (1 + pct_above / 100)
    i = df.index[-1]
    df.loc[i, ["Open", "Low", "Close", "High"]] = [df["Close"].iloc[-2], df["Close"].iloc[-2] * 0.995, c, c * 1.003]
    df.loc[i, "Volume"] = df["Volume"].iloc[-21:-1].mean() * vol_mult
    return df


@pytest.fixture
def cfg():
    return load_config()


@pytest.fixture
def uptrend():
    return make_ohlcv(seed=1)


@pytest.fixture
def breakout_df():
    return add_breakout(make_ohlcv(seed=2))


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """The suite must never touch the network (it runs on GitHub before every scan).
    Tests that need a provider monkeypatch it themselves, which overrides this guard."""
    import yfinance

    from src.data import nse

    def blocked(*_a, **_k):
        raise RuntimeError("network access attempted inside a unit test")

    monkeypatch.setattr(nse, "_get", blocked)
    monkeypatch.setattr(yfinance, "download", blocked)
    monkeypatch.setattr(yfinance, "Ticker", blocked)
