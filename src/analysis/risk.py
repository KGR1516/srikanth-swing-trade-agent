"""⑥ Risk engine primitives: regime exposure, stop placement, position sizing."""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd


def round_tick(price: float, tick: float = 0.05, mode: str = "nearest") -> float:
    """Round to NSE tick size."""
    if tick <= 0:
        return round(price, 2)
    n = price / tick
    n = math.ceil(n - 1e-9) if mode == "up" else math.floor(n + 1e-9) if mode == "down" else round(n)
    return round(n * tick, 2)


def regime_exposure(regime: str, rules: dict) -> float:
    return float(rules["market_regime"]["exposure"].get(regime, 0.5))


@dataclass
class RiskEnvelope:
    atr: float
    swing_low: float
    structural_stop: float
    risk_budget: float            # ₹ at risk allowed for this trade
    position_cap: float           # ₹ max position value
    approved: bool
    reason: str = ""


def risk_envelope(df: pd.DataFrame, capital: float, exposure: float, rules: dict) -> RiskEnvelope:
    """Pre-trade risk checks independent of the exact entry price."""
    r = rules["risk"]
    last = df.iloc[-1]
    atr_v = float(last["ATR"])
    swing_low = float(df["Low"].tail(r["swing_low_lookback"]).min())
    structural = swing_low - r["stop_buffer_atr"] * atr_v
    budget = capital * r["risk_per_trade_pct"] / 100 * exposure
    cap = capital * r["max_position_pct"] / 100
    close = float(last["Close"])

    if exposure <= 0:
        return RiskEnvelope(atr_v, swing_low, structural, 0, cap, False, "market regime: no new longs")
    min_possible_stop_pct = max(r["min_stop_pct"], r["min_stop_atr"] * atr_v / close * 100)
    if min_possible_stop_pct > r["max_stop_pct"]:
        return RiskEnvelope(atr_v, swing_low, structural, budget, cap, False,
                            f"too volatile: min stop {min_possible_stop_pct:.1f}% > {r['max_stop_pct']}%")
    return RiskEnvelope(atr_v, swing_low, structural, budget, cap, True)


def compute_stop(entry: float, env: RiskEnvelope, rules: dict, tick: float = 0.05) -> tuple[float, str]:
    """Tighter of structural (swing-low) and ATR stop, but never inside noise."""
    r = rules["risk"]
    atr_stop = entry - r["atr_stop_multiple"] * env.atr
    structural = env.structural_stop if env.structural_stop < entry else atr_stop
    stop, basis = (structural, "swing low") if structural >= atr_stop else (atr_stop, f"{r['atr_stop_multiple']}×ATR")
    min_dist = max(entry * r["min_stop_pct"] / 100, r["min_stop_atr"] * env.atr)
    if entry - stop < min_dist:
        stop, basis = entry - min_dist, "min distance"
    return round_tick(stop, tick, "down"), basis


def position_size(risk_budget: float, entry: float, stop: float, position_cap: float) -> int:
    """Shares = risk budget / risk per share, capped by max position value."""
    rps = entry - stop
    if rps <= 0 or entry <= 0 or risk_budget <= 0:
        return 0
    return max(0, min(int(risk_budget // rps), int(position_cap // entry)))


def apply_portfolio_limits(setups: list, rules: dict, top_n: int) -> tuple[list, list[tuple[object, str]]]:
    """Keep best-scored setups within max positions and per-sector caps."""
    r = rules["risk"]
    limit = min(top_n, r["max_open_positions"])
    kept, dropped, per_sector = [], [], {}
    for s in sorted(setups, key=lambda x: x.final_score, reverse=True):
        if len(kept) >= limit:
            dropped.append((s, f"max open positions ({limit}) reached"))
        elif s.sector not in ("", "Unclassified") and per_sector.get(s.sector, 0) >= r["max_per_sector"]:
            dropped.append((s, f"sector cap ({r['max_per_sector']}) for {s.sector}"))
        else:
            kept.append(s)
            per_sector[s.sector] = per_sector.get(s.sector, 0) + 1
    return kept, dropped
