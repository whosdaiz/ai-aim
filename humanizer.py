"""Humanizer profile storage.

The session itself (Gridshot, Flicking, Reaction, Tracking) runs in the menu, and the
analysis happens there too, because that is where the high-rate mouse samples
are. This module only stores what comes back: the profile (outlier-filtered
statistics of how the user aims) and a compact replay of each run.

Layout, next to Argus:

    humanizer/profile.json        the current profile (from the latest run)
    humanizer/runs/<id>.json      one file per run: profile + replay

Only the most recent MAX_RUNS runs are kept. Nothing here is connected to the
aim modules; it is a record of the user's own aim.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "humanizer"
RUNS_DIR = DATA_DIR / "runs"
PROFILE_PATH = DATA_DIR / "profile.json"
# The Click reaction times of every kept run, {run id: [ms, ...]}. Small on
# purpose: the trigger reads it, and the runs themselves carry megabytes of
# replay.
REACTIONS_PATH = DATA_DIR / "reactions.json"
REACTION_MIN_MS = 60.0
REACTION_MAX_MS = 1500.0
MAX_RUNS = 10
MAX_RUN_BYTES = 24 * 1024 * 1024
# A profile must come from the current session layout and be recent; the menu
# requires a run before anything else when it is not.
PROFILE_VERSION = 2
RERUN_DAYS = 14
_ID_RE = re.compile(r"^\d{8}-\d{6}(?:-\d+)?$")
_LOCK = threading.Lock()


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def _reaction_values(profile: Any) -> list[float]:
    """The kept Click reaction times a profile carries, cleaned."""
    metrics = profile.get("metrics") if isinstance(profile, dict) else None
    entry = metrics.get("click_rt_ms") if isinstance(metrics, dict) else None
    raw = entry.get("values") if isinstance(entry, dict) else None
    out: list[float] = []
    for value in raw if isinstance(raw, list) else ():
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if REACTION_MIN_MS <= number <= REACTION_MAX_MS:
            out.append(round(number, 1))
    return out[:400]


def _reactions_read() -> dict[str, list[float]]:
    data = _read(REACTIONS_PATH)
    if not isinstance(data, dict):
        return {}
    return {
        run_id: [float(v) for v in values if isinstance(v, (int, float))]
        for run_id, values in data.items()
        if _ID_RE.match(str(run_id)) and isinstance(values, list)
    }


def _reactions_sync(add: tuple[str, list[float]] | None = None) -> None:
    """Bring the reaction index in line with the runs on disk."""
    index = _reactions_read()
    if add is not None and add[1]:
        index[add[0]] = add[1]
    alive = {p.stem for p in _run_files()}
    index = {run_id: values for run_id, values in index.items() if run_id in alive and values}
    try:
        _write(REACTIONS_PATH, index)
    except OSError:
        pass


def reaction_pool() -> tuple[list[float], int]:
    """(every recorded Click reaction still on disk, how many runs gave them)."""
    with _LOCK:
        index = _reactions_read()
        alive = {p.stem for p in _run_files()}
    pool: list[float] = []
    runs = 0
    for run_id, values in index.items():
        if run_id in alive and values:
            pool.extend(values)
            runs += 1
    return pool, runs


def _summary(run_id: str, profile: dict[str, Any]) -> dict[str, Any]:
    overall = profile.get("overall") if isinstance(profile.get("overall"), dict) else {}
    return {
        "id": run_id,
        "createdAt": profile.get("createdAt"),
        "score": overall.get("score"),
        "accuracy": overall.get("accuracy"),
        "hits": overall.get("hits"),
        "durationS": profile.get("durationS"),
    }


def _run_files() -> list[Path]:
    """Run files, newest first. Ids are 'YYYYmmdd-HHMMSS' plus '-n' when two
    land in the same second, so the suffix is compared as a number."""
    def key(path: Path) -> tuple[str, int]:
        stamp, _, n = path.stem.partition("-")[2].partition("-")
        return (path.stem[:8] + stamp, int(n) if n.isdigit() else 0)

    if not RUNS_DIR.is_dir():
        return []
    return sorted((p for p in RUNS_DIR.glob("*.json") if _ID_RE.match(p.stem)), key=key, reverse=True)


def _runs() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for path in _run_files():
        data = _read(path)
        if isinstance(data, dict) and isinstance(data.get("profile"), dict):
            out.append(_summary(path.stem, data["profile"]))
    return out


def _requirement(profile: Any) -> dict[str, Any]:
    """Whether a (re)run is required: no profile, one from an older session
    layout, or one older than RERUN_DAYS. `dueAt` is epoch seconds."""
    if not isinstance(profile, dict):
        return {"required": True, "reason": "missing", "dueAt": None}
    saved = profile.get("savedAt")
    try:
        version = int(profile.get("version") or 0)
    except (TypeError, ValueError):
        version = 0
    if version < PROFILE_VERSION or not isinstance(saved, (int, float)):
        return {"required": True, "reason": "outdated", "dueAt": None}
    due_at = float(saved) + RERUN_DAYS * 86400
    expired = time.time() >= due_at
    return {"required": expired, "reason": "expired" if expired else None, "dueAt": due_at}


def state() -> dict[str, Any]:
    with _LOCK:
        profile = _read(PROFILE_PATH)
        profile = profile if isinstance(profile, dict) else None
        return {"ok": True, "profile": profile, "runs": _runs(), "rerunDays": RERUN_DAYS, **_requirement(profile)}


def save_run(payload: str) -> dict[str, Any]:
    """Store a finished run. `payload` is JSON: {"profile": {...}, "replay": {...}}."""
    if not isinstance(payload, str) or len(payload) > MAX_RUN_BYTES:
        return {"ok": False, "error": "Run data is missing or too large"}
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return {"ok": False, "error": "Run data could not be read"}
    if not isinstance(data, dict) or not isinstance(data.get("profile"), dict) or not isinstance(data.get("replay"), dict):
        return {"ok": False, "error": "Run data is incomplete"}
    with _LOCK:
        # Count up past any run saved in the same second, never reusing an
        # id, so ids always sort in save order.
        run_id = time.strftime("%Y%m%d-%H%M%S")
        same = [p.stem for p in _run_files() if p.stem == run_id or p.stem.startswith(run_id + "-")]
        if same:
            last = max(int(s.rpartition("-")[2]) if s != run_id else 0 for s in same)
            run_id = f"{run_id}-{last + 1}"
        profile = dict(data["profile"])
        profile["runId"] = run_id
        profile["savedAt"] = time.time()
        try:
            _write(RUNS_DIR / f"{run_id}.json", {"profile": profile, "replay": data["replay"]})
            _write(PROFILE_PATH, profile)
        except OSError as exc:
            return {"ok": False, "error": str(exc) or "Could not save the run"}
        for old in _run_files()[MAX_RUNS:]:
            try:
                old.unlink()
            except OSError:
                pass
        _reactions_sync((run_id, _reaction_values(profile)))
        return {"ok": True, "profile": profile, "runs": _runs(), "rerunDays": RERUN_DAYS, **_requirement(profile)}


def delete_run(run_id: str) -> dict[str, Any]:
    """Remove one saved run.

    The profile shown on the Humanizer page belongs to a particular run, so
    deleting that run hands the page to the newest one left. Deleting the
    last run leaves no profile at all, which puts the menu back to requiring
    a session.
    """
    run_id = str(run_id or "").strip()
    if not _ID_RE.match(run_id):
        return {"ok": False, "error": "Unknown run"}
    with _LOCK:
        path = RUNS_DIR / f"{run_id}.json"
        if not path.is_file():
            return {"ok": False, "error": "That run is no longer saved"}
        try:
            path.unlink()
        except OSError as exc:
            return {"ok": False, "error": str(exc) or "Could not delete the run"}
        _reactions_sync()

        profile = _read(PROFILE_PATH)
        current = profile.get("runId") if isinstance(profile, dict) else None
        if current == run_id:
            profile = None
            for remaining in _run_files():
                data = _read(remaining)
                if isinstance(data, dict) and isinstance(data.get("profile"), dict):
                    profile = data["profile"]
                    break
            try:
                if profile is None:
                    PROFILE_PATH.unlink(missing_ok=True)
                else:
                    _write(PROFILE_PATH, profile)
            except OSError as exc:
                return {"ok": False, "error": str(exc) or "Could not update the profile"}
        return {
            "ok": True,
            "profile": profile if isinstance(profile, dict) else None,
            "runs": _runs(),
            "rerunDays": RERUN_DAYS,
            **_requirement(profile),
        }


def get_run(run_id: str) -> dict[str, Any]:
    run_id = str(run_id or "").strip()
    if not _ID_RE.match(run_id):
        return {"ok": False, "error": "Unknown run"}
    data = _read(RUNS_DIR / f"{run_id}.json")
    if not isinstance(data, dict):
        return {"ok": False, "error": "That run is no longer saved"}
    return {"ok": True, "run": data}
