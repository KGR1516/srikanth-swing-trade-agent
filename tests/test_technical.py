import numpy as np
import pandas as pd

from src.analysis.technical import adx, atr, ema, enrich, rsi


def test_ema():
    s = pd.Series(range(1, 30), dtype=float)
    out = ema(s, 5)
    assert len(out) == len(s) and out.iloc[-1] > out.iloc[0]


def test_rsi_bounds_and_monotonic_series():
    s = pd.Series(range(1, 30), dtype=float)
    out = rsi(s, 14).dropna()
    assert ((out >= 0) & (out <= 100)).all()
    assert out.iloc[-1] == 100.0          # only gains → RSI 100, not NaN


def test_atr_adx_positive(uptrend):
    assert (atr(uptrend).dropna() > 0).all()
    a = adx(uptrend).dropna()
    assert ((a >= 0) & (a <= 100)).all()


def test_enrich_columns(uptrend, cfg):
    out = enrich(uptrend, cfg.settings)
    for col in ["EMA20", "EMA50", "EMA200", "RSI", "ATR", "ADX", "MACDHist", "PriorHigh20",
                "High52w", "VolumeRatio", "AvgTradedValueCr20", "CloseRangePos"]:
        assert col in out
    last = out.iloc[-1]
    assert np.isfinite(last[["EMA200", "ATR", "High52w", "PriorHigh20"]].astype(float)).all()
    # PriorHigh20 must exclude today's bar
    assert last["PriorHigh20"] == uptrend["High"].iloc[-21:-1].max()
