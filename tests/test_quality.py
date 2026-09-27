from datetime import timedelta

from src.data.quality import clean_history, validate_history
from tests.conftest import AS_OF, make_ohlcv


def test_clean_data_passes(uptrend, cfg):
    assert validate_history("X", uptrend, cfg.settings, AS_OF).status == "PASS"


def test_stale_data_fails(uptrend, cfg):
    q = validate_history("X", uptrend, cfg.settings, AS_OF + timedelta(days=10))
    assert q.status == "FAIL" and any("stale" in i for i in q.issues)


def test_short_history_fails(cfg):
    q = validate_history("X", make_ohlcv(n=100), cfg.settings, AS_OF)
    assert any("insufficient_history" in i for i in q.issues)


def test_unadjusted_split_detected(uptrend, cfg):
    df = uptrend.copy()
    df.iloc[-50:, :4] = df.iloc[-50:, :4] / 2       # 1:2 split not adjusted
    q = validate_history("X", df, cfg.settings, AS_OF)
    assert q.status == "FAIL" and any("extreme_move" in i for i in q.issues)


def test_nse_crosscheck_mismatch(uptrend, cfg):
    ref = float(uptrend["Close"].iloc[-1]) * 1.05
    q = validate_history("X", uptrend, cfg.settings, AS_OF, reference_close=ref, tolerance_pct=1.5)
    assert any("nse_close_mismatch" in i for i in q.issues)


def test_clean_history_dedupes(uptrend):
    df = clean_history(__import__("pandas").concat([uptrend, uptrend.tail(1)]))
    assert df.index.is_unique


def test_holiday_placeholder_bar_removed(uptrend, cfg):
    """Yahoo inserts a flat, zero-volume bar for NSE holidays — it must not reach the indicators."""
    df = uptrend.copy()
    i = df.index[-10]
    prev_close = df["Close"].iloc[-11]
    df.loc[i, ["Open", "High", "Low", "Close", "Volume"]] = [prev_close, prev_close, prev_close, prev_close, 0]
    assert validate_history("X", df, cfg.settings, AS_OF).status == "WARN"      # before: zero-volume warning
    cleaned = clean_history(df)
    assert i not in cleaned.index and len(cleaned) == len(df) - 1
    assert validate_history("X", cleaned, cfg.settings, AS_OF).status == "PASS"


def test_holiday_row_removed_from_index_data():
    from src.data.quality import drop_holiday_rows
    idx = make_ohlcv(volume=0)                                                   # indices report 0 volume
    i = idx.index[-5]
    c = idx["Close"].iloc[-6]
    idx.loc[i, ["Open", "High", "Low", "Close"]] = [c, c, c, c]
    out = drop_holiday_rows(idx)
    assert i not in out.index and len(out) == len(idx) - 1
