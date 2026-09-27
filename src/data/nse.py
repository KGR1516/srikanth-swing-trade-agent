"""NSE-specific helpers: universe constituents, symbol mapping and bhavcopy.

NSE blocks many cloud IPs and requires browser-like headers, so every
network call here is best-effort and falls back gracefully.
"""
from __future__ import annotations

import io
import logging
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/csv,application/octet-stream,*/*",
    "Referer": "https://www.nseindia.com/",
}

INDEX_CSV = {  # primary + mirror for each index list
    name: [f"https://archives.nseindia.com/content/indices/ind_{name}list.csv",
           f"https://nsearchives.nseindia.com/content/indices/ind_{name}list.csv",
           f"https://www.niftyindices.com/IndexConstituent/ind_{name}list.csv"]
    for name in ("nifty50", "nifty100", "nifty200", "nifty500")
}
UNIVERSE_LABELS = {"static": "Custom list", "watchlist": "Watchlist", "nifty50": "Nifty 50",
                   "nifty100": "Nifty 100", "nifty200": "Nifty 200", "nifty500": "Nifty 500",
                   "full_nse": "Full NSE"}

EQUITY_LIST_URLS = [
    "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
    "https://archives.nseindia.com/content/equities/EQUITY_L.csv",
]

# UDiFF common bhavcopy (NSE format since July 2024)
BHAVCOPY_URL = (
    "https://nsearchives.nseindia.com/content/cm/"
    "BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
)


def to_yahoo(symbol: str, suffix: str = ".NS") -> str:
    return symbol if symbol.startswith("^") or symbol.endswith(suffix) else f"{symbol}{suffix}"


def from_yahoo(ticker: str, suffix: str = ".NS") -> str:
    return ticker[: -len(suffix)] if ticker.endswith(suffix) else ticker


def _get(url: str, timeout: int = 20) -> bytes | None:
    try:
        with requests.Session() as s:
            s.headers.update(HEADERS)
            r = s.get(url, timeout=timeout)
            if r.status_code == 200 and r.content:
                return r.content
            log.warning("NSE fetch %s → HTTP %s", url, r.status_code)
    except requests.RequestException as exc:
        log.warning("NSE fetch failed for %s: %s", url, exc)
    return None


def load_watchlist(path: Path | None) -> list[str]:
    """One NSE symbol per line; '#' lines ignored (stock-2 data/input/watchlist.txt)."""
    if not path or not Path(path).exists():
        return []
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        sym = line.split("#", 1)[0].strip().upper()
        if sym:
            out.append(sym)
    return list(dict.fromkeys(out))


def _read_csv(raw: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(raw))
    df.columns = [c.strip() for c in df.columns]
    return df


def _index_list(source: str) -> dict[str, str] | None:
    raw = next((r for r in (_get(u) for u in INDEX_CSV[source]) if r), None)
    if raw is None:
        return None
    df = _read_csv(raw)
    if "Series" in df:
        df = df[df["Series"].astype(str).str.strip() == "EQ"]
    return dict(zip(df["Symbol"].astype(str).str.strip(), df["Industry"].astype(str).str.strip()))


def _full_nse(sector_hint: dict[str, str]) -> dict[str, str] | None:
    """Every EQ-series stock listed on NSE (EQUITY_L.csv), as in stock-2's FULL_NSE mode."""
    for url in EQUITY_LIST_URLS:
        raw = _get(url)
        if raw is None or len(raw) < 5000:
            continue
        df = _read_csv(raw)
        if "SERIES" in df:
            df = df[df["SERIES"].astype(str).str.strip() == "EQ"]
        syms = df["SYMBOL"].dropna().astype(str).str.strip()
        return {s: sector_hint.get(s, "Unclassified") for s in syms}
    return None


def static_universe(universe_cfg: dict) -> dict[str, str]:
    """{symbol: sector} from the symbols listed in universe.yaml (no network)."""
    raw_symbols = universe_cfg.get("symbols") or {}
    if isinstance(raw_symbols, list):
        return {str(s).strip().upper(): "Unclassified" for s in raw_symbols if str(s).strip()}
    return {str(k).strip().upper(): str(v) for k, v in raw_symbols.items()}


def resolve_universe(universe_cfg: dict, root: Path | None = None) -> tuple[dict[str, str], str]:
    """Return ({symbol: sector}, label actually used).

    Priority: watchlist file (if it has symbols) → live NSE list (index or full_nse)
    → for full_nse, Nifty 500 → static list in universe.yaml.
    `symbols` may be a {SYMBOL: sector} map or a list.
    """
    static = static_universe(universe_cfg)

    wl_path = universe_cfg.get("watchlist_file")
    if wl_path:
        wl_path = Path(wl_path) if Path(wl_path).is_absolute() or root is None else root / wl_path
        wl = load_watchlist(wl_path)
        if wl:
            log.info("Universe: %d symbols from watchlist %s (overrides config)", len(wl), wl_path)
            return {s: static.get(s, "Unclassified") for s in wl}, "Watchlist"

    source = str(universe_cfg.get("source", "static")).lower()
    if source == "static":
        return static, UNIVERSE_LABELS["static"]

    out, label = None, UNIVERSE_LABELS.get(source, source)
    if source == "full_nse":
        n500 = _index_list("nifty500") or {}
        hint = {**static, **n500}
        out = _full_nse(hint)
        if not out and n500:
            log.warning("Full NSE list unavailable — falling back to Nifty 500")
            out, label = n500, "Nifty 500 (fallback from Full NSE)"
    elif source in INDEX_CSV:
        out = _index_list(source)
    else:
        log.warning("Unknown universe source '%s'; using static list", source)

    if not out:
        log.warning("NSE %s list unavailable — falling back to static universe (%d symbols)", source, len(static))
        return static, f"{UNIVERSE_LABELS['static']} (fallback from {UNIVERSE_LABELS.get(source, source)})"
    log.info("Universe: %d symbols from %s", len(out), label)
    return out, label


def get_universe(universe_cfg: dict, root: Path | None = None) -> dict[str, str]:
    return resolve_universe(universe_cfg, root)[0]


def fetch_bhavcopy(trade_date: date | None = None, max_back_days: int = 5) -> pd.DataFrame | None:
    """Download the latest available NSE cash-market bhavcopy.

    Returns DataFrame indexed by symbol with columns Open/High/Low/Close/Volume
    and a `date` attribute, or None if NSE is unreachable.
    """
    d = trade_date or date.today()
    for _ in range(max_back_days + 1):
        if d.weekday() < 5:
            raw = _get(BHAVCOPY_URL.format(d=d))
            if raw:
                try:
                    df = pd.read_csv(io.BytesIO(raw), compression="zip")
                    df = df[df["SctySrs"] == "EQ"]
                    out = pd.DataFrame(
                        {
                            "Open": df["OpnPric"].values,
                            "High": df["HghPric"].values,
                            "Low": df["LwPric"].values,
                            "Close": df["ClsPric"].values,
                            "Volume": df["TtlTradgVol"].values,
                        },
                        index=df["TckrSymb"].str.strip().values,
                    )
                    out.attrs["date"] = d
                    log.info("NSE bhavcopy loaded for %s (%d rows)", d, len(out))
                    return out
                except Exception as exc:  # format change etc.
                    log.warning("Could not parse bhavcopy for %s: %s", d, exc)
                    return None
        d -= timedelta(days=1)
    return None
