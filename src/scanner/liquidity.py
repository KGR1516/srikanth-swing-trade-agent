"""Liquidity filter — only stocks you can enter and exit without slippage."""
from __future__ import annotations

import pandas as pd

from src.models import Evidence, ScanResult


def scan_liquidity(df: pd.DataFrame, cfg: dict) -> ScanResult:
    c = cfg["scanner"]["liquidity"]
    last = df.iloc[-1]
    price = float(last["Close"])
    avg_vol = float(last["AvgVolume20"])
    avg_val = float(last["AvgTradedValueCr20"])
    ev = [
        Evidence("liq:min_price", round(price, 2), f">= {c['min_price']}", price >= c["min_price"]),
        Evidence("liq:avg_volume_20d", round(avg_vol), f">= {c['min_avg_volume']:,}", avg_vol >= c["min_avg_volume"]),
        Evidence("liq:avg_traded_value_cr", round(avg_val, 2), f">= {c['min_avg_traded_value_cr']}",
                 avg_val >= c["min_avg_traded_value_cr"]),
    ]
    passed = all(e.passed for e in ev)
    score = min(100.0, avg_val / c["min_avg_traded_value_cr"] * 50) if c["min_avg_traded_value_cr"] else 100.0
    return ScanResult("liquidity", passed, round(score, 1), ev, {"avg_traded_value_cr": round(avg_val, 2)})


def quick_liquid(df: pd.DataFrame, cfg: dict, buffer: float = 0.5) -> bool:
    """Cheap pre-filter on raw OHLCV, run BEFORE indicators.

    Keeps anything within `buffer` of the real thresholds, so borderline stocks still get the
    full liquidity check later; clearly illiquid names (most of the full-NSE list) skip the
    expensive indicator work entirely.
    """
    c = cfg["scanner"]["liquidity"]
    tail = df.tail(20)
    if tail.empty:
        return False
    price = float(tail["Close"].iloc[-1])
    avg_val_cr = float((tail["Close"] * tail["Volume"]).mean()) / 1e7
    return price >= c["min_price"] * (1 - buffer) and avg_val_cr >= c["min_avg_traded_value_cr"] * buffer
