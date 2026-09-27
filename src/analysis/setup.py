"""⑦ Setup engine: turn an approved candidate into a concrete next-session plan.

Entry trigger → buy-stop above a confirmation level (never "buy at market").
Stop loss     → from the risk engine (swing low / ATR, bounded).
Targets       → R-multiples of the actual risk per share.
"""
from __future__ import annotations

import pandas as pd

from src.analysis.risk import RiskEnvelope, compute_stop, position_size, round_tick
from src.models import Candidate, Evidence, Setup

ENTRY_BASIS = {
    "Breakout": "above breakout-day high (continuation confirmation)",
    "Pre-Breakout": "above 20-day high (breakout confirmation)",
    "Pullback": "above pullback-day high (bounce confirmation)",
}


def entry_trigger(setup_type: str, df: pd.DataFrame, rules: dict) -> float:
    last = df.iloc[-1]
    buf = rules["setup"]["entry_buffer_atr"] * float(last["ATR"])
    if setup_type == "Pre-Breakout":
        level = max(float(last["PriorHigh20"]), float(last["High"]))
    else:  # Breakout, Pullback
        level = float(last["High"])
    return round_tick(level + buf, rules["setup"]["tick_size"], "up")


def build_setup(
    cand: Candidate, df: pd.DataFrame, env: RiskEnvelope, rules: dict, settings: dict
) -> tuple[Setup | None, str]:
    r, s = rules["risk"], rules["setup"]
    tick = s["tick_size"]
    last = df.iloc[-1]

    trigger = entry_trigger(cand.setup_type, df, rules)
    stop, stop_basis = compute_stop(trigger, env, rules, tick)
    rps = trigger - stop
    if rps <= 0:
        return None, "invalid stop (≥ entry)"
    stop_pct = rps / trigger * 100
    if stop_pct > r["max_stop_pct"]:
        return None, f"stop {stop_pct:.1f}% wider than max {r['max_stop_pct']}%"

    multiples = sorted(r["target_r_multiples"])
    if multiples[0] < r["min_reward_risk"]:
        return None, f"T1 multiple {multiples[0]}R below min R:R {r['min_reward_risk']}"
    t1 = round_tick(trigger + multiples[0] * rps, tick)
    t2 = round_tick(trigger + multiples[-1] * rps, tick)

    qty = position_size(env.risk_budget, trigger, stop, env.position_cap)
    if qty <= 0:
        return None, "risk budget too small for one share"

    risks = list(cand.risks)
    high52 = float(last["High52w"]) if pd.notna(last["High52w"]) else None
    room_r = None
    if high52 and high52 > trigger * 1.005:
        room_r = round((high52 - trigger) / rps, 2)
        if room_r < multiples[0]:
            risks.append(f"52-week high {high52:.2f} is only {room_r}R above entry — T1 faces overhead supply.")

    evidence = list(cand.evidence) + [
        Evidence("setup:entry_trigger", trigger, ENTRY_BASIS.get(cand.setup_type, ""), True),
        Evidence("setup:stop", stop, stop_basis, True, f"{stop_pct:.2f}% below entry"),
        Evidence("setup:reward_risk_t1", multiples[0], f">= {r['min_reward_risk']}", True),
    ]

    setup = Setup(
        rank=0,
        symbol=cand.symbol,
        sector=cand.sector,
        as_of=cand.as_of,
        setup_type=cand.setup_type,
        close=round(cand.close, 2),
        entry_trigger=trigger,
        entry_limit=round_tick(trigger * (1 + s["max_gap_up_pct"] / 100), tick, "down"),
        stop=stop,
        target1=t1,
        target2=t2,
        risk_per_share=round(rps, 2),
        stop_pct=round(stop_pct, 2),
        rr_t1=multiples[0],
        rr_t2=multiples[-1],
        quantity=qty,
        position_value=round(qty * trigger, 2),
        capital_at_risk=round(qty * rps, 2),
        room_to_52w_high_r=room_r,
        valid_sessions=int(s["valid_sessions"]),
        time_stop_sessions=int(settings["project"]["holding_days"]),
        technical_score=cand.technical_score,
        rs_percentile=cand.rs_percentile,
        sector_score=cand.sector_score,
        fundamental_score=cand.fundamental_score,
        event_score=cand.event_score,
        final_score=cand.final_score,
        confluence_score=cand.confluence_score,
        penalty=cand.penalty,
        breakout_grade=cand.breakout_grade,
        live_status=cand.live_status,
        follow_through=cand.follow_through,
        score_breakdown=dict(cand.score_breakdown),
        confluence=dict(cand.confluence),
        evidence=evidence,
        risks=risks,
    )
    return setup, ""
