"""Multi-indicator confluence — ported from KGR1516/srikanth-stock-2 (src/utils/indicators.py).

Computes the full pandas-ta-classic catalogue (~220 indicators) on a symbol's OHLCV and
reduces it to one directional consensus for the latest bar. Each indicator with an
unambiguous bullish/bearish reading casts one vote; votes are pooled per category
(trend, momentum, overlap, volume, candles) and categories are averaged equally, so the
~46 moving averages can't drown out everything else. Magnitude-only indicators (ADX,
CHOP, volatility, statistics...) are computed but never vote.

Optional: if pandas-ta-classic isn't installed, `available()` is False and the pipeline
drops the confluence weight instead of scoring stocks down for a missing library.
Cost is ~0.5–1 s CPU per symbol, so it only runs on the shortlist.
"""
from __future__ import annotations

import logging
import warnings

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

try:  # optional dependency
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import pandas_ta_classic as _pta  # noqa: F401  (registers the DataFrame.ta accessor)
except Exception:  # pragma: no cover - depends on host
    _pta = None


def available() -> bool:
    return _pta is not None


# Same default exclusions pandas-ta-classic's own strategy("all") applies:
# these need extra non-OHLCV inputs and can't run standalone off a plain
# price/volume frame.
_DEFAULT_BULK_EXCLUDE = {
    "above", "above_value", "below", "below_value", "cross", "cross_value",
    "long_run", "short_run", "td_seq", "tsignals", "vp", "xsignals",
}


# ------------------------------------------------- Multi-indicator confluence
# How each indicator family is read as bullish / bearish. Only indicators with
# an unambiguous directional meaning vote; the rest are still computed and
# available as columns, they just abstain -- a wrong vote is worse than no vote.

# Magnitude, not direction: these must never vote. ADX/ADXR/DX say how strong a
# trend is, not which way it points; CHOP and VHF measure choppiness; the CPR
# level columns are support/resistance geometry; KVOs is a signal line.
_VOTE_NEVER = (
    "ADX", "DX_", "CHOP", "VHF", "PSARAF", "PSARR", "SAREXT", "PMAX", "QS_",
    "LDECAY", "EDECAY", "MARKETFI", "PVOL", "PVR", "VOSC", "WAD", "KVOS",
    "VFI", "CPR_TC", "CPR_BC", "CPR_R", "CPR_S", "CPR_WIDTH",
)
_VOTE_GT0 = (            # bullish when > 0
    "MACD", "MOM_", "ROC_", "TRIX", "PPO", "APO_", "AO_", "BOP", "CCI_",
    "CMO_", "BIAS_", "CFO_", "FISHERT", "KST", "PGO_", "RVGI", "SLOPE",
    "SMI_", "TSI_", "COPC", "DPO_", "CTI_", "CMF_", "EFI_", "KVO_", "ADOSC",
    "EOM_", "EMV", "TTM_TRND", "AROONOSC", "INC_",
)
_VOTE_GT50 = (           # bullish when > 50 (0-100 scales on a bull/bear axis)
    "RSI_", "RSX_", "STOCHK", "STOCHD", "STOCHRSIK", "STOCHRSID", "MFI_",
    "INERTIA", "PSL_", "STC_", "UO_", "CRSI", "QQE_",
)
_VOTE_GT_NEG50 = ("WILLR",)          # bullish when > -50
_VOTE_BEARISH_GT0 = ("DEC_", "QQES") # bullish when <= 0
# "Long run" / "short run" regime flags: 1 means that regime is active.
_VOTE_LONG_RUN = ("AMATE_LR", "AOBV_LR", "QQEL")
_VOTE_SHORT_RUN = ("AMATE_SR", "AOBV_SR")
# Cumulative accumulation lines: rising over the last week is accumulation.
# Matched on the exact column name or an explicit prefix -- a bare "AD" prefix
# would also swallow ADX and ADXR, which must not vote at all.
_VOTE_RISING_EXACT = {"AD", "OBV", "PVT"}
_VOTE_RISING_PREFIX = ("OBV_", "OBVE_", "PVI_", "NVI_")
# Paired indicators: bullish when the first line is above the second.
_VOTE_PAIRS = (
    ("DMP_", "DMN_"),          # +DI vs -DI
    ("PLUS_DM", "MINUS_DM"),   # raw directional movement
    ("AROONU_", "AROOND_"),    # Aroon up vs down
    ("VTXP_", "VTXM_"),        # Vortex + vs -
    ("CKSPL_", "CKSPS_"),      # Chande-Kroll long vs short stop
)
# Paired stops where only one leg is live at a time: whichever is non-NaN wins.
_VOTE_ACTIVE_PAIRS = (("PSARL_", "PSARS_"),)
# Categories whose output is magnitude/shape, not direction -- computed, never voted.
_NON_DIRECTIONAL_CATS = {"volatility", "statistics", "cycles", "performance"}


def _vote_column(name: str, series: pd.Series, close_last: float, category: str):
    """Read one indicator column as True (bullish), False (bearish) or None."""
    up = name.upper()
    if any(up.startswith(p) for p in _VOTE_NEVER):
        return None

    s = series.dropna()
    if s.empty:
        return None
    val = float(s.iloc[-1])
    if not np.isfinite(val):
        return None

    if up.startswith("CDL_"):                 # candlestick pattern: sign = direction
        return None if val == 0 else bool(val > 0)
    if up.startswith("SUPERTD"):              # supertrend direction: 1 / -1
        return bool(val > 0)
    if any(up.startswith(p) for p in _VOTE_LONG_RUN):
        return bool(val > 0)
    if any(up.startswith(p) for p in _VOTE_SHORT_RUN):
        return bool(val <= 0)
    if any(up.startswith(p) for p in _VOTE_GT_NEG50):
        return bool(val > -50)
    if any(up.startswith(p) for p in _VOTE_GT50):
        return bool(val > 50)
    if any(up.startswith(p) for p in _VOTE_BEARISH_GT0):
        return bool(val <= 0)
    if any(up.startswith(p) for p in _VOTE_GT0):
        return bool(val > 0)
    if up in _VOTE_RISING_EXACT or any(up.startswith(p) for p in _VOTE_RISING_PREFIX):
        return bool(s.iloc[-1] > s.iloc[-6]) if len(s) > 6 else None
    if up == "CPR_PIVOT":                     # trading above the pivot is bullish
        return bool(close_last > val)
    if category == "overlap":
        # Price-level moving averages: trading above the line is bullish.
        # The range guard skips overlap outputs that aren't price levels.
        if 0.2 * close_last <= val <= 5 * close_last:
            return bool(close_last > val)
    return None


def _pair_votes(enriched: pd.DataFrame, cols: list):
    """Directional votes that need two columns compared against each other.

    Returns (votes, consumed) so the single-column pass can skip these columns
    instead of double-counting them -- or, in PSAR's case, reading them wrongly:
    only one of its two stop lines is live at a time, so voting on each
    separately always produced one bull and one bear that cancelled out.
    """
    votes: list[bool] = []
    consumed: set = set()
    by_upper = {c.upper(): c for c in cols}

    def _find(prefix):
        return next((by_upper[u] for u in sorted(by_upper) if u.startswith(prefix)), None)

    for bull_prefix, bear_prefix in _VOTE_PAIRS:
        bull_col, bear_col = _find(bull_prefix), _find(bear_prefix)
        if not (bull_col and bear_col):
            continue
        a, b = enriched[bull_col].dropna(), enriched[bear_col].dropna()
        consumed.update((bull_col, bear_col))
        if len(a) and len(b):
            votes.append(bool(float(a.iloc[-1]) > float(b.iloc[-1])))

    for bull_prefix, bear_prefix in _VOTE_ACTIVE_PAIRS:
        bull_col, bear_col = _find(bull_prefix), _find(bear_prefix)
        if not (bull_col and bear_col):
            continue
        consumed.update((bull_col, bear_col))
        bull_live = bool(pd.notna(enriched[bull_col].iloc[-1]))
        bear_live = bool(pd.notna(enriched[bear_col].iloc[-1]))
        if bull_live != bear_live:
            votes.append(bull_live)

    return votes, consumed


def _compute_by_category(df: pd.DataFrame, exclude: set | None = None):
    """Run every indicator, tracking which category each new column came from.

    Returns (enriched_df, {category: [column names]}).
    """
    skip = _DEFAULT_BULK_EXCLUDE | set(exclude or [])
    out = df.copy()
    mapping: dict[str, list[str]] = {}
    for category, names in _pta.Category.items():
        cols: list[str] = []
        for name in names:
            if name in skip:
                continue
            before = set(out.columns)
            try:
                getattr(out.ta, name)(append=True)
            except Exception as exc:
                log.debug("confluence: skipped '%s' (%s)", name, exc)
                continue
            cols.extend([c for c in out.columns if c not in before])
        if cols:
            mapping[category] = cols
    return out, mapping


def confluence_signals(df: pd.DataFrame, min_votes: int = 3) -> dict:
    """Compute every pandas-ta-classic indicator and reduce them to a single
    directional consensus for the latest bar.

    Rather than treating the 224-indicator catalogue as inert extra columns,
    this reads each indicator that has an unambiguous bullish/bearish meaning
    as one vote, then aggregates.

    Votes are pooled *within* each category first and the categories are then
    averaged equally. That matters: pandas-ta-classic ships ~46 overlap
    (moving-average) indicators but only ~20 volume ones, so a naive count
    across all columns would quietly turn into "whatever the moving averages
    think". Equal-weighting the categories keeps trend, momentum, volume and
    price-vs-average as four independent opinions.

    Args:
        df: OHLCV DataFrame. Column names may be upper or lower case.
        min_votes: a category needs at least this many usable votes to count.

    Returns:
        dict with confluence_score (0-100, higher = more bullish agreement),
        the per-category bull percentages, and how many indicator columns were
        computed. Values are None when there isn't enough data to judge.
    """
    if _pta is None:
        raise RuntimeError(
            "confluence_signals() requires pandas-ta-classic. "
            "Install it with `pip install pandas-ta-classic` (see requirements.txt)."
        )

    frame = df.rename(columns=str.lower)
    needed = {"open", "high", "low", "close", "volume"}
    missing = needed - set(frame.columns)
    if missing:
        raise ValueError(f"confluence_signals() needs OHLCV columns, missing: {sorted(missing)}")
    frame = frame[["open", "high", "low", "close", "volume"]]

    with warnings.catch_warnings(), np.errstate(all="ignore"):
        warnings.simplefilter("ignore")
        enriched, mapping = _compute_by_category(frame)
    close_last = float(frame["close"].iloc[-1])

    per_category: dict[str, float] = {}
    for category, cols in mapping.items():
        if category in _NON_DIRECTIONAL_CATS:
            continue
        pair_results, consumed = _pair_votes(enriched, cols)
        bulls = sum(1 for v in pair_results if v)
        bears = sum(1 for v in pair_results if not v)
        for col in cols:
            if col in consumed:
                continue
            verdict = _vote_column(col, enriched[col], close_last, category)
            if verdict is True:
                bulls += 1
            elif verdict is False:
                bears += 1
        if bulls + bears >= min_votes:
            per_category[category] = round(bulls / (bulls + bears) * 100, 1)

    total_cols = sum(len(v) for v in mapping.values())
    score = round(float(np.mean(list(per_category.values()))), 1) if per_category else None
    log.debug("confluence: %d columns, score=%s, per-category=%s", total_cols, score, per_category)

    return {
        "confluence_score": score,
        "conf_trend": per_category.get("trend"),
        "conf_momentum": per_category.get("momentum"),
        "conf_overlap": per_category.get("overlap"),
        "conf_volume": per_category.get("volume"),
        "indicators_computed": total_cols,
    }
