"""Trajectory library distilled from the user's own Humanizer runs.

Each saved run (``humanizer/runs/<id>.json``) carries a 170 Hz replay of the
mouse path plus the target spawn/hit events. This module cuts that replay into
individual flicks, normalises each one into its own frame (translate to the
start, rotate so the target lies on +X, divide by the distance) and keeps the
result as a small library of dimensionless shapes:

    along[i]  how much of the straight-line distance was covered
    perp[i]   how far the path bowed sideways, signed

Because both are fractions, they transfer to any resolution, FOV or
sensitivity with no conversion, and they carry the real path curve, the real
speed distribution along a flick, and any real overshoot together.

**Reaction time is deliberately removed.** The window from a target appearing
to the click includes the user's reaction dwell; every flick is trimmed to its
movement onset before the time axis is normalised, so the schedule starts at
the moment the hand begins to move.

Nothing here imports the aim loop, so it can be driven from a standalone
script. Every failure path degrades to an inert library rather than raising:
a missing, corrupt or too-thin set of runs simply makes the Humanizer sliders
do nothing.
"""

from __future__ import annotations

import bisect
import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import humanizer

DATA_DIR = humanizer.DATA_DIR
RUNS_DIR = humanizer.RUNS_DIR
PROFILE_PATH = humanizer.PROFILE_PATH
CACHE_PATH = DATA_DIR / "paths.json"

LIBRARY_VERSION = 6
REPLAY_MIN_VERSION = 1
SAMPLES = 16
MIN_FLICKS = 20
MIN_FRAMES = 6
MIN_DIST_PX = 40.0
# Movement onset: the first frame past this share of the straight-line
# distance. Everything before it is the reaction dwell and is discarded.
ONSET_FRACTION = 0.02
# Quality gate. A handful of recorded flicks are not flicks at all: the hand
# set off the wrong way, or the segment caught the tail of a previous target.
# Replaying those would drive the aim backwards or sideways off the screen,
# so they are dropped. On the reference runs this rejects 16 of 511 and
# leaves the average path shape unchanged.
MAX_BACKTRACK = 0.15
END_ALONG_MIN = 0.70
END_ALONG_MAX = 1.40
MAX_PERP = 0.50
# Ceiling on the speed term. Floored at 1.0 so it can never slow a module,
# a recorded flick's mid-flight speed runs to several times its own average.
# Left uncapped that turns the slider into a blunt strength multiplier, so it
# is held to a modest boost; the module's own max step still binds on top.
GAIN_MAX = 2.5
# Only the two clicking phases that produce real flicks.
FLICK_PHASES = (0, 1)
DIST_EDGES = (80.0, 160.0, 280.0, 440.0)
OCTANTS = 8
_TAU = math.pi * 2.0


@dataclass(frozen=True)
class Flick:
    """One normalised flick. ``along``/``perp`` are fractions of ``dist``."""

    dist: float
    angle: float
    dur_ms: float
    along: tuple[float, ...]
    perp: tuple[float, ...]
    ease: tuple[float, ...]
    gain: tuple[float, ...]

    def gain_at(self, progress: float) -> float:
        """How fast this flick was moving once ``progress`` of the distance
        was covered, relative to its own average speed.

        Never below 1.0: the recorded shape may make a module reach its own
        speed limit sooner, but must never hold it back.
        """
        if not self.gain:
            return 1.0
        last = len(self.gain) - 1
        pos = max(0.0, min(1.0, progress)) * last
        i = int(pos)
        if i >= last:
            return self.gain[last]
        return self.gain[i] + (self.gain[i + 1] - self.gain[i]) * (pos - i)

    def ease_at(self, t: float) -> float:
        """Share of the distance covered by time fraction ``t``.

        Normalised to land exactly on the target and forced to never go
        backwards, so a module driven by it still arrives where and when it
        would have anyway.
        """
        if t <= 0.0:
            return 0.0
        if t >= 1.0:
            return 1.0
        last = len(self.ease) - 1
        pos = t * last
        i = int(pos)
        if i >= last:
            return self.ease[last]
        return self.ease[i] + (self.ease[i + 1] - self.ease[i]) * (pos - i)

    def perp_at(self, progress: float) -> float:
        """Sideways offset once ``progress`` of the distance is covered.

        The recorded path is sampled evenly in time, so this walks ``along``
        to find where that much ground had been made up. Values are
        fractions of the flick's own distance.
        """
        if progress <= 0.0:
            return self.perp[0]
        if progress >= 1.0:
            return self.perp[-1]
        previous = self.along[0]
        for i in range(1, len(self.along)):
            current = self.along[i]
            if current >= progress > previous:
                span = current - previous
                t = (progress - previous) / span if span > 1e-9 else 0.0
                return self.perp[i - 1] + (self.perp[i] - self.perp[i - 1]) * t
            previous = current
        return self.perp[-1]

    def to_json(self) -> dict[str, Any]:
        return {
            "d": round(self.dist, 2),
            "a": round(self.angle, 4),
            "ms": round(self.dur_ms, 1),
            "al": [round(v, 5) for v in self.along],
            "pe": [round(v, 5) for v in self.perp],
            "ea": [round(v, 5) for v in self.ease],
            "ga": [round(v, 4) for v in self.gain],
        }

    @staticmethod
    def from_json(raw: Any) -> "Flick | None":
        if not isinstance(raw, dict):
            return None
        try:
            along = tuple(float(v) for v in raw["al"])
            perp = tuple(float(v) for v in raw["pe"])
            ease = tuple(float(v) for v in raw["ea"])
            gain = tuple(float(v) for v in raw["ga"])
            flick = Flick(
                float(raw["d"]), float(raw["a"]), float(raw["ms"]), along, perp, ease, gain
            )
        except (KeyError, TypeError, ValueError):
            return None
        return flick if _flick_ok(flick) else None


def _flick_ok(flick: Flick) -> bool:
    if len(flick.along) != SAMPLES or len(flick.perp) != SAMPLES:
        return False
    if not math.isfinite(flick.dist) or flick.dist < MIN_DIST_PX:
        return False
    if len(flick.ease) != SAMPLES or len(flick.gain) != SAMPLES:
        return False
    if min(flick.gain) < 1.0 or max(flick.gain) > GAIN_MAX + 1e-6:
        return False
    values = flick.along + flick.perp + flick.ease + flick.gain + (flick.angle, flick.dur_ms)
    if not all(math.isfinite(v) for v in values):
        return False
    if flick.along[0] != 0.0 or min(flick.along) < -MAX_BACKTRACK:
        return False
    if not END_ALONG_MIN <= flick.along[-1] <= END_ALONG_MAX:
        return False
    return max(abs(v) for v in flick.perp) <= MAX_PERP


def _bucket(dist: float, angle: float) -> tuple[int, int]:
    """Distance band and direction octant, so short corrections and long
    cross-screen flicks keep their own shapes and handedness."""
    band = bisect.bisect_right(DIST_EDGES, dist)
    octant = int(((angle % _TAU) / _TAU) * OCTANTS) % OCTANTS
    return band, octant


@dataclass(frozen=True)
class PathLibrary:
    """An immutable snapshot, rebound whole so the aim thread never locks."""

    ok: bool = False
    generation: int = 0
    flicks: tuple[Flick, ...] = ()
    # (band, octant) -> flicks, then band -> flicks, for the lookup fallback.
    by_bucket: dict[tuple[int, int], tuple[Flick, ...]] = field(default_factory=dict)
    by_band: dict[int, tuple[Flick, ...]] = field(default_factory=dict)
    # Typical tremor across every run, already in mouse counts. Each run is
    # converted with its own sensitivity, so runs recorded at different
    # cm/360 still combine correctly.
    jitter_counts: float = 0.0
    sources: tuple[str, ...] = ()
    note: str = "no runs"

    def pick(self, dist: float, angle: float, rng: random.Random) -> Flick | None:
        """A recorded flick of a similar length and direction, or None."""
        if not self.ok:
            return None
        band, octant = _bucket(dist, angle)
        pool = self.by_bucket.get((band, octant)) or self.by_band.get(band) or self.flicks
        return rng.choice(pool) if pool else None


DISABLED = PathLibrary()

_current: PathLibrary = DISABLED
_next_generation = 1


# ----------------------------------------------------------------- extraction


def _frames_ok(frames: Any) -> bool:
    if not isinstance(frames, dict):
        return False
    try:
        n = len(frames["t"])
    except (KeyError, TypeError):
        return False
    return n >= MIN_FRAMES and all(
        isinstance(frames.get(k), list) and len(frames[k]) == n for k in ("t", "x", "y")
    )


def _resample(
    times: Sequence[float], values: Sequence[float], count: int
) -> list[float]:
    """Sample ``values`` at ``count`` evenly spaced time fractions."""
    out: list[float] = []
    last = count - 1
    for k in range(count):
        frac = k / last if last else 0.0
        i = bisect.bisect_right(times, frac) - 1
        if i < 0:
            out.append(values[0])
            continue
        if i >= len(times) - 1:
            out.append(values[-1])
            continue
        span = times[i + 1] - times[i]
        t = (frac - times[i]) / span if span > 0.0 else 0.0
        out.append(values[i] + (values[i + 1] - values[i]) * t)
    return out


def _make_ease(along: Sequence[float]) -> tuple[float, ...]:
    """Turn a recorded distance-over-time curve into a usable ease.

    Scaled so it finishes on the target rather than wherever the click
    happened to land, and made non-decreasing so a move driven by it never
    backs up mid-flight.
    """
    reach = along[-1]
    if not math.isfinite(reach) or reach <= 1e-6:
        return ()
    out: list[float] = []
    highest = 0.0
    for value in along:
        highest = max(highest, max(0.0, min(1.0, value / reach)))
        out.append(highest)
    out[0] = 0.0
    out[-1] = 1.0
    return tuple(out)


def _make_gain(ease: Sequence[float]) -> tuple[float, ...]:
    """Speed along the path, sampled evenly by distance covered.

    ``ease`` runs 0..1 over 0..1 of the time, so its slope is already in
    units of the flick's own average speed. Values below 1.0 are lifted to
    1.0 so blending this in can only ever make a module quicker.
    """
    n = len(ease)
    if n < 2:
        return ()
    steps = n - 1
    speed = [(ease[i + 1] - ease[i]) * steps for i in range(steps)]
    out: list[float] = []
    for k in range(n):
        progress = k / steps
        i = 0
        while i < steps - 1 and ease[i + 1] < progress:
            i += 1
        out.append(max(1.0, min(GAIN_MAX, speed[i])))
    return tuple(out)


def _extract_flick(
    frames: dict[str, list[float]],
    i0: int,
    i1: int,
    target_x: float,
    target_y: float,
) -> Flick | None:
    """One flick, trimmed to movement onset and normalised to its own frame."""
    T, X, Y = frames["t"], frames["x"], frames["y"]
    reach = math.hypot(target_x - X[i0], target_y - Y[i0])
    if reach < MIN_DIST_PX:
        return None

    # Skip the reaction dwell: walk forward until the hand has actually moved.
    onset = i0
    travelled = 0.0
    threshold = reach * ONSET_FRACTION
    while onset < i1 and travelled < threshold:
        onset += 1
        travelled += math.hypot(X[onset] - X[onset - 1], Y[onset] - Y[onset - 1])
    if i1 - onset < MIN_FRAMES:
        return None

    sx, sy = X[onset], Y[onset]
    dx, dy = target_x - sx, target_y - sy
    dist = math.hypot(dx, dy)
    if dist < MIN_DIST_PX:
        return None
    span_ms = T[i1] - T[onset]
    if span_ms <= 0.0:
        return None

    ux, uy = dx / dist, dy / dist
    times: list[float] = []
    along: list[float] = []
    perp: list[float] = []
    for i in range(onset, i1 + 1):
        px, py = X[i] - sx, Y[i] - sy
        times.append((T[i] - T[onset]) / span_ms)
        along.append((px * ux + py * uy) / dist)
        perp.append((px * uy - py * ux) / dist)

    along_s = _resample(times, along, SAMPLES)
    perp_s = _resample(times, perp, SAMPLES)
    # Take out the straight-line drift so the bow starts and ends on the
    # module's own path. What is left is the shape of the arc; where the
    # click actually landed is terminal error, which is deliberately not
    # something the modules copy.
    end = perp_s[-1]
    reach = along_s[-1]
    if abs(reach) > 1e-6:
        perp_s = [p - end * (a / reach) for p, a in zip(perp_s, along_s)]
    perp_s[0] = 0.0
    perp_s[-1] = 0.0

    ease = _make_ease(along_s)
    flick = Flick(
        dist=dist,
        angle=math.atan2(dy, dx),
        dur_ms=span_ms,
        along=tuple(along_s),
        perp=tuple(perp_s),
        ease=ease,
        gain=_make_gain(ease),
    )
    return flick if _flick_ok(flick) else None


def _extract_run(payload: Any) -> list[Flick]:
    """Every usable flick in one saved run."""
    if not isinstance(payload, dict):
        return []
    replay = payload.get("replay")
    if not isinstance(replay, dict):
        return []
    try:
        if int(replay.get("version") or 0) < REPLAY_MIN_VERSION:
            return []
    except (TypeError, ValueError):
        return []
    frames = replay.get("frames")
    events = replay.get("events")
    if not _frames_ok(frames) or not isinstance(events, list):
        return []

    spawns: dict[Any, dict[str, Any]] = {}
    for event in events:
        if isinstance(event, dict) and event.get("k") == "s":
            spawns[event.get("id")] = event

    T = frames["t"]
    out: list[Flick] = []
    # Gridshot chains straight from the last hit; Flicking starts at the spawn.
    last_hit: dict[int, float] = {}
    for event in events:
        if not isinstance(event, dict) or event.get("k") != "c":
            continue
        hit = event.get("hit")
        phase = event.get("p")
        if hit is None or phase not in FLICK_PHASES:
            continue
        spawn = spawns.get(hit)
        if spawn is None:
            continue
        try:
            end_t = float(event["t"])
            spawn_t = float(spawn["t"])
            target_x = float(spawn["x"])
            target_y = float(spawn["y"])
        except (KeyError, TypeError, ValueError):
            continue
        if spawn.get("vx"):
            continue  # a moving Reaction ball has no fixed aim point
        start_t = spawn_t if phase == 1 else max(last_hit.get(phase, 0.0), spawn_t)
        last_hit[phase] = end_t
        if end_t <= start_t:
            continue
        i0 = bisect.bisect_left(T, start_t)
        i1 = bisect.bisect_right(T, end_t) - 1
        if i1 - i0 < MIN_FRAMES:
            continue
        flick = _extract_flick(frames, i0, i1, target_x, target_y)
        if flick is not None:
            out.append(flick)
    return out


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    if not n:
        return 0.0
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) * 0.5


def _run_jitter_counts(payload: Any) -> float:
    """This run's tremor in mouse counts, or 0.0 if it cannot be read."""
    if not isinstance(payload, dict):
        return 0.0
    profile = payload.get("profile")
    if not isinstance(profile, dict):
        return 0.0
    jitter, counts_per_px = _profile_scalars(profile)
    return jitter * counts_per_px


def _profile_scalars(profile: Any) -> tuple[float, float]:
    """``(jitter_px, counts_per_px)`` for one run's profile, or zeros.

    ``profile["sens"]`` is view pixels per mouse count, so its reciprocal
    converts that run's pixels into the counts a game actually receives.
    """
    if not isinstance(profile, dict):
        return 0.0, 0.0
    metrics = profile.get("metrics")
    jitter = 0.0
    if isinstance(metrics, dict):
        entry = metrics.get("jitter_px")
        if isinstance(entry, dict):
            try:
                value = float(entry.get("median"))
                if math.isfinite(value) and value >= 0.0:
                    jitter = value
            except (TypeError, ValueError):
                jitter = 0.0
    counts_per_px = 0.0
    try:
        sens = float(profile.get("sens"))
        if math.isfinite(sens) and sens > 1e-9:
            counts_per_px = 1.0 / sens
    except (TypeError, ValueError):
        counts_per_px = 0.0
    if counts_per_px <= 0.0:
        # Fall back to the recorded aim settings: counts per 360 / degrees.
        aim = profile.get("aim")
        if isinstance(aim, dict):
            try:
                dpi = float(aim["dpi"])
                cm360 = float(aim["cm360"])
                px_per_deg = float(aim["pxPerDeg"])
                if dpi > 0.0 and cm360 > 0.0 and px_per_deg > 0.0:
                    counts_per_px = (dpi * cm360 / 2.54) / (360.0 * px_per_deg)
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                counts_per_px = 0.0
    return jitter, counts_per_px


# ------------------------------------------------------------------- assembly


def _run_files() -> list[Path]:
    if not RUNS_DIR.is_dir():
        return []
    return sorted(p for p in RUNS_DIR.glob("*.json") if humanizer._ID_RE.match(p.stem))


def _stamp(paths: Sequence[Path]) -> list[list[Any]]:
    """Source fingerprint, so the cache is only rebuilt when a run changes."""
    out: list[list[Any]] = []
    for path in paths:
        try:
            out.append([path.stem, int(path.stat().st_mtime_ns)])
        except OSError:
            continue
    return out


def _assemble(
    flicks: list[Flick],
    sources: Sequence[str],
    note: str,
    jitter: Sequence[float] = (),
) -> PathLibrary:
    global _next_generation
    generation = _next_generation
    _next_generation += 1
    if len(flicks) < MIN_FLICKS:
        return PathLibrary(
            generation=generation,
            note=f"{note} ({len(flicks)} flicks, need {MIN_FLICKS})",
        )
    by_bucket: dict[tuple[int, int], list[Flick]] = {}
    by_band: dict[int, list[Flick]] = {}
    for flick in flicks:
        band, octant = _bucket(flick.dist, flick.angle)
        by_bucket.setdefault((band, octant), []).append(flick)
        by_band.setdefault(band, []).append(flick)
    return PathLibrary(
        ok=True,
        generation=generation,
        flicks=tuple(flicks),
        by_bucket={k: tuple(v) for k, v in by_bucket.items()},
        by_band={k: tuple(v) for k, v in by_band.items()},
        jitter_counts=_median([v for v in jitter if v > 0.0]),
        sources=tuple(sources),
        note=note,
    )


def _read_cache(stamp: list[list[Any]]) -> PathLibrary | None:
    raw = humanizer._read(CACHE_PATH)
    if not isinstance(raw, dict):
        return None
    try:
        if int(raw.get("version") or 0) != LIBRARY_VERSION:
            return None
        if int(raw.get("samples") or 0) != SAMPLES:
            return None
    except (TypeError, ValueError):
        return None
    if raw.get("stamp") != stamp:
        return None
    entries = raw.get("flicks")
    if not isinstance(entries, list):
        return None
    flicks = [f for f in (Flick.from_json(e) for e in entries) if f is not None]
    if len(flicks) != len(entries):
        return None
    jitter = raw.get("jitter")
    if not isinstance(jitter, list) or not all(isinstance(v, (int, float)) for v in jitter):
        return None
    sources = [str(s[0]) for s in stamp]
    return _assemble(flicks, sources, "cached", [float(v) for v in jitter])


def _write_cache(library: PathLibrary, stamp: list[list[Any]]) -> None:
    payload = {
        "version": LIBRARY_VERSION,
        "samples": SAMPLES,
        "stamp": stamp,
        "jitter": [library.jitter_counts] if library.jitter_counts else [],
        "flicks": [f.to_json() for f in library.flicks],
    }
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_PATH.with_suffix(CACHE_PATH.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        tmp.replace(CACHE_PATH)
    except OSError:
        pass


def build() -> PathLibrary:
    """Rebuild the library from the saved runs. Never raises."""
    try:
        paths = _run_files()
        if not paths:
            return _assemble([], (), "no runs")
        stamp = _stamp(paths)
        cached = _read_cache(stamp)
        if cached is not None:
            return cached
        flicks: list[Flick] = []
        used: list[str] = []
        jitter: list[float] = []
        for path in paths:
            # Guard each run on its own: one damaged replay should cost that
            # run's flicks, not everybody else's.
            try:
                payload = humanizer._read(path)
                found = _extract_run(payload)
            except Exception:
                continue
            if found:
                flicks.extend(found)
                used.append(path.stem)
                jitter.append(_run_jitter_counts(payload))
        library = _assemble(flicks, used, f"{len(used)} run(s)", jitter)
        if library.ok:
            _write_cache(library, stamp)
        return library
    except Exception:  # a bad replay must never take the aim loop down
        return _assemble([], (), "unreadable")


def get() -> PathLibrary:
    """The current snapshot. Safe to call from any thread."""
    return _current


def generation() -> int:
    return _current.generation


def reload() -> PathLibrary:
    """Rebuild and publish. Call off the aim thread."""
    global _current
    _current = build()
    return _current
