"""Multi-core map for the CPU-heavy steps (indicators, confluence).

GitHub's standard Linux runners have 4 cores; indicators for ~1,000 stocks drop from
~30 s to ~8 s. Small jobs run in-process (process start-up would cost more than it saves).
"""
from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from typing import Callable, Iterable, TypeVar

T = TypeVar("T")
R = TypeVar("R")


def worker_count(requested: int | None = 0) -> int:
    return requested if requested and requested > 0 else (os.cpu_count() or 1)


def pmap(fn: Callable[[T], R], items: Iterable[T], workers: int | None = 0, min_items: int = 40,
         chunksize: int = 8) -> list[R]:
    items = list(items)
    n = worker_count(workers)
    if n <= 1 or len(items) < min_items:
        return [fn(x) for x in items]
    with ProcessPoolExecutor(max_workers=n) as ex:
        return list(ex.map(fn, items, chunksize=chunksize))
