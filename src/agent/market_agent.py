"""Market regime agent: is the tape supportive of new long swing trades?"""
from __future__ import annotations

import logging

import pandas as pd

from src.analysis.risk import regime_exposure
from src.analysis.technical import ema
from src.models import MarketRegime

log = logging.getLogger(__name__)


class MarketAgent:
    def __init__(self, rules: dict):
        self.rules = rules

    def run(self, benchmark: pd.DataFrame, enriched: dict[str, pd.DataFrame]) -> MarketRegime:
        mr = self.rules["market_regime"]
        breadth_vals = [bool(df["Close"].iloc[-1] > df["EMA50"].iloc[-1]) for df in enriched.values() if len(df)]
        breadth = sum(breadth_vals) / len(breadth_vals) * 100 if breadth_vals else 50.0
        notes: list[str] = []

        if benchmark is None or benchmark.empty or len(benchmark) < 200:
            notes.append("Benchmark data unavailable — regime set to neutral from breadth only.")
            regime = "bullish" if breadth >= mr["breadth_bullish_pct"] else (
                "bearish" if breadth < mr["breadth_bearish_pct"] else "neutral")
            regime = "neutral" if regime == "bullish" else regime
            return MarketRegime("n/a", regime, regime_exposure(regime, self.rules), 0.0, 0.0,
                                False, False, round(breadth, 1), notes)

        c = benchmark["Close"]
        e50, e200 = ema(c, 50), ema(c, 200)
        close = float(c.iloc[-1])
        above50, above200 = close > e50.iloc[-1], close > e200.iloc[-1]
        e50_rising = e50.iloc[-1] > e50.iloc[-11]
        ret20 = (close / float(c.iloc[-21]) - 1) * 100 if len(c) > 21 else 0.0

        if above50 and above200 and e50_rising and breadth >= mr["breadth_bullish_pct"]:
            regime = "bullish"
        elif (not above200 and breadth < mr["breadth_bearish_pct"]) or (not above50 and not above200):
            regime = "bearish"
        else:
            regime = "neutral"

        notes.append(f"Nifty {'above' if above50 else 'below'} 50-EMA, {'above' if above200 else 'below'} 200-EMA; "
                     f"50-EMA {'rising' if e50_rising else 'falling'}.")
        notes.append(f"Breadth: {breadth:.0f}% of universe above 50-EMA.")
        if regime == "neutral":
            notes.append("Neutral regime → position risk halved.")
        if regime == "bearish":
            notes.append("Bearish regime → no new long setups; watchlist only.")

        result = MarketRegime(
            as_of=str(pd.Timestamp(c.index[-1]).date()),
            regime=regime,
            exposure=regime_exposure(regime, self.rules),
            benchmark_close=round(close, 2),
            benchmark_return_20d_pct=round(ret20, 2),
            above_ema50=bool(above50),
            above_ema200=bool(above200),
            breadth_pct=round(breadth, 1),
            notes=notes,
        )
        log.info("Market regime: %s (exposure %.2f, breadth %.0f%%)", regime, result.exposure, breadth)
        return result
