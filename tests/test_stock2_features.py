"""Features ported from KGR1516/srikanth-stock-2."""
from datetime import datetime
from email import message_from_bytes

import pytest

from src.agent.report_agent import build_email
from src.analysis import confluence
from src.analysis.scoring import action_for, gated_action, penalties_for, score_breakdown
from src.analysis.technical import enrich, recent_failed_breakout, supertrend
from src.data.nse import get_universe, load_watchlist
from src.data.quality import drop_incomplete_bar
from src.scanner.breakout import follow_through, grade_breakout, live_status
from tests.conftest import AS_OF, add_breakout, make_ohlcv


# ── setup grades (stock-2 breakout_engine) ────────────────────────────
@pytest.mark.parametrize("close,vol,rsi,expected", [
    (100.5, 6, 60, "Strong Fresh"),
    (101.5, 6, 60, "Fresh"),
    (102.5, 3.5, 72, "Solid"),
    (106.0, 6, 60, "Extended"),
    (101.0, 6, 76, "Extended"),
    (99.0, 6, 60, "Failed"),
    (101.5, 2.0, 60, "Unconfirmed"),   # stock-2 called this Extended
])
def test_grade_breakout(close, vol, rsi, expected):
    assert grade_breakout(close, 100.0, rsi, vol) == expected


def test_live_status_and_follow_through(uptrend):
    assert live_status(105, 104, 100) == "Held"
    assert live_status(103, 104, 100) == "Slipped"
    assert live_status(99, 101, 100) == "Failed"
    assert follow_through(enrich(make_ohlcv(seed=3, drift=0.003)).iloc[-1]) in ("Strong", "Partial")
    assert follow_through(enrich(make_ohlcv(seed=3, drift=-0.003)).iloc[-1]) == "Weak"


def test_failed_breakout_detected(cfg):
    df = add_breakout(make_ohlcv(seed=2))
    df = __import__("pandas").concat([df, df.tail(1)])          # extra bar …
    df.index = __import__("pandas").bdate_range(end=df.index[-2] + __import__("pandas").offsets.BDay(1),
                                                periods=len(df))
    lvl = df["High"].iloc[-22:-2].max()
    df.iloc[-1, df.columns.get_loc("Close")] = lvl * 0.97           # … that closes back below the level
    df.iloc[-1, df.columns.get_loc("Low")] = lvl * 0.96
    assert recent_failed_breakout(enrich(df, cfg.settings))
    assert not recent_failed_breakout(enrich(add_breakout(make_ohlcv(seed=2)), cfg.settings))


# ── indicators ────────────────────────────────────────────────────────
def test_supertrend_direction():
    _, d_up = supertrend(make_ohlcv(seed=5, drift=0.004))
    _, d_dn = supertrend(make_ohlcv(seed=5, drift=-0.004))
    assert d_up.iloc[-1] == 1 and d_dn.iloc[-1] == -1


def test_enrich_adds_stock2_columns(uptrend, cfg):
    out = enrich(uptrend, cfg.settings)
    for c in ["PriorCloseHigh60", "BBPercent", "StochK", "OBV", "OBVRising", "VWAPDistPct", "SupertrendDir"]:
        assert c in out


@pytest.mark.skipif(not confluence.available(), reason="pandas-ta-classic not installed")
def test_confluence_separates_trends():
    up = confluence.confluence_signals(make_ohlcv(seed=7, drift=0.003))
    dn = confluence.confluence_signals(make_ohlcv(seed=7, drift=-0.003))
    assert up["confluence_score"] > 65 > 35 > dn["confluence_score"]
    assert up["indicators_computed"] > 200


# ── scoring, penalties, verdicts (stock-2 true_quality / settings) ────
def test_action_bands():
    assert [action_for(x) for x in (85, 70, 55, 40, 10)] == ["BUY NOW", "BUY", "WATCH", "AVOID", "SKIP"]
    assert gated_action(61, is_setup=True) == "BUY"          # passed all gates → at least BUY
    assert gated_action(90, is_setup=False) == "WATCH"       # rejected → never BUY
    assert gated_action(86, is_setup=True) == "BUY NOW"


def test_penalties_and_missing_confluence_renormalise():
    assert penalties_for(True, True) == -23
    base = score_breakdown(80, 80, 80, 80, 80)                # no confluence → weight dropped
    assert base["final"] == 80
    with_conf = score_breakdown(80, 80, 80, 80, 80, confluence=20, penalty=-8)
    assert with_conf["final"] == pytest.approx(80 * 0.85 + 20 * 0.15 - 8, abs=0.2)
    parts = sum(v for k, v in with_conf.items() if k.endswith("_pts"))
    assert parts == pytest.approx(with_conf["weighted"], abs=0.3)


# ── universe (watchlist override, stock-2 data/input/watchlist.txt) ───
def test_watchlist_overrides_universe(tmp_path):
    wl = tmp_path / "watchlist.txt"
    wl.write_text("# comment\nreliance\nTCS  # inline\n\nIRCTC\nTCS\n")
    assert load_watchlist(wl) == ["RELIANCE", "TCS", "IRCTC"]
    uni = get_universe({"source": "nifty500", "watchlist_file": str(wl), "symbols": {"TCS": "IT"}})
    assert uni == {"RELIANCE": "Unclassified", "TCS": "IT", "IRCTC": "Unclassified"}


def test_empty_watchlist_uses_config(tmp_path):
    wl = tmp_path / "w.txt"
    wl.write_text("# RELIANCE\n")
    assert get_universe({"source": "static", "watchlist_file": str(wl), "symbols": ["INFY"]}) == {"INFY": "Unclassified"}


# ── intraday-run guard ────────────────────────────────────────────────
def test_partial_candle_dropped_only_during_market_hours():
    df = make_ohlcv(end=AS_OF)
    during = datetime(AS_OF.year, AS_OF.month, AS_OF.day, 11, 0)
    after = datetime(AS_OF.year, AS_OF.month, AS_OF.day, 17, 0)
    assert len(drop_incomplete_bar(df, during)[0]) == len(df) - 1
    assert len(drop_incomplete_bar(df, after)[0]) == len(df)


# ── email (stock-2 send_email.py) ─────────────────────────────────────
def test_email_has_summary_and_attachment(cfg, tmp_path):
    from tests.test_pipeline import _run
    res = _run(cfg, tmp_path)
    msg = build_email(res, "me@example.com", "you@example.com", tmp_path / "swing_agent.xlsx")
    parsed = message_from_bytes(msg.as_bytes())
    assert res.as_of in parsed["Subject"]
    parts = list(parsed.walk())
    assert any(p.get_filename() == f"swing_agent_{res.as_of}_{res.session}.xlsx" for p in parts)
    body = next(p for p in parts if p.get_content_type() == "text/plain").get_payload(decode=True).decode()
    assert res.setups[0].symbol in body and "not investment advice" in body


def test_pipeline_runs_without_pandas_ta(cfg, tmp_path, monkeypatch):
    """No pandas-ta-classic → confluence skipped, weights renormalised, still produces setups."""
    monkeypatch.setattr(confluence, "_pta", None)
    from tests.test_pipeline import _run
    res = _run(cfg, tmp_path)
    assert res.setups and all(s.confluence_score is None for s in res.setups)
    assert "confluence_pts" not in res.setups[0].score_breakdown
    assert not any("confluence" in src for src in res.data_sources)
