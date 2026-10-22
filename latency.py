"""Rolling pipeline latency measurements for the dashboard.

Each stage keeps its last WINDOW samples in a fixed ring, written by whichever
thread measures it (the vision thread for capture and inference, the aim loop
for the rest) and summarised by the UI thread. Every stage has exactly one
writer, so there is no lock on the hot path: an add is an index bump and a
list store.

Stages, in the order a frame travels:

    capture   waiting for / copying the next desktop frame (the vision thread)
    infer     everything after the frame arrives until the result is ready:
              preprocess, GPU inference, decode, marker filter, target pick
    age       how old that result already is when the aim loop picks it up
              (the wait for the next aim tick)
    compute   the aim loop working out this tick's movement and clicks
    send      handing that movement to SendInput / the Feather
    total     frame grabbed -> input sent, one sample per tick that sent
              something. This is what Argus itself adds; the game's own frame
              time, the display and the network sit on either side of it
    jitter    how late the aim loop woke up for its tick
"""

from __future__ import annotations

import time
from typing import Any

WINDOW = 1024
STAGES = ("capture", "infer", "age", "compute", "send", "total", "jitter")
# Stages that add up to `total` (capture is waiting for the game, not work).
CHAIN = ("infer", "age", "compute", "send")
HIST_BINS = 14


def _quantile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    pos = (len(sorted_values) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


class LatencyStats:
    def __init__(self, window: int = WINDOW) -> None:
        self._window = max(16, int(window))
        self.reset()

    def reset(self) -> None:
        """Forget everything. Called when capture starts; never while it runs."""
        self._buf = {stage: [0.0] * self._window for stage in STAGES}
        self._count = dict.fromkeys(STAGES, 0)
        self._last_at = dict.fromkeys(STAGES, 0.0)

    def add(self, stage: str, ms: float) -> None:
        n = self._count[stage]
        self._buf[stage][n % self._window] = ms
        self._count[stage] = n + 1
        self._last_at[stage] = time.perf_counter()

    def _values(self, stage: str) -> list[float]:
        n = self._count[stage]
        buf = self._buf[stage]
        return buf[:n] if n < self._window else list(buf)

    def snapshot(self) -> dict[str, Any]:
        now = time.perf_counter()
        stages: dict[str, Any] = {}
        totals: list[float] = []
        for stage in STAGES:
            values = sorted(self._values(stage))
            if stage == "total":
                totals = values
            n = len(values)
            last = self._last_at[stage]
            stages[stage] = {
                "n": n,
                "avg": (sum(values) / n) if n else None,
                "p50": _quantile(values, 0.5) if n else None,
                "p95": _quantile(values, 0.95) if n else None,
                "p99": _quantile(values, 0.99) if n else None,
                "max": values[-1] if n else None,
                "ago": (now - last) if last else None,
            }
        return {"stages": stages, "hist": _histogram(totals)}


def _histogram(sorted_values: list[float]) -> dict[str, Any]:
    """Bin the totals between 0 and just past the 99th percentile."""
    if len(sorted_values) < 8:
        return {"bins": [], "hi": 0.0}
    hi = max(1.0, _quantile(sorted_values, 0.99) * 1.05)
    bins = [0] * HIST_BINS
    for value in sorted_values:
        i = min(HIST_BINS - 1, int(value / hi * HIST_BINS))
        bins[i] += 1
    return {"bins": bins, "hi": round(hi, 2)}
