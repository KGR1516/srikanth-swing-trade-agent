"""Machine-readable JSON report."""
from __future__ import annotations

import json
import math
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.models import RunResult


def _default(o: Any):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if math.isnan(o) else float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, (date, datetime, pd.Timestamp)):
        return o.isoformat()
    if is_dataclass(o):
        return asdict(o)
    return str(o)


def _clean(obj: Any) -> Any:
    """Replace NaN/inf floats with None so the output is strict JSON."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v) for v in obj]
    return obj


def to_payload(res: RunResult) -> dict:
    return _clean(json.loads(json.dumps({
        "as_of": res.as_of,
        "generated_at": res.generated_at,
        "capital": res.capital,
        "universe_size": res.universe_size,
        "data_sources": res.data_sources,
        "market_regime": asdict(res.regime),
        "setups": [s.to_dict() for s in res.setups],
        "watchlist": [
            {k: v for k, v in c.to_dict().items() if k != "evidence"} for c in res.watchlist
        ],
        "sectors": res.sectors.to_dict(orient="records") if res.sectors is not None else [],
        "full_scan": res.full_scan.to_dict(orient="records") if res.full_scan is not None else [],
        "data_quality": [asdict(q) for q in res.quality if q.status != "PASS"],
        "disclaimer": res.disclaimer,
    }, default=_default)))


def write_json(res: RunResult, path: Path) -> Path:
    path.write_text(json.dumps(to_payload(res), indent=2, ensure_ascii=False), encoding="utf-8")
    return path
