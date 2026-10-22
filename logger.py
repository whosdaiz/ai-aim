"""Quiet tagged logging. Event-based only — never per-frame."""

from __future__ import annotations

import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from queue import Empty, SimpleQueue
from typing import Any

MODULE_TAGS = (
    ("visuals", "Visuals"),
    ("rp_aim_assist", "AimAssist"),
    ("aim_director", "AimDirector"),
    ("aim_corrector", "AimCorrector"),
    ("tracking_assist", "TrackingAssist"),
    ("spray_acquire", "SprayAssist"),
    ("rp_one_tap_assist", "OneTapAssist"),
    ("tap_assist", "TapAssist"),
    ("trigger", "Trigger"),
    ("magnet", "MagnetTrigger"),
)

LOG_TAGS = (
    ("ERROR", "#ff6b78"),
    ("Argus", ""),
    ("Status", ""),
    ("Capture", ""),
    ("Settings", ""),
    ("Logger", ""),
    ("Game", ""),
    ("Config", ""),
    ("RP2040", ""),
    ("Pause", ""),
    ("Visuals", ""),
    ("AimAssist", ""),
    ("AimDirector", ""),
    ("AimCorrector", ""),
    ("TrackingAssist", ""),
    ("SprayAssist", ""),
    ("OneTapAssist", ""),
    ("TapAssist", ""),
    ("Trigger", ""),
    ("MagnetTrigger", ""),
)


STATUS_INTERVAL_S = 30.0
BUFFER_MAX = 800
_SENTINEL = object()

_enabled = True
_lock = threading.Lock()
_next_id = 1
_buffer: deque[dict[str, Any]] = deque(maxlen=BUFFER_MAX)
_queue: SimpleQueue[Any] = SimpleQueue()
_writer: threading.Thread | None = None
_session_path = ""
_started = False


def set_enabled(enabled: bool) -> None:
    global _enabled
    _enabled = bool(enabled)


def is_enabled() -> bool:
    return _enabled


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _emit(tag: str, message: str, err: bool) -> None:
    global _next_id
    rec = {
        "id": 0,
        "tag": tag,
        "msg": message,
        "err": err,
        "line": f"[{tag}] {message}",
        "t": time.time(),
    }
    with _lock:
        rec["id"] = _next_id
        _next_id += 1
        _buffer.append(rec)
    _queue.put_nowait(rec)


def log(tag: str, message: str) -> None:
    if not _enabled:
        return
    _emit(tag, message, False)


def error(message: str) -> None:
    _emit("ERROR", message, True)


def since(after_id: int = 0) -> dict[str, Any]:
    try:
        after = int(after_id or 0)
    except (TypeError, ValueError):
        after = 0
    with _lock:
        lines = [
            {
                "id": rec["id"],
                "tag": rec["tag"],
                "msg": rec["msg"],
                "err": rec["err"],
                "line": rec["line"],
            }
            for rec in _buffer
            if rec["id"] > after
        ]
        next_id = _next_id - 1
        path = _session_path
    return {"lines": lines, "next_id": next_id, "path": path}


def session_path() -> str:
    return _session_path


def log_tags() -> list[dict[str, str]]:
    return [{"tag": tag, "color": color} for tag, color in LOG_TAGS]


def start(base_dir: Path | None = None) -> str:
    global _writer, _session_path, _started
    if _started:
        return _session_path
    root = Path(base_dir) if base_dir else Path(__file__).resolve().parent
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = log_dir / f"argus-{stamp}.log"
    _session_path = str(path)
    _started = True
    _writer = threading.Thread(target=_writer_loop, args=(path,), name="log-writer", daemon=True)
    _writer.start()
    return _session_path


def stop() -> None:
    global _started, _writer
    if not _started:
        return
    _queue.put(_SENTINEL)
    if _writer is not None:
        _writer.join(timeout=2.0)
    _writer = None
    _started = False


def _writer_loop(path: Path) -> None:
    frozen = _frozen()
    handle = None
    try:
        handle = path.open("a", encoding="utf-8", newline="\n")
        handle.write(f"# Argus session {datetime.now().isoformat(timespec='seconds')}\n")
        while True:
            item = _queue.get()
            batch = [item]
            while True:
                try:
                    batch.append(_queue.get_nowait())
                except Empty:
                    break
            stop = False
            for rec in batch:
                if rec is _SENTINEL:
                    stop = True
                    continue
                text = rec["line"]
                handle.write(text + "\n")
                if not frozen:
                    stream = sys.stderr if rec.get("err") else sys.stdout
                    print(text, file=stream, flush=False)
            handle.flush()
            if stop:
                break
    except OSError:
        pass
    finally:
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass
