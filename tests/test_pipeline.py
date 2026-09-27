"""End-to-end offline run of the full 8-stage pipeline."""
import json
from datetime import timedelta

import openpyxl

from src.agent.orchestrator import run_scan
from src.analysis.technical import enrich
from src.analysis.setup import entry_trigger
from tests.conftest import AS_OF, add_breakout, make_ohlcv

GOOD_FUND = {"roe": 0.2, "debt_to_equity_pct": 30, "revenue_growth": 0.15, "earnings_growth": 0.2,
             "profit_margin": 0.12, "pe": 35, "market_cap": 8e11}


def _universe():
    prices = {
        "BRKA": add_breakout(make_ohlcv(seed=11, drift=0.002)),
        "BRKB": add_breakout(make_ohlcv(seed=12, drift=0.002)),
        "EARN": add_breakout(make_ohlcv(seed=13, drift=0.002)),
        "FLAT": make_ohlcv(seed=14, drift=0.0),
        "DOWN": make_ohlcv(seed=15, drift=-0.002),
        "THIN": add_breakout(make_ohlcv(seed=16, drift=0.002, volume=10_000)),
        "SHORT": make_ohlcv(seed=17, n=120),
    }
    for i in range(8):  # healthy breadth
        prices[f"UP{i}"] = make_ohlcv(seed=30 + i, drift=0.0015)
    return prices, make_ohlcv(seed=99, drift=0.001, start=24000, volume=0)


def _run(cfg, tmp_path, capital=500000):
    cfg.settings["capital"]["total"] = capital
    cfg.settings["report"]["archive_daily"] = False
    prices, bench = _universe()
    events = {"EARN": {"next_earnings": AS_OF + timedelta(days=2), "ex_dividend": None}}
    return run_scan(
        cfg, prices=prices, benchmark=bench,
        fundamentals_fn=lambda syms: {s: GOOD_FUND for s in syms},
        events_fn=lambda syms: {s: events.get(s, {"next_earnings": None, "ex_dividend": None}) for s in syms},
        as_of=AS_OF, output_dir=tmp_path,
    )


def test_pipeline_end_to_end(cfg, tmp_path):
    res = _run(cfg, tmp_path)
    syms = [s.symbol for s in res.setups]

    assert res.regime.regime in {"bullish", "neutral"}
    assert "BRKA" in syms
    # BRKB breaks out with RSI > 75 → graded Extended → watchlist, not a setup (stock-2 rule)
    brkb = next(c for c in res.watchlist if c.symbol == "BRKB")
    assert brkb.breakout_grade == "Extended" and "Extended" in brkb.rejected_reason
    assert "EARN" not in syms and "THIN" not in syms and "SHORT" not in syms
    earn = next(c for c in res.watchlist if c.symbol == "EARN")
    assert "earnings blackout" in earn.rejected_reason
    assert any(q.symbol == "SHORT" and q.status == "FAIL" for q in res.quality)

    for s in res.setups:
        assert s.stop < s.entry_trigger < s.target1 < s.target2
        assert abs((s.target1 - s.entry_trigger) / s.risk_per_share - s.rr_t1) < 0.05
        assert s.rr_t1 >= cfg.rules["risk"]["min_reward_risk"]
        assert s.capital_at_risk <= 500000 * cfg.rules["risk"]["risk_per_trade_pct"] / 100 * res.regime.exposure + 1
        assert s.position_value <= 500000 * cfg.rules["risk"]["max_position_pct"] / 100 + 1
        assert s.entry_trigger > s.close

    # ⑧ reports
    for ext in ("xlsx", "json", "md"):
        assert (tmp_path / f"swing_agent.{ext}").exists()
    payload = json.loads((tmp_path / "swing_agent.json").read_text())
    assert payload["setups"][0]["symbol"] in syms and payload["market_regime"]["regime"] == res.regime.regime
    wb = openpyxl.load_workbook(tmp_path / "swing_agent.xlsx")
    assert wb.sheetnames == ["Summary", "Next Session Setups", "Watchlist", "Score Breakdown",
                             "Pre-Market Checklist", "Full Scan", "Sectors", "Evidence", "Data Quality"]
    # stock-2 verdicts: every setup is at least BUY; rejected names never read BUY
    assert all(s.action in ("BUY", "BUY NOW") for s in res.setups)
    assert all(c.action in ("WATCH", "AVOID", "SKIP") for c in res.watchlist)
    assert {"action", "breakout_grade", "supertrend", "confluence_score"} <= set(res.full_scan.columns)
    assert "Next-session setups" in (tmp_path / "swing_agent.md").read_text()


def test_bearish_regime_produces_no_setups(cfg, tmp_path):
    cfg.rules["market_regime"]["exposure"]["neutral"] = 0.0
    cfg.rules["market_regime"]["exposure"]["bullish"] = 0.0
    res = _run(cfg, tmp_path)
    assert res.setups == []
    assert all("regime" in c.rejected_reason or c.rejected_reason for c in res.watchlist)


def test_entry_trigger_above_breakout_high(cfg):
    df = enrich(add_breakout(make_ohlcv(seed=11, drift=0.002)), cfg.settings)
    assert entry_trigger("Breakout", df, cfg.rules) > df["High"].iloc[-1]


def test_aborts_when_data_missing(cfg, tmp_path):
    import pytest
    from src.agent.orchestrator import DataUnavailableError
    prices = {"AAA": make_ohlcv(n=100), "BBB": make_ohlcv(n=90), "CCC": make_ohlcv()}  # 1/3 usable
    with pytest.raises(DataUnavailableError):
        run_scan(cfg, prices=prices, benchmark=make_ohlcv(), as_of=AS_OF,
                 output_dir=tmp_path, fundamentals_fn=lambda s: {}, events_fn=lambda s: {})
    assert not (tmp_path / "swing_agent.json").exists()
