"""Technical indicators (pure pandas, no TA-lib dependency)."""
from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_TECH = {
    "ema_fast": 20, "ema_mid": 50, "ema_slow": 200,
    "rsi_period": 14, "atr_period": 14, "adx_period": 14,
    "breakout_lookback": 20, "week52_bars": 252,
}
DEFAULT_IND = {
    "major_lookback": 60, "bbands_period": 20, "bbands_std": 2.0,
    "supertrend_period": 10, "supertrend_multiplier": 3.0,
    "obv_rising_bars": 10, "vwap_window": 20,
}


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    # no losses in window → RSI 100 (instead of NaN); first bar stays NaN
    out = out.where(~(loss == 0) | (gain == 0), 100.0)
    out.iloc[0] = np.nan
    return out


def true_range(df: pd.DataFrame) -> pd.Series:
    prev = df["Close"].shift(1)
    return pd.concat(
        [df["High"] - df["Low"], (df["High"] - prev).abs(), (df["Low"] - prev).abs()], axis=1
    ).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1 / period, adjust=False).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    up = df["High"].diff()
    down = -df["Low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    a = atr(df, period).replace(0, np.nan)
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / a
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / a
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def bbands(close: pd.Series, period: int = 20, std: float = 2.0):
    """Bollinger Bands → (upper, middle, lower)."""
    mid = close.rolling(period, min_periods=period).mean()
    dev = close.rolling(period, min_periods=period).std()
    return mid + std * dev, mid, mid - std * dev


def stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3, smooth_k: int = 3):
    """Slow stochastic → (%K, %D)."""
    ll = df["Low"].rolling(k_period, min_periods=k_period).min()
    hh = df["High"].rolling(k_period, min_periods=k_period).max()
    raw_k = 100 * (df["Close"] - ll) / (hh - ll).replace(0, np.nan)
    k = raw_k.rolling(smooth_k, min_periods=smooth_k).mean()
    return k, k.rolling(d_period, min_periods=d_period).mean()


def obv(df: pd.DataFrame) -> pd.Series:
    """On-Balance Volume."""
    return (np.sign(df["Close"].diff()).fillna(0) * df["Volume"]).cumsum()


def rolling_vwap(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """Volume-weighted average price over the last `window` sessions."""
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    vol = df["Volume"].rolling(window, min_periods=window).sum()
    return (tp * df["Volume"]).rolling(window, min_periods=window).sum() / vol.replace(0, np.nan)


def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0):
    """Supertrend → (line, direction) with direction +1 up / -1 down."""
    hl2 = ((df["High"] + df["Low"]) / 2).to_numpy()
    a = atr(df, period).to_numpy()
    close = df["Close"].to_numpy()
    upper, lower = hl2 + multiplier * a, hl2 - multiplier * a
    fu, fl = upper.copy(), lower.copy()
    direction = np.ones(len(close))
    line = np.full(len(close), np.nan)
    for i in range(1, len(close)):
        fu[i] = min(upper[i], fu[i - 1]) if close[i - 1] <= fu[i - 1] else upper[i]
        fl[i] = max(lower[i], fl[i - 1]) if close[i - 1] >= fl[i - 1] else lower[i]
        if close[i] > fu[i - 1]:
            direction[i] = 1
        elif close[i] < fl[i - 1]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]
        line[i] = fl[i] if direction[i] == 1 else fu[i]
    return pd.Series(line, index=df.index), pd.Series(direction, index=df.index)


def recent_failed_breakout(df: pd.DataFrame, lookback: int = 5) -> bool:
    """True if a 20-day breakout in the last `lookback` sessions has since closed back below its level."""
    if "PriorHigh20" not in df or len(df) < lookback + 2:
        return False
    close_now = float(df["Close"].iloc[-1])
    for i in range(-lookback - 1, -1):
        level = df["PriorHigh20"].iloc[i]
        if pd.notna(level) and df["Close"].iloc[i] > level and close_now < level:
            return True
    return False


def enrich(df: pd.DataFrame, cfg: dict | None = None) -> pd.DataFrame:
    """Add all indicator columns used by the scanners."""
    t = {**DEFAULT_TECH, **((cfg or {}).get("technical") or {})}
    out = df.copy()
    c = out["Close"]
    out["EMA20"] = ema(c, t["ema_fast"])
    out["EMA50"] = ema(c, t["ema_mid"])
    out["EMA200"] = ema(c, t["ema_slow"])
    out["EMA200Slope20"] = (out["EMA200"] / out["EMA200"].shift(20) - 1) * 100
    out["EMA50Slope10"] = (out["EMA50"] / out["EMA50"].shift(10) - 1) * 100
    out["RSI"] = rsi(c, t["rsi_period"])
    out["ATR"] = atr(out, t["atr_period"])
    out["ATRPct"] = out["ATR"] / c * 100
    out["ADX"] = adx(out, t["adx_period"])
    out["MACD"], out["MACDSignal"], out["MACDHist"] = macd(c)

    out["AvgVolume20"] = out["Volume"].rolling(20).mean()
    prior_avg_vol = out["Volume"].rolling(20).mean().shift(1)
    out["VolumeRatio"] = out["Volume"] / prior_avg_vol.replace(0, np.nan)
    out["AvgVolume5"] = out["Volume"].rolling(5).mean()
    out["TradedValueCr"] = c * out["Volume"] / 1e7
    out["AvgTradedValueCr20"] = out["TradedValueCr"].rolling(20).mean()

    n = t["breakout_lookback"]
    out["PriorHigh20"] = out["High"].rolling(n).max().shift(1)
    out["PriorLow20"] = out["Low"].rolling(n).min().shift(1)
    w = t["week52_bars"]
    out["High52w"] = out["High"].rolling(w, min_periods=min(w, 200)).max()
    out["Low52w"] = out["Low"].rolling(w, min_periods=min(w, 200)).min()

    out["Return20"] = c.pct_change(20)
    out["Return63"] = c.pct_change(63)
    out["ExtensionEMA20Pct"] = (c / out["EMA20"] - 1) * 100
    rng = (out["High"] - out["Low"]).replace(0, np.nan)
    out["CloseRangePos"] = ((c - out["Low"]) / rng).fillna(0.5)

    # ── indicators added from srikanth-stock-2 ──
    ind = {**DEFAULT_IND, **((cfg or {}).get("indicators") or {})}
    maj = int(ind["major_lookback"])
    out["PriorCloseHigh60"] = c.rolling(maj, min_periods=maj).max().shift(1)   # stock-2 "breakout_level"
    up, _, lo = bbands(c, ind["bbands_period"], ind["bbands_std"])
    out["BBPercent"] = (c - lo) / (up - lo).replace(0, np.nan)
    out["StochK"], out["StochD"] = stochastic(out)
    out["OBV"] = obv(out)
    out["OBVRising"] = out["OBV"] > out["OBV"].shift(ind["obv_rising_bars"])
    vw = rolling_vwap(out, ind["vwap_window"])
    out["VWAPDistPct"] = (c / vw - 1) * 100
    _, out["SupertrendDir"] = supertrend(out, ind["supertrend_period"], ind["supertrend_multiplier"])
    out["Return5"] = c.pct_change(5)
    return out
