import pytest

from src.analysis.risk import (RiskEnvelope, apply_portfolio_limits, compute_stop, position_size,
                               risk_envelope, round_tick)
from src.analysis.technical import enrich
from src.models import Setup


def test_round_tick():
    assert round_tick(100.03, 0.05, "up") == 100.05
    assert round_tick(100.03, 0.05, "down") == 100.0
    assert round_tick(100.10, 0.05, "up") == 100.10


def test_position_size_risk_and_cap():
    # ₹5,000 risk / ₹5 per share = 1000 sh, but cap ₹50,000 / ₹100 = 500 sh
    assert position_size(5000, 100, 95, 50000) == 500
    assert position_size(1000, 100, 95, 1e9) == 200
    assert position_size(1000, 100, 101, 1e9) == 0


def test_compute_stop_respects_min_distance(cfg):
    env = RiskEnvelope(atr=1.0, swing_low=99.8, structural_stop=99.7, risk_budget=5000,
                       position_cap=75000, approved=True)
    stop, basis = compute_stop(100.0, env, cfg.rules)
    # min distance = max(1.5% of 100, 1×ATR) = 1.5
    assert stop == pytest.approx(98.5)
    assert basis == "min distance"


def test_compute_stop_picks_tighter_of_swing_and_atr(cfg):
    env = RiskEnvelope(atr=2.0, swing_low=90, structural_stop=89.8, risk_budget=5000,
                       position_cap=75000, approved=True)
    stop, basis = compute_stop(100.0, env, cfg.rules)
    assert stop == pytest.approx(97.0)      # 1.5 × ATR tighter than far swing low
    assert "ATR" in basis


def test_bearish_regime_blocks_new_longs(uptrend, cfg):
    env = risk_envelope(enrich(uptrend), 500000, 0.0, cfg.rules)
    assert not env.approved and "regime" in env.reason


def test_neutral_regime_halves_budget(uptrend, cfg):
    full = risk_envelope(enrich(uptrend), 500000, 1.0, cfg.rules)
    half = risk_envelope(enrich(uptrend), 500000, 0.5, cfg.rules)
    assert half.risk_budget == pytest.approx(full.risk_budget / 2) == 2500


def _setup(sym, sector, score):
    return Setup(0, sym, sector, "2026-09-25", "Breakout", 100, 101, 103, 98, 107, 110, 3, 3, 2, 3, 10, 1010,
                 30, None, 2, 5, 80, 80, 80, 80, 100, score)


def test_portfolio_limits_sector_cap(cfg):
    setups = [_setup("A", "IT", 90), _setup("B", "IT", 85), _setup("C", "IT", 80), _setup("D", "Bank", 70)]
    kept, dropped = apply_portfolio_limits(setups, cfg.rules, top_n=10)
    assert [s.symbol for s in kept] == ["A", "B", "D"]
    assert dropped[0][0].symbol == "C" and "sector cap" in dropped[0][1]
