"""10:30 morning (setups + live status) and 14:45 afternoon (provisional) runs."""
from datetime import date, datetime

import pandas as pd
import pytest

from src.agent.orchestrator import run_scan
from src.analysis.setup import live_check
from src.data.quality import project_partial_volume, resolve_session, session_fraction
from tests.conftest import AS_OF, add_breakout, make_ohlcv
from tests.test_pipeline import GOOD_FUND

MON = date(2026, 9, 28)   # next trading day after AS_OF (Fri 25 Sep)


def at(d, hh, mm):
    return datetime(d.year, d.month, d.day, hh, mm)


def test_resolve_session():
    assert resolve_session("auto", at(MON, 10, 30)) == "morning"
    assert resolve_session("auto", at(MON, 14, 45)) == "afternoon"
    assert resolve_session("auto", at(MON, 17, 0)) == "eod"
    assert resolve_session("auto", at(date(2026, 9, 27), 10, 30)) == "eod"   # Sunday
    assert resolve_session("afternoon", at(MON, 17, 0)) == "afternoon"       # explicit wins


def test_volume_projection_at_1445():
    assert session_fraction(at(MON, 14, 45)) == pytest.approx(330 / 375)
    df = make_ohlcv(end=MON)
    out, done = project_partial_volume(df, at(MON, 14, 45))
    assert done and out["Volume"].iloc[-1] == pytest.approx(df["Volume"].iloc[-1] * 375 / 330)
    assert out["Volume"].iloc[:-1].equals(df["Volume"].iloc[:-1])
    _, done_after_close = project_partial_volume(df, at(MON, 17, 0))
    assert not done_after_close


class _S:  # minimal setup stub
    entry_trigger, entry_limit, stop, target1, risk_per_share = 100.0, 102.0, 97.0, 106.0, 3.0


@pytest.mark.parametrize("o,h,l,c,expect", [
    (103.0, 104, 102.5, 103.5, "SKIP"),
    (99.0, 99.8, 98.5, 99.5, "Not triggered"),
    (99.5, 101.5, 99.0, 101.0, "TRIGGERED"),
    (99.5, 101.5, 96.0, 96.5, "STOPPED OUT"),
    (100.5, 106.5, 100.0, 105.5, "T1 HIT"),
    (99.5, 101.0, 96.5, 100.2, "stop level also traded"),
])
def test_live_check(o, h, l, c, expect):
    status, last = live_check(_S(), pd.Series({"Open": o, "High": h, "Low": l, "Close": c}))
    assert expect in status and last == c


def _universe_with_today_bar():
    prices = {}
    for i, sym in enumerate(["BRKA", "BRKC"]):
        df = add_breakout(make_ohlcv(seed=11 + i * 10, drift=0.002, end=AS_OF))
        hi = df["High"].iloc[-1]
        # today (Mon): gap-free open, trades through the breakout-day high
        today = pd.DataFrame({"Open": [df["Close"].iloc[-1]], "High": [hi * 1.01], "Low": [hi * 0.985],
                              "Close": [hi * 1.008], "Volume": [df["Volume"].iloc[-21:-1].mean()]},
                             index=[pd.Timestamp(MON)])
        prices[sym] = pd.concat([df, today])
    for i in range(8):
        prices[f"UP{i}"] = make_ohlcv(seed=30 + i, drift=0.0015, end=MON)
    return prices, make_ohlcv(seed=99, drift=0.001, start=24000, volume=0, end=MON)


def _run(cfg, tmp_path, hh, mm):
    cfg.settings["report"]["archive_daily"] = False
    prices, bench = _universe_with_today_bar()
    return run_scan(cfg, prices=prices, benchmark=bench, as_of=MON, output_dir=tmp_path, now=at(MON, hh, mm),
                    fundamentals_fn=lambda syms: {s: GOOD_FUND for s in syms},
                    events_fn=lambda syms: {s: {"next_earnings": None, "ex_dividend": None} for s in syms})


def test_morning_run_uses_friday_close_and_reports_live_status(cfg, tmp_path):
    res = _run(cfg, tmp_path, 10, 30)
    assert res.session == "morning" and res.as_of == str(AS_OF)          # setups from Friday's completed candle
    brka = next(s for s in res.setups if s.symbol == "BRKA")
    assert brka.today_status.startswith("TRIGGERED")                      # Monday traded through the trigger
    assert "Morning run 10:30 IST" in res.session_label
    assert "Live status today" in (tmp_path / "swing_agent.md").read_text()


def test_afternoon_run_is_provisional_on_todays_candle(cfg, tmp_path):
    res = _run(cfg, tmp_path, 14, 45)
    assert res.session == "afternoon" and res.as_of == str(MON)           # today's forming candle is used
    assert "PROVISIONAL" in res.session_label
    assert any("volume projected" in s for s in res.data_sources)
    assert all(not s.today_status for s in res.setups)
