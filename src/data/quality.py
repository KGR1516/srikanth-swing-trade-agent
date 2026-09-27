"""③ Data quality gate.

Bad data must never look like a signal. Every symbol gets PASS / WARN / FAIL;
only PASS and WARN continue to the scanner.
"""
from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd

from src.models import QualityReport


def drop_holiday_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Remove placeholder bars Yahoo inserts for NSE holidays (no range, no volume).

    Left in, a flat zero-volume bar shrinks ATR (tighter stops than the stock deserves) and
    drags the volume average down (breakout volume looks bigger than it is).
    Index data (volume always 0) is judged on the flat range alone.
    """
    if df is None or df.empty:
        return df
    flat = (df["High"] == df["Low"]) & (df["Close"] == df["Close"].shift(1))
    if "Volume" in df and (df["Volume"] > 0).any():
        flat &= df["Volume"].fillna(0) == 0
    return df[~flat]


def clean_history(df: pd.DataFrame) -> pd.DataFrame:
    """Drop duplicate dates, rows without a close, holiday placeholder bars, and sort."""
    out = df[~df.index.duplicated(keep="last")].sort_index()
    out = out.dropna(subset=["Close"])
    out[["Open", "High", "Low"]] = out[["Open", "High", "Low"]].fillna(
        pd.DataFrame({c: out["Close"] for c in ["Open", "High", "Low"]})
    )
    out["Volume"] = out["Volume"].fillna(0)
    return drop_holiday_rows(out)


def validate_history(
    symbol: str,
    df: pd.DataFrame,
    cfg: dict,
    as_of: date | None = None,
    reference_close: float | None = None,
    tolerance_pct: float = 1.5,
) -> QualityReport:
    """Run all checks. `reference_close` = official NSE close for cross-check."""
    dq = cfg["data_quality"]
    as_of = as_of or date.today()
    fails: list[str] = []
    warns: list[str] = []

    if df is None or df.empty:
        return QualityReport(symbol, "FAIL", ["no_data"])

    bars = len(df)
    last_date = pd.Timestamp(df.index[-1]).date()

    if bars < dq["min_history_bars"]:
        fails.append(f"insufficient_history:{bars}<{dq['min_history_bars']}")

    age = (as_of - last_date).days
    if age > dq["max_staleness_days"]:
        fails.append(f"stale_data:last_bar_{age}d_old")

    last = df.iloc[-1]
    if last[["Open", "High", "Low", "Close"]].isna().any():
        fails.append("nan_in_last_bar")
    if (df["Volume"] < 0).any():
        fails.append("negative_volume")
    if (df["High"] < df["Low"]).any():
        fails.append("high_below_low")
    bad_close = (df["Close"] > df["High"] * 1.001) | (df["Close"] < df["Low"] * 0.999)
    if bad_close.tail(60).any():
        fails.append(f"close_outside_range:{int(bad_close.tail(60).sum())}_bars")
    if (df["Close"] <= 0).any():
        fails.append("non_positive_price")

    moves = df["Close"].pct_change().abs().tail(252) * 100
    if (moves > dq["max_daily_move_pct"]).any():
        big = moves[moves > dq["max_daily_move_pct"]]
        fails.append(
            f"extreme_move:{big.max():.1f}%_on_{pd.Timestamp(big.idxmax()).date()}"
            " (unadjusted split/bonus?)"
        )

    zero_vol = int((df["Volume"].tail(20) == 0).sum())
    if zero_vol > dq["max_zero_volume_days"]:
        fails.append(f"zero_volume_days:{zero_vol}/20")
    elif zero_vol > 0:
        warns.append(f"zero_volume_days:{zero_vol}/20")

    if reference_close is not None and np.isfinite(reference_close) and reference_close > 0:
        diff = abs(float(last["Close"]) / reference_close - 1) * 100
        if diff > tolerance_pct:
            fails.append(f"nse_close_mismatch:{diff:.2f}%")

    status = "FAIL" if fails else ("WARN" if warns else "PASS")
    return QualityReport(symbol, status, fails + warns, str(last_date), bars)


MARKET_OPEN = (9, 15)
EOD_READY = (16, 0)      # Yahoo daily bar is reliable ~30 min after the 15:30 close


def is_session_incomplete(now_ist: datetime) -> bool:
    """True between the open and EOD-ready time on a weekday (IST)."""
    if now_ist.weekday() >= 5:
        return False
    hm = (now_ist.hour, now_ist.minute)
    return MARKET_OPEN <= hm < EOD_READY


def drop_incomplete_bar(df: pd.DataFrame, now_ist: datetime) -> tuple[pd.DataFrame, bool]:
    """Remove today's still-forming candle when run during market hours.

    Stock-2 scanned pre-market / mid-session / post-close; this agent works on completed
    daily candles only, so an intraday run uses the last *completed* session.
    """
    if df is None or df.empty or not is_session_incomplete(now_ist):
        return df, False
    if pd.Timestamp(df.index[-1]).date() == now_ist.date():
        return df.iloc[:-1], True
    return df, False


SESSION_MINUTES = 375    # 09:15 → 15:30


def session_fraction(now_ist: datetime) -> float:
    """Share of today's trading session that has elapsed (0.05–1.0)."""
    mins = (now_ist.hour * 60 + now_ist.minute) - (MARKET_OPEN[0] * 60 + MARKET_OPEN[1])
    return max(0.05, min(1.0, mins / SESSION_MINUTES))


def resolve_session(requested: str | None, now_ist: datetime, afternoon_from: tuple[int, int] = (13, 0)) -> str:
    """morning | afternoon | eod.

    morning   → setups from the last completed candle + live status of each setup today
    afternoon → provisional scan on today's still-forming candle (volume projected to a full day)
    eod       → final scan on completed candles (post-close / weekends)
    """
    if requested and requested != "auto":
        return requested
    if is_session_incomplete(now_ist):
        return "morning" if (now_ist.hour, now_ist.minute) < afternoon_from else "afternoon"
    return "eod"


def project_partial_volume(df: pd.DataFrame, now_ist: datetime) -> tuple[pd.DataFrame, bool]:
    """Scale today's partial volume up to a full-day estimate (linear in elapsed session time)."""
    if df is None or df.empty or not is_session_incomplete(now_ist):
        return df, False
    if pd.Timestamp(df.index[-1]).date() != now_ist.date():
        return df, False
    out = df.copy()
    out.iloc[-1, out.columns.get_loc("Volume")] = float(out["Volume"].iloc[-1]) / session_fraction(now_ist)
    return out, True
