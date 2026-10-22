"""The user's own click reaction times, as something the trigger can draw from.

The Humanizer's Click reaction part records how long the user takes to click a
circle that turns green. Every kept time from every saved run is pooled here.
A draw picks one of those recorded times at random and blurs it by a small
kernel width, so the trigger's first-shot delay follows the *shape* of the
user's reactions (their spread, their slow ones) instead of sitting on one
average, and never repeats the exact same number.

Nothing here touches the aim loop's timing budget: `sample()` is a list index
and one Gaussian, and the pool is rebuilt only when a run is saved or deleted.
"""

from __future__ import annotations

import random
import threading
from typing import Any, Optional

import humanizer

# Fewer recorded reactions than this say nothing about a distribution.
MIN_SAMPLES = 8
FLOOR_MS = 80.0

_lock = threading.Lock()
_pool: tuple[float, ...] = ()
_bandwidth = 0.0
_runs = 0
_summary: dict[str, Any] = {"n": 0, "have": 0, "need": MIN_SAMPLES, "runs": 0}
_loaded = False


def _quantile(sorted_values: list[float], q: float) -> float:
    pos = (len(sorted_values) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def reload() -> None:
    """Rebuild the pool from the saved runs. Call after a run is saved or deleted."""
    global _pool, _bandwidth, _runs, _summary, _loaded
    values, runs = humanizer.reaction_pool()
    values = sorted(v for v in values if v >= FLOOR_MS)
    n = len(values)
    bandwidth = 0.0
    summary: dict[str, Any] = {"n": 0, "have": n, "need": MIN_SAMPLES, "runs": runs}
    if n >= MIN_SAMPLES:
        mean = sum(values) / n
        std = (sum((v - mean) ** 2 for v in values) / n) ** 0.5
        iqr = _quantile(values, 0.75) - _quantile(values, 0.25)
        spread = min(std, iqr / 1.34) if iqr > 0 else std
        # Silverman's rule of thumb, with a floor so identical values still blur.
        bandwidth = max(2.0, 0.9 * spread * n ** -0.2)
        summary.update(
            n=n,
            min=values[0],
            max=values[-1],
            median=_quantile(values, 0.5),
        )
    with _lock:
        _pool = tuple(values) if n >= MIN_SAMPLES else ()
        _bandwidth = bandwidth
        _runs = runs
        _summary = summary
        _loaded = True


def summary() -> dict[str, Any]:
    """What the UI shows: {n, have, need, runs, min, max, median}. n is 0 until usable."""
    if not _loaded:
        reload()
    return dict(_summary)


def usable() -> bool:
    if not _loaded:
        reload()
    return bool(_pool)


def sample(rng: Optional[random.Random] = None) -> Optional[float]:
    """One reaction time in ms drawn from the user's own, or None if there are too few."""
    if not _loaded:
        reload()
    pool = _pool
    if not pool:
        return None
    r = rng or random
    value = pool[r.randrange(len(pool))] + r.gauss(0.0, _bandwidth)
    return min(max(value, max(FLOOR_MS, pool[0] - _bandwidth)), pool[-1] + _bandwidth)
