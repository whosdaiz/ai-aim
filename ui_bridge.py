"""Frameless settings panel hosted with pywebview."""

from __future__ import annotations

import ctypes
import json
import faulthandler
import os
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any

import webview

import cs2_inventory
import humanizer
import humanizer_paths
import humanizer_reaction
import leetify

UI_DIR = Path(__file__).resolve().parent / "ui"
UI_INDEX = UI_DIR / "index.html"
UI_SETTINGS_PATH = Path(__file__).resolve().parent / "argus-ui.json"
LOG_COLORS_PATH = Path(__file__).resolve().parent / "log-colors.json"
WINDOW_STATE_PATH = Path(__file__).resolve().parent / "argus-window.json"
WEBVIEW_DATA_DIR = Path(__file__).resolve().parent / "webview-data"
HANG_LOG_PATH = Path(__file__).resolve().parent / "logs" / "argus-hang.txt"
WINDOW_TITLE = "Argus AI"


def _humanizer_paths_summary() -> dict[str, Any]:
    """What the aim modules are actually learning from, for the page."""
    library = humanizer_paths.get()
    return {
        "ok": library.ok,
        "runs": len(library.sources),
        "flicks": len(library.flicks),
        "maxRuns": humanizer.MAX_RUNS,
    }


def _app_window() -> Any:
    windows = list(webview.windows)
    return windows[0] if windows else None


def _native_form() -> Any:
    win = _app_window()
    return getattr(win, "native", None) if win is not None else None


def _hwnd() -> int:
    handle = getattr(_native_form(), "Handle", None)
    if handle is not None:
        try:
            if hasattr(handle, "ToInt32"):
                return int(handle.ToInt32())
            if hasattr(handle, "ToInt64"):
                return int(handle.ToInt64())
            return int(handle)
        except (TypeError, ValueError, OverflowError, AttributeError):
            pass
    return int(ctypes.windll.user32.FindWindowW(None, WINDOW_TITLE) or 0)


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_int),
        ("top", ctypes.c_int),
        ("right", ctypes.c_int),
        ("bottom", ctypes.c_int),
    ]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_uint),
        ("flags", ctypes.c_uint),
        ("showCmd", ctypes.c_uint),
        ("ptMinPosition", _POINT),
        ("ptMaxPosition", _POINT),
        ("rcNormalPosition", _RECT),
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", ctypes.c_ulong),
    ]


SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
MIN_WINDOW = (980, 640)
DEFAULT_WINDOW = {"width": 1180, "height": 780, "x": 24, "y": 72, "maximized": False}

_geometry_lock = threading.Lock()
_geometry: dict[str, Any] = dict(DEFAULT_WINDOW)
_geometry_timer: threading.Timer | None = None
_maximized = False
# Window placement to go back to after a Humanizer fullscreen, or None.
_fullscreen_restore: dict[str, Any] | None = None


def _looks_like_monitor(width: int, height: int) -> bool:
    user32 = ctypes.windll.user32
    sizes = (
        (int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))),
        (int(user32.GetSystemMetrics(78)), int(user32.GetSystemMetrics(79))),
    )
    return any(abs(width - cw) <= 16 and abs(height - ch) <= 16 for cw, ch in sizes)


def _on_screen(x: int, y: int, width: int, height: int) -> bool:
    user32 = ctypes.windll.user32
    vx = int(user32.GetSystemMetrics(76))
    vy = int(user32.GetSystemMetrics(77))
    vw = int(user32.GetSystemMetrics(78))
    vh = int(user32.GetSystemMetrics(79))
    return not (x + width < vx + 40 or y + 40 < vy or x > vx + vw - 40 or y > vy + vh - 40)


def _hwnd_rect() -> _RECT | None:
    hwnd = _hwnd()
    if not hwnd:
        return None
    rect = _RECT()
    if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return rect


def _sync_geometry_from_hwnd() -> None:
    if _fullscreen_restore is not None:
        return  # the fullscreen rect is never the window's own size
    if _maximized:
        with _geometry_lock:
            _geometry["maximized"] = True
        return
    rect = _hwnd_rect()
    if rect is None:
        return
    width = max(MIN_WINDOW[0], int(rect.right - rect.left))
    height = max(MIN_WINDOW[1], int(rect.bottom - rect.top))
    if _looks_like_monitor(width, height):
        return
    with _geometry_lock:
        _geometry["x"] = int(rect.left)
        _geometry["y"] = int(rect.top)
        _geometry["width"] = width
        _geometry["height"] = height
        _geometry["maximized"] = False


def _write_window_state() -> None:
    with _geometry_lock:
        data = dict(_geometry)
        data["maximized"] = bool(_maximized)
    try:
        WINDOW_STATE_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass


def _schedule_window_state() -> None:
    global _geometry_timer
    with _geometry_lock:
        if _geometry_timer is not None:
            _geometry_timer.cancel()
        _geometry_timer = threading.Timer(0.3, _write_window_state)
        _geometry_timer.daemon = True
        _geometry_timer.start()


def _flush_window_state() -> None:
    global _geometry_timer
    with _geometry_lock:
        timer = _geometry_timer
        _geometry_timer = None
    if timer is not None:
        timer.cancel()
    _sync_geometry_from_hwnd()
    _write_window_state()


def _load_window_state() -> dict[str, Any]:
    out = dict(DEFAULT_WINDOW)
    try:
        parsed = json.loads(WINDOW_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    if not isinstance(parsed, dict):
        return out
    try:
        out["width"] = max(MIN_WINDOW[0], int(parsed.get("width") or DEFAULT_WINDOW["width"]))
        out["height"] = max(MIN_WINDOW[1], int(parsed.get("height") or DEFAULT_WINDOW["height"]))
    except (TypeError, ValueError):
        pass
    for key in ("x", "y"):
        try:
            out[key] = int(parsed[key])
        except (KeyError, TypeError, ValueError):
            pass
    out["maximized"] = bool(parsed.get("maximized"))
    if _looks_like_monitor(out["width"], out["height"]):
        out["width"] = DEFAULT_WINDOW["width"]
        out["height"] = DEFAULT_WINDOW["height"]
        out["x"] = DEFAULT_WINDOW["x"]
        out["y"] = DEFAULT_WINDOW["y"]
    return out


def _on_window_resized(_width: int, _height: int) -> None:
    _sync_geometry_from_hwnd()
    _schedule_window_state()


def _on_window_moved(_x: int, _y: int) -> None:
    _sync_geometry_from_hwnd()
    _schedule_window_state()

UI_BOOLS = (
    "compactNav",
    "altNav",
    "frostNav",
    "autoHideNav",
    "reduceMotion",
    "staticGradients",
)
UI_ALLOWED = {
    "theme",
    "compactNav",
    "altNav",
    "frostNav",
    "frostStrength",
    "frostScale",
    "autoHideNav",
    "reduceMotion",
    "themeSpeed",
    "staticGradients",
    "bgFx",
    "bgFxOpts",
    "customStatic",
    "customAnim",
}


def _file_dialog_type() -> Any:
    if hasattr(webview, "FileDialog"):
        return webview.FileDialog.OPEN
    return webview.OPEN_DIALOG


def _clamp_int(value: Any, lo: int, hi: int, fallback: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return fallback


def _clean_custom_theme(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    out: dict[str, Any] = {}
    name = str(value.get("name") or "Custom").strip()[:18]
    if name:
        out["name"] = name
    for key in ("accent", "accent2", "blue", "orange", "red"):
        raw = str(value.get(key) or "").strip()
        if raw.startswith("#") and 4 <= len(raw) <= 9:
            out[key] = raw
    if "animMode" in value:
        out["animMode"] = "fade" if value.get("animMode") == "fade" else "rgb"
    for key in ("gradientPct", "gradientFade", "gradientAngle"):
        if key in value:
            out[key] = _clamp_int(value.get(key), 0, 360 if key == "gradientAngle" else 100, 50)
    return out


def _clean_ui_settings(parsed: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key in UI_ALLOWED:
        if key not in parsed:
            continue
        if key in UI_BOOLS:
            clean[key] = bool(parsed[key])
        elif key == "theme":
            name = str(parsed.get("theme") or "omega").strip()[:24]
            clean[key] = name or "omega"
        elif key == "themeSpeed":
            clean[key] = _clamp_int(parsed.get(key), 1, 10, 5)
        elif key == "frostStrength":
            clean[key] = _clamp_int(parsed.get(key), 1, 10, 8)
        elif key == "frostScale":
            clean[key] = _clamp_int(parsed.get(key), 1, 8, 2)
        elif key == "bgFx":
            value = str(parsed.get("bgFx") or "off").strip().lower()
            if value in {"orbits"}:
                value = "hive"
            if value in {"ripples"}:
                value = "stars"
            clean[key] = value if value in {"off", "particles", "neural", "mesh", "hive", "stars"} else "off"
        elif key == "bgFxOpts":
            raw = parsed.get("bgFxOpts") if isinstance(parsed.get("bgFxOpts"), dict) else {}
            modes = {
                "particles": ("count", "speed", "attract", "size", "links", "click", "clickAmt"),
                "neural": ("count", "speed", "link", "mouse", "glow", "click", "clickAmt"),
                "mesh": ("density", "pull", "radius", "click", "clickAmt", "clickScale"),
                "hive": ("density", "radius", "glow", "click", "clickAmt"),
                "stars": (
                    "density",
                    "size",
                    "sizeJitter",
                    "twinkle",
                    "twinkleSpeed",
                    "parallax",
                    "showers",
                    "showerJitter",
                    "meteorSize",
                    "meteorJitter",
                    "meteorSpeed",
                    "meteorSpeedScale",
                    "burnout",
                    "burnoutJitter",
                ),
            }
            out: dict[str, dict[str, int]] = {}
            for mode, fields in modes.items():
                block = raw.get(mode) if isinstance(raw.get(mode), dict) else {}
                kept: dict[str, int] = {}
                for field in fields:
                    kept[field] = _clamp_int(block.get(field), 0, 10, 5)
                out[mode] = kept
            clean[key] = out
        elif key in {"customStatic", "customAnim"}:
            theme = _clean_custom_theme(parsed.get(key))
            if theme:
                clean[key] = theme
    return clean


def _clean_log_colors(parsed: Any) -> dict[str, str]:
    if not isinstance(parsed, dict):
        return {}
    clean: dict[str, str] = {}
    for key, value in parsed.items():
        tag = str(key or "").strip()[:32]
        raw = str(value or "").strip()
        if not tag or not raw.startswith("#") or not (4 <= len(raw) <= 9):
            continue
        clean[tag] = raw
    return clean


class SettingsApi:
    """JS bridge. Keep attributes private so pywebview does not serialize WinForms/COM graphs."""

    def __init__(self, controller: Any) -> None:
        self._controller = controller
        self._maximized = False

    def get_state(self) -> dict[str, Any]:
        from main import games_for_ui

        live = self._controller.live
        cfg = live.get()
        return {
            "settings": live.to_ui_dict(),
            "status": self._controller.status.to_dict(),
            "readonly": {
                "engine": cfg.engine_path,
                "fps": cfg.target_fps,
                "output_idx": cfg.output_idx,
                "verify_inject": cfg.verify_inject,
                "offset_demo": cfg.offset_fn is not None,
            },
            "games": games_for_ui(),
            **self._controller.configs_state(),
        }

    def get_status(self) -> dict[str, Any]:
        cfg = self._controller.live.get()
        return {
            "status": self._controller.status.to_dict(),
            "dry_run": cfg.dry_run,
            "engine": cfg.engine_path,
        }

    def get_logs(self, since_id: int = 0) -> dict[str, Any]:
        from logger import since as logs_since

        try:
            after = int(since_id or 0)
        except (TypeError, ValueError):
            after = 0
        return logs_since(after)

    def get_log_tags(self) -> dict[str, Any]:
        from logger import log_tags

        return {"ok": True, "tags": log_tags()}

    def load_log_colors(self) -> dict[str, Any]:
        try:
            if not LOG_COLORS_PATH.is_file():
                return {"ok": True, "colors": {}}
            parsed = json.loads(LOG_COLORS_PATH.read_text(encoding="utf-8"))
            return {"ok": True, "colors": _clean_log_colors(parsed)}
        except (OSError, json.JSONDecodeError):
            return {"ok": True, "colors": {}}

    def save_log_colors(self, colors: dict[str, Any]) -> dict[str, Any]:
        try:
            clean = _clean_log_colors(colors if isinstance(colors, dict) else {})
            LOG_COLORS_PATH.write_text(json.dumps(clean, indent=2) + "\n", encoding="utf-8")
            return {"ok": True}
        except OSError as exc:
            return {"ok": False, "error": str(exc) or "Could not save log colors"}

    def export_logs_html(self, html: str) -> dict[str, Any]:
        if not isinstance(html, str) or not html.strip():
            return {"ok": False, "error": "Nothing to export"}
        from logger import session_path as current_session_path

        src = Path(current_session_path() or "")
        dest = src.with_suffix(".html") if src.suffix else Path(__file__).resolve().parent / "logs" / "argus-log.html"
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(html, encoding="utf-8")
        except OSError as exc:
            return {"ok": False, "error": str(exc) or "Could not export log"}
        return {"ok": True, "path": str(dest)}

    def set_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(updates, dict):
            return {"ok": False, "error": "settings must be an object"}
        return self._controller.apply_settings(updates)

    def select_game(self, game_id: str) -> dict[str, Any]:
        if not isinstance(game_id, str) or not game_id.strip():
            return {"ok": False, "error": "Select a game"}
        return self._controller.apply_settings({"game": game_id.strip().lower()})

    def create_config(self, name: str) -> dict[str, Any]:
        if not isinstance(name, str):
            return {"ok": False, "error": "Enter a config name"}
        return self._controller.create_config(name)

    def save_config(self) -> dict[str, Any]:
        return self._controller.save_config()

    def select_config(self, name: str) -> dict[str, Any]:
        if not isinstance(name, str) or not name.strip():
            return {"ok": False, "error": "Select a config"}
        return self._controller.select_config(name)

    def delete_config(self, name: str) -> dict[str, Any]:
        if not isinstance(name, str):
            return {"ok": False, "error": "Select a config"}
        return self._controller.delete_config(name)

    def start_capture(self) -> dict[str, Any]:
        return self._controller.start_capture()

    def stop_capture(self) -> dict[str, Any]:
        self._controller.stop_capture()
        return {"ok": True, "settings": self._controller.live.to_ui_dict()}

    def hide_menu(self) -> None:
        if self._controller.menu_visible:
            self._controller.toggle_menu()

    # --- CS2 inventory module (cs2_inventory.py) ---
    def inv_state(self) -> dict[str, Any]:
        return cs2_inventory.state()

    def inv_sign_in(self) -> dict[str, Any]:
        return cs2_inventory.sign_in()

    def inv_cancel_sign_in(self) -> dict[str, Any]:
        return cs2_inventory.cancel_sign_in()

    def inv_sign_out(self) -> dict[str, Any]:
        return cs2_inventory.sign_out()

    def inv_load(self, force: bool = False) -> dict[str, Any]:
        return cs2_inventory.inventory(bool(force))

    def inv_prices(self) -> dict[str, Any]:
        return cs2_inventory.prices()

    def inv_keys(self) -> dict[str, Any]:
        return cs2_inventory.keys()

    def inv_icon_boxes(self) -> dict[str, Any]:
        return cs2_inventory.icon_boxes()

    def inv_icon_box_original(self, key: str) -> dict[str, Any]:
        return cs2_inventory.icon_box_original(key)

    def inv_save_key(self, name: str, value: str) -> dict[str, Any]:
        return cs2_inventory.save_key(name, value)

    def inv_reveal_key(self, name: str) -> dict[str, Any]:
        return cs2_inventory.reveal_key(name)

    def inv_delete_key(self, name: str) -> dict[str, Any]:
        return cs2_inventory.delete_key(name)

    # --- CS2 stats module (leetify.py) ---
    def stats_state(self) -> dict[str, Any]:
        return leetify.state()

    def stats_profile(self, force: bool = False) -> dict[str, Any]:
        return leetify.profile(bool(force))

    def stats_match(self, game_id: str) -> dict[str, Any]:
        return leetify.match(game_id)

    # --- Humanizer (humanizer.py) ---
    def humanizer_state(self) -> dict[str, Any]:
        state = humanizer.state()
        state["paths"] = _humanizer_paths_summary()
        return state

    def humanizer_save_run(self, payload: str) -> dict[str, Any]:
        result = humanizer.save_run(payload)
        if result.get("ok"):
            # Re-distil the trajectory library here, on the UI thread, so the
            # aim loop only ever swaps in a finished snapshot.
            humanizer_paths.reload()
            humanizer_reaction.reload()
            result["paths"] = _humanizer_paths_summary()
        return result

    def humanizer_run(self, run_id: str) -> dict[str, Any]:
        return humanizer.get_run(run_id)

    def humanizer_delete_run(self, run_id: str) -> dict[str, Any]:
        result = humanizer.delete_run(run_id)
        if result.get("ok"):
            # One less run to learn from; redistil before the page redraws.
            humanizer_paths.reload()
            humanizer_reaction.reload()
            result["paths"] = _humanizer_paths_summary()
        return result

    def humanizer_session(self, active: bool) -> dict[str, Any]:
        """Pause cursor unclipping while the session holds pointer lock."""
        self._controller.humanizer_active = bool(active)
        return {"ok": True}

    def humanizer_fullscreen(self, active: bool) -> dict[str, Any]:
        """Cover the whole monitor (taskbar included) for a Humanizer session
        or sensitivity test, then put the window back exactly as it was."""
        global _fullscreen_restore
        user32 = ctypes.windll.user32
        hwnd = _hwnd() or int(getattr(self._controller, "hwnd", 0) or 0)
        if not hwnd:
            return {"ok": False, "error": "No window"}
        if active:
            if _fullscreen_restore is not None:
                return {"ok": True}
            # The placement holds the normal size and the maximized state
            # together, so a maximized window comes back maximized and still
            # un-maximizes to its usual size.
            placement = _WINDOWPLACEMENT()
            placement.length = ctypes.sizeof(_WINDOWPLACEMENT)
            if not user32.GetWindowPlacement(ctypes.c_void_p(hwnd), ctypes.byref(placement)):
                return {"ok": False, "error": "Could not read the window size"}
            zoomed = bool(user32.IsZoomed(hwnd))
            user32.MonitorFromWindow.restype = ctypes.c_void_p
            user32.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MONITORINFO)]
            monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
            info = _MONITORINFO()
            info.cbSize = ctypes.sizeof(_MONITORINFO)
            if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
                return {"ok": False, "error": "Could not find the monitor"}
            _fullscreen_restore = {"placement": placement, "zoomed": zoomed}
            if zoomed:
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE, so the new size sticks
            m = info.rcMonitor
            user32.SetWindowPos(hwnd, 0, m.left, m.top, m.right - m.left, m.bottom - m.top, SWP_NOZORDER)
            return {"ok": True}
        saved = _fullscreen_restore
        if saved is None:
            return {"ok": True}
        placement = saved["placement"]
        placement.showCmd = 3 if saved["zoomed"] else 1  # SW_SHOWMAXIMIZED / SW_SHOWNORMAL
        user32.SetWindowPlacement(ctypes.c_void_p(hwnd), ctypes.byref(placement))
        _fullscreen_restore = None
        return {"ok": True}

    def open_url(self, url: str) -> dict[str, Any]:
        return cs2_inventory.open_url(url)

    def stop(self) -> None:
        _flush_window_state()
        cs2_inventory.shutdown()
        self._controller.stop()
        for window in list(webview.windows):
            try:
                window.destroy()
            except Exception:
                pass

    def load_ui_settings(self) -> dict[str, Any]:
        try:
            if not UI_SETTINGS_PATH.is_file():
                return {"ok": True, "data": {}}
            parsed = json.loads(UI_SETTINGS_PATH.read_text(encoding="utf-8"))
            if not isinstance(parsed, dict):
                return {"ok": True, "data": {}}
            return {"ok": True, "data": _clean_ui_settings(parsed)}
        except (OSError, json.JSONDecodeError):
            return {"ok": True, "data": {}}

    def save_ui_settings(self, payload: str) -> dict[str, Any]:
        try:
            parsed = json.loads(payload)
            if not isinstance(parsed, dict):
                return {"ok": False, "error": "Invalid settings"}
            clean = _clean_ui_settings(parsed)
            UI_SETTINGS_PATH.write_text(json.dumps(clean, indent=2) + "\n", encoding="utf-8")
            return {"ok": True}
        except (OSError, json.JSONDecodeError) as exc:
            return {"ok": False, "error": str(exc) or "Could not save UI settings"}

    def window_minimize(self) -> dict[str, Any]:
        windows = list(webview.windows)
        if windows:
            windows[0].minimize()
        return {"ok": True}

    def window_toggle_max(self) -> dict[str, Any]:
        global _maximized
        win = _app_window()
        if not win:
            return {"ok": False, "maximized": False}
        if self._maximized:
            self._maximized = False
            _maximized = False
            try:
                win.restore()
            except Exception:
                pass
            with _geometry_lock:
                x = int(_geometry.get("x") if _geometry.get("x") is not None else DEFAULT_WINDOW["x"])
                y = int(_geometry.get("y") if _geometry.get("y") is not None else DEFAULT_WINDOW["y"])
                width = max(MIN_WINDOW[0], int(_geometry.get("width") or DEFAULT_WINDOW["width"]))
                height = max(MIN_WINDOW[1], int(_geometry.get("height") or DEFAULT_WINDOW["height"]))
            hwnd = _hwnd() or int(getattr(self._controller, "hwnd", 0) or 0)
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 1)  # SW_SHOWNORMAL
                ctypes.windll.user32.SetWindowPos(
                    hwnd, 0, x, y, width, height, SWP_NOZORDER | SWP_NOACTIVATE
                )
                if hasattr(self._controller, "_menu_was_zoomed"):
                    self._controller._menu_was_zoomed = False
        else:
            _sync_geometry_from_hwnd()
            self._maximized = True
            _maximized = True
            try:
                win.maximize()
            except Exception:
                pass
        with _geometry_lock:
            _geometry["maximized"] = self._maximized
        _schedule_window_state()
        return {"ok": True, "maximized": self._maximized}

    def window_state(self) -> dict[str, Any]:
        return {"ok": True, "maximized": self._maximized}

    def window_close(self) -> dict[str, Any]:
        self.stop()
        return {"ok": True}

    def window_geometry(self) -> dict[str, Any]:
        hwnd = _hwnd() or int(getattr(self._controller, "hwnd", 0) or 0)
        if not hwnd:
            return {"ok": False}
        rect = _RECT()
        if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return {"ok": False}
        return {
            "ok": True,
            "x": int(rect.left),
            "y": int(rect.top),
            "width": int(rect.right - rect.left),
            "height": int(rect.bottom - rect.top),
        }

    def window_move(self, x: int, y: int) -> dict[str, Any]:
        hwnd = _hwnd() or int(getattr(self._controller, "hwnd", 0) or 0)
        if not hwnd:
            return {"ok": False}
        ctypes.windll.user32.SetWindowPos(
            hwnd,
            0,
            int(x),
            int(y),
            0,
            0,
            SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE,
        )
        _sync_geometry_from_hwnd()
        _schedule_window_state()
        return {"ok": True}

    def window_set_bounds(self, x: int, y: int, width: int, height: int) -> dict[str, Any]:
        hwnd = _hwnd() or int(getattr(self._controller, "hwnd", 0) or 0)
        if not hwnd:
            return {"ok": False}
        width = max(MIN_WINDOW[0], int(width))
        height = max(MIN_WINDOW[1], int(height))
        ctypes.windll.user32.SetWindowPos(
            hwnd,
            0,
            int(x),
            int(y),
            width,
            height,
            SWP_NOZORDER | SWP_NOACTIVATE,
        )
        _sync_geometry_from_hwnd()
        _schedule_window_state()
        return {"ok": True}


def _start_hang_watchdog(controller: Any) -> None:
    """If the window stops responding, write every Python thread's stack to logs/argus-hang.txt.

    Two layers: this thread checks the window each second and dumps stacks after
    ~3 s without an answer; faulthandler dumps without needing the GIL if even
    this thread cannot run for 20 s.
    """
    SMTO_ABORTIFHUNG = 0x0002
    user32 = ctypes.windll.user32
    try:
        HANG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        stream = open(HANG_LOG_PATH, "a", encoding="utf-8")
    except OSError:
        return

    def dump(reason: str) -> None:
        names = {t.ident: t.name for t in threading.enumerate()}
        stream.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} {reason} =====\n")
        for ident, frame in sys._current_frames().items():
            stream.write(f"\n--- thread {names.get(ident, ident)} ---\n")
            stream.write("".join(traceback.format_stack(frame)))
        stream.flush()

    def loop() -> None:
        misses = 0
        dumped = False
        while True:
            faulthandler.dump_traceback_later(20, file=stream)
            time.sleep(1.0)
            hwnd = int(getattr(controller, "hwnd", 0) or 0)
            if not hwnd:
                continue
            result = ctypes.c_size_t()
            if user32.SendMessageTimeoutW(hwnd, 0, 0, 0, SMTO_ABORTIFHUNG, 2000, ctypes.byref(result)):
                misses, dumped = 0, False
                continue
            misses += 1
            if misses >= 2 and not dumped:
                dumped = True
                dump("window not responding")

    threading.Thread(target=loop, name="hang-watchdog", daemon=True).start()


def start_ui(controller: Any) -> None:
    global _geometry, _maximized
    if not UI_INDEX.is_file():
        raise FileNotFoundError(f"Settings UI not found: {UI_INDEX}")

    os.environ.setdefault(
        "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS",
        "--disable-features=CalculateNativeWinOcclusion",
    )

    state = _load_window_state()
    with _geometry_lock:
        _geometry = dict(state)
    _maximized = bool(state.get("maximized"))

    api = SettingsApi(controller)
    api._maximized = _maximized

    create_args: dict[str, Any] = {
        "title": WINDOW_TITLE,
        "url": UI_INDEX.as_uri(),
        "js_api": api,
        "width": int(state["width"]),
        "height": int(state["height"]),
        "min_size": MIN_WINDOW,
        "frameless": True,
        "easy_drag": False,
        "on_top": True,
        "resizable": True,
    }
    x = int(state.get("x", DEFAULT_WINDOW["x"]))
    y = int(state.get("y", DEFAULT_WINDOW["y"]))
    if _on_screen(x, y, int(state["width"]), int(state["height"])):
        create_args["x"] = x
        create_args["y"] = y
    else:
        create_args["x"] = DEFAULT_WINDOW["x"]
        create_args["y"] = DEFAULT_WINDOW["y"]

    window = webview.create_window(**create_args)
    _start_hang_watchdog(controller)

    def _capture_hwnd() -> None:
        hwnd = _hwnd()
        if hwnd:
            controller.hwnd = hwnd

    def _restore_maximized() -> None:
        _capture_hwnd()
        if not _maximized:
            return
        try:
            window.maximize()
        except Exception:
            pass

    def on_closed() -> None:
        _flush_window_state()
        controller.hwnd = 0
        cs2_inventory.shutdown()
        controller.stop()

    window.events.shown += _restore_maximized
    window.events.loaded += _restore_maximized
    window.events.resized += _on_window_resized
    window.events.moved += _on_window_moved
    window.events.closed += on_closed
    icon_path = Path(__file__).resolve().parent / "icons" / "argus.ico"
    # Persistent storage keeps the Steam sign-in for the CS2 inventory module.
    webview.start(
        debug=False,
        private_mode=False,
        storage_path=str(WEBVIEW_DATA_DIR),
        icon=str(icon_path) if icon_path.exists() else None,
    )
