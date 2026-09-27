"""Trend/momentum template and trend-pullback scanner."""
from __future__ import annotations

import pandas as pd

from src.models import Evidence, ScanResult


def scan_momentum(df: pd.DataFrame, cfg: dict) -> ScanResult:
    """Stage-2 style trend template. Score = weighted share of conditions met."""
    m = cfg["scanner"]["momentum"]
    last = df.iloc[-1]
    close = float(last["Close"])
    pct_52w = close / float(last["High52w"]) * 100 if pd.notna(last["High52w"]) else 0.0
    rsi_v, adx_v = float(last["RSI"]), float(last["ADX"])

    checks = [
        (Evidence("mo:close>EMA20", round(close, 2), round(float(last["EMA20"]), 2), close > last["EMA20"]), 10),
        (Evidence("mo:EMA20>EMA50", round(float(last["EMA20"]), 2), round(float(last["EMA50"]), 2),
                  last["EMA20"] > last["EMA50"]), 15),
        (Evidence("mo:EMA50>EMA200", round(float(last["EMA50"]), 2), round(float(last["EMA200"]), 2),
                  last["EMA50"] > last["EMA200"]), 15),
        (Evidence("mo:EMA200_rising_20d_pct", round(float(last["EMA200Slope20"]), 2), "> 0",
                  last["EMA200Slope20"] > 0), 10),
        (Evidence("mo:RSI", round(rsi_v, 1), f"{m['rsi_min']}–{m['rsi_max']}", m["rsi_min"] <= rsi_v <= m["rsi_max"]), 15),
        (Evidence("mo:ADX", round(adx_v, 1), f">= {m['adx_min']}", adx_v >= m["adx_min"]), 10),
        (Evidence("mo:MACD_hist", round(float(last["MACDHist"]), 3), "> 0", last["MACDHist"] > 0), 10),
        (Evidence("mo:pct_of_52w_high", round(pct_52w, 1), f">= {m['min_pct_of_52w_high']}",
                  pct_52w >= m["min_pct_of_52w_high"]), 15),
    ]
    # stock-2 indicators: Supertrend direction and OBV accumulation
    if "SupertrendDir" in df:
        st_up = bool(last["SupertrendDir"] > 0)
        checks.append((Evidence("mo:supertrend_up", "Up" if st_up else "Down", "Up", st_up), 10))
    if "OBVRising" in df:
        obv_up = bool(last["OBVRising"])
        checks.append((Evidence("mo:OBV_rising", obv_up, True, obv_up, "accumulation"), 5))
    for e, _ in checks:
        e.passed = bool(e.passed)
    total = sum(w for _, w in checks)
    score = round(sum(w for e, w in checks if e.passed) / total * 100, 1)
    return ScanResult("momentum", score >= m["pass_score"], float(score), [e for e, _ in checks],
                      {"rsi": round(rsi_v, 1), "adx": round(adx_v, 1), "pct_of_52w_high": round(pct_52w, 1)})


def scan_pullback(df: pd.DataFrame, cfg: dict) -> ScanResult:
    """Uptrend stock that has pulled back to the 20-EMA with cooled RSI."""
    p = cfg["scanner"]["pullback"]
    last, prev = df.iloc[-1], df.iloc[-2]
    close = float(last["Close"])
    dist = abs(close / float(last["EMA20"]) - 1) * 100
    rsi_v = float(last["RSI"])
    uptrend = bool(last["EMA20"] > last["EMA50"] > last["EMA200"] and close > last["EMA50"])
    ema50_up = bool(last["EMA50Slope10"] > 0)
    bounce = bool(close > float(prev["Close"]) or float(last["CloseRangePos"]) >= 0.6)

    ev = [
        Evidence("pl:uptrend_EMA20>EMA50>EMA200", uptrend, True, uptrend),
        Evidence("pl:EMA50_rising", round(float(last["EMA50Slope10"]), 2), "> 0", ema50_up),
        Evidence("pl:dist_from_EMA20_pct", round(dist, 2), f"<= {p['max_dist_from_ema20_pct']}%",
                 dist <= p["max_dist_from_ema20_pct"]),
        Evidence("pl:RSI_cooled", round(rsi_v, 1), f"{p['rsi_min']}–{p['rsi_max']}", p["rsi_min"] <= rsi_v <= p["rsi_max"]),
        Evidence("pl:bounce_signal", bounce, True, bounce, "up close or strong close in range"),
    ]
    weights = [30, 15, 25, 15, 15]
    score = sum(w for e, w in zip(ev, weights) if e.passed)
    passed = all(e.passed for e in ev)
    return ScanResult("pullback", passed, float(score), ev, {"dist_from_ema20_pct": round(dist, 2)})
