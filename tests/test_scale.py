"""Scale / efficiency features: retries, prefetch cache + refresh, pre-filter, multi-core, universe fallback."""
from datetime import date, datetime

import pandas as pd
import pytest

from src.agent import orchestrator
from src.data import nse
from src.data.market_data import download, fetch_ohlcv, merge_recent, refresh_recent
from src.parallel import pmap
from src.scanner.liquidity import quick_liquid
from tests.conftest import AS_OF, add_breakout, make_ohlcv
from tests.test_pipeline import GOOD_FUND

MON = date(2026, 9, 28)


def _as_download(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    return pd.concat(frames, axis=1) if frames else pd.DataFrame()


@pytest.fixture
def no_sleep(monkeypatch):
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)


def test_download_retries_throttled_batches(monkeypatch, no_sleep):
    import yfinance
    calls = {"n": 0}

    def fake(batch, **kw):
        calls["n"] += 1
        ok = [t for t in batch if calls["n"] > 1 or not t.startswith("B")]   # first pass: "B*" throttled
        return _as_download({t: make_ohlcv(n=30) for t in ok})

    monkeypatch.setattr(yfinance, "download", fake)
    out = download(["A1", "A2", "B1", "B2"], batch_size=4, retries=2, start="2025-01-01")
    assert set(out) == {"A1", "A2", "B1", "B2"} and calls["n"] == 2


def test_fetch_uses_cache_and_reports_it(tmp_path, monkeypatch, no_sleep):
    import yfinance
    monkeypatch.setattr(yfinance, "download", lambda batch, **kw: _as_download({t: make_ohlcv(n=30) for t in batch}))
    first, cached1 = fetch_ohlcv(["X.NS", "Y.NS"], 60, tmp_path)
    assert cached1 == set() and len(list(tmp_path.glob("*.csv"))) == 2
    monkeypatch.setattr(yfinance, "download", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network")))
    second, cached2 = fetch_ohlcv(["X.NS", "Y.NS"], 60, tmp_path)
    assert cached2 == {"X.NS", "Y.NS"} and len(second["X.NS"]) == 30


def test_merge_and_refresh_recent(monkeypatch, no_sleep):
    base = make_ohlcv(n=50, end=AS_OF)
    recent = make_ohlcv(n=3, end=MON, seed=5) * 1.1           # overlaps last 2 bars + adds Monday
    merged = merge_recent(base, recent)
    assert merged.index[-1].date() == MON and merged.index.is_unique
    assert merged.loc[recent.index[0], "Close"] == recent["Close"].iloc[0]

    import yfinance
    seen = {}
    def fake(batch, **kw):
        seen.update(kw)
        return _as_download({t: recent for t in batch})
    monkeypatch.setattr(yfinance, "download", fake)
    prices = {"ABC": base}
    assert refresh_recent(prices, ["ABC"], days=5) == 1
    assert seen.get("period") == "5d" and prices["ABC"].index[-1].date() == MON


def test_quick_liquid_prefilter(cfg):
    assert quick_liquid(make_ohlcv(volume=2_000_000), cfg.settings)          # ~₹20 Cr/day
    assert not quick_liquid(make_ohlcv(volume=10_000), cfg.settings)         # ~₹0.1 Cr/day


def _square(x):
    return x * x


def test_pmap_parallel_matches_serial():
    items = list(range(50))
    assert pmap(_square, items, workers=2, min_items=1) == [x * x for x in items]
    assert pmap(_square, items[:3], workers=4) == [0, 1, 4]                # small job stays in-process


def test_full_nse_falls_back_to_nifty500_then_static(monkeypatch):
    n500_csv = b"Company Name,Industry,Symbol,Series,ISIN Code\nA Ltd,IT,AAA,EQ,x\nB Ltd,Power,BBB,EQ,y\n"
    monkeypatch.setattr(nse, "_get", lambda url, timeout=20: n500_csv if "nifty500" in url else None)
    uni, label = nse.resolve_universe({"source": "full_nse", "symbols": ["ZZZ"]})
    assert uni == {"AAA": "IT", "BBB": "Power"} and "fallback" in label

    monkeypatch.setattr(nse, "_get", lambda url, timeout=20: None)
    uni, label = nse.resolve_universe({"source": "full_nse", "symbols": ["ZZZ"]})
    assert uni == {"ZZZ": "Unclassified"} and "fallback" in label

    monkeypatch.setattr(nse, "_get", lambda url, timeout=20: None if "archives" in url else n500_csv)
    uni, label = nse.resolve_universe({"source": "nifty500"})            # mirror URL used
    assert label == "Nifty 500" and set(uni) == {"AAA", "BBB"}


def _big_universe(n=60, end=AS_OF):
    prices = {f"S{i:02d}": make_ohlcv(seed=100 + i, drift=0.0015, end=end) for i in range(n)}
    prices["BRKA"] = add_breakout(make_ohlcv(seed=11, drift=0.002, end=end))
    prices["THIN"] = make_ohlcv(seed=16, drift=0.002, volume=10_000, end=end)
    return prices


def test_pipeline_prefilters_and_runs_multicore(cfg, tmp_path):
    cfg.settings["report"]["archive_daily"] = True
    cfg.settings["performance"]["workers"] = 2
    prices = _big_universe()
    res = orchestrator.run_scan(
        cfg, prices=prices, benchmark=make_ohlcv(seed=99, drift=0.001, start=24000, volume=0),
        as_of=AS_OF, output_dir=tmp_path, now=datetime(AS_OF.year, AS_OF.month, AS_OF.day, 17, 0),
        fundamentals_fn=lambda syms: {s: GOOD_FUND for s in syms},
        events_fn=lambda syms: {s: {"next_earnings": None, "ex_dividend": None} for s in syms})
    assert "THIN" not in set(res.full_scan["symbol"])                     # skipped before indicators
    assert any("illiquid stocks skipped" in s for s in res.data_sources)
    assert "BRKA" in {s.symbol for s in res.setups}
    hist = list((tmp_path / "history" / str(AS_OF)).iterdir())
    assert {p.suffix for p in hist} == {".md", ".xlsx"}                    # JSON not archived


def test_prefetched_afternoon_run_refreshes_only_liquid(cfg, tmp_path, monkeypatch):
    """Scan after a prefetch: history from cache, only liquid names + benchmark re-downloaded."""
    prices = _big_universe(n=10, end=MON)
    bench = make_ohlcv(seed=99, drift=0.001, start=24000, volume=0, end=MON)
    cfg.universe = {"source": "static", "symbols": list(prices), "watchlist_file": None}
    cfg.settings["data"]["nse_bhavcopy_crosscheck"] = False
    cfg.settings["report"]["archive_daily"] = False
    monkeypatch.setattr(orchestrator, "load_prices", lambda *a, **k: ({k2: v.copy() for k2, v in prices.items()},
                                                                       bench.copy(), len(prices)))
    refreshed = []
    monkeypatch.setattr(orchestrator, "refresh_recent", lambda box, syms, *a, **k: refreshed.append(list(syms)) or len(syms))
    res = orchestrator.run_scan(cfg, now=datetime(MON.year, MON.month, MON.day, 14, 45), as_of=MON,
                                output_dir=tmp_path,
                                fundamentals_fn=lambda syms: {s: GOOD_FUND for s in syms},
                                events_fn=lambda syms: {s: {"next_earnings": None, "ex_dividend": None} for s in syms})
    assert res.session == "afternoon"
    stocks = refreshed[0]
    assert "THIN" not in stocks and "BRKA" in stocks                        # illiquid never re-downloaded
    assert refreshed[1] == [cfg.settings["data"]["benchmark"]]
    assert any("live refresh" in s for s in res.data_sources)
