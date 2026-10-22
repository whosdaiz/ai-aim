
"""
Execution module: orchestrates screen capture, target tracking, pointer filtering,
Raw Input mouse capture, and SendInput dispatch for adaptive cursor research.
"""

from __future__ import annotations

import argparse
import json
import ctypes
import ctypes.wintypes
import math
import random
import sys
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

import numpy as np

from overlay import OverlayBuffer, OverlayRenderer, OverlayWorld
from prediction import TargetPredictor
from pointer_filter import (
    APPROACH_FADE_END_PX,
    APPROACH_FADE_START_PX,
    CLOSE_LIMIT,
    COS_ALIGN_THRESHOLD,
    GAIN_COEFF,
    LPF_CUTOFF_HZ,
    OVERSHOOT_LIMIT,
    TARGET_DEADZONE_PX,
    TARGET_FADE_RADIUS_PX,
    TRACK_COEFF,
    TRACK_RADIUS_PX,
    STICKINESS,
    STICKY_RADIUS_PX,
    ACQUIRE_RAMP_MS,
    VELOCITY_REF_PX,
    HUMAN_AIM_STOP_RADIUS_PX,
    HumanAimResult,
    aim_corrector_vector,
    aim_director_vector,
    resolve_aim_curve,
    spray_acquire_vector,
    tracking_retention_vector,
)
import humanizer_paths
import humanizer_reaction
import humanizer_shaper
from latency import LatencyStats
from vision_tracker import (
    AIM_BONE_FROM_TOP,
    BONE_ALIASES,
    AIM_LOCK_PX,
    AIM_MODES,
    DEFAULT_FOV_PX,
    ROI_CENTER,
    ROI_SIZE,
    TRIGGER_BONES,
    AimBox,
    ScreenCapture,
    VisionTracker,
    clamp_fov_radius,
    hitbox_point,
    resolve_aim_offset,
    bbox_iou,
)

from logger import (
    MODULE_TAGS,
    STATUS_INTERVAL_S,
    error as log_error,
    log,
    set_enabled,
    start as start_logger,
    stop as stop_logger,
)

# --- Windows constants --------------------------------------------------------

WM_INPUT = 0x00FF
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_QUIT = 0x0012
RIM_TYPEMOUSE = 0
RIDEV_INPUTSINK = 0x00000100
RIDEV_REMOVE = 0x00000001
MOUSE_MOVE_RELATIVE = 0x00
MOUSE_FPS_MIN = 30
MOUSE_FPS_MAX = 600
RAW_INPUT_CLASS = "ArgusRawInputWindow"

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_MOVE_NOCOALESCE = 0x2000
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
VK_MBUTTON = 0x04
VK_XBUTTON1 = 0x05
VK_XBUTTON2 = 0x06
VK_INSERT = 0x2D
TAP_INJECT_IGNORE_S = 0.08

PROJECT_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = PROJECT_DIR / "settings.json"
CONFIGS_DIR = PROJECT_DIR / "configs"
CONFIG_ACTIVE_PATH = PROJECT_DIR / "active-config.json"
DEFAULT_CONFIG_NAME = "Default"
CONFIG_NAME_MAX = 32
MODELS_DIR = PROJECT_DIR / "models"
MENU_HOTKEY_DEFAULT = "insert"

GAME_META = {
    "aimlabs": {"name": "Aimlabs", "art": "assets/aimlabs.jpg"},
    "apex legends": {
        "name": "Apex Legends",
        "art": "assets/apex.jpg",
        "engine": "apex legends.engine",
    },
    "cs2": {"name": "Counter-Strike 2", "art": "assets/cs2.png"},
    "deadlock": {"name": "Deadlock", "art": "assets/deadlock.png", "engine": "deadlock.engine"},
    "fortnite": {"name": "Fortnite", "art": "assets/fortnite.jpg"},
    "pubg": {"name": "PUBG", "art": "assets/pubg.jpg"},
    "r6": {"name": "Rainbow Six", "art": "assets/r6.jpg"},
    "roblox": {"name": "Roblox", "art": "assets/roblox.jpg"},
    "rust": {"name": "Rust", "art": "assets/rust.jpg"},
    "valorant": {"name": "Valorant", "art": "assets/valorant.jpg"},
    "wz": {"name": "COD Warzone", "art": "assets/CODWZ.jpg", "engine": "WZ.engine"},
    "universal": {"name": "Universal", "art": "", "generic": True},
}


def _discover_games() -> dict[str, dict[str, Any]]:
    games: dict[str, dict[str, Any]] = {}
    stems: dict[str, str] = {}
    if MODELS_DIR.is_dir():
        for path in MODELS_DIR.glob("*.onnx"):
            stems[path.stem.lower()] = path.stem
        # The built engine is what capture loads. Its filename wins over the ONNX stem.
        for path in MODELS_DIR.glob("*.engine"):
            stems[path.stem.lower()] = path.stem
    if stems:
        for game_id, stem in stems.items():
            meta = GAME_META.get(game_id, {})
            games[game_id] = {
                "name": str(meta.get("name") or stem),
                "engine": str(meta.get("engine") or f"{stem}.engine"),
                "art": str(meta.get("art") or ""),
                "generic": bool(meta.get("generic") or game_id == "universal"),
            }
        return games
    for game_id, meta in GAME_META.items():
        stem = {
            "cs2": "CS2",
            "pubg": "PUBG",
            "r6": "R6",
            "wz": "WZ",
        }.get(game_id, game_id.capitalize())
        games[game_id] = {
            "name": str(meta.get("name") or stem),
            "engine": str(meta.get("engine") or f"{stem}.engine"),
            "art": str(meta.get("art") or ""),
            "generic": bool(meta.get("generic") or game_id == "universal"),
        }
    return games


GAMES = _discover_games()


def games_for_ui() -> list[dict[str, Any]]:
    items = [
        {"id": game_id, **spec, "cal_sens": game_cal_sens(game_id)}
        for game_id, spec in GAMES.items()
    ]
    items.sort(key=lambda item: (bool(item.get("generic")), str(item.get("name") or "").lower()))
    return items


def resolve_game_engine(game_id: str) -> str:
    spec = GAMES.get(str(game_id).strip().lower())
    if spec is None:
        return ""
    return str((MODELS_DIR / spec["engine"]).resolve())


def game_id_from_engine(path: str) -> str:
    name = Path(path or "").name.lower()
    for game_id, spec in GAMES.items():
        if spec["engine"].lower() == name:
            return game_id
    return ""

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
LRESULT = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
WPARAM = ctypes.wintypes.WPARAM
LPARAM = ctypes.wintypes.LPARAM
HWND = ctypes.wintypes.HWND


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", ctypes.c_ushort),
        ("usUsage", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("hwndTarget", ctypes.c_void_p),
    ]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", ctypes.c_ulong),
        ("dwSize", ctypes.c_ulong),
        ("hDevice", ctypes.c_void_p),
        ("wParam", WPARAM),
    ]


class RAWMOUSE(ctypes.Structure):
    class _U1(ctypes.Union):
        class _S1(ctypes.Structure):
            _fields_ = [("usButtonFlags", ctypes.c_ushort), ("usButtonData", ctypes.c_ushort)]

        _fields_ = [("ulButtons", ctypes.c_ulong), ("_s1", _S1)]

    _fields_ = [
        ("usFlags", ctypes.c_ushort),
        ("_u1", _U1),
        ("ulRawButtons", ctypes.c_ulong),
        ("lLastX", ctypes.c_long),
        ("lLastY", ctypes.c_long),
        ("ulExtraInformation", ctypes.c_ulong),
    ]


class RAWINPUT(ctypes.Structure):
    class Data(ctypes.Union):
        _fields_ = [("mouse", RAWMOUSE)]

    _anonymous_ = ("data",)
    _fields_ = [("header", RAWINPUTHEADER), ("data", Data)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_ushort),
        ("wParamH", ctypes.c_ushort),
    ]


class INPUT(ctypes.Structure):
    class _I(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    _fields_ = [("type", ctypes.c_ulong), ("_i", _I)]


WNDPROC = ctypes.WINFUNCTYPE(LRESULT, HWND, ctypes.c_uint, WPARAM, LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", ctypes.c_uint),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", ctypes.c_void_p),
        ("hIcon", ctypes.c_void_p),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", ctypes.c_void_p),
        ("lpszMenuName", ctypes.c_wchar_p),
        ("lpszClassName", ctypes.c_wchar_p),
    ]


def _clamp_mouse_fps(value: float) -> int:
    return max(MOUSE_FPS_MIN, min(MOUSE_FPS_MAX, int(round(float(value)))))


@dataclass
class AppConfig:
    engine_path: str = ""
    target_fps: int = 240
    conf_threshold: float = 0.30
    target_class_ids: Optional[tuple[int, ...]] = None
    cos_align_threshold: float = COS_ALIGN_THRESHOLD
    gain_coeff: float = GAIN_COEFF
    lpf_cutoff_hz: float = LPF_CUTOFF_HZ
    marker_hsv_lower: tuple[int, int, int] = (0, 120, 120)
    marker_hsv_upper: tuple[int, int, int] = (10, 255, 255)
    dry_run: bool = False
    output_idx: int = 0
    max_assist_px: int = 40
    assist_scale: float = 1.0
    verify_inject: bool = False
    target_deadzone_px: float = TARGET_DEADZONE_PX
    target_fade_radius_px: float = TARGET_FADE_RADIUS_PX
    velocity_ref_px: float = VELOCITY_REF_PX
    overshoot_limit: float = OVERSHOOT_LIMIT
    track_coeff: float = TRACK_COEFF
    track_radius_px: float = TRACK_RADIUS_PX
    approach_fade_start_px: float = APPROACH_FADE_START_PX
    approach_fade_end_px: float = APPROACH_FADE_END_PX
    close_limit: float = CLOSE_LIMIT
    stickiness: float = STICKINESS
    sticky_radius_px: float = STICKY_RADIUS_PX
    acquire_ramp_ms: float = ACQUIRE_RAMP_MS
    hold_trigger_vk: Optional[int] = None
    hold_trigger_name: Optional[str] = None
    trigger: bool = True
    trigger_radius_px: float = TARGET_DEADZONE_PX
    trigger_interval_ms: float = 295.0
    trigger_interval_max_ms: float = 360.0
    trigger_initial_delay_ms: float = 0.0
    # Draw the first-shot delay from the user's own recorded click reactions
    # (Humanizer) instead of using trigger_initial_delay_ms.
    trigger_humanized_delay: bool = False
    trigger_bones: tuple[str, ...] = ("head", "neck", "chest")
    trigger_humanizer: float = 0.0
    magnet: bool = False
    magnet_vk: Optional[int] = None
    magnet_key_name: Optional[str] = None
    magnet_speed: float = 17.0
    magnet_smooth: float = 40.0
    magnet_strength: float = 100.0
    magnet_radius_px: float = 80.0
    magnet_bones: tuple[str, ...] = ("head", "neck", "chest")
    magnet_humanizer: float = 0.0
    magnet_trigger_radius_px: float = TARGET_DEADZONE_PX
    magnet_trigger_interval_ms: float = 295.0
    magnet_trigger_interval_max_ms: float = 360.0
    magnet_trigger_initial_delay_ms: float = 0.0
    magnet_trigger_humanized_delay: bool = False
    magnet_trigger_bones: tuple[str, ...] = ("head", "neck", "chest")
    tap_assist: bool = True
    tap_on_trigger: bool = False
    tap_max_nudge_px: float = 10.0
    tap_range_px: float = 48.0
    tap_bone: str = "chest"
    tap_humanizer: float = 0.0
    aim_director: bool = False
    aim_director_radius_px: float = 120.0
    aim_director_strength: float = 12.0
    aim_director_max_step_px: float = 2.0
    aim_director_curve: str = "ease_out"
    aim_director_deadzone: bool = True
    aim_director_deadzone_px: float = 10.0
    aim_director_bones: tuple[str, ...] = ("chest",)
    aim_director_mode: str = "always"
    aim_director_key_name: Optional[str] = None
    aim_director_vk: Optional[int] = None
    aim_director_humanizer: float = 0.0
    aim_corrector: bool = False
    aim_corrector_radius_px: float = 100.0
    aim_corrector_strength: float = 18.0
    aim_corrector_max_bend_deg: float = 7.0
    aim_corrector_bone: str = "chest"
    aim_corrector_humanizer: float = 0.0
    tracking_assist: bool = False
    tracking_assist_radius_px: float = 34.0
    tracking_assist_strength: float = 12.0
    tracking_assist_breakaway_px: float = 9.0
    tracking_assist_dwell_ms: float = 100.0
    tracking_assist_humanizer: float = 0.0
    spray_acquire: bool = False
    spray_acquire_radius_px: float = 90.0
    spray_acquire_strength: float = 25.0
    spray_acquire_max_step_px: float = 2.0
    spray_switch_delay_ms: float = 0.0
    spray_switch_delay_max_ms: float = 0.0
    spray_acquire_humanizer: float = 0.0
    aim_mode: str = "closest"
    fov_px: float = DEFAULT_FOV_PX
    visuals: bool = True
    vis_target_box: bool = False
    vis_aim_director_fov: bool = False
    vis_aim_director_spot: bool = False
    vis_aim_corrector_fov: bool = False
    vis_aim_corrector_spot: bool = False
    vis_tracking_assist_fov: bool = False
    vis_tracking_assist_spot: bool = False
    vis_spray_acquire_fov: bool = False
    vis_spray_acquire_spot: bool = False
    vis_tap_fov: bool = False
    vis_tap_spot: bool = False
    vis_trigger_fov: bool = False
    vis_trigger_spot: bool = False
    vis_magnet_fov: bool = False
    vis_magnet_spot: bool = False
    offset_fn: Optional[Callable[[float], Tuple[float, float]]] = None
    ui: bool = True
    menu_hotkey_name: str = MENU_HOTKEY_DEFAULT
    menu_hotkey_vk: int = VK_INSERT
    # Pause: holds every module, click and injected move off without stopping
    # capture. The hotkey toggles it (unbound by default); auto-pause does the
    # same for as long as the settings panel is open.
    pause_hotkey_name: str = ""
    pause_hotkey_vk: int = 0
    auto_pause_menu: bool = True
    # Input method: "windows" injects via SendInput, "rp2040" via the Feather's
    # USB HID command channel. All regular modules work on both; the rp_* modules below
    # modify the physical mouse packets inside the firmware, so they require
    # "rp2040" and a mouse plugged into the Feather's USB-A (MITM mode).
    input_method: str = "windows"
    rp2040_port: str = "auto"
    mouse_dpi: float = 800.0
    mouse_sens: float = 1.0
    rp_aim_assist: bool = False
    rp_aim_slowdown: float = 45.0  # % slower while crosshair is on a player
    rp_aim_radius_px: float = 6.0  # extra pixels around each selected bone
    rp_aim_ramp_ms: float = 80.0  # time to ease into full slowdown; 0 is instant
    rp_aim_ramp_out_ms: float = 80.0  # time to ease back to full speed; 0 is instant
    rp_aim_release_on_shot: bool = False
    rp_aim_bones: tuple[str, ...] = ("head", "neck", "chest")
    rp_one_tap_assist: bool = False
    rp_one_tap_fov_px: float = 30.0
    rp_one_tap_reset_fov_px: float = 70.0
    rp_one_tap_speed: float = 2200.0
    rp_one_tap_smooth_pct: float = 65.0
    rp_one_tap_cooldown_ms: float = 280.0
    rp_one_tap_hold_ms: float = 32.0
    rp_one_tap_humanizer: float = 0.0
    vis_rp_one_tap_fov: bool = False
    vis_rp_one_tap_reset: bool = False
    vis_rp_one_tap_spot: bool = False
    vis_rp_aim_fov: bool = False
    vis_rp_aim_spot: bool = False
    logging_enabled: bool = True
    # Extras (ui/inventory.js, ui/stats.js). Saved per config like the aim modules.
    cs2_inventory: bool = False
    cs2_stats: bool = False
    # Target prediction (prediction.py). Leads the aim point by the target's own
    # velocity, with the camera's contribution removed.
    target_prediction: bool = False
    prediction_lead_ms: float = 12.0      # on top of the measured pipeline delay
    prediction_strength: float = 100.0    # percent of the computed lead
    prediction_max_px: float = 60.0
    prediction_track_frames: int = 8
    vis_prediction: bool = False


def _bones_to_str(bones: tuple[str, ...]) -> str:
    return ",".join(bones)


def _parse_bones(value: str) -> tuple[str, ...]:
    parts = [
        p.strip().lower().replace(" ", "_")
        for p in str(value).split(",")
        if p.strip()
    ]
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        name = BONE_ALIASES.get(part, part)
        if name not in TRIGGER_BONES or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return tuple(out)


def _parse_single_bone(value: str, default: str = "chest") -> str:
    bones = _parse_bones(value)
    if bones:
        return bones[0]
    fallback = BONE_ALIASES.get(default, default)
    return fallback if fallback in AIM_BONE_FROM_TOP else "chest"


def _classes_to_str(class_ids: Optional[tuple[int, ...]]) -> str:
    if not class_ids:
        return ""
    return ",".join(str(i) for i in class_ids)


def _parse_classes(value: str) -> Optional[tuple[int, ...]]:
    text = value.strip()
    if not text:
        return None
    return tuple(int(x.strip()) for x in text.split(",") if x.strip())


class LiveConfig:
    """Thread-safe wrapper around AppConfig for live UI updates."""

    def __init__(self, config: AppConfig) -> None:
        self._lock = threading.Lock()
        self._config = config
        self._generation = 0
        set_enabled(config.logging_enabled)

    def get(self) -> AppConfig:
        with self._lock:
            return replace(self._config)

    def generation(self) -> int:
        with self._lock:
            return self._generation

    def to_ui_dict(self) -> dict[str, Any]:
        cfg = self.get()
        return {
            "conf": cfg.conf_threshold,
            "classes": _classes_to_str(cfg.target_class_ids),
            "gain_coeff": cfg.gain_coeff,
            "assist_scale": cfg.assist_scale,
            "max_assist": cfg.max_assist_px,
            "velocity_ref": cfg.velocity_ref_px,
            "overshoot_limit": cfg.overshoot_limit,
            "close_limit": cfg.close_limit,
            "track_coeff": cfg.track_coeff,
            "track_radius": cfg.track_radius_px,
            "target_deadzone": cfg.target_deadzone_px,
            "target_fade_radius": cfg.target_fade_radius_px,
            "approach_fade_start": cfg.approach_fade_start_px,
            "approach_fade_end": cfg.approach_fade_end_px,
            "acquire_ramp_ms": cfg.acquire_ramp_ms,
            "stickiness": cfg.stickiness,
            "sticky_radius": cfg.sticky_radius_px,
            "hold_trigger": cfg.hold_trigger_name or "",
            "trigger_enabled": cfg.trigger,
            "trigger_radius": cfg.trigger_radius_px,
            "trigger_interval_ms": cfg.trigger_interval_ms,
            "trigger_interval_max_ms": cfg.trigger_interval_max_ms,
            "trigger_initial_delay_ms": cfg.trigger_initial_delay_ms,
            "trigger_humanized_delay": cfg.trigger_humanized_delay,
            "trigger_bones": _bones_to_str(cfg.trigger_bones),
            "trigger_humanizer": cfg.trigger_humanizer,
            "magnet": cfg.magnet,
            "magnet_key": cfg.magnet_key_name or "",
            "magnet_speed": cfg.magnet_speed,
            "magnet_smooth": cfg.magnet_smooth,
            "magnet_strength": cfg.magnet_strength,
            "magnet_scale": "px",
            "magnet_radius": cfg.magnet_radius_px,
            "magnet_bones": _bones_to_str(cfg.magnet_bones),
            "magnet_humanizer": cfg.magnet_humanizer,
            "magnet_trigger_radius": cfg.magnet_trigger_radius_px,
            "magnet_trigger_interval_ms": cfg.magnet_trigger_interval_ms,
            "magnet_trigger_interval_max_ms": cfg.magnet_trigger_interval_max_ms,
            "magnet_trigger_initial_delay_ms": cfg.magnet_trigger_initial_delay_ms,
            "magnet_trigger_humanized_delay": cfg.magnet_trigger_humanized_delay,
            "magnet_trigger_bones": _bones_to_str(cfg.magnet_trigger_bones),
            "tap_assist": cfg.tap_assist,
            "tap_on_trigger": cfg.tap_on_trigger,
            "tap_max_nudge": cfg.tap_max_nudge_px,
            "tap_range": cfg.tap_range_px,
            "tap_bone": cfg.tap_bone,
            "tap_humanizer": cfg.tap_humanizer,
            "visuals_enabled": cfg.visuals,
            "logging_enabled": cfg.logging_enabled,
            "cs2_inventory": cfg.cs2_inventory,
            "cs2_stats": cfg.cs2_stats,
            "target_prediction": cfg.target_prediction,
            "prediction_lead_ms": cfg.prediction_lead_ms,
            "prediction_strength": cfg.prediction_strength,
            "prediction_max_px": cfg.prediction_max_px,
            "prediction_track_frames": cfg.prediction_track_frames,
            "vis_prediction": cfg.vis_prediction,
            "aim_director": cfg.aim_director,
            "aim_director_radius": cfg.aim_director_radius_px,
            "aim_director_strength": cfg.aim_director_strength,
            "aim_director_max_step": cfg.aim_director_max_step_px,
            "aim_director_curve": cfg.aim_director_curve,
            "aim_director_deadzone": cfg.aim_director_deadzone,
            "aim_director_deadzone_px": cfg.aim_director_deadzone_px,
            "aim_director_bone": _bones_to_str(cfg.aim_director_bones),
            "aim_director_mode": cfg.aim_director_mode,
            "aim_director_key": cfg.aim_director_key_name or "",
            "aim_director_humanizer": cfg.aim_director_humanizer,
            "aim_corrector": cfg.aim_corrector,
            "aim_corrector_radius": cfg.aim_corrector_radius_px,
            "aim_corrector_strength": cfg.aim_corrector_strength,
            "aim_corrector_max_bend": cfg.aim_corrector_max_bend_deg,
            "aim_corrector_bone": cfg.aim_corrector_bone,
            "aim_corrector_humanizer": cfg.aim_corrector_humanizer,
            "tracking_assist": cfg.tracking_assist,
            "tracking_assist_radius": cfg.tracking_assist_radius_px,
            "tracking_assist_strength": cfg.tracking_assist_strength,
            "tracking_assist_breakaway": cfg.tracking_assist_breakaway_px,
            "tracking_assist_dwell_ms": cfg.tracking_assist_dwell_ms,
            "tracking_assist_humanizer": cfg.tracking_assist_humanizer,
            "spray_acquire": cfg.spray_acquire,
            "spray_acquire_radius": cfg.spray_acquire_radius_px,
            "spray_acquire_strength": cfg.spray_acquire_strength,
            "spray_acquire_max_step": cfg.spray_acquire_max_step_px,
            "spray_switch_delay_ms": cfg.spray_switch_delay_ms,
            "spray_switch_delay_max_ms": cfg.spray_switch_delay_max_ms,
            "spray_acquire_humanizer": cfg.spray_acquire_humanizer,
            "aim_mode": cfg.aim_mode,
            "fov": cfg.fov_px,
            "vis_target_box": cfg.vis_target_box,
            "vis_aim_director_fov": cfg.vis_aim_director_fov,
            "vis_aim_director_spot": cfg.vis_aim_director_spot,
            "vis_aim_corrector_fov": cfg.vis_aim_corrector_fov,
            "vis_aim_corrector_spot": cfg.vis_aim_corrector_spot,
            "vis_tracking_assist_fov": cfg.vis_tracking_assist_fov,
            "vis_tracking_assist_spot": cfg.vis_tracking_assist_spot,
            "vis_spray_acquire_fov": cfg.vis_spray_acquire_fov,
            "vis_spray_acquire_spot": cfg.vis_spray_acquire_spot,
            "vis_tap_fov": cfg.vis_tap_fov,
            "vis_tap_spot": cfg.vis_tap_spot,
            "vis_trigger_fov": cfg.vis_trigger_fov,
            "vis_trigger_spot": cfg.vis_trigger_spot,
            "vis_magnet_fov": cfg.vis_magnet_fov,
            "vis_magnet_spot": cfg.vis_magnet_spot,
            "dry_run": cfg.dry_run,
            "engine": cfg.engine_path,
            "game": game_id_from_engine(cfg.engine_path),
            "fps": cfg.target_fps,
            "output_idx": cfg.output_idx,
            "menu_hotkey": cfg.menu_hotkey_name,
            "pause_hotkey": cfg.pause_hotkey_name,
            "auto_pause_menu": cfg.auto_pause_menu,
            "input_method": cfg.input_method,
            "rp2040_port": cfg.rp2040_port,
            "mouse_dpi": cfg.mouse_dpi,
            "mouse_sens": cfg.mouse_sens,
            "rp_aim_assist": cfg.rp_aim_assist,
            "rp_aim_slowdown": cfg.rp_aim_slowdown,
            "rp_aim_radius": cfg.rp_aim_radius_px,
            "rp_aim_ramp": cfg.rp_aim_ramp_ms,
            "rp_aim_ramp_out": cfg.rp_aim_ramp_out_ms,
            "rp_aim_release_on_shot": cfg.rp_aim_release_on_shot,
            "rp_aim_bones": _bones_to_str(cfg.rp_aim_bones),
            "rp_one_tap_assist": cfg.rp_one_tap_assist,
            "rp_one_tap_fov": cfg.rp_one_tap_fov_px,
            "rp_one_tap_reset_fov": cfg.rp_one_tap_reset_fov_px,
            "rp_one_tap_speed": cfg.rp_one_tap_speed,
            "rp_one_tap_smooth": cfg.rp_one_tap_smooth_pct,
            "rp_one_tap_cooldown": cfg.rp_one_tap_cooldown_ms,
            "rp_one_tap_hold": cfg.rp_one_tap_hold_ms,
            "rp_one_tap_humanizer": cfg.rp_one_tap_humanizer,
            "humanizer_ready": _humanizer_ready(),
            "vis_rp_one_tap_fov": cfg.vis_rp_one_tap_fov,
            "vis_rp_one_tap_reset": cfg.vis_rp_one_tap_reset,
            "vis_rp_one_tap_spot": cfg.vis_rp_one_tap_spot,
            "vis_rp_aim_fov": cfg.vis_rp_aim_fov,
            "vis_rp_aim_spot": cfg.vis_rp_aim_spot,
        }

    def save(self) -> None:
        SETTINGS_PATH.write_text(
            json.dumps(_persistable(self.to_ui_dict()), indent=2), encoding="utf-8"
        )


    def update(self, updates: dict[str, Any]) -> Optional[str]:
        """Apply a partial settings dict. Returns an error string, or None on success."""
        with self._lock:
            cfg = self._config
            try:
                if "conf" in updates:
                    cfg.conf_threshold = max(0.0, min(1.0, float(updates["conf"])))
                if "classes" in updates:
                    cfg.target_class_ids = _parse_classes(str(updates["classes"]))
                if "gain_coeff" in updates:
                    cfg.gain_coeff = max(0.0, min(3.0, float(updates["gain_coeff"])))
                if "assist_scale" in updates:
                    cfg.assist_scale = max(0.0, min(8.0, float(updates["assist_scale"])))
                if "max_assist" in updates:
                    cfg.max_assist_px = max(0, int(round(float(updates["max_assist"]))))
                if "velocity_ref" in updates:
                    cfg.velocity_ref_px = max(0.0, float(updates["velocity_ref"]))
                if "overshoot_limit" in updates:
                    cfg.overshoot_limit = max(0.0, min(1.0, float(updates["overshoot_limit"])))
                if "close_limit" in updates:
                    cfg.close_limit = max(0.0, min(1.0, float(updates["close_limit"])))
                if "track_coeff" in updates:
                    cfg.track_coeff = max(0.0, min(1.5, float(updates["track_coeff"])))
                if "track_radius" in updates:
                    cfg.track_radius_px = max(0.0, float(updates["track_radius"]))
                if "target_deadzone" in updates:
                    cfg.target_deadzone_px = max(0.0, float(updates["target_deadzone"]))
                if "target_fade_radius" in updates:
                    cfg.target_fade_radius_px = max(0.0, float(updates["target_fade_radius"]))
                if "approach_fade_start" in updates:
                    cfg.approach_fade_start_px = max(0.0, float(updates["approach_fade_start"]))
                if "approach_fade_end" in updates:
                    cfg.approach_fade_end_px = max(0.0, float(updates["approach_fade_end"]))
                if "acquire_ramp_ms" in updates:
                    cfg.acquire_ramp_ms = max(0.0, float(updates["acquire_ramp_ms"]))
                if "stickiness" in updates:
                    cfg.stickiness = max(0.0, min(5.0, float(updates["stickiness"])))
                if "sticky_radius" in updates:
                    cfg.sticky_radius_px = max(0.0, float(updates["sticky_radius"]))
                if "hold_trigger" in updates:
                    name = str(updates["hold_trigger"]).strip()
                    if not name:
                        cfg.hold_trigger_name = None
                        cfg.hold_trigger_vk = None
                    else:
                        cfg.hold_trigger_vk = _parse_hold_key(name)
                        cfg.hold_trigger_name = name
                if "trigger_enabled" in updates:
                    cfg.trigger = bool(updates["trigger_enabled"])
                if "trigger" in updates:
                    cfg.trigger = bool(updates["trigger"])
                if "magnet" in updates:
                    cfg.magnet = bool(updates["magnet"])
                if "visuals_enabled" in updates:
                    cfg.visuals = bool(updates["visuals_enabled"])
                if "logging_enabled" in updates:
                    cfg.logging_enabled = bool(updates["logging_enabled"])
                if "cs2_inventory" in updates:
                    cfg.cs2_inventory = bool(updates["cs2_inventory"])
                if "cs2_stats" in updates:
                    cfg.cs2_stats = bool(updates["cs2_stats"])
                if "target_prediction" in updates:
                    cfg.target_prediction = bool(updates["target_prediction"])
                if "prediction_lead_ms" in updates:
                    cfg.prediction_lead_ms = max(
                        0.0, min(120.0, float(updates["prediction_lead_ms"]))
                    )
                if "prediction_strength" in updates:
                    cfg.prediction_strength = max(
                        0.0, min(150.0, float(updates["prediction_strength"]))
                    )
                if "prediction_max_px" in updates:
                    cfg.prediction_max_px = max(
                        4.0, min(200.0, float(updates["prediction_max_px"]))
                    )
                if "prediction_track_frames" in updates:
                    cfg.prediction_track_frames = int(
                        max(4, min(20, float(updates["prediction_track_frames"])))
                    )
                if "vis_prediction" in updates:
                    cfg.vis_prediction = bool(updates["vis_prediction"])
                if "aim_director" in updates:
                    cfg.aim_director = bool(updates["aim_director"])
                if "aim_director_radius" in updates:
                    cfg.aim_director_radius_px = max(
                        0.0, min(320.0, float(updates["aim_director_radius"]))
                    )
                if "aim_director_strength" in updates:
                    cfg.aim_director_strength = max(
                        0.0, min(100.0, float(updates["aim_director_strength"]))
                    )
                if "aim_director_max_step" in updates:
                    cfg.aim_director_max_step_px = max(
                        0.0, min(100.0, float(updates["aim_director_max_step"]))
                    )
                if "aim_director_curve" in updates:
                    cfg.aim_director_curve = resolve_aim_curve(
                        str(updates["aim_director_curve"])
                    )
                if "aim_director_deadzone" in updates:
                    cfg.aim_director_deadzone = bool(updates["aim_director_deadzone"])
                if "aim_director_deadzone_px" in updates:
                    cfg.aim_director_deadzone_px = max(
                        0.0, min(60.0, float(updates["aim_director_deadzone_px"]))
                    )
                if "aim_director_bone" in updates:
                    cfg.aim_director_bones = _parse_bones(str(updates["aim_director_bone"]))
                if "aim_director_mode" in updates:
                    mode = str(updates["aim_director_mode"]).strip().lower()
                    if mode not in AIM_DIRECTOR_MODES:
                        return "aim_director_mode must be always, toggle, or hold"
                    cfg.aim_director_mode = mode
                if "aim_director_key" in updates:
                    name = str(updates["aim_director_key"]).strip()
                    if not name:
                        cfg.aim_director_key_name = None
                        cfg.aim_director_vk = None
                    else:
                        cfg.aim_director_vk = _parse_hold_key(name)
                        cfg.aim_director_key_name = name
                if "aim_corrector" in updates:
                    cfg.aim_corrector = bool(updates["aim_corrector"])
                if "aim_corrector_radius" in updates:
                    cfg.aim_corrector_radius_px = max(
                        0.0, min(320.0, float(updates["aim_corrector_radius"]))
                    )
                if "aim_corrector_strength" in updates:
                    cfg.aim_corrector_strength = max(
                        0.0, min(50.0, float(updates["aim_corrector_strength"]))
                    )
                if "aim_corrector_max_bend" in updates:
                    cfg.aim_corrector_max_bend_deg = max(
                        0.0, min(45.0, float(updates["aim_corrector_max_bend"]))
                    )
                if "aim_corrector_bone" in updates:
                    cfg.aim_corrector_bone = _parse_single_bone(
                        str(updates["aim_corrector_bone"])
                    )
                if "tracking_assist" in updates:
                    cfg.tracking_assist = bool(updates["tracking_assist"])
                if "tracking_assist_radius" in updates:
                    cfg.tracking_assist_radius_px = max(
                        0.0, min(160.0, float(updates["tracking_assist_radius"]))
                    )
                if "tracking_assist_strength" in updates:
                    cfg.tracking_assist_strength = max(
                        0.0, min(50.0, float(updates["tracking_assist_strength"]))
                    )
                if "tracking_assist_breakaway" in updates:
                    cfg.tracking_assist_breakaway_px = max(
                        0.0, min(500.0, float(updates["tracking_assist_breakaway"]))
                    )
                if "tracking_assist_dwell_ms" in updates:
                    cfg.tracking_assist_dwell_ms = max(
                        0.0, min(1000.0, float(updates["tracking_assist_dwell_ms"]))
                    )
                if "spray_acquire" in updates:
                    cfg.spray_acquire = bool(updates["spray_acquire"])
                if "spray_acquire_radius" in updates:
                    cfg.spray_acquire_radius_px = max(
                        0.0, min(320.0, float(updates["spray_acquire_radius"]))
                    )
                if "spray_acquire_strength" in updates:
                    cfg.spray_acquire_strength = max(
                        0.0, min(100.0, float(updates["spray_acquire_strength"]))
                    )
                if "spray_acquire_max_step" in updates:
                    cfg.spray_acquire_max_step_px = max(
                        0.0, min(100.0, float(updates["spray_acquire_max_step"]))
                    )
                if "spray_switch_delay_ms" in updates:
                    cfg.spray_switch_delay_ms = max(0.0, float(updates["spray_switch_delay_ms"]))
                if "spray_switch_delay_max_ms" in updates:
                    cfg.spray_switch_delay_max_ms = max(
                        0.0, float(updates["spray_switch_delay_max_ms"])
                    )
                if "magnet_key" in updates:
                    name = str(updates["magnet_key"]).strip()
                    if not name:
                        cfg.magnet_key_name = None
                        cfg.magnet_vk = None
                    else:
                        cfg.magnet_vk = _parse_hold_key(name)
                        cfg.magnet_key_name = name
                if "magnet_speed" in updates:
                    cfg.magnet_speed = max(0.0, min(100.0, float(updates["magnet_speed"])))
                if "magnet_smooth" in updates:
                    cfg.magnet_smooth = max(0.0, min(100.0, float(updates["magnet_smooth"])))
                if "magnet_strength" in updates:
                    cfg.magnet_strength = max(0.0, min(100.0, float(updates["magnet_strength"])))
                if "magnet_radius" in updates:
                    cfg.magnet_radius_px = max(0.0, float(updates["magnet_radius"]))
                if "magnet_bones" in updates:
                    cfg.magnet_bones = _parse_bones(str(updates["magnet_bones"]))
                if "magnet_trigger_radius" in updates:
                    cfg.magnet_trigger_radius_px = max(0.0, float(updates["magnet_trigger_radius"]))
                if "magnet_trigger_interval_ms" in updates:
                    cfg.magnet_trigger_interval_ms = max(0.0, float(updates["magnet_trigger_interval_ms"]))
                if "magnet_trigger_interval_max_ms" in updates:
                    cfg.magnet_trigger_interval_max_ms = max(
                        0.0, float(updates["magnet_trigger_interval_max_ms"])
                    )
                if "magnet_trigger_initial_delay_ms" in updates:
                    cfg.magnet_trigger_initial_delay_ms = max(
                        0.0, float(updates["magnet_trigger_initial_delay_ms"])
                    )
                if "magnet_trigger_bones" in updates:
                    cfg.magnet_trigger_bones = _parse_bones(str(updates["magnet_trigger_bones"]))
                if "trigger_radius" in updates:
                    cfg.trigger_radius_px = max(0.0, float(updates["trigger_radius"]))
                if "trigger_interval_ms" in updates:
                    cfg.trigger_interval_ms = max(0.0, float(updates["trigger_interval_ms"]))
                if "trigger_interval_max_ms" in updates:
                    cfg.trigger_interval_max_ms = max(0.0, float(updates["trigger_interval_max_ms"]))
                if "trigger_initial_delay_ms" in updates:
                    cfg.trigger_initial_delay_ms = max(0.0, float(updates["trigger_initial_delay_ms"]))
                if "trigger_humanized_delay" in updates:
                    cfg.trigger_humanized_delay = bool(updates["trigger_humanized_delay"])
                if "magnet_trigger_humanized_delay" in updates:
                    cfg.magnet_trigger_humanized_delay = bool(
                        updates["magnet_trigger_humanized_delay"]
                    )
                if "trigger_bones" in updates:
                    cfg.trigger_bones = _parse_bones(str(updates["trigger_bones"]))
                if "tap_assist" in updates:
                    cfg.tap_assist = bool(updates["tap_assist"])
                if "tap_on_trigger" in updates:
                    cfg.tap_on_trigger = bool(updates["tap_on_trigger"])
                if "tap_max_nudge" in updates:
                    cfg.tap_max_nudge_px = max(0.0, min(120.0, float(updates["tap_max_nudge"])))
                if "tap_range" in updates:
                    cfg.tap_range_px = max(0.0, float(updates["tap_range"]))
                if "tap_bone" in updates:
                    cfg.tap_bone = _parse_single_bone(str(updates["tap_bone"]))
                if "aim_mode" in updates:
                    mode = str(updates["aim_mode"]).strip().lower()
                    if mode not in AIM_MODES:
                        return "aim_mode must be head, neck, chest, or closest"
                    cfg.aim_mode = mode
                if "fov" in updates:
                    cfg.fov_px = clamp_fov_radius(float(updates["fov"]))
                if "vis_aim_director_fov" in updates:
                    cfg.vis_aim_director_fov = bool(updates["vis_aim_director_fov"])
                if "vis_target_box" in updates:
                    cfg.vis_target_box = bool(updates["vis_target_box"])
                if "vis_aim_director_spot" in updates:
                    cfg.vis_aim_director_spot = bool(
                        updates["vis_aim_director_spot"]
                    )
                if "vis_aim_corrector_fov" in updates:
                    cfg.vis_aim_corrector_fov = bool(updates["vis_aim_corrector_fov"])
                if "vis_aim_corrector_spot" in updates:
                    cfg.vis_aim_corrector_spot = bool(
                        updates["vis_aim_corrector_spot"]
                    )
                if "vis_tracking_assist_fov" in updates:
                    cfg.vis_tracking_assist_fov = bool(
                        updates["vis_tracking_assist_fov"]
                    )
                if "vis_tracking_assist_spot" in updates:
                    cfg.vis_tracking_assist_spot = bool(
                        updates["vis_tracking_assist_spot"]
                    )
                if "vis_spray_acquire_fov" in updates:
                    cfg.vis_spray_acquire_fov = bool(
                        updates["vis_spray_acquire_fov"]
                    )
                if "vis_spray_acquire_spot" in updates:
                    cfg.vis_spray_acquire_spot = bool(
                        updates["vis_spray_acquire_spot"]
                    )
                if "vis_tap_fov" in updates:
                    cfg.vis_tap_fov = bool(updates["vis_tap_fov"])
                if "vis_tap_spot" in updates:
                    cfg.vis_tap_spot = bool(updates["vis_tap_spot"])
                if "vis_trigger_fov" in updates:
                    cfg.vis_trigger_fov = bool(updates["vis_trigger_fov"])
                if "vis_trigger_spot" in updates:
                    cfg.vis_trigger_spot = bool(updates["vis_trigger_spot"])
                if "vis_magnet_fov" in updates:
                    cfg.vis_magnet_fov = bool(updates["vis_magnet_fov"])
                if "vis_magnet_spot" in updates:
                    cfg.vis_magnet_spot = bool(updates["vis_magnet_spot"])
                if "dry_run" in updates:
                    cfg.dry_run = bool(updates["dry_run"])
                if "game" in updates:
                    game_id = str(updates["game"]).strip().lower()
                    if game_id not in GAMES:
                        return "unknown game"
                    cfg.engine_path = resolve_game_engine(game_id)
                if "engine" in updates:
                    engine = str(updates["engine"]).strip()
                    cfg.engine_path = (
                        str(Path(engine).expanduser().resolve()) if engine else ""
                    )
                if "fps" in updates:
                    cfg.target_fps = _clamp_mouse_fps(updates["fps"])
                if "output_idx" in updates:
                    cfg.output_idx = max(0, int(round(float(updates["output_idx"]))))
                if "menu_hotkey" in updates:
                    name = str(updates["menu_hotkey"]).strip() or MENU_HOTKEY_DEFAULT
                    cfg.menu_hotkey_vk = _parse_hold_key(name)
                    cfg.menu_hotkey_name = name
                if "pause_hotkey" in updates:
                    name = str(updates["pause_hotkey"]).strip()
                    cfg.pause_hotkey_vk = _parse_hold_key(name) if name else 0
                    cfg.pause_hotkey_name = name
                if "auto_pause_menu" in updates:
                    cfg.auto_pause_menu = bool(updates["auto_pause_menu"])
                if "input_method" in updates:
                    method = str(updates["input_method"]).strip().lower()
                    if method not in ("windows", "rp2040"):
                        return "input_method must be windows or rp2040"
                    cfg.input_method = method
                if "rp2040_port" in updates:
                    cfg.rp2040_port = str(updates["rp2040_port"]).strip() or "auto"
                if "mouse_dpi" in updates:
                    cfg.mouse_dpi = max(
                        50.0, min(32000.0, float(updates["mouse_dpi"]))
                    )
                if "mouse_sens" in updates:
                    cfg.mouse_sens = max(
                        MOUSE_SENS_MIN, min(MOUSE_SENS_MAX, float(updates["mouse_sens"]))
                    )
                if "rp_aim_assist" in updates:
                    cfg.rp_aim_assist = bool(updates["rp_aim_assist"])
                if "rp_aim_slowdown" in updates:
                    cfg.rp_aim_slowdown = max(0.0, min(95.0, float(updates["rp_aim_slowdown"])))
                if "rp_aim_radius" in updates:
                    cfg.rp_aim_radius_px = max(0.0, min(60.0, float(updates["rp_aim_radius"])))
                if "rp_aim_ramp" in updates:
                    cfg.rp_aim_ramp_ms = max(0.0, min(200.0, float(updates["rp_aim_ramp"])))
                if "rp_aim_ramp_out" in updates:
                    cfg.rp_aim_ramp_out_ms = max(
                        0.0, min(200.0, float(updates["rp_aim_ramp_out"]))
                    )
                if "rp_aim_release_on_shot" in updates:
                    cfg.rp_aim_release_on_shot = bool(updates["rp_aim_release_on_shot"])
                if "rp_aim_bones" in updates:
                    cfg.rp_aim_bones = _parse_bones(str(updates["rp_aim_bones"]))
                if "rp_one_tap_assist" in updates:
                    cfg.rp_one_tap_assist = bool(updates["rp_one_tap_assist"])
                if "rp_one_tap_fov" in updates:
                    cfg.rp_one_tap_fov_px = max(
                        2.0, min(80.0, float(updates["rp_one_tap_fov"]))
                    )
                if "rp_one_tap_reset_fov" in updates:
                    cfg.rp_one_tap_reset_fov_px = max(
                        4.0, min(160.0, float(updates["rp_one_tap_reset_fov"]))
                    )
                if "rp_one_tap_speed" in updates:
                    cfg.rp_one_tap_speed = max(
                        200.0, min(8000.0, float(updates["rp_one_tap_speed"]))
                    )
                if "rp_one_tap_smooth" in updates:
                    cfg.rp_one_tap_smooth_pct = max(
                        0.0, min(100.0, float(updates["rp_one_tap_smooth"]))
                    )
                if "rp_one_tap_cooldown" in updates:
                    cfg.rp_one_tap_cooldown_ms = max(
                        50.0, min(800.0, float(updates["rp_one_tap_cooldown"]))
                    )
                if "rp_one_tap_hold" in updates:
                    cfg.rp_one_tap_hold_ms = max(
                        20.0, min(60.0, float(updates["rp_one_tap_hold"]))
                    )
                if "vis_rp_one_tap_fov" in updates:
                    cfg.vis_rp_one_tap_fov = bool(updates["vis_rp_one_tap_fov"])
                if "vis_rp_one_tap_reset" in updates:
                    cfg.vis_rp_one_tap_reset = bool(updates["vis_rp_one_tap_reset"])
                if "vis_rp_one_tap_spot" in updates:
                    cfg.vis_rp_one_tap_spot = bool(updates["vis_rp_one_tap_spot"])
                if "vis_rp_aim_fov" in updates:
                    cfg.vis_rp_aim_fov = bool(updates["vis_rp_aim_fov"])
                if "vis_rp_aim_spot" in updates:
                    cfg.vis_rp_aim_spot = bool(updates["vis_rp_aim_spot"])
                cfg.rp_one_tap_reset_fov_px = max(
                    cfg.rp_one_tap_reset_fov_px, cfg.rp_one_tap_fov_px
                )
                for key in HUMANIZER_BLEND_KEYS:
                    if key in updates:
                        setattr(cfg, key, _clamp_percent(updates[key], getattr(cfg, key)))
            except ValueError as exc:
                return str(exc)
            self._generation += 1
        return None


def _clamp_percent(value: Any, fallback: float = 0.0) -> float:
    """A 0-100 slider value from settings or a hand-edited config file.

    Anything unreadable keeps the current value rather than failing the whole
    settings update, which would otherwise take an unrelated slider down with
    it.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    if not math.isfinite(number):
        return float(fallback)
    return max(0.0, min(100.0, number))


def _humanizer_ready() -> bool:
    """True when there are enough recorded runs to shape movement with."""
    return humanizer_paths.get().ok


HUMANIZER_BLEND_KEYS = (
    "aim_director_humanizer",
    "aim_corrector_humanizer",
    "tracking_assist_humanizer",
    "spray_acquire_humanizer",
    "magnet_humanizer",
    "tap_humanizer",
    "trigger_humanizer",
    "rp_one_tap_humanizer",
)


def _normalize_config_name(name: str) -> str:
    return " ".join(str(name or "").split())[:CONFIG_NAME_MAX]


def _config_name_error(name: str) -> Optional[str]:
    if not name:
        return "Enter a config name"
    if any(ch in name for ch in '\\/:*?"<>|'):
        return "Config name cannot contain \\ / : * ? \" < > |"
    return None


def _config_stem(name: str) -> str:
    cleaned = "".join(ch for ch in name if ch not in '\\/:*?"<>|').strip(" .")
    return cleaned[:64] or "config"


# Reported to the UI but never stored: it describes the recorded runs on
# disk, not a setting, so a saved copy would go stale the moment they change.
DERIVED_UI_KEYS = ("humanizer_ready",)


def _persistable(data: dict[str, Any]) -> dict[str, Any]:
    for key in DERIVED_UI_KEYS:
        data.pop(key, None)
    return data


def _config_payload(live: LiveConfig) -> dict[str, Any]:
    data = live.to_ui_dict()
    data.pop("_name", None)
    data.pop("active_config", None)
    return _persistable(data)


def _read_active_config_name() -> str:
    if not CONFIG_ACTIVE_PATH.is_file():
        return ""
    try:
        data = json.loads(CONFIG_ACTIVE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if isinstance(data, dict):
        return _normalize_config_name(str(data.get("name") or ""))
    return ""


def _write_active_config_name(name: str) -> None:
    CONFIG_ACTIVE_PATH.write_text(
        json.dumps({"name": name}, indent=2) + "\n", encoding="utf-8"
    )


def _iter_config_files() -> list[tuple[str, Path, dict[str, Any]]]:
    items: list[tuple[str, Path, dict[str, Any]]] = []
    if not CONFIGS_DIR.is_dir():
        return items
    for path in CONFIGS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        name = _normalize_config_name(str(data.get("_name") or path.stem))
        if not name:
            continue
        items.append((name, path, data))
    items.sort(key=lambda item: item[0].lower())
    return items


def _find_config(name: str) -> Optional[tuple[str, Path, dict[str, Any]]]:
    needle = name.casefold()
    for item in _iter_config_files():
        if item[0].casefold() == needle:
            return item
    return None


def _config_path_for_name(name: str) -> Path:
    existing = _find_config(name)
    if existing:
        return existing[1]
    CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
    path = CONFIGS_DIR / f"{_config_stem(name)}.json"
    if not path.exists():
        return path
    stem = _config_stem(name)
    index = 2
    while True:
        candidate = CONFIGS_DIR / f"{stem} {index}.json"
        if not candidate.exists():
            return candidate
        index += 1


def _write_config_file(name: str, payload: dict[str, Any]) -> Path:
    data = dict(payload)
    data["_name"] = name
    path = _config_path_for_name(name)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def _migrate_interval_ranges(data: dict[str, Any]) -> dict[str, Any]:
    """Saved before intervals had an upper bound: keep them fixed, not random."""
    migrated = dict(data)
    for lo_key, hi_key in (
        ("trigger_interval_ms", "trigger_interval_max_ms"),
        ("magnet_trigger_interval_ms", "magnet_trigger_interval_max_ms"),
    ):
        if lo_key in migrated and hi_key not in migrated:
            migrated[hi_key] = migrated[lo_key]
    return migrated


def _migrate_magnet_units(data: dict[str, Any]) -> dict[str, Any]:
    """Map the old 0–5 speed and 0–1 force/smoothness onto px and percent."""
    if data.get("magnet_scale") == "px":
        return data
    migrated = dict(data)

    def scale(key: str, legacy_max: float, factor: float, new_max: float, digits: int) -> None:
        if key not in migrated:
            return
        try:
            value = float(migrated[key])
        except (TypeError, ValueError):
            return
        if value <= legacy_max:
            value *= factor
        migrated[key] = round(max(0.0, min(new_max, value)), digits)

    scale("magnet_speed", 5.0, 20.0, 100.0, 1)
    scale("magnet_strength", 1.0, 100.0, 100.0, 0)
    scale("magnet_smooth", 1.0, 100.0, 100.0, 0)
    migrated["magnet_scale"] = "px"
    return migrated


# Config switches that are off unless the config file turns them on, so a
# config saved before a module existed never inherits it from the last config.
CONFIG_OFF_BY_DEFAULT = (
    "cs2_inventory",
    "cs2_stats",
    "target_prediction",
    "vis_prediction",
    "trigger_humanized_delay",
    "magnet_trigger_humanized_delay",
)


def _profile_updates(data: dict[str, Any]) -> dict[str, Any]:
    migrated = _migrate_interval_ranges(_migrate_magnet_units(data))
    updates = {key: value for key, value in migrated.items() if key not in {"_name", "active_config"}}
    for key in CONFIG_OFF_BY_DEFAULT:
        updates.setdefault(key, False)
    # A config saved before pausing existed has no pause key and pauses while
    # the menu is open, the same as a fresh install.
    updates.setdefault("pause_hotkey", "")
    updates.setdefault("auto_pause_menu", True)
    # Same reasoning for the Humanizer blends: a config saved before they
    # existed means off, not "whatever the last config was set to".
    for key in HUMANIZER_BLEND_KEYS:
        updates.setdefault(key, 0.0)
    return updates


def _migrate_extras_flags() -> None:
    """One-time move of the CS2 inventory/stats switches into settings and the active config.

    They used to live in inventory/state.json ("enabled"/"statsEnabled"). The
    current values are copied into settings.json and the active config file where
    those files don't have the keys yet, then removed from state.json.
    """
    state_path = PROJECT_DIR / "inventory" / "state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(state, dict) or not ({"enabled", "statsEnabled"} & state.keys()):
        return
    legacy = {"cs2_inventory": bool(state.get("enabled")), "cs2_stats": bool(state.get("statsEnabled"))}
    targets = [SETTINGS_PATH]
    found = _find_config(_read_active_config_name())
    if found:
        targets.append(found[1])
    for path in targets:
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        missing = {k: v for k, v in legacy.items() if k not in data}
        if missing:
            data.update(missing)
            try:
                path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            except OSError:
                pass
    state.pop("enabled", None)
    state.pop("statsEnabled", None)
    try:
        state_path.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")
    except OSError:
        pass


def _list_config_names() -> list[str]:
    return [name for name, _path, _data in _iter_config_files()]


def _ensure_default_config(live: LiveConfig) -> tuple[list[str], str]:
    names = _list_config_names()
    active = _read_active_config_name()
    if not names:
        _write_config_file(DEFAULT_CONFIG_NAME, _config_payload(live))
        _write_active_config_name(DEFAULT_CONFIG_NAME)
        return [DEFAULT_CONFIG_NAME], DEFAULT_CONFIG_NAME
    if not active or not _find_config(active):
        active = names[0]
        _write_active_config_name(active)
    return names, active



@dataclass
class RuntimeStatus:
    loop_fps: float = 0.0
    vision_fps: float = 0.0
    vision_capture_ms: float = 0.0
    vision_infer_ms: float = 0.0
    prediction_speed: float = 0.0        # target's own speed, ROI px/s
    prediction_confidence: float = 0.0   # 0..1, how much lead is being applied
    prediction_lead_px: float = 0.0
    target_present: bool = False
    last_assist_mag: float = 0.0
    running: bool = False
    error: str = ""
    rp2040_connected: bool = False
    rp2040_port: str = ""
    rp2040_handshake: str = ""
    # "" while assisting; "menu" or "hotkey" while everything is held off.
    pause_reason: str = ""
    latency: LatencyStats = field(default_factory=LatencyStats)

    def to_dict(self) -> dict[str, Any]:
        return {
            "loop_fps": self.loop_fps,
            "vision_fps": self.vision_fps,
            "vision_capture_ms": self.vision_capture_ms,
            "vision_infer_ms": self.vision_infer_ms,
            "target_present": self.target_present,
            "last_assist_mag": self.last_assist_mag,
            "running": self.running,
            "error": self.error,
            "rp2040_connected": self.rp2040_connected,
            "rp2040_port": self.rp2040_port,
            "rp2040_handshake": self.rp2040_handshake,
            "paused": bool(self.pause_reason),
            "pause_reason": self.pause_reason,
            "latency": self.latency.snapshot(),
            "click_reaction": humanizer_reaction.summary(),
        }


def _log_config_changes(before: AppConfig, after: AppConfig) -> None:
    logger_changed = before.logging_enabled != after.logging_enabled
    if logger_changed:
        set_enabled(True)
    for attr, tag in MODULE_TAGS:
        old = bool(getattr(before, attr))
        new = bool(getattr(after, attr))
        if old != new:
            log(tag, "enabled" if new else "disabled")
    if logger_changed:
        log("Logger", "enabled" if after.logging_enabled else "disabled")
    if before.target_fps != after.target_fps:
        log("Settings", f"mouse FPS {before.target_fps} -> {after.target_fps}")
    if abs(before.conf_threshold - after.conf_threshold) > 1e-9:
        log(
            "Settings",
            f"detection confidence {before.conf_threshold:.2f} -> {after.conf_threshold:.2f}",
        )
    if before.output_idx != after.output_idx:
        log("Settings", f"monitor {before.output_idx} -> {after.output_idx}")
    if before.input_method != after.input_method:
        log("Settings", f"input method {before.input_method} -> {after.input_method}")
    if before.dry_run != after.dry_run:
        log("Settings", "dry run on" if after.dry_run else "dry run off")
    set_enabled(after.logging_enabled)


def _loaded_model_label(engine_path: str) -> str:
    path = Path(engine_path or "")
    game = GAMES.get(game_id_from_engine(engine_path), {})
    name = str(game.get("name") or "").strip()
    file_name = path.name
    if name and file_name and name.lower() != path.stem.lower():
        return f"{name} ({file_name})"
    return file_name or name or "none"


def _log_pause_change(before: str, after: str) -> None:
    label = {"hotkey": "pause key", "menu": "menu open"}
    if after:
        log("Pause", f"assist held off ({label.get(after, after)})")
    else:
        log("Pause", "resumed")


def _log_capture_start(cfg: AppConfig) -> None:
    enabled = [tag for attr, tag in MODULE_TAGS if getattr(cfg, attr)]
    extra = f" ({', '.join(enabled)})" if enabled else ""
    log("Capture", f"started{extra}")


@dataclass
class StatusAccum:
    mouse_sum: float = 0.0
    mouse_n: int = 0
    vision_sum: float = 0.0
    cap_sum: float = 0.0
    infer_sum: float = 0.0
    vision_n: int = 0
    assist_sum: float = 0.0
    target_n: int = 0
    frame_n: int = 0

    def add_frame(self, has_target: bool, assist_mag: float) -> None:
        self.frame_n += 1
        self.assist_sum += assist_mag
        if has_target:
            self.target_n += 1

    def add_sample(
        self,
        mouse_fps: float,
        vision_fps: float,
        cap_ms: float,
        infer_ms: float,
    ) -> None:
        self.mouse_sum += mouse_fps
        self.mouse_n += 1
        self.vision_sum += vision_fps
        self.cap_sum += cap_ms
        self.infer_sum += infer_ms
        self.vision_n += 1


def _log_runtime_status(
    status: RuntimeStatus, cfg: AppConfig, accum: StatusAccum
) -> None:
    mouse_n = max(1, accum.mouse_n)
    vision_n = max(1, accum.vision_n)
    frame_n = max(1, accum.frame_n)
    parts = [
        f"mouse {accum.mouse_sum / mouse_n:.0f}/{cfg.target_fps}fps",
        f"vision {accum.vision_sum / vision_n:.0f}fps",
        f"cap {accum.cap_sum / vision_n:.1f}ms",
        f"infer {accum.infer_sum / vision_n:.1f}ms",
        f"target {100.0 * accum.target_n / frame_n:.0f}%",
        f"assist {accum.assist_sum / frame_n:.1f}px",
    ]
    if cfg.input_method == "rp2040":
        if status.rp2040_connected:
            parts.append(f"rp2040 {status.rp2040_port or '?'}")
        else:
            parts.append("rp2040 down")
    if cfg.dry_run:
        parts.append("dry-run")
    log("Status", "  ".join(parts))


class RuntimeController:
    """Shared handle between the capture loop and the settings panel."""

    def __init__(self, config: AppConfig) -> None:
        self.live = LiveConfig(config)
        self.status = RuntimeStatus()
        self.quit_event = threading.Event()
        self.loop_stop = threading.Event()
        self.ready = threading.Event()
        self.error: Optional[str] = None
        self.menu_visible = True
        self.user_paused = False
        self._menu_was_zoomed = False
        self.hwnd = 0
        self._game_hwnd = 0
        # True while a Humanizer session holds pointer lock in the menu; the
        # cursor-clip clearing below would otherwise let the cursor escape it.
        self.humanizer_active = False
        self.overlay_world = OverlayBuffer()
        self._loop_thread: Optional[threading.Thread] = None
        self._thread_lock = threading.Lock()
        _ensure_default_config(self.live)

    def configs_state(self) -> dict[str, Any]:
        names, active = _ensure_default_config(self.live)
        return {"configs": names, "active_config": active}

    def create_config(self, name: str) -> dict[str, Any]:
        label = _normalize_config_name(name)
        error = _config_name_error(label)
        if error:
            return {"ok": False, "error": error, "settings": self.live.to_ui_dict(), **self.configs_state()}
        if _find_config(label):
            return {
                "ok": False,
                "error": f"Config '{label}' already exists",
                "settings": self.live.to_ui_dict(),
                **self.configs_state(),
            }
        try:
            _write_config_file(label, _config_payload(self.live))
            _write_active_config_name(label)
        except OSError as exc:
            log_error(f"Could not create config: {exc}")
            return {
                "ok": False,
                "error": str(exc) or "Could not create config",
                "settings": self.live.to_ui_dict(),
                **self.configs_state(),
            }
        log("Config", f"created {label}")
        return {"ok": True, "settings": self.live.to_ui_dict(), **self.configs_state()}

    def save_config(self) -> dict[str, Any]:
        names, active = _ensure_default_config(self.live)
        state = {"configs": names, "active_config": active, "settings": self.live.to_ui_dict()}
        if not active:
            return {"ok": False, "error": "No config selected", **state}
        try:
            _write_config_file(active, _config_payload(self.live))
        except OSError as exc:
            log_error(f"Could not save config: {exc}")
            return {"ok": False, "error": str(exc) or "Could not save config", **state}
        log("Config", f"saved {active}")
        return {"ok": True, **state}

    def select_config(self, name: str) -> dict[str, Any]:
        label = _normalize_config_name(name)
        found = _find_config(label)
        if not found:
            return {
                "ok": False,
                "error": f"Config '{label}' not found",
                "settings": self.live.to_ui_dict(),
                **self.configs_state(),
            }
        stored_name, _path, data = found
        before = self.live.get()
        error = self.live.update(_profile_updates(data))
        if error:
            log_error(error)
            return {"ok": False, "error": error, "settings": self.live.to_ui_dict(), **self.configs_state()}
        # Commit the switch before touching capture so a failed restart cannot
        # leave the loaded settings attached to the previous config's name.
        _write_active_config_name(stored_name)
        log("Config", f"loaded {stored_name}")
        result = self._settings_changed(before)
        result.update(self.configs_state())
        return result

    def delete_config(self, name: str) -> dict[str, Any]:
        label = _normalize_config_name(name) or _read_active_config_name()
        found = _find_config(label)
        if not found:
            return {
                "ok": False,
                "error": f"Config '{label}' not found" if label else "No config selected",
                "settings": self.live.to_ui_dict(),
                **self.configs_state(),
            }
        stored_name, path, _data = found
        was_active = _read_active_config_name().casefold() == stored_name.casefold()
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            log_error(f"Could not delete config: {exc}")
            return {
                "ok": False,
                "error": str(exc) or "Could not delete config",
                "settings": self.live.to_ui_dict(),
                **self.configs_state(),
            }
        log("Config", f"deleted {stored_name}")
        remaining = _list_config_names()
        if not remaining:
            _write_config_file(DEFAULT_CONFIG_NAME, _config_payload(self.live))
            _write_active_config_name(DEFAULT_CONFIG_NAME)
            return {"ok": True, "settings": self.live.to_ui_dict(), **self.configs_state()}
        if was_active:
            return self.select_config(remaining[0])
        return {"ok": True, "settings": self.live.to_ui_dict(), **self.configs_state()}

    def apply_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        before = self.live.get()
        error = self.live.update(updates)
        if error:
            log_error(error)
            return {"ok": False, "error": error, "settings": self.live.to_ui_dict()}
        return self._settings_changed(before)

    def _settings_changed(self, before: AppConfig) -> dict[str, Any]:
        self.live.save()
        after = self.live.get()
        _log_config_changes(before, after)
        engine_changed = before.engine_path != after.engine_path
        capture_changed = (
            before.output_idx != after.output_idx
            or before.input_method != after.input_method
            or before.rp2040_port != after.rp2040_port
        )
        if engine_changed and not after.engine_path:
            self.stop_capture()
        elif after.engine_path and (engine_changed or (capture_changed and self.status.running)):
            result = self.restart_capture()
            if not result.get("ok"):
                return result
        return {"ok": True, "settings": self.live.to_ui_dict()}

    def start_capture(self) -> dict[str, Any]:
        cfg = self.live.get()
        engine = cfg.engine_path.strip()
        if not engine:
            log_error("Select a game in Settings first")
            return {
                "ok": False,
                "error": "Select a game in Settings first",
                "settings": self.live.to_ui_dict(),
            }
        if not Path(engine).is_file():
            log_error(f"Engine file not found: {engine}")
            return {
                "ok": False,
                "error": f"Engine file not found: {engine}",
                "settings": self.live.to_ui_dict(),
            }
        with self._thread_lock:
            if self._loop_thread is not None and self._loop_thread.is_alive():
                log_error("Capture is still stopping. Press Start again.")
                return {
                    "ok": False,
                    "error": "Capture is still stopping. Press Start again.",
                    "settings": self.live.to_ui_dict(),
                }
            self.loop_stop.clear()
            self.error = None
            self.status.error = ""
            self.status.rp2040_handshake = ""
            self.ready = threading.Event()
            self._loop_thread = threading.Thread(
                target=_capture_loop, args=(self,), name="capture-loop", daemon=True
            )
            self._loop_thread.start()
        if not self.ready.wait(timeout=45.0):
            self.loop_stop.set()
            log_error("Timed out starting capture")
            return {
                "ok": False,
                "error": "Timed out starting capture",
                "settings": self.live.to_ui_dict(),
            }
        if self.error:
            self.status.error = self.error
            return {"ok": False, "error": self.error, "settings": self.live.to_ui_dict()}
        return {"ok": True, "settings": self.live.to_ui_dict()}

    def stop_capture(self) -> None:
        self.loop_stop.set()
        thread = self._loop_thread
        if thread is not None:
            thread.join(timeout=8.0)
        with self._thread_lock:
            if self._loop_thread is not None and not self._loop_thread.is_alive():
                self._loop_thread = None
        self.status.running = False
        self.status.loop_fps = 0.0
        self.status.vision_fps = 0.0
        self.status.target_present = False
        self.overlay_world.clear()

    def restart_capture(self) -> dict[str, Any]:
        self.stop_capture()
        if self._loop_thread is not None and self._loop_thread.is_alive():
            time.sleep(0.25)
            self.stop_capture()
        return self.start_capture()

    def stop(self) -> None:
        self.quit_event.set()
        self.loop_stop.set()

    def pause_reason(self, cfg: AppConfig) -> str:
        """Why assist is held off right now: "hotkey", "menu", or "" for not."""
        if self.user_paused:
            return "hotkey"
        if cfg.auto_pause_menu and self.menu_visible:
            return "menu"
        return ""

    def toggle_pause(self) -> None:
        self.user_paused = not self.user_paused
        if not self.status.running:
            # With capture stopped the loop is not there to say so.
            log("Pause", "on" if self.user_paused else "off")

    def toggle_menu(self) -> None:
        """Show/hide via Win32 so WebView2 COM is never touched off the UI thread."""
        hwnd = int(self.hwnd)
        if hwnd == 0:
            return
        user32 = ctypes.windll.user32
        visible = bool(user32.IsWindowVisible(hwnd))
        if visible:
            self._menu_was_zoomed = bool(user32.IsZoomed(hwnd))
            user32.ShowWindow(hwnd, 0)  # SW_HIDE
            self.menu_visible = False
            self._restore_game_focus()
            return
        # SW_RESTORE drops fullscreen to the restored window. Remember the
        # state from this hide only, so a later windowed hide stays windowed.
        user32.ShowWindow(hwnd, 3 if self._menu_was_zoomed else 5)
        user32.SetWindowPos(
            hwnd,
            -1,  # HWND_TOPMOST
            0,
            0,
            0,
            0,
            0x0001 | 0x0002 | 0x0040,  # NOSIZE | NOMOVE | SHOWWINDOW
        )
        # Hand the mouse back. A fullscreen game usually owns the cursor and
        # clips it to its own window, which would leave the menu unclickable.
        previous = _force_foreground_window(hwnd)
        if previous and previous != hwnd:
            self._game_hwnd = previous
        _clear_cursor_clip()
        self.menu_visible = True

    def keep_cursor_free(self) -> None:
        """Some games re-apply their cursor clip every frame, so keep clearing it.

        Only while the menu actually holds focus, so alt-tabbing back to the
        game hands the cursor straight back to it.
        """
        hwnd = int(self.hwnd)
        if not self.menu_visible or not hwnd or self.humanizer_active:
            return
        if int(ctypes.windll.user32.GetForegroundWindow() or 0) != hwnd:
            return
        _clear_cursor_clip()

    def _restore_game_focus(self) -> None:
        """Give focus back to the game so it starts taking input again."""
        game = int(self._game_hwnd or 0)
        self._game_hwnd = 0
        if game and ctypes.windll.user32.IsWindow(game):
            _force_foreground_window(game)


def _required_target_fov(cfg: AppConfig) -> float:
    """Keep target selection wide enough for every enabled module."""
    radii = [float(cfg.fov_px)]
    if cfg.aim_director:
        radii.append(float(cfg.aim_director_radius_px))
    if cfg.aim_corrector:
        radii.append(float(cfg.aim_corrector_radius_px))
    if cfg.tracking_assist:
        radii.append(float(cfg.tracking_assist_radius_px))
    if cfg.spray_acquire:
        radii.append(float(cfg.spray_acquire_radius_px))
    if cfg.tap_assist:
        radii.append(float(cfg.tap_range_px))
    if cfg.trigger:
        radii.append(float(cfg.trigger_radius_px))
    if cfg.magnet:
        radii.extend(
            (
                float(cfg.magnet_radius_px),
                float(cfg.magnet_trigger_radius_px),
            )
        )
    if cfg.rp_one_tap_assist:
        radii.append(float(cfg.rp_one_tap_reset_fov_px))
        radii.append(float(cfg.rp_one_tap_fov_px))
    return clamp_fov_radius(max(radii))


class RawMouseReader:
    """
    Background thread reading physical mouse relative deltas via Raw Input.

    Uses RIDEV_INPUTSINK so deltas are captured independently of SendInput output.
    """

    def __init__(self) -> None:
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._user32.DefWindowProcW.argtypes = [HWND, ctypes.c_uint, WPARAM, LPARAM]
        self._user32.DefWindowProcW.restype = LRESULT
        self._user32.GetRawInputData.argtypes = [
            LPARAM,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint),
            ctypes.c_uint,
        ]
        self._user32.GetRawInputData.restype = ctypes.c_uint
        self._user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
        self._user32.FindWindowW.restype = HWND
        self._user32.CreateWindowExW.argtypes = [
            ctypes.c_ulong,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            HWND,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        self._user32.CreateWindowExW.restype = HWND
        self._user32.DestroyWindow.argtypes = [HWND]
        self._user32.DestroyWindow.restype = ctypes.wintypes.BOOL
        self._user32.UnregisterClassW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p]
        self._user32.UnregisterClassW.restype = ctypes.wintypes.BOOL
        self._kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        self._kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        self._pending_dx = 0
        self._pending_dy = 0
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._hwnd = None
        self._wndproc_ref = None
        self._start_error: Optional[str] = None

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._start_error = None
        self._hwnd = None
        self._thread = threading.Thread(target=self._message_loop, daemon=True)
        self._thread.start()
        deadline = time.perf_counter() + 2.0
        while self._hwnd is None and self._start_error is None and time.perf_counter() < deadline:
            time.sleep(0.01)
        if self._hwnd is None:
            self._running = False
            raise RuntimeError(
                self._start_error or "Failed to initialize Raw Input message window"
            )

    def stop(self) -> None:
        self._running = False
        hwnd = self._hwnd
        if hwnd:
            self._user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        if self._thread:
            self._thread.join(timeout=2.0)
        self._thread = None

    def consume_delta(self) -> Tuple[int, int]:
        with self._lock:
            dx, dy = self._pending_dx, self._pending_dy
            self._pending_dx = 0
            self._pending_dy = 0
        return dx, dy

    def compensate_injection(self, dx: int, dy: int) -> None:
        """Remove synthetic SendInput deltas if they echo back through Raw Input."""
        if dx == 0 and dy == 0:
            return
        with self._lock:
            self._pending_dx -= dx
            self._pending_dy -= dy

    def _accumulate(self, dx: int, dy: int) -> None:
        with self._lock:
            self._pending_dx += dx
            self._pending_dy += dy

    def _drop_stale_window(self, class_name: str, hinstance: int | ctypes.c_void_p) -> None:
        leftover = self._user32.FindWindowW(class_name, class_name)
        if leftover:
            self._user32.DestroyWindow(leftover)
        self._user32.UnregisterClassW(class_name, hinstance)

    def _unregister_raw_input(self) -> None:
        rid = RAWINPUTDEVICE()
        rid.usUsagePage = 0x01
        rid.usUsage = 0x02
        rid.dwFlags = RIDEV_REMOVE
        rid.hwndTarget = None
        try:
            self._user32.RegisterRawInputDevices(
                ctypes.byref(rid), 1, ctypes.sizeof(rid)
            )
        except Exception:
            pass

    def _message_loop(self) -> None:
        def wnd_proc(hwnd, msg, wparam, lparam):
            if msg == WM_INPUT:
                self._handle_raw_input(lparam)
                return 0
            if msg == WM_CLOSE:
                self._user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                self._user32.PostQuitMessage(0)
                return 0
            return self._user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wndproc_ref = WNDPROC(wnd_proc)
        hinstance = self._kernel32.GetModuleHandleW(None)
        class_name = RAW_INPUT_CLASS
        hwnd = None
        registered = False
        try:
            self._drop_stale_window(class_name, hinstance)
            wc = WNDCLASSW()
            wc.lpfnWndProc = self._wndproc_ref
            wc.hInstance = hinstance
            wc.lpszClassName = class_name
            atom = self._user32.RegisterClassW(ctypes.byref(wc))
            if not atom:
                self._drop_stale_window(class_name, hinstance)
                atom = self._user32.RegisterClassW(ctypes.byref(wc))
            if not atom:
                raise RuntimeError("RegisterClassW failed for Raw Input window")
            registered = True

            hwnd = self._user32.CreateWindowExW(
                0,
                class_name,
                class_name,
                0,
                0,
                0,
                0,
                0,
                None,
                None,
                hinstance,
                None,
            )
            if not hwnd:
                raise RuntimeError("CreateWindowExW failed for Raw Input window")

            rid = RAWINPUTDEVICE()
            rid.usUsagePage = 0x01
            rid.usUsage = 0x02
            rid.dwFlags = RIDEV_INPUTSINK
            rid.hwndTarget = hwnd
            if not self._user32.RegisterRawInputDevices(
                ctypes.byref(rid), 1, ctypes.sizeof(rid)
            ):
                raise RuntimeError("RegisterRawInputDevices failed")
            self._hwnd = hwnd

            msg = ctypes.wintypes.MSG()
            while self._running:
                ret = self._user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret == 0 or ret == -1:
                    break
                self._user32.TranslateMessage(ctypes.byref(msg))
                self._user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as exc:
            self._start_error = str(exc)
        finally:
            self._running = False
            self._unregister_raw_input()
            if hwnd:
                self._user32.DestroyWindow(hwnd)
            self._hwnd = None
            if registered:
                self._user32.UnregisterClassW(class_name, hinstance)

    def _handle_raw_input(self, lparam: int | LPARAM) -> None:
        if not isinstance(lparam, LPARAM):
            lparam = LPARAM(lparam)
        size = ctypes.c_uint(0)
        self._user32.GetRawInputData(
            lparam,
            0x10000003,  # RID_INPUT
            None,
            ctypes.byref(size),
            ctypes.sizeof(RAWINPUTHEADER),
        )
        if size.value == 0:
            return

        buffer = ctypes.create_string_buffer(size.value)
        if (
            self._user32.GetRawInputData(
                lparam,
                0x10000003,
                buffer,
                ctypes.byref(size),
                ctypes.sizeof(RAWINPUTHEADER),
            )
            == 0xFFFFFFFF
        ):
            return

        raw = RAWINPUT.from_buffer(buffer)
        if raw.header.dwType != RIM_TYPEMOUSE:
            return
        if raw.mouse.usFlags != MOUSE_MOVE_RELATIVE:
            return
        self._accumulate(int(raw.mouse.lLastX), int(raw.mouse.lLastY))


class MouseDispatcher:
    """Inject relative mouse movement via SendInput."""

    def __init__(self) -> None:
        self._user32 = ctypes.windll.user32
        self._user32.SendInput.argtypes = [
            ctypes.c_uint,
            ctypes.POINTER(INPUT),
            ctypes.c_int,
        ]
        self._user32.SendInput.restype = ctypes.c_uint

    def move(self, dx: int, dy: int) -> None:
        if dx == 0 and dy == 0:
            return
        inp = INPUT()
        inp.type = INPUT_MOUSE
        inp._i.mi = MOUSEINPUT(
            dx=dx,
            dy=dy,
            mouseData=0,
            dwFlags=MOUSEEVENTF_MOVE | MOUSEEVENTF_MOVE_NOCOALESCE,
            time=0,
            dwExtraInfo=0,
        )
        sent = self._user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
        if sent != 1:
            raise RuntimeError("SendInput failed to inject mouse movement")

    def _button(self, flags: int) -> None:
        inp = INPUT()
        inp.type = INPUT_MOUSE
        inp._i.mi = MOUSEINPUT(
            dx=0,
            dy=0,
            mouseData=0,
            dwFlags=flags,
            time=0,
            dwExtraInfo=0,
        )
        sent = self._user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
        if sent != 1:
            raise RuntimeError("SendInput failed to inject mouse button event")

    def click_left(self) -> None:
        self._button(MOUSEEVENTF_LEFTDOWN)
        self._button(MOUSEEVENTF_LEFTUP)

    def left_down(self) -> None:
        self._button(MOUSEEVENTF_LEFTDOWN)

    def left_up(self) -> None:
        self._button(MOUSEEVENTF_LEFTUP)


def _parse_hold_key(name: str) -> int:
    """Map a key/button name to a Windows virtual-key code."""
    key = name.lower().strip()
    named = {
        "mouse1": VK_LBUTTON,
        "left": VK_LBUTTON,
        "lmb": VK_LBUTTON,
        "mouse2": VK_RBUTTON,
        "right": VK_RBUTTON,
        "rmb": VK_RBUTTON,
        "mouse3": VK_MBUTTON,
        "middle": VK_MBUTTON,
        "mmb": VK_MBUTTON,
        "mouse4": VK_XBUTTON1,
        "mouse5": VK_XBUTTON2,
        "xbutton1": VK_XBUTTON1,
        "xbutton2": VK_XBUTTON2,
        "side1": VK_XBUTTON1,
        "side2": VK_XBUTTON2,
        "shift": 0x10,
        "ctrl": 0x11,
        "control": 0x11,
        "alt": 0x12,
        "space": 0x20,
        "tab": 0x09,
        "capslock": 0x14,
        "enter": 0x0D,
        "backspace": 0x08,
        "lshift": 0xA0,
        "rshift": 0xA1,
        "lctrl": 0xA2,
        "rctrl": 0xA3,
        "lalt": 0xA4,
        "ralt": 0xA5,
        "insert": VK_INSERT,
        "ins": VK_INSERT,
        "home": 0x24,
        "end": 0x23,
        "delete": 0x2E,
        "escape": 0x1B,
        "esc": 0x1B,
        "pageup": 0x21,
        "pagedown": 0x22,
        "arrowup": 0x26,
        "arrowdown": 0x28,
        "arrowleft": 0x25,
        "arrowright": 0x27,
        "up": 0x26,
        "down": 0x28,
    }
    if key in named:
        return named[key]
    if len(key) == 1:
        return ord(key.upper())
    if key.startswith("f") and key[1:].isdigit():
        n = int(key[1:])
        if 1 <= n <= 24:
            return 0x6F + n
    raise ValueError(
        f"Unknown hold key '{name}'. Examples: insert, x, mouse1, mouse5, f8"
    )


def _is_key_held(vk: int) -> bool:
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


def _clear_cursor_clip() -> None:
    """Drop any ClipCursor box a fullscreen game has locked the cursor into."""
    try:
        ctypes.windll.user32.ClipCursor(None)
    except Exception:
        pass


def _force_foreground_window(hwnd: int) -> int:
    """Take focus even from a fullscreen game. Returns the previous window.

    Windows normally refuses a foreground steal from a background process, so
    attach to the current foreground thread's input queue first, which makes
    the two threads share focus state and lets the call through.
    """
    user32 = ctypes.windll.user32
    previous = int(user32.GetForegroundWindow() or 0)
    if previous == hwnd:
        return previous
    other_tid = int(user32.GetWindowThreadProcessId(previous, None)) if previous else 0
    self_tid = int(ctypes.windll.kernel32.GetCurrentThreadId())
    attached = False
    try:
        if other_tid and other_tid != self_tid:
            attached = bool(user32.AttachThreadInput(other_tid, self_tid, True))
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
    except Exception:
        pass
    finally:
        if attached:
            try:
                user32.AttachThreadInput(other_tid, self_tid, False)
            except Exception:
                pass
    return previous


TRIGGER_ZONE_HALF = {
    "head": (0.28, 0.10),
    "neck": (0.24, 0.08),
    "spine3": (0.30, 0.10),
    "chest": (0.32, 0.12),
    "stomach": (0.34, 0.12),
    "pelvis": (0.32, 0.12),
    "shoulder_r": (0.16, 0.10),
    "shoulder_l": (0.16, 0.10),
    "elbow_r": (0.14, 0.10),
    "elbow_l": (0.14, 0.10),
    "hip_r": (0.16, 0.10),
    "hip_l": (0.16, 0.10),
    "knee_r": (0.14, 0.12),
    "knee_l": (0.14, 0.12),
    "ankle_r": (0.12, 0.10),
    "ankle_l": (0.12, 0.10),
}


def _target_bbox(target: object) -> Optional[Tuple[float, float, float, float]]:
    bbox = getattr(target, "bbox", None)
    if bbox is None or len(bbox) != 4:
        return None
    box = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    head = getattr(target, "head_bbox", None)
    if head is not None and len(head) == 4:
        # Same tuple to every caller; hitbox_point uses the head box it carries.
        return AimBox(box, head)
    return box


def _bone_spots(
    target: Optional[object],
    bones: tuple[str, ...],
) -> Tuple[Tuple[float, float], ...]:
    bbox = _target_bbox(target) if target is not None else None
    if bbox is None:
        return ()
    return tuple(
        hitbox_point(bbox, bone)
        for bone in bones
        if bone in AIM_BONE_FROM_TOP
    )


def _bone_spot(
    target: Optional[object],
    bone: str,
) -> Optional[Tuple[float, float]]:
    spots = _bone_spots(target, (bone,))
    return spots[0] if spots else None


def _publish_overlay(
    controller: RuntimeController,
    region: Tuple[int, int, int, int],
    target: Optional[object],
    cfg: AppConfig,
    predicted: Optional[object] = None,
) -> None:
    controller.overlay_world.set(
        OverlayWorld(
            roi=(int(region[0]), int(region[1]), int(region[2]), int(region[3])),
            target_box=_target_bbox(target),
            aim_director_spots=_bone_spots(target, cfg.aim_director_bones),
            aim_corrector_spot=_bone_spot(target, cfg.aim_corrector_bone),
            tracking_assist_spot=_bbox_center_spot(target),
            spray_acquire_spot=_bbox_center_spot(target),
            tap_spot=_bone_spot(target, cfg.tap_bone),
            rp_one_tap_spot=_bone_spot(target, "head"),
            rp_aim_spots=_bone_spots(target, cfg.rp_aim_bones),
            trigger_spots=_bone_spots(target, cfg.trigger_bones),
            magnet_pull_spots=_bone_spots(target, cfg.magnet_bones),
            magnet_fire_spots=_bone_spots(target, cfg.magnet_trigger_bones),
            prediction_box=_target_bbox(predicted) if cfg.vis_prediction else None,
        )
    )


def _is_on_target(
    target: Optional[object],
    radius_px: float,
    trigger_bones: tuple[str, ...] = (),
) -> bool:
    """Fire if the crosshair is within radius of any selected bone."""
    if target is None:
        return False
    bones = tuple(b for b in trigger_bones if b in AIM_BONE_FROM_TOP)
    if not bones:
        return False
    bbox = _target_bbox(target)
    if bbox is None:
        return radius_px > 0.0 and math.hypot(float(target.dx), float(target.dy)) <= radius_px

    x1, y1, x2, y2 = bbox
    bw, bh = abs(x2 - x1), abs(y2 - y1)
    if bw <= 1.0 or bh <= 1.0:
        return False

    hx, hy = ROI_CENTER
    radius = max(0.0, radius_px)
    for bone in bones:
        px, py = hitbox_point(bbox, bone)
        zrx, zry = TRIGGER_ZONE_HALF.get(bone, TRIGGER_ZONE_HALF["chest"])
        rx = bw * zrx + radius
        ry = bh * zry + radius
        if abs(hx - px) <= rx and abs(hy - py) <= ry:
            return True
    return False


def _bone_in_fov(
    bbox: Tuple[float, float, float, float],
    bone: str,
    radius_px: float,
) -> bool:
    x1, y1, x2, y2 = bbox
    bw, bh = abs(x2 - x1), abs(y2 - y1)
    if bw <= 1.0 or bh <= 1.0:
        return False
    px, py = hitbox_point(bbox, bone)
    hx, hy = ROI_CENTER
    zrx, zry = TRIGGER_ZONE_HALF.get(bone, TRIGGER_ZONE_HALF["chest"])
    rx = bw * zrx + max(0.0, radius_px)
    ry = bh * zry + max(0.0, radius_px)
    return abs(hx - px) <= rx and abs(hy - py) <= ry


def _bones_in_fov(
    target: Optional[object],
    bones: tuple[str, ...],
    radius_px: float,
) -> list[Tuple[str, float, float, float]]:
    """(bone, dx, dy, distance) for each selected bone inside the FOV."""
    if target is None:
        return []
    selected = tuple(b for b in bones if b in AIM_BONE_FROM_TOP)
    if not selected:
        return []
    bbox = _target_bbox(target)
    hx, hy = ROI_CENTER
    if bbox is None:
        dx, dy = float(target.dx), float(target.dy)
        dist = math.hypot(dx, dy)
        return [(selected[0], dx, dy, dist)] if dist <= max(0.0, radius_px) else []
    inside: list[Tuple[str, float, float, float]] = []
    for bone in selected:
        if not _bone_in_fov(bbox, bone, radius_px):
            continue
        px, py = hitbox_point(bbox, bone)
        dx, dy = px - hx, py - hy
        inside.append((bone, dx, dy, math.hypot(dx, dy)))
    return inside


# How much recent hand movement (px) toward another selected bone it takes for
# Aim director to hand the pull over to that bone.
DIRECTOR_INTENT_TAU_S = 0.18
DIRECTOR_INTENT_SWITCH_PX = 6.0
DIRECTOR_INTENT_ALIGN = 0.6
DIRECTOR_ON_BONE_PX = 6.0
AIM_DIRECTOR_MODES = ("always", "toggle", "hold")


def _director_aim_offset(
    target: Optional[object],
    bones: tuple[str, ...],
    radius_px: float,
    locked_bone: Optional[str],
    intent_x: float,
    intent_y: float,
) -> Tuple[Optional[Tuple[float, float]], Optional[str]]:
    """Pick the bone the user is steering toward, else keep the current one."""
    inside = _bones_in_fov(target, bones, radius_px)
    if not inside:
        return None, None
    intent = math.hypot(intent_x, intent_y)
    if intent >= DIRECTOR_INTENT_SWITCH_PX:
        ahead = [
            item
            for item in inside
            if item[3] > DIRECTOR_ON_BONE_PX
            and (intent_x * item[1] + intent_y * item[2]) / (intent * item[3]) >= DIRECTOR_INTENT_ALIGN
        ]
        if ahead:
            best = min(ahead, key=lambda item: item[3])
            return (best[1], best[2]), best[0]
    if locked_bone:
        for item in inside:
            if item[0] == locked_bone:
                return (item[1], item[2]), item[0]
    best = min(inside, key=lambda item: item[3])
    return (best[1], best[2]), best[0]


def _director_armed(config: "AppConfig", state: "AdaptiveAimState") -> bool:
    if not config.aim_director:
        state.director_toggled = False
        state.director_key_down = False
        return False
    if config.aim_director_mode == "always":
        return True
    down = config.aim_director_vk is not None and _is_key_held(config.aim_director_vk)
    if config.aim_director_mode == "toggle" and down and not state.director_key_down:
        state.director_toggled = not state.director_toggled
        log("AimDirector", "armed" if state.director_toggled else "disarmed")
    state.director_key_down = down
    return down if config.aim_director_mode == "hold" else state.director_toggled


def _magnet_aim_offset(
    target: Optional[object],
    bones: tuple[str, ...],
    radius_px: float,
    locked_bone: Optional[str],
) -> Tuple[Optional[Tuple[float, float]], Optional[str]]:
    """Offset to the nearest selected magnet bone that sits inside magnet FOV."""
    inside = _bones_in_fov(target, bones, radius_px)
    if not inside:
        return None, None
    best = min(inside, key=lambda item: item[3])
    if locked_bone:
        for item in inside:
            if item[0] == locked_bone and item[3] <= best[3] + AIM_LOCK_PX:
                best = item
                break
    return (best[1], best[2]), best[0]


CLI_FLAG_MAP = {
    "--engine": "engine",
    "--fps": "fps",
    "--conf": "conf",
    "--classes": "classes",
    "--max-assist": "max_assist",
    "--gain-coeff": "gain_coeff",
    "--assist-scale": "assist_scale",
    "--target-deadzone": "target_deadzone",
    "--target-fade-radius": "target_fade_radius",
    "--velocity-ref": "velocity_ref",
    "--overshoot-limit": "overshoot_limit",
    "--track-coeff": "track_coeff",
    "--track-radius": "track_radius",
    "--approach-fade-start": "approach_fade_start",
    "--approach-fade-end": "approach_fade_end",
    "--close-limit": "close_limit",
    "--stickiness": "stickiness",
    "--sticky-radius": "sticky_radius",
    "--acquire-ramp-ms": "acquire_ramp_ms",
    "--hold-trigger": "hold_trigger",
    "--trigger-radius": "trigger_radius",
    "--trigger-interval-ms": "trigger_interval_ms",
    "--trigger-bones": "trigger_bones",
    "--tap-max-nudge": "tap_max_nudge",
    "--tap-range": "tap_range",
    "--aim": "aim_mode",
    "--fov": "fov",
    "--output-idx": "output_idx",
    "--menu-hotkey": "menu_hotkey",
}


def _load_saved_settings() -> dict[str, Any]:
    if not SETTINGS_PATH.is_file():
        return {}
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return _migrate_interval_ranges(_migrate_magnet_units(data)) if isinstance(data, dict) else {}


def _explicit_cli_updates(argv: list[str]) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--dry-run":
            updates["dry_run"] = True
        elif token == "--tap-assist":
            updates["tap_assist"] = True
        elif token == "--no-tap-assist":
            updates["tap_assist"] = False
        elif token in CLI_FLAG_MAP and i + 1 < len(argv) and not argv[i + 1].startswith("-"):
            updates[CLI_FLAG_MAP[token]] = argv[i + 1]
            i += 1
        i += 1
    return updates


def _apply_saved_settings(config: AppConfig, argv: list[str]) -> AppConfig:
    _migrate_extras_flags()
    saved = _load_saved_settings()
    live = LiveConfig(config)
    if saved:
        error = live.update(saved)
        if error:
            log_error(f"ignored saved settings ({error})")
    cli_updates = _explicit_cli_updates(argv)
    if cli_updates:
        error = live.update(cli_updates)
        if error:
            raise ValueError(error)
    merged = live.get()
    merged.ui = config.ui
    merged.offset_fn = config.offset_fn
    merged.verify_inject = config.verify_inject
    set_enabled(merged.logging_enabled)
    return merged


def parse_args(argv: Optional[list[str]] = None) -> AppConfig:
    parser = argparse.ArgumentParser(
        description="Argus AI — all-seeing vision assist"
    )
    parser.add_argument("--engine", default="", help="Path to TensorRT .engine file (optional if chosen in the UI)")
    parser.add_argument(
        "--fps",
        type=int,
        default=240,
        help="Mouse-assist loop FPS (30-600). Does not cap detection.",
    )
    parser.add_argument("--conf", type=float, default=0.30, help="YOLO confidence threshold")
    parser.add_argument("--classes", type=str, default="", help="Comma-separated class IDs to track")
    parser.add_argument("--dry-run", action="store_true", help="Log vectors without SendInput")
    parser.add_argument("--output-idx", type=int, default=0, help="DXCam monitor output index")
    parser.add_argument(
        "--offset-demo",
        action="store_true",
        help="Enable demo time-indexed O_offset: (2t, 0.5*sin(t))",
    )
    parser.add_argument(
        "--max-assist",
        type=int,
        default=40,
        help="Max aligned assist injection per frame in pixels (live mode)",
    )
    parser.add_argument(
        "--gain-coeff",
        type=float,
        default=GAIN_COEFF,
        help="Aligned boost strength (default 0.15 = up to ~15%% extra speed)",
    )
    parser.add_argument(
        "--assist-scale",
        type=float,
        default=1.0,
        help="Multiplier applied to assist before clamping (for tuning)",
    )
    parser.add_argument(
        "--verify-inject",
        action="store_true",
        help="On startup, inject a test mouse move on the desktop to verify SendInput",
    )
    parser.add_argument(
        "--target-deadzone",
        type=float,
        default=TARGET_DEADZONE_PX,
        help="Disable assist when target is within this many pixels of ROI center",
    )
    parser.add_argument(
        "--target-fade-radius",
        type=float,
        default=TARGET_FADE_RADIUS_PX,
        help="Full assist strength resumes beyond this target distance (px)",
    )
    parser.add_argument(
        "--velocity-ref",
        type=float,
        default=VELOCITY_REF_PX,
        help="User speed (px/frame) above which assist is reduced to limit overshoot",
    )
    parser.add_argument(
        "--overshoot-limit",
        type=float,
        default=OVERSHOOT_LIMIT,
        help="Max assist per frame as a fraction of remaining target distance (0-1)",
    )
    parser.add_argument(
        "--track-coeff",
        type=float,
        default=TRACK_COEFF,
        help="How hard to rotate your throw toward the bone without changing speed",
    )
    parser.add_argument(
        "--track-radius",
        type=float,
        default=TRACK_RADIUS_PX,
        help="Distance (px) where steer starts. Outside this, assist stays off.",
    )
    parser.add_argument(
        "--approach-fade-start",
        type=float,
        default=APPROACH_FADE_START_PX,
        help="Below this target distance (px), extra speed is full. Farther than Approach out, extra speed is zero.",
    )
    parser.add_argument(
        "--approach-fade-end",
        type=float,
        default=APPROACH_FADE_END_PX,
        help="Beyond this target distance (px), extra speed is off so a long sweep keeps your throw speed.",
    )
    parser.add_argument(
        "--close-limit",
        type=float,
        default=CLOSE_LIMIT,
        help="Max user+assist closing speed per frame as fraction of target distance",
    )
    parser.add_argument(
        "--stickiness",
        type=float,
        default=STICKINESS,
        help="On-target magnet strength (0=off, ~1=subtle sticky, ~2=stronger). Yields when you flick away.",
    )
    parser.add_argument(
        "--sticky-radius",
        type=float,
        default=STICKY_RADIUS_PX,
        help="Distance (px) beyond which sticky assist fades out",
    )
    parser.add_argument(
        "--acquire-ramp-ms",
        type=float,
        default=ACQUIRE_RAMP_MS,
        help="Milliseconds to ease assist in when a target is first detected (0=instant)",
    )
    parser.add_argument(
        "--hold-trigger",
        type=str,
        default="",
        help="While held, fire mouse1 when on target (e.g. mouse4, mouse5, x, shift)",
    )
    parser.add_argument(
        "--trigger-radius",
        type=float,
        default=TARGET_DEADZONE_PX,
        help="Extra pixels around each selected fire-on bone",
    )
    parser.add_argument(
        "--trigger-interval-ms",
        type=float,
        default=295.0,
        help="Repeat click interval while held on a selected bone",
    )
    parser.add_argument(
        "--trigger-bones",
        type=str,
        default="head,neck,chest",
        help="Comma-separated bones the trigger may fire on (head, neck, chest, stomach, limbs, ...)",
    )
    parser.add_argument(
        "--aim",
        choices=AIM_MODES,
        default="closest",
        help="Where assist pulls: head, neck, chest, or closest of those",
    )
    parser.add_argument(
        "--fov",
        type=float,
        default=DEFAULT_FOV_PX,
        help="Circular detection FOV radius in pixels (40-320)",
    )
    parser.add_argument(
        "--tap-assist",
        action="store_true",
        default=None,
        help="Enable a single aim nudge on left-click press",
    )
    parser.add_argument(
        "--no-tap-assist",
        action="store_true",
        help="Disable tap assist",
    )
    parser.add_argument(
        "--tap-max-nudge",
        type=float,
        default=10.0,
        help="Max pixels to move toward the aim point on a left-click tap",
    )
    parser.add_argument(
        "--tap-range",
        type=float,
        default=48.0,
        help="Only nudge if the aim point is within this many pixels",
    )
    parser.add_argument(
        "--no-ui",
        action="store_true",
        help="Run without the frameless settings panel",
    )
    parser.add_argument(
        "--menu-hotkey",
        type=str,
        default="",
        help="Global key to show/hide the settings panel (default insert)",
    )
    args = parser.parse_args(argv)

    class_ids: Optional[tuple[int, ...]] = None
    if args.classes.strip():
        class_ids = tuple(int(x.strip()) for x in args.classes.split(",") if x.strip())

    offset_fn: Optional[Callable[[float], Tuple[float, float]]] = None
    if args.offset_demo:
        offset_fn = lambda t: (2.0 * t, 0.5 * math.sin(t))

    hold_trigger_vk: Optional[int] = None
    hold_trigger_name: Optional[str] = None
    if args.hold_trigger.strip():
        hold_trigger_name = args.hold_trigger.strip()
        hold_trigger_vk = _parse_hold_key(hold_trigger_name)

    menu_hotkey_name = MENU_HOTKEY_DEFAULT
    menu_hotkey_vk = VK_INSERT
    if args.menu_hotkey.strip():
        menu_hotkey_name = args.menu_hotkey.strip()
        menu_hotkey_vk = _parse_hold_key(menu_hotkey_name)

    config = AppConfig(
        engine_path=args.engine.strip(),
        target_fps=_clamp_mouse_fps(args.fps),
        conf_threshold=args.conf,
        target_class_ids=class_ids,
        dry_run=args.dry_run,
        output_idx=args.output_idx,
        max_assist_px=max(0, args.max_assist),
        assist_scale=max(0.0, args.assist_scale),
        verify_inject=args.verify_inject,
        gain_coeff=args.gain_coeff,
        target_deadzone_px=max(0.0, args.target_deadzone),
        target_fade_radius_px=max(0.0, args.target_fade_radius),
        velocity_ref_px=max(0.0, args.velocity_ref),
        overshoot_limit=max(0.0, min(1.0, args.overshoot_limit)),
        track_coeff=max(0.0, args.track_coeff),
        track_radius_px=max(0.0, args.track_radius),
        approach_fade_start_px=max(0.0, args.approach_fade_start),
        approach_fade_end_px=max(0.0, args.approach_fade_end),
        close_limit=max(0.0, min(1.0, args.close_limit)),
        stickiness=max(0.0, args.stickiness),
        sticky_radius_px=max(0.0, args.sticky_radius),
        acquire_ramp_ms=max(0.0, args.acquire_ramp_ms),
        hold_trigger_vk=hold_trigger_vk,
        hold_trigger_name=hold_trigger_name,
        trigger_radius_px=max(0.0, args.trigger_radius),
        trigger_interval_ms=max(0.0, args.trigger_interval_ms),
        trigger_bones=_parse_bones(args.trigger_bones) or ("head", "neck", "chest"),
        tap_assist=False if args.no_tap_assist else True,
        tap_max_nudge_px=max(0.0, args.tap_max_nudge),
        tap_range_px=max(0.0, args.tap_range),
        aim_mode=args.aim,
        fov_px=clamp_fov_radius(args.fov),
        offset_fn=offset_fn,
        ui=not args.no_ui,
        menu_hotkey_name=menu_hotkey_name,
        menu_hotkey_vk=menu_hotkey_vk,
    )
    config = _apply_saved_settings(config, argv if argv is not None else sys.argv[1:])
    if not config.engine_path:
        nearby = MODELS_DIR / "CS2.engine"
        if nearby.is_file():
            config.engine_path = str(nearby.resolve())
    return config


def _verify_sendinput(dispatcher: MouseDispatcher) -> None:
    """Quick desktop check that SendInput relative moves work in this session."""
    user32 = ctypes.windll.user32

    class POINT(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    before = POINT()
    after = POINT()
    user32.GetCursorPos(ctypes.byref(before))
    dispatcher.move(40, 0)
    user32.GetCursorPos(ctypes.byref(after))
    moved = after.x - before.x
    log("Capture", f"SendInput verify: cursor moved {moved}px horizontally (expected ~40).")
    if moved == 0:
        log_error(
            "SendInput did not move the desktop cursor. "
            "Assist will likely be invisible in live mode."
        )


AIM_CAL_DPI = 800.0
AIM_CAL_SENS = 0.86
MOUSE_SENS_MIN = 0.0001
MOUSE_SENS_MAX = 200.0
# In-game sensitivity that matches CS2 0.86 at the same DPI.
GAME_SENS_AT_CAL = {
    "aimlabs": 0.378,
    "apex legends": AIM_CAL_SENS,
    "cs2": AIM_CAL_SENS,
    "deadlock": 0.43,
    "fortnite": 3.406,
    "pubg": 28.9,
    "r6": 3.302,
    "roblox": 0.01872,
    "rust": 0.168,
    "valorant": 0.27,
    "wz": 2.864,
    "universal": AIM_CAL_SENS,
}


def game_cal_sens(game_id: str) -> float:
    """Sensitivity in that game which matches the 800 DPI × CS2 0.86 calibration."""
    return float(GAME_SENS_AT_CAL.get(str(game_id or "").strip().lower(), AIM_CAL_SENS))


def _ease_lookup(table: Tuple[float, ...], t: float) -> float:
    """Read an ease table sampled evenly in time."""
    if not table:
        return t
    if t <= 0.0:
        return 0.0
    if t >= 1.0:
        return 1.0
    last = len(table) - 1
    pos = t * last
    i = int(pos)
    if i >= last:
        return table[last]
    return table[i] + (table[i + 1] - table[i]) * (pos - i)


def _humanizer_ease(
    cfg: AppConfig,
    library: "humanizer_paths.PathLibrary",
    offset: Tuple[float, float],
    rng: random.Random,
) -> Tuple[float, ...]:
    """Pick a recorded flick of a similar length to snap along, or nothing."""
    if cfg.rp_one_tap_humanizer <= 0.0 or not library.ok:
        return ()
    dx, dy = float(offset[0]), float(offset[1])
    distance = math.hypot(dx, dy)
    if distance <= 0.0:
        return ()
    flick = library.pick(distance, math.atan2(dy, dx), rng)
    return flick.ease if flick is not None else ()


def _humanizer_gap_px(
    aim_result: Optional[HumanAimResult],
    state: AdaptiveAimState,
    magnet_state: MagnetState,
    target_dx: float,
    target_dy: float,
) -> float:
    """``target_dx``/``target_dy`` are already 0.0 when there is no target."""
    """How far the aim still has to travel, taken from whichever module is
    doing the most work this tick. Each already tracks its own gap."""
    best = 0.0
    weight = 0.0
    if aim_result is not None:
        for dx, dy, gap in (
            (aim_result.director_dx, aim_result.director_dy, state.director_gap_px),
            (aim_result.corrector_dx, aim_result.corrector_dy, state.corrector_gap_px),
            (aim_result.tracking_dx, aim_result.tracking_dy, state.tracking_gap_px),
            (aim_result.spray_dx, aim_result.spray_dy, state.spray_gap_px),
        ):
            magnitude = math.hypot(dx, dy)
            if magnitude > weight and gap > 0.0:
                best, weight = gap, magnitude
    if magnet_state.gap_px > 0.0 and weight <= 0.0:
        best = magnet_state.gap_px
    if best <= 0.0:
        best = math.hypot(target_dx, target_dy)
    return best


def _shape_humanized(
    shaper: "humanizer_shaper.HumanizerShaper",
    library: "humanizer_paths.PathLibrary",
    cfg: AppConfig,
    assist_dx: int,
    assist_dy: int,
    aim_result: Optional[HumanAimResult],
    tap_dx: int,
    tap_dy: int,
    mag_dx: int,
    mag_dy: int,
    n_dx: int,
    n_dy: int,
    one_tap_dx: int,
    one_tap_dy: int,
    state: AdaptiveAimState,
    magnet_state: MagnetState,
    target_dx: Optional[float],
    target_dy: Optional[float],
    now: float,
    frame_dt: float,
) -> Tuple[int, int]:
    """Blend the recorded aim signature into this tick's assist.

    ``target_dx``/``target_dy`` are None on every tick with no target, which
    is most of them; that simply means there is no engagement to shape.

    One-tap is deliberately left out of the weighting: it keeps its own
    ledger of how far it still has to travel by subtracting the step it
    returned, so anything added to its output here would make it stop short.
    Its humanization happens inside the module instead.
    """
    aim_dx = float(target_dx) if target_dx is not None else 0.0
    aim_dy = float(target_dy) if target_dy is not None else 0.0
    contributions: list[tuple[float, float, float]] = []
    if aim_result is not None:
        contributions.extend(
            (
                (aim_result.director_dx, aim_result.director_dy, cfg.aim_director_humanizer),
                (aim_result.corrector_dx, aim_result.corrector_dy, cfg.aim_corrector_humanizer),
                (aim_result.tracking_dx, aim_result.tracking_dy, cfg.tracking_assist_humanizer),
                (aim_result.spray_dx, aim_result.spray_dy, cfg.spray_acquire_humanizer),
            )
        )
    contributions.append((float(mag_dx), float(mag_dy), cfg.magnet_humanizer))
    contributions.append((float(tap_dx), float(tap_dy), cfg.tap_humanizer))
    contributions.append((float(n_dx), float(n_dy), cfg.trigger_humanizer))

    scale = _mouse_output_scale(cfg)
    shaped_dx, shaped_dy = shaper.shape(
        library,
        assist_dx - one_tap_dx,
        assist_dy - one_tap_dy,
        blend=humanizer_shaper.blend_weight(tuple(contributions)),
        gap_px=_humanizer_gap_px(aim_result, state, magnet_state, aim_dx, aim_dy),
        aim_dx=aim_dx,
        aim_dy=aim_dy,
        px_per_count=1.0 / scale if scale > 1e-9 else 1.0,
        now=now,
        dt=frame_dt,
    )
    # The one-tap step goes back untouched.
    return shaped_dx + one_tap_dx, shaped_dy + one_tap_dy


def _mouse_output_scale(cfg: AppConfig) -> float:
    """HID counts per vision-pixel. 1.0 at 800 DPI when sens is that game's CS2 0.86 equivalent."""
    dpi = max(50.0, float(cfg.mouse_dpi))
    sens = max(MOUSE_SENS_MIN, float(cfg.mouse_sens))
    cal = game_cal_sens(game_id_from_engine(cfg.engine_path))
    return (AIM_CAL_DPI * cal) / (dpi * sens)


@dataclass
class MouseOutputState:
    rem_x: float = 0.0
    rem_y: float = 0.0


def _scale_mouse_output(
    state: MouseOutputState,
    cfg: AppConfig,
    dx: int,
    dy: int,
) -> Tuple[int, int]:
    """Convert a vision-pixel assist step into mouse counts for this DPI/sens."""
    if dx == 0 and dy == 0:
        return 0, 0
    scale = _mouse_output_scale(cfg)
    if abs(scale - 1.0) < 1e-9:
        return int(dx), int(dy)
    x = float(dx) * scale + state.rem_x
    y = float(dy) * scale + state.rem_y
    out_x = int(round(x))
    out_y = int(round(y))
    state.rem_x = x - out_x
    state.rem_y = y - out_y
    return out_x, out_y


def _clamp_assist(dx: float, dy: float, max_px: int) -> Tuple[int, int]:
    """Clamp assist vector magnitude to a per-frame safety limit."""
    if max_px <= 0:
        return 0, 0
    out_dx = int(round(dx))
    out_dy = int(round(dy))
    if out_dx == 0 and out_dy == 0:
        return 0, 0
    mag = math.hypot(out_dx, out_dy)
    if mag <= max_px:
        return out_dx, out_dy
    scale = max_px / mag
    return int(round(out_dx * scale)), int(round(out_dy * scale))


@dataclass
class HoldTriggerState:
    armed: bool = True
    last_fire_time: float = 0.0
    last_magnet_fire: float = 0.0
    mouse_down: bool = False
    aim_bone: str = "chest"
    next_interval_ms: float = 0.0
    next_magnet_interval_ms: float = 0.0
    trigger_engaged_at: float = 0.0
    magnet_engaged_at: float = 0.0
    # First-shot delay drawn from the user's recorded reactions for the current
    # engagement; None means use the configured delay.
    trigger_drawn_delay_ms: Optional[float] = None
    magnet_drawn_delay_ms: Optional[float] = None
    fired_trigger: bool = False
    fired_magnet: bool = False

    def clear_shot_timing(self) -> None:
        self.next_interval_ms = 0.0
        self.next_magnet_interval_ms = 0.0
        self.trigger_engaged_at = 0.0
        self.magnet_engaged_at = 0.0
        self.trigger_drawn_delay_ms = None
        self.magnet_drawn_delay_ms = None


@dataclass
class TapAssistState:
    lmb_down: bool = False
    ignore_until: float = 0.0
    primed: bool = False


@dataclass
class MagnetState:
    fx: float = 0.0
    fy: float = 0.0
    bone: Optional[str] = None
    gap_px: float = 0.0


@dataclass
class AdaptiveAimState:
    residual_x: float = 0.0
    residual_y: float = 0.0
    tracking_bbox: Optional[Tuple[float, float, float, float]] = None
    tracking_since: float = 0.0
    spray_held_since: float = 0.0
    vision_generation: int = -1
    motion_since_vision_x: float = 0.0
    motion_since_vision_y: float = 0.0
    director_bone: Optional[str] = None
    director_gap_px: float = 0.0
    corrector_gap_px: float = 0.0
    tracking_gap_px: float = 0.0
    spray_gap_px: float = 0.0
    spray_bbox: Optional[Tuple[float, float, float, float]] = None
    spray_switch_at: float = 0.0
    spray_switch_delay_ms: float = 0.0
    director_intent_x: float = 0.0
    director_intent_y: float = 0.0
    director_toggled: bool = False
    director_key_down: bool = False

    def clear_residual(self) -> None:
        self.residual_x = 0.0
        self.residual_y = 0.0

    def clear_tracking(self) -> None:
        self.tracking_bbox = None
        self.tracking_since = 0.0

    def begin_vision_generation(self, generation: int) -> None:
        if generation == self.vision_generation:
            return
        self.vision_generation = generation
        self.motion_since_vision_x = 0.0
        self.motion_since_vision_y = 0.0

    def predict_offset(
        self, offset: Optional[Tuple[float, float]]
    ) -> Optional[Tuple[float, float]]:
        if offset is None:
            return None
        # Mouse counts are only an approximation of screen pixels, but using
        # them conservatively prevents repeated assistance against one stale
        # detector frame after the user's movement has already crossed it.
        return (
            float(offset[0]) - self.motion_since_vision_x,
            float(offset[1]) - self.motion_since_vision_y,
        )

    def record_motion(self, dx: float, dy: float) -> None:
        self.motion_since_vision_x = max(
            -float(ROI_SIZE), min(float(ROI_SIZE), self.motion_since_vision_x + dx)
        )
        self.motion_since_vision_y = max(
            -float(ROI_SIZE), min(float(ROI_SIZE), self.motion_since_vision_y + dy)
        )


# Per-module activity logging. Modules run at the mouse loop rate, so a line
# is only written when a module starts (or finishes) doing something, never per
# tick. A module must sit idle for the grace window before the next engagement
# counts as new, which folds rapid on/off flicker into one line, and each entry
# is rate limited so a pathological case still cannot flood the console.
MODULE_LOG_GRACE_S = 0.45
MODULE_LOG_MIN_GAP_S = 1.2


def _build_detail(detail: Any, args: tuple) -> str:
    """Detail may be a plain string or a builder run only when logging."""
    return detail(*args) if callable(detail) else detail


@dataclass
class _ModuleLogEntry:
    active: bool = False
    idle_since: float = 0.0
    started_at: float = 0.0
    last_log_at: float = 0.0
    events: int = 0
    detail: str = ""
    skipped: int = 0
    extra: str = ""
    last_event_at: float = 0.0
    gaps: list[float] = field(default_factory=list)


class ModuleActivityLog:
    """Edge-triggered module logging: one line per engagement, never per tick."""

    def __init__(self) -> None:
        self._entries: dict[str, _ModuleLogEntry] = {}

    def _entry(self, key: str) -> _ModuleLogEntry:
        entry = self._entries.get(key)
        if entry is None:
            entry = _ModuleLogEntry()
            self._entries[key] = entry
        return entry

    def _emit(self, entry: _ModuleLogEntry, tag: str, now: float, detail: str) -> None:
        entry.last_log_at = now
        if entry.skipped:
            detail = f"{detail} (+{entry.skipped} more not shown)"
            entry.skipped = 0
        log(tag, detail)

    def _rate_limited(self, entry: _ModuleLogEntry, now: float) -> bool:
        if now - entry.last_log_at >= MODULE_LOG_MIN_GAP_S:
            return False
        entry.skipped += 1
        return True

    def _idle_settled(self, entry: _ModuleLogEntry, now: float) -> bool:
        """True once the module has been idle long enough to end an engagement."""
        if entry.idle_since <= 0.0:
            entry.idle_since = now
            return False
        return (now - entry.idle_since) >= MODULE_LOG_GRACE_S

    def engaged(self, key: str, tag: str, now: float, active: bool, detail: Any, *args: Any) -> None:
        """Continuous module: log once when it starts acting on a target.

        `detail` may be a builder function, called with `args` only when a line
        is really written, so the hot path never formats a discarded string.
        """
        entry = self._entry(key)
        if active:
            entry.idle_since = 0.0
            if not entry.active:
                entry.active = True
                entry.started_at = now
                if not self._rate_limited(entry, now):
                    self._emit(entry, tag, now, _build_detail(detail, args))
        elif entry.active and self._idle_settled(entry, now):
            entry.active = False
            entry.idle_since = 0.0

    def burst(
        self,
        key: str,
        tag: str,
        now: float,
        fired: bool,
        detail: Any,
        extra: Any = "",
        *args: Any,
    ) -> None:
        """Repeating action: count it, then log one summary when the burst ends."""
        entry = self._entry(key)
        if fired:
            entry.idle_since = 0.0
            if not entry.active:
                entry.active = True
                entry.started_at = now
                entry.events = 0
                entry.gaps.clear()
            elif entry.last_event_at > 0.0:
                entry.gaps.append((now - entry.last_event_at) * 1000.0)
            entry.last_event_at = now
            entry.events += 1
            entry.detail = _build_detail(detail, args)
            entry.extra = _build_detail(extra, args)
            return
        if not entry.active or not self._idle_settled(entry, now):
            return
        count = entry.events
        held = max(0.0, now - entry.started_at - MODULE_LOG_GRACE_S)
        gaps = list(entry.gaps)
        entry.active = False
        entry.idle_since = 0.0
        entry.events = 0
        entry.last_event_at = 0.0
        entry.gaps.clear()
        if count <= 0 or self._rate_limited(entry, now):
            return
        text = f"{count} {entry.detail.format(s='' if count == 1 else 's')}"
        if count > 1 and held >= 0.2:
            text = f"{text} over {held:.1f}s"
        if gaps:
            shown = ", ".join(f"{g:.0f}" for g in gaps[:5])
            if len(gaps) > 5:
                shown = f"{shown}, ..."
            text = f"{text}, gaps {shown}ms"
        if entry.extra:
            text = f"{text} ({entry.extra})"
        self._emit(entry, tag, now, text)


def _bone_label(bone: Optional[str]) -> str:
    return str(bone).replace("_", " ") if bone else "target"


# Detail builders. These only run when a line is actually written, so the mouse
# loop never formats a string it is about to throw away.


def _detail_director(cfg: AppConfig, st: "AdaptiveAimState") -> str:
    mode = cfg.aim_director_mode
    arming = "always on" if mode == "always" else f"{mode} on {cfg.aim_director_key_name or 'unbound'}"
    return (
        f"pulling to {_bone_label(st.director_bone)} from {st.director_gap_px:.0f}px"
        f" (force {cfg.aim_director_strength:.0f}%, fov {cfg.aim_director_radius_px:.0f}px,"
        f" {cfg.aim_director_curve.replace('_', ' ')}, {arming})"
    )


def _detail_corrector(cfg: AppConfig, st: "AdaptiveAimState") -> str:
    return (
        f"bending flick toward {_bone_label(cfg.aim_corrector_bone)} from {st.corrector_gap_px:.0f}px"
        f" (correction {cfg.aim_corrector_strength:.0f}%,"
        f" max bend {cfg.aim_corrector_max_bend_deg:.0f}deg,"
        f" fov {cfg.aim_corrector_radius_px:.0f}px)"
    )


def _detail_tracking(cfg: AppConfig, st: "AdaptiveAimState") -> str:
    return (
        f"softening drift {st.tracking_gap_px:.0f}px off the box center"
        f" (retention {cfg.tracking_assist_strength:.0f}%,"
        f" hold {cfg.tracking_assist_radius_px:.0f}px,"
        f" breakaway {cfg.tracking_assist_breakaway_px:.0f}px)"
    )


def _detail_spray(cfg: AppConfig, st: "AdaptiveAimState") -> str:
    switch = ""
    if cfg.spray_switch_delay_ms > 0.0 or cfg.spray_switch_delay_max_ms > 0.0:
        switch = (
            f", switch delay {cfg.spray_switch_delay_ms:.0f}-"
            f"{cfg.spray_switch_delay_max_ms:.0f}ms"
        )
        if st.spray_switch_delay_ms > 0.0:
            switch = f"{switch}, waited {st.spray_switch_delay_ms:.0f}ms"
    return (
        f"closing {st.spray_gap_px:.0f}px sideways onto the closest player"
        f" (force {cfg.spray_acquire_strength:.0f}%,"
        f" fov {cfg.spray_acquire_radius_px:.0f}px{switch})"
    )


def _detail_magnet_pull(cfg: AppConfig, st: "MagnetState") -> str:
    return (
        f"pulling to {_bone_label(st.bone)} from {st.gap_px:.0f}px"
        f" (speed {cfg.magnet_speed:.0f}, force {cfg.magnet_strength:.0f}%,"
        f" smooth {cfg.magnet_smooth:.0f}%, fov {cfg.magnet_radius_px:.0f}px)"
    )


def _detail_rp_aim(cfg: AppConfig) -> str:
    released = ", released while firing" if cfg.rp_aim_release_on_shot else ""
    return (
        f"slowing mouse to {100.0 - cfg.rp_aim_slowdown:.0f}% speed on a selected bone"
        f" (ramp in {cfg.rp_aim_ramp_ms:.0f}ms, out {cfg.rp_aim_ramp_out_ms:.0f}ms,"
        f" margin {cfg.rp_aim_radius_px:.0f}px{released})"
    )


def _detail_shots(cfg: AppConfig, st: "HoldTriggerState") -> str:
    return f"shot{{s}} on {_bone_label(st.aim_bone)}"


def _detail_trigger_cfg(cfg: AppConfig, st: "HoldTriggerState") -> str:
    return (
        f"fov {cfg.trigger_radius_px:.0f}px,"
        f" interval {cfg.trigger_interval_ms:.0f}-{cfg.trigger_interval_max_ms:.0f}ms,"
        f" delay {cfg.trigger_initial_delay_ms:.0f}ms"
    )


def _detail_magnet_fire_cfg(cfg: AppConfig, st: "HoldTriggerState") -> str:
    return (
        f"fire fov {cfg.magnet_trigger_radius_px:.0f}px,"
        f" interval {cfg.magnet_trigger_interval_ms:.0f}-{cfg.magnet_trigger_interval_max_ms:.0f}ms,"
        f" delay {cfg.magnet_trigger_initial_delay_ms:.0f}ms"
    )


def _detail_tap(cfg: AppConfig, st: "HoldTriggerState") -> str:
    return f"click nudge{{s}} toward {_bone_label(cfg.tap_bone)}"


def _detail_tap_cfg(cfg: AppConfig, st: "HoldTriggerState") -> str:
    on_trigger = ", incl. trigger clicks" if cfg.tap_on_trigger else ""
    return f"max {cfg.tap_max_nudge_px:.0f}px, fov {cfg.tap_range_px:.0f}px{on_trigger}"


def _detail_one_tap(cfg: AppConfig, st: "RpOneTapState") -> str:
    return f"tap{{s}} snapped onto the head from {st.acquire_start_mag:.0f}px"


def _detail_one_tap_cfg(cfg: AppConfig, st: "RpOneTapState") -> str:
    return (
        f"fov {cfg.rp_one_tap_fov_px:.0f}px,"
        f" speed {cfg.rp_one_tap_speed:.0f}px/s,"
        f" smoothness {cfg.rp_one_tap_smooth_pct:.0f}%,"
        f" cooldown {cfg.rp_one_tap_cooldown_ms:.0f}ms"
    )


def _log_module_activity(
    activity: ModuleActivityLog,
    cfg: AppConfig,
    now: float,
    aim: Optional[HumanAimResult],
    aim_state: AdaptiveAimState,
    magnet_state: MagnetState,
    hold_state: HoldTriggerState,
    rp_aim_state: "RpAimState",
    one_tap_state: "RpOneTapState",
    tap_moved: bool,
    one_tap_click: bool,
) -> None:
    """Feed every module's current activity to the edge-triggered logger."""
    activity.engaged(
        "director", "AimDirector", now, bool(aim and (aim.director_dx or aim.director_dy)),
        _detail_director, cfg, aim_state,
    )
    activity.engaged(
        "corrector", "AimCorrector", now, bool(aim and (aim.corrector_dx or aim.corrector_dy)),
        _detail_corrector, cfg, aim_state,
    )
    activity.engaged(
        "tracking", "TrackingAssist", now, bool(aim and (aim.tracking_dx or aim.tracking_dy)),
        _detail_tracking, cfg, aim_state,
    )
    activity.engaged(
        "spray", "SprayAssist", now, bool(aim and (aim.spray_dx or aim.spray_dy)),
        _detail_spray, cfg, aim_state,
    )
    activity.engaged(
        "magnet_pull", "MagnetTrigger", now, magnet_state.bone is not None,
        _detail_magnet_pull, cfg, magnet_state,
    )
    activity.engaged(
        "rp_aim", "AimAssist", now, bool(rp_aim_state.slowed),
        _detail_rp_aim, cfg,
    )
    activity.burst(
        "trigger_fire", "Trigger", now, hold_state.fired_trigger,
        _detail_shots, _detail_trigger_cfg, cfg, hold_state,
    )
    activity.burst(
        "magnet_fire", "MagnetTrigger", now, hold_state.fired_magnet,
        _detail_shots, _detail_magnet_fire_cfg, cfg, hold_state,
    )
    activity.burst(
        "tap", "TapAssist", now, tap_moved,
        _detail_tap, _detail_tap_cfg, cfg, hold_state,
    )
    activity.burst(
        "one_tap", "OneTapAssist", now, one_tap_click,
        _detail_one_tap, _detail_one_tap_cfg, cfg, one_tap_state,
    )


def _hold_key_down(enabled: bool, vk: Optional[int]) -> bool:
    return bool(enabled and vk is not None and _is_key_held(vk))


def _poll_magnet(
    config: AppConfig,
    state: MagnetState,
    target: Optional[object],
    dt: float,
    hz_gain: Callable[[float], float] = lambda _pct: 1.0,
) -> Tuple[int, int]:
    """Pull toward selected magnet bones while the magnet key is held."""
    held = _hold_key_down(config.magnet, config.magnet_vk)
    if not held:
        state.fx = 0.0
        state.fy = 0.0
        state.bone = None
        return 0, 0
    aim, bone = _magnet_aim_offset(
        target, config.magnet_bones, config.magnet_radius_px, state.bone
    )
    state.bone = bone
    if aim is None:
        state.fx = 0.0
        state.fy = 0.0
        return 0, 0
    dx, dy = float(aim[0]), float(aim[1])
    remaining = math.hypot(dx, dy)
    state.gap_px = remaining
    if remaining < 0.4:
        state.fx = 0.0
        state.fy = 0.0
        return 0, 0
    # 100 px is the old speed of 5, so saved pulls keep the same close rate.
    speed = max(0.0, min(100.0, float(config.magnet_speed))) * 0.05
    strength = max(0.0, min(100.0, float(config.magnet_strength))) / 100.0
    frac = (1.0 - math.exp(-3.6 * speed * max(dt, 1e-4))) * strength
    # Recorded flick speed at this point in the pull. The clamps below still
    # hold the step to the remaining gap and to max_assist_px.
    frac *= max(1.0, hz_gain(config.magnet_humanizer))
    raw_x = dx * frac
    raw_y = dy * frac
    smooth = max(0.0, min(100.0, float(config.magnet_smooth))) / 100.0
    if smooth <= 1e-4:
        state.fx, state.fy = raw_x, raw_y
    else:
        rate = max(0.4, (1.0 - smooth) * 16.0)
        alpha = 1.0 - math.exp(-rate * max(dt, 1e-4))
        state.fx += (raw_x - state.fx) * alpha
        state.fy += (raw_y - state.fy) * alpha
    out_mag = math.hypot(state.fx, state.fy)
    if out_mag > remaining > 0.0:
        scale = remaining / out_mag
        state.fx *= scale
        state.fy *= scale
        out_mag = remaining
    cap = float(config.max_assist_px)
    if cap > 0.0 and out_mag > cap:
        scale = cap / out_mag
        state.fx *= scale
        state.fy *= scale
    return int(round(state.fx)), int(round(state.fy))


def _bbox_center_spot(
    target: Optional[object],
) -> Optional[Tuple[float, float]]:
    bbox = _target_bbox(target) if target is not None else None
    if bbox is None:
        return None
    x1, y1, x2, y2 = bbox
    return (x1 + x2) * 0.5, (y1 + y2) * 0.5


def _bbox_center_offset(
    target: Optional[object],
) -> Optional[Tuple[float, float]]:
    """Offset from crosshair to the target box center."""
    if target is None:
        return None
    hx, hy = ROI_CENTER
    spot = _bbox_center_spot(target)
    if spot is None:
        return float(target.dx), float(target.dy)
    return spot[0] - hx, spot[1] - hy


def _bone_aim_offset(
    target: Optional[object],
    bone: str,
) -> Optional[Tuple[float, float]]:
    """Offset from crosshair to one named bone on the target box."""
    if target is None:
        return None
    name = bone if bone in AIM_BONE_FROM_TOP else "chest"
    bbox = _target_bbox(target)
    hx, hy = ROI_CENTER
    if bbox is None:
        return float(target.dx), float(target.dy)
    px, py = hitbox_point(bbox, name)
    return px - hx, py - hy


ADAPTIVE_TARGET_STALE_S = 0.25
ADAPTIVE_AIM_HARD_CAP_PX = 8.0
TRACKING_TARGET_IOU = 0.18

# --- RP2040 MITM modules --------------------------------------------------
# These do not inject movement at all. They command the firmware to modify
# the physical mouse's own packets (SCALE = slow it down), which is only
# possible when the mouse is plugged into the Feather's USB-A.

RP_SCALE_REASSERT_S = 0.1  # re-send SCALE while on target (see timeout below)
RP_SCALE_TIMEOUT_MS = 300  # firmware auto-reverts if we stop re-asserting
RP_ARM_REASSERT_S = 0.1
RP_ARM_TIMEOUT_MS = 300
RP_ONE_TAP_MAX_STEP_PX = 32.0
RP_ONE_TAP_MAX_ACQUIRE_S = 0.12
RP_ONE_TAP_MIN_ACQUIRE_S = 0.008
RP_ONE_TAP_LATCH_TTL_S = 0.12
RP_ONE_TAP_LOCKOUT_S = 0.12


def _crosshair_over_target(
    target: Optional[object],
    margin_px: float,
    now: float,
    vision_updated_at: float,
    bones: tuple[str, ...] = (),
) -> bool:
    """True when the crosshair sits on a selected bone and vision is fresh."""
    if target is None or not bones:
        return False
    if vision_updated_at <= 0.0 or (now - vision_updated_at) > ADAPTIVE_TARGET_STALE_S:
        return False
    return _is_on_target(target, margin_px, bones)


@dataclass
class RpAimState:
    slowed: bool = False
    last_assert: float = 0.0
    last_q8: int = 256
    current_scale: float = 1.0
    ramp_t0: float = 0.0
    ramp_from: float = 1.0
    ramp_to: float = 1.0


def _send_rp_aim_scale(
    link: Any,
    state: RpAimState,
    scale: float,
    now: float,
    *,
    hold: bool,
) -> None:
    q8 = max(0, min(1024, int(round(float(scale) * 256.0))))
    timeout = RP_SCALE_TIMEOUT_MS if hold and q8 != 256 else 0
    due = (now - state.last_assert) >= RP_SCALE_REASSERT_S
    if q8 == state.last_q8 and state.slowed == (q8 != 256) and not (hold and due):
        state.current_scale = q8 / 256.0
        return
    link.send_scale(q8 / 256.0, timeout)
    state.last_q8 = q8
    state.last_assert = now
    state.current_scale = q8 / 256.0
    state.slowed = q8 != 256


def _poll_rp_aim_assist(
    cfg: AppConfig,
    state: RpAimState,
    link: Any,
    on_target: bool,
    now: float,
) -> None:
    """Slow the physical mouse while the crosshair is on a selected bone.

    SCALE is sent with a short firmware-side timeout and re-asserted every
    RP_SCALE_REASSERT_S while on target, so a dropped link or a crashed app
    can never leave the mouse permanently slowed. Firing with release-on-shot
    or disabling the module restores 1.0 immediately. Leaving a bone eases
    back over Ramp out.
    """
    enabled = bool(cfg.rp_aim_assist) and cfg.rp_aim_slowdown > 0.0
    shooting = enabled and cfg.rp_aim_release_on_shot and _is_key_held(VK_LBUTTON)
    want = 1.0
    if enabled and on_target and not shooting:
        want = 1.0 - cfg.rp_aim_slowdown / 100.0

    slowing = want < 0.999
    if slowing:
        ramp_s = max(0.0, float(cfg.rp_aim_ramp_ms)) * 0.001
    elif not enabled or shooting:
        if state.slowed or state.current_scale < 0.999:
            _send_rp_aim_scale(link, state, 1.0, now, hold=False)
        state.ramp_to = 1.0
        state.ramp_from = 1.0
        return
    else:
        ramp_s = max(0.0, float(cfg.rp_aim_ramp_out_ms)) * 0.001
        if ramp_s <= 0.0 or (not state.slowed and state.current_scale >= 0.999):
            if state.slowed or state.current_scale < 0.999:
                _send_rp_aim_scale(link, state, 1.0, now, hold=False)
            state.ramp_to = 1.0
            state.ramp_from = 1.0
            return

    if abs(want - state.ramp_to) > 1e-4:
        state.ramp_from = state.current_scale
        state.ramp_to = want
        state.ramp_t0 = now

    if ramp_s <= 0.0:
        scale = want
    else:
        t = min(1.0, (now - state.ramp_t0) / max(ramp_s, 1e-6))
        ease = t * t * (3.0 - 2.0 * t)
        scale = state.ramp_from + (state.ramp_to - state.ramp_from) * ease
    _send_rp_aim_scale(link, state, scale, now, hold=scale < 0.999)


@dataclass
class RpOneTapState:
    armed: bool = False
    last_assert: float = 0.0
    last_phys_lmb: float = 0.0
    cooldown_cleared: bool = True
    acquiring: bool = False
    snapped: bool = False
    acquire_t0: float = 0.0
    acquire_duration: float = 0.0
    acquire_start_mag: float = 0.0
    latched_dx: float = 0.0
    latched_dy: float = 0.0
    latch_valid: bool = False
    latch_at: float = 0.0
    acq_dx: float = 0.0
    acq_dy: float = 0.0
    click_only: bool = False
    # Ease table from a recorded flick, chosen when this snap started.
    hz_ease: tuple = ()
    frozen: bool = False
    lockout_until: float = 0.0


def _rp_one_tap_busy(state: RpOneTapState, now: float) -> bool:
    """True while a tap is in flight or just finished — other modules must yield."""
    return bool(
        state.acquiring
        or state.click_only
        or (state.lockout_until > 0.0 and now < state.lockout_until)
    )


def _rp_one_tap_finish(
    state: RpOneTapState,
    link: Any,
    tap_state: "TapAssistState",
    now: float,
    dry_run: bool,
) -> None:
    """End the snap; caller fires the swallowed shot after the last move."""
    if state.frozen and not dry_run:
        link.send_freeze(0)
    state.acquiring = False
    state.snapped = False
    state.frozen = False
    state.click_only = False
    state.acq_dx = 0.0
    state.acq_dy = 0.0
    state.lockout_until = now + RP_ONE_TAP_LOCKOUT_S
    tap_state.ignore_until = now + TAP_INJECT_IGNORE_S


def _rp_one_tap_unfreeze(
    state: RpOneTapState,
    link: Any,
    dry_run: bool,
) -> None:
    if state.frozen and not dry_run:
        link.send_freeze(0)
    state.frozen = False


def _poll_rp_one_tap_assist(
    cfg: AppConfig,
    state: RpOneTapState,
    link: Any,
    tap_state: "TapAssistState",
    aim_state: AdaptiveAimState,
    target: Optional[object],
    now: float,
    vision_updated_at: float,
    hz_paths: "humanizer_paths.PathLibrary",
    hz_rng: random.Random,
) -> Tuple[int, int, bool]:
    """ARM in FOV; one frozen snap on withheld down; click only on a consumed tap."""
    withheld = False
    consumed = False
    passed = False
    for event in link.drain_lmb_events():
        state.last_phys_lmb = event.at
        state.cooldown_cleared = False
        if event.withheld:
            withheld = True
        elif event.consumed:
            consumed = True
        else:
            passed = True

    vision_fresh = (
        vision_updated_at > 0.0
        and (now - vision_updated_at) <= ADAPTIVE_TARGET_STALE_S
    )
    fov = float(cfg.rp_one_tap_fov_px)
    reset_fov = max(fov, float(cfg.rp_one_tap_reset_fov_px))
    live = (
        aim_state.predict_offset(_bone_aim_offset(target, "head"))
        if vision_fresh
        else None
    )
    live_mag = (
        math.hypot(live[0], live[1]) if live is not None else float("inf")
    )
    if live is not None and live_mag < fov:
        state.latched_dx = float(live[0])
        state.latched_dy = float(live[1])
        state.latch_valid = True
        state.latch_at = now
    elif live is not None and live_mag >= reset_fov and not state.acquiring:
        state.latch_valid = False
        state.latched_dx = 0.0
        state.latched_dy = 0.0

    latch_fresh = bool(
        state.latch_valid
        and state.latch_at > 0.0
        and (now - state.latch_at) <= RP_ONE_TAP_LATCH_TTL_S
    )
    # The latch is only a stand-in for frames where vision has not refreshed,
    # and it may be up to RP_ONE_TAP_LATCH_TTL_S old. Whenever a live head is
    # on screen, prefer it, so the snap follows a head that is still moving
    # instead of landing where it used to be. Bounded to the engage FOV: the
    # tap was aimed at a head inside that circle, so one that leaves it is not
    # chased, it just keeps the position it had.
    if live is not None and live_mag < fov:
        aim: Optional[Tuple[float, float]] = (float(live[0]), float(live[1]))
    elif latch_fresh:
        aim = (state.latched_dx, state.latched_dy)
    else:
        aim = None

    if state.acquiring:
        if aim is not None:
            state.acq_dx, state.acq_dy = aim
        offset: Optional[Tuple[float, float]] = (state.acq_dx, state.acq_dy)
    else:
        offset = aim

    mag = math.hypot(offset[0], offset[1]) if offset is not None else float("inf")
    if live is not None and live_mag >= reset_fov:
        state.cooldown_cleared = True
    elif live is None and not state.acquiring:
        state.cooldown_cleared = True

    cooldown_s = max(0.0, float(cfg.rp_one_tap_cooldown_ms)) * 0.001
    cooled = state.cooldown_cleared or (
        state.last_phys_lmb <= 0.0 or (now - state.last_phys_lmb) >= cooldown_s
    )
    want_arm = (
        bool(cfg.rp_one_tap_assist)
        and not cfg.dry_run
        and not state.acquiring
        and latch_fresh
        and cooled
    )

    # Never disarm while the firmware is holding a click for us. Receiving the
    # withheld click sets last_phys_lmb, which drops `cooled` on that very
    # tick, so a plain else-disarm would tell the firmware to let go of the
    # click before the snap has moved anywhere: the shot would land first and
    # the correction would arrive after it, which is not the point of one tap.
    intercept_in_flight = bool(withheld or state.acquiring or state.click_only)
    if want_arm:
        if not state.armed or (now - state.last_assert) >= RP_ARM_REASSERT_S:
            link.send_arm(True, cfg.rp_one_tap_hold_ms, RP_ARM_TIMEOUT_MS)
            state.armed = True
            state.last_assert = now
    elif state.armed and not intercept_in_flight:
        link.send_arm(False, cfg.rp_one_tap_hold_ms, 0)
        state.armed = False
    elif state.armed and (now - state.last_assert) >= RP_ARM_REASSERT_S:
        # Keep the firmware's arm timeout alive so it does not release the
        # click on its own part-way through the snap.
        link.send_arm(True, cfg.rp_one_tap_hold_ms, RP_ARM_TIMEOUT_MS)
        state.last_assert = now

    def _step_toward(amount: float, limit: float = RP_ONE_TAP_MAX_STEP_PX) -> Tuple[int, int]:
        if offset is None or amount <= 0.0:
            return 0, 0
        current = math.hypot(offset[0], offset[1])
        if current <= 1e-6:
            return 0, 0
        amount = min(amount, limit, fov, current)
        return (
            int(round(offset[0] / current * amount)),
            int(round(offset[1] / current * amount)),
        )

    if passed and (state.acquiring or state.click_only):
        _rp_one_tap_finish(state, link, tap_state, now, cfg.dry_run)
        return 0, 0, False

    if withheld and cfg.rp_one_tap_assist and not state.acquiring:
        can_snap = latch_fresh and mag < fov
        if can_snap and offset is not None:
            state.acquiring = True
            state.snapped = False
            state.click_only = False
            state.hz_ease = _humanizer_ease(cfg, hz_paths, offset, hz_rng)
            state.acq_dx = float(offset[0])
            state.acq_dy = float(offset[1])
            state.acquire_t0 = now
            # Travel time comes from Speed, so a longer flick takes longer
            # rather than every correction being one indistinguishable jump.
            # Bounded by the hold window so the movement normally finishes
            # before the click is released, and by a hard ceiling either way.
            hold_s = max(0.0, float(cfg.rp_one_tap_hold_ms)) * 0.001
            ceiling = RP_ONE_TAP_MAX_ACQUIRE_S
            if hold_s > 0.0:
                ceiling = min(ceiling, hold_s)
            state.acquire_duration = max(
                RP_ONE_TAP_MIN_ACQUIRE_S,
                min(ceiling, mag / max(1.0, float(cfg.rp_one_tap_speed))),
            )
            state.acquire_start_mag = mag if mag < 1e6 else 0.0
            freeze_ms = int(round(state.acquire_duration * 1000.0)) + 24
            if freeze_ms > 0 and not cfg.dry_run:
                link.send_freeze(min(500, freeze_ms))
                state.frozen = True
        else:
            state.click_only = True

    if consumed:
        if state.click_only and not state.acquiring:
            _rp_one_tap_finish(state, link, tap_state, now, cfg.dry_run)
            return 0, 0, True
        if not cfg.rp_one_tap_assist and not state.acquiring:
            tap_state.ignore_until = now + TAP_INJECT_IGNORE_S
            return 0, 0, True
        # Last chance before the click goes out: close the whole remaining gap,
        # not just one eased step, so the shot lands on the head.
        remaining = mag if offset is not None and mag < fov and not state.snapped else 0.0
        move = _step_toward(remaining, limit=fov)
        _rp_one_tap_finish(state, link, tap_state, now, cfg.dry_run)
        return move[0], move[1], True

    if not state.acquiring or state.snapped:
        return 0, 0, False

    duration = state.acquire_duration
    elapsed = now - state.acquire_t0
    stop = HUMAN_AIM_STOP_RADIUS_PX
    if offset is None or mag >= 1e6 or mag <= stop or elapsed >= duration:
        step = _step_toward(mag if offset is not None and mag < fov else 0.0, limit=fov)
        state.acq_dx -= float(step[0])
        state.acq_dy -= float(step[1])
        state.snapped = True
        _rp_one_tap_unfreeze(state, link, cfg.dry_run)
        return step[0], step[1], False

    # Smoothness blends a constant-speed slide into a smoothstep: ramp up,
    # quickest through the middle, settle on the head. That reads as a flick,
    # where a plain ease-out just lurches at the start and crawls at the end.
    t = min(1.0, elapsed / max(duration, 1e-6))
    smooth = max(0.0, min(1.0, float(cfg.rp_one_tap_smooth_pct) * 0.01))
    shaped = t + (t * t * (3.0 - 2.0 * t) - t) * smooth
    if state.hz_ease:
        # Trace the user's own flick instead, but never fall behind the
        # built-in curve: the snap must not take longer than it would have.
        blend = max(0.0, min(1.0, float(cfg.rp_one_tap_humanizer) * 0.01))
        human = _ease_lookup(state.hz_ease, t)
        shaped = max(shaped, shaped + (human - shaped) * blend)
    target_rem = state.acquire_start_mag * (1.0 - shaped)
    take = mag - target_rem
    if take <= 0.0:
        return 0, 0, False
    step = _step_toward(min(take, max(0.0, mag - stop)), limit=fov)
    state.acq_dx -= float(step[0])
    state.acq_dy -= float(step[1])
    if math.hypot(state.acq_dx, state.acq_dy) <= stop:
        state.snapped = True
        _rp_one_tap_unfreeze(state, link, cfg.dry_run)
    return step[0], step[1], False


def _lead_target(target: Any, lead_dx: float, lead_dy: float) -> Any:
    """Target moved to where prediction says it will be.

    Every module resolves its aim point from the target's bbox, so shifting the
    box here makes director, corrector, tracking, spray, tap, one tap, trigger
    and magnet all lead the same way without any of them knowing about it.
    """
    if lead_dx == 0.0 and lead_dy == 0.0:
        return target
    fields: dict[str, Any] = {}
    bbox = getattr(target, "bbox", None)
    if bbox is not None:
        fields["bbox"] = (bbox[0] + lead_dx, bbox[1] + lead_dy, bbox[2] + lead_dx, bbox[3] + lead_dy)
    head = getattr(target, "head_bbox", None)
    if head is not None:
        fields["head_bbox"] = (head[0] + lead_dx, head[1] + lead_dy, head[2] + lead_dx, head[3] + lead_dy)
    for name in ("raw_centroid", "compensated_centroid"):
        point = getattr(target, name, None)
        if point is not None:
            fields[name] = (point[0] + lead_dx, point[1] + lead_dy)
    if hasattr(target, "dx"):
        fields["dx"] = float(target.dx) + lead_dx
        fields["dy"] = float(target.dy) + lead_dy
    if not fields:
        return target
    try:
        return replace(target, **fields)
    except TypeError:
        return target


def _spray_target_ready(
    config: AppConfig,
    state: AdaptiveAimState,
    target: Optional[object],
    now: float,
) -> bool:
    """Hesitate for a random delay before sliding onto a different player.

    The first player of a spray is taken immediately; only a genuine switch to
    another player waits. While waiting, spray contributes no movement at all.
    """
    bbox = _target_bbox(target) if target is not None else None
    if bbox is None:
        return True
    had_target = state.spray_bbox is not None
    same_target = (
        had_target and bbox_iou(state.spray_bbox, bbox) >= TRACKING_TARGET_IOU
    )
    state.spray_bbox = bbox
    if not had_target:
        # First player of this spray: take it straight away.
        state.spray_switch_at = 0.0
        state.spray_switch_delay_ms = 0.0
        return True
    if same_target:
        return state.spray_switch_at <= 0.0 or now >= state.spray_switch_at
    if state.spray_switch_at > 0.0 and now < state.spray_switch_at:
        # Already hesitating and the player changed again: keep waiting out the
        # existing delay instead of restarting the clock on every flicker.
        return False
    delay_ms = _pick_interval_ms(
        config.spray_switch_delay_ms, config.spray_switch_delay_max_ms
    )
    state.spray_switch_delay_ms = delay_ms
    if delay_ms <= 0.0:
        state.spray_switch_at = 0.0
        return True
    state.spray_switch_at = now + delay_ms * 0.001
    return False


def _tracking_target_ready(
    config: AppConfig,
    state: AdaptiveAimState,
    target: Optional[object],
    target_offset: Optional[Tuple[float, float]],
    now: float,
) -> bool:
    """Require a short, stable on-target dwell before retention can engage."""
    if (
        not config.tracking_assist
        or target is None
        or target_offset is None
        or math.hypot(*target_offset) >= config.tracking_assist_radius_px
    ):
        state.clear_tracking()
        return False

    bbox = _target_bbox(target)
    if bbox is None:
        state.clear_tracking()
        return False
    same_target = (
        state.tracking_bbox is not None
        and bbox_iou(state.tracking_bbox, bbox) >= TRACKING_TARGET_IOU
    )
    if not same_target:
        state.tracking_bbox = bbox
        state.tracking_since = now
        return config.tracking_assist_dwell_ms <= 0.0
    state.tracking_bbox = bbox
    dwell_s = max(0.0, config.tracking_assist_dwell_ms) * 0.001
    return (now - state.tracking_since) >= dwell_s


def _quantize_adaptive_aim(
    state: AdaptiveAimState,
    dx: float,
    dy: float,
    max_assist_px: int,
) -> Tuple[int, int]:
    """Carry sub-pixel help across active frames without a delayed stale nudge."""
    if dx == 0.0 and dy == 0.0:
        state.clear_residual()
        return 0, 0

    cap = min(float(max(0, max_assist_px)), ADAPTIVE_AIM_HARD_CAP_PX)
    if cap <= 0.0:
        state.clear_residual()
        return 0, 0
    mag = math.hypot(dx, dy)
    if mag > cap:
        scale = cap / mag
        dx *= scale
        dy *= scale

    # Never let fractional carry from the previous direction create a kick
    # after the user changes direction.
    if state.residual_x * dx + state.residual_y * dy < 0.0:
        state.clear_residual()
    combined_x = dx + state.residual_x
    combined_y = dy + state.residual_y
    out_x, out_y = _clamp_assist(
        combined_x,
        combined_y,
        max(1, int(math.ceil(cap))),
    )
    state.residual_x = max(-0.999, min(0.999, combined_x - out_x))
    state.residual_y = max(-0.999, min(0.999, combined_y - out_y))
    return out_x, out_y


def _poll_adaptive_aim(
    config: AppConfig,
    state: AdaptiveAimState,
    target: Optional[object],
    user_dx: float,
    user_dy: float,
    now: float,
    frame_dt: float,
    vision_updated_at: float,
    vision_generation: int,
    spray_active: bool = False,
    hz_gain: Callable[[float], float] = lambda _pct: 1.0,
) -> Tuple[int, int, HumanAimResult]:
    """Run the independent cooperative aim modules."""
    spray_on = config.spray_acquire and spray_active
    if spray_on:
        if state.spray_held_since <= 0.0:
            state.spray_held_since = now
    else:
        state.spray_held_since = 0.0
        state.spray_bbox = None
        state.spray_switch_at = 0.0
        state.spray_switch_delay_ms = 0.0
    director_on = _director_armed(config, state)
    if director_on:
        decay = math.exp(-max(frame_dt, 0.0) / DIRECTOR_INTENT_TAU_S)
        state.director_intent_x = state.director_intent_x * decay + user_dx
        state.director_intent_y = state.director_intent_y * decay + user_dy
    else:
        state.director_bone = None
        state.director_intent_x = state.director_intent_y = 0.0
    any_enabled = (
        director_on
        or config.aim_corrector
        or config.tracking_assist
        or spray_on
    )
    target_is_fresh = (
        target is not None
        and vision_updated_at > 0.0
        and (now - vision_updated_at) <= ADAPTIVE_TARGET_STALE_S
    )
    if not any_enabled or not target_is_fresh:
        state.clear_residual()
        state.clear_tracking()
        state.director_bone = None
        return 0, 0, HumanAimResult()

    state.begin_vision_generation(vision_generation)
    tracking_target = (
        state.predict_offset(_bbox_center_offset(target))
        if config.tracking_assist
        else None
    )
    tracking_ready = _tracking_target_ready(
        config,
        state,
        target,
        tracking_target,
        now,
    )
    if (
        user_dx == 0.0
        and user_dy == 0.0
        and not director_on
        and not spray_on
    ):
        state.clear_residual()
        return 0, 0, HumanAimResult()

    director_dx = director_dy = 0.0
    if director_on:
        aim, state.director_bone = _director_aim_offset(
            target,
            config.aim_director_bones,
            config.aim_director_radius_px,
            state.director_bone,
            state.director_intent_x,
            state.director_intent_y,
        )
        offset = state.predict_offset(aim)
        if offset is not None:
            state.director_gap_px = math.hypot(float(offset[0]), float(offset[1]))
            director_dx, director_dy = aim_director_vector(
                float(offset[0]),
                float(offset[1]),
                strength=config.aim_director_strength * 0.01,
                radius_px=config.aim_director_radius_px,
                max_step_px=config.aim_director_max_step_px,
                dt_seconds=frame_dt,
                curve=config.aim_director_curve,
                user_dx=user_dx,
                user_dy=user_dy,
                deadzone_px=(
                    config.aim_director_deadzone_px
                    if config.aim_director_deadzone
                    else 0.0
                ),
                humanize_gain=hz_gain(config.aim_director_humanizer),
            )

    corrector_dx = corrector_dy = 0.0
    if config.aim_corrector:
        offset = state.predict_offset(
            _bone_aim_offset(target, config.aim_corrector_bone)
        )
        if offset is not None:
            state.corrector_gap_px = math.hypot(float(offset[0]), float(offset[1]))
            corrector_dx, corrector_dy = aim_corrector_vector(
                user_dx,
                user_dy,
                float(offset[0]),
                float(offset[1]),
                strength=config.aim_corrector_strength * 0.01,
                radius_px=config.aim_corrector_radius_px,
                max_bend_deg=config.aim_corrector_max_bend_deg,
            )

    tracking_dx = tracking_dy = 0.0
    if tracking_ready and tracking_target is not None:
        state.tracking_gap_px = math.hypot(
            float(tracking_target[0]), float(tracking_target[1])
        )
        tracking_dx, tracking_dy = tracking_retention_vector(
            user_dx,
            user_dy,
            float(tracking_target[0]),
            float(tracking_target[1]),
            strength=config.tracking_assist_strength * 0.01,
            radius_px=config.tracking_assist_radius_px,
            breakaway_px=config.tracking_assist_breakaway_px,
        )

    spray_dx = spray_dy = 0.0
    if spray_on and _spray_target_ready(config, state, target, now):
        offset = state.predict_offset(_bbox_center_offset(target))
        if offset is not None:
            state.spray_gap_px = abs(float(offset[0]))
            spray_dx, spray_dy = spray_acquire_vector(
                float(offset[0]),
                float(offset[1]),
                strength=config.spray_acquire_strength * 0.01,
                radius_px=config.spray_acquire_radius_px,
                max_step_px=config.spray_acquire_max_step_px,
                dt_seconds=frame_dt,
                user_dx=user_dx,
                held_seconds=(
                    now - state.spray_held_since
                    if state.spray_held_since > 0.0
                    else 0.0
                ),
            )

    result = HumanAimResult(
        director_dx=director_dx,
        director_dy=director_dy,
        corrector_dx=corrector_dx,
        corrector_dy=corrector_dy,
        tracking_dx=tracking_dx,
        tracking_dy=tracking_dy,
        spray_dx=spray_dx,
        spray_dy=spray_dy,
    )
    out_x, out_y = _quantize_adaptive_aim(
        state,
        result.total_dx,
        result.total_dy,
        config.max_assist_px,
    )
    state.record_motion(user_dx + out_x, user_dy + out_y)
    return out_x, out_y, result


def _tap_nudge_vector(
    target_dx: float,
    target_dy: float,
    max_nudge_px: float,
    range_px: float,
) -> Tuple[int, int]:
    """One-shot move toward the aim point, never past it."""
    if max_nudge_px <= 0.0 or range_px <= 0.0:
        return 0, 0
    mag = math.hypot(target_dx, target_dy)
    if mag <= 0.5 or mag > range_px:
        return 0, 0
    amount = min(max_nudge_px, mag)
    return int(round(target_dx / mag * amount)), int(round(target_dy / mag * amount))


def _poll_tap_assist(
    config: AppConfig,
    state: TapAssistState,
    target: Optional[object],
    now: float,
) -> Tuple[int, int]:
    """Rising edge of left mouse only. Holding does not repeat."""
    pressed = _is_key_held(VK_LBUTTON)
    if not state.primed:
        state.lmb_down = pressed
        state.primed = True
        return 0, 0
    edge = pressed and not state.lmb_down
    state.lmb_down = pressed
    if not config.tap_assist or config.tap_max_nudge_px <= 0.0:
        return 0, 0
    if not edge or now < state.ignore_until:
        return 0, 0
    offset = _bone_aim_offset(target, config.tap_bone)
    if offset is None:
        return 0, 0
    return _tap_nudge_vector(
        float(offset[0]),
        float(offset[1]),
        config.tap_max_nudge_px,
        config.tap_range_px,
    )


def _interval_ready(now: float, last: float, interval_ms: float) -> bool:
    interval = max(0.0, float(interval_ms)) * 0.001
    return interval <= 0.0 or (now - last) >= interval


def _pick_interval_ms(lo: float, hi: float) -> float:
    """A random interval in [lo, hi], accepting either bound in either order."""
    lo, hi = sorted((max(0.0, float(lo)), max(0.0, float(hi))))
    return lo if hi <= lo else random.uniform(lo, hi)


def _initial_delay_passed(now: float, engaged_at: float, delay_ms: float) -> bool:
    """True once the first-shot delay has elapsed since going on target."""
    delay = max(0.0, float(delay_ms)) * 0.001
    return delay <= 0.0 or (now - engaged_at) >= delay


def _hold_trigger_tick(
    config: AppConfig,
    dispatcher: MouseDispatcher,
    target: Optional[object],
    target_dx: Optional[float],
    target_dy: Optional[float],
    now: float,
    state: HoldTriggerState,
) -> Tuple[bool, int, int]:
    """Return (should_click, nudge_dx, nudge_dy). Click is applied after mouse move."""
    state.fired_trigger = False
    state.fired_magnet = False
    trigger_held = _hold_key_down(config.trigger, config.hold_trigger_vk)
    magnet_held = _hold_key_down(config.magnet, config.magnet_vk)
    if not trigger_held and not magnet_held:
        state.armed = True
        state.clear_shot_timing()
        if state.mouse_down and not config.dry_run:
            dispatcher.left_up()
        state.mouse_down = False
        return False, 0, 0

    trigger_on = trigger_held and _is_on_target(
        target, config.trigger_radius_px, config.trigger_bones
    )
    magnet_on = magnet_held and _is_on_target(
        target, config.magnet_trigger_radius_px, config.magnet_trigger_bones
    )
    if not trigger_on and not magnet_on:
        state.armed = True
        state.clear_shot_timing()
        if state.mouse_down and not config.dry_run:
            dispatcher.left_up()
        state.mouse_down = False
        return False, 0, 0

    fire = False
    if trigger_on:
        if state.trigger_engaged_at <= 0.0:
            state.trigger_engaged_at = now
            state.next_interval_ms = _pick_interval_ms(
                config.trigger_interval_ms, config.trigger_interval_max_ms
            )
            state.trigger_drawn_delay_ms = (
                humanizer_reaction.sample() if config.trigger_humanized_delay else None
            )
        delay_ms = (
            state.trigger_drawn_delay_ms
            if state.trigger_drawn_delay_ms is not None
            else config.trigger_initial_delay_ms
        )
        if _initial_delay_passed(
            now, state.trigger_engaged_at, delay_ms
        ) and _interval_ready(now, state.last_fire_time, state.next_interval_ms):
            state.last_fire_time = now
            state.next_interval_ms = _pick_interval_ms(
                config.trigger_interval_ms, config.trigger_interval_max_ms
            )
            state.fired_trigger = True
            fire = True
    else:
        state.trigger_engaged_at = 0.0
        state.next_interval_ms = 0.0
        state.trigger_drawn_delay_ms = None

    if magnet_on:
        if state.magnet_engaged_at <= 0.0:
            state.magnet_engaged_at = now
            state.next_magnet_interval_ms = _pick_interval_ms(
                config.magnet_trigger_interval_ms, config.magnet_trigger_interval_max_ms
            )
            state.magnet_drawn_delay_ms = (
                humanizer_reaction.sample() if config.magnet_trigger_humanized_delay else None
            )
        delay_ms = (
            state.magnet_drawn_delay_ms
            if state.magnet_drawn_delay_ms is not None
            else config.magnet_trigger_initial_delay_ms
        )
        if _initial_delay_passed(
            now, state.magnet_engaged_at, delay_ms
        ) and _interval_ready(now, state.last_magnet_fire, state.next_magnet_interval_ms):
            state.last_magnet_fire = now
            state.next_magnet_interval_ms = _pick_interval_ms(
                config.magnet_trigger_interval_ms, config.magnet_trigger_interval_max_ms
            )
            state.fired_magnet = True
            fire = True
    else:
        state.magnet_engaged_at = 0.0
        state.next_magnet_interval_ms = 0.0
        state.magnet_drawn_delay_ms = None
    if not fire:
        return False, 0, 0

    nudge_dx, nudge_dy = 0, 0
    if config.tap_on_trigger:
        offset = _bone_aim_offset(target, config.tap_bone)
        if offset is not None:
            nudge_dx, nudge_dy = _tap_nudge_vector(
                float(offset[0]),
                float(offset[1]),
                config.tap_max_nudge_px,
                config.tap_range_px,
            )
    return True, nudge_dx, nudge_dy


def _wait_for_tick(next_tick: float) -> None:
    remaining = next_tick - time.perf_counter()
    if remaining <= 0.0:
        return
    if remaining > 0.0005:
        time.sleep(max(0.0, remaining - 0.0003))
    while time.perf_counter() < next_tick:
        pass


def _begin_1ms_timer() -> Any:
    winmm = ctypes.windll.winmm
    winmm.timeBeginPeriod(1)
    return winmm


def _end_1ms_timer(winmm: Any) -> None:
    try:
        winmm.timeEndPeriod(1)
    except Exception:
        pass


_HIGH_PRIORITY_CLASS = 0x00000080
_THREAD_PRIORITY_HIGHEST = 2
_THREAD_PRIORITY_TIME_CRITICAL = 15
_PROCESS_POWER_THROTTLING = 4
_THREAD_POWER_THROTTLING = 3
_POWER_THROTTLING_EXECUTION_SPEED = 0x1


class _POWER_THROTTLING_STATE(ctypes.Structure):
    _fields_ = [
        ("Version", ctypes.wintypes.DWORD),
        ("ControlMask", ctypes.wintypes.DWORD),
        ("StateMask", ctypes.wintypes.DWORD),
    ]


def _boost_process_scheduling() -> None:
    """Keep Argus off Windows EcoQoS so TensorRT is not parked behind CS2."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    try:
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), _HIGH_PRIORITY_CLASS)
    except Exception:
        pass
    try:
        kernel32.SetProcessInformation.argtypes = [
            ctypes.wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.wintypes.DWORD,
        ]
        kernel32.SetProcessInformation.restype = ctypes.wintypes.BOOL
        state = _POWER_THROTTLING_STATE(1, _POWER_THROTTLING_EXECUTION_SPEED, 0)
        kernel32.SetProcessInformation(
            kernel32.GetCurrentProcess(),
            _PROCESS_POWER_THROTTLING,
            ctypes.byref(state),
            ctypes.sizeof(state),
        )
    except Exception:
        pass


def _boost_current_thread(priority: int = _THREAD_PRIORITY_HIGHEST) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    try:
        kernel32.SetThreadPriority(kernel32.GetCurrentThread(), priority)
    except Exception:
        pass
    try:
        kernel32.SetThreadInformation.argtypes = [
            ctypes.wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.wintypes.DWORD,
        ]
        kernel32.SetThreadInformation.restype = ctypes.wintypes.BOOL
        state = _POWER_THROTTLING_STATE(1, _POWER_THROTTLING_EXECUTION_SPEED, 0)
        kernel32.SetThreadInformation(
            kernel32.GetCurrentThread(),
            _THREAD_POWER_THROTTLING,
            ctypes.byref(state),
            ctypes.sizeof(state),
        )
    except Exception:
        pass


class _LatestVision:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._target: Optional[object] = None
        self._detections: tuple = ()
        self._updated_at = 0.0
        self._generation = 0
        # When the frame behind the current result was grabbed.
        self.frame_at = 0.0

    def set(
        self,
        target: Optional[object],
        detections: tuple = (),
        frame_at: float = 0.0,
    ) -> None:
        with self._lock:
            self._target = target
            self._detections = detections
            self._updated_at = time.perf_counter()
            self.frame_at = frame_at or self._updated_at
            self._generation += 1

    def get(self) -> Tuple[Optional[object], tuple, float, int]:
        with self._lock:
            return (
                self._target,
                self._detections,
                self._updated_at,
                self._generation,
            )


def _vision_loop(
    controller: RuntimeController,
    capture: ScreenCapture,
    shared: _LatestVision,
    stop_event: threading.Event,
    tracker_holder: dict[str, Any],
    errors: list[BaseException],
    ready: threading.Event,
) -> None:
    tracker = None
    try:
        # TIME_CRITICAL: this thread busy-polls the GPU (SCHED_SPIN) waiting on
        # TensorRT's stream.synchronize(); the sooner Windows schedules it back
        # in after that spin resolves, the less latency stacks up when CS2 is
        # also hammering the CPU/GPU.
        _boost_current_thread(_THREAD_PRIORITY_TIME_CRITICAL)
        config = controller.live.get()
        tracker = VisionTracker(
            engine_path=config.engine_path,
            conf_threshold=config.conf_threshold,
            target_class_ids=config.target_class_ids,
            marker_hsv_lower=np.array(config.marker_hsv_lower, dtype=np.uint8),
            marker_hsv_upper=np.array(config.marker_hsv_upper, dtype=np.uint8),
            offset_fn=config.offset_fn,
        )
        tracker.fov_radius_px = _required_target_fov(config)
        tracker.detector.attach_thread()
        tracker_holder["tracker"] = tracker
        log("Game", f"loaded {_loaded_model_label(config.engine_path)}")
        ready.set()

        last_gen = -1
        t0 = time.perf_counter()
        latency = controller.status.latency
        vision_frames = 0
        last_vision_report = t0
        capture_time_acc = 0.0
        infer_time_acc = 0.0
        cfg = config
        try:
            import cv2 as _cv2

            _cv2.setNumThreads(1)
        except Exception:
            pass
        while (
            not stop_event.is_set()
            and not controller.loop_stop.is_set()
            and not controller.quit_event.is_set()
        ):
            gen = controller.live.generation()
            if gen != last_gen:
                cfg = controller.live.get()
                tracker.detector.conf_threshold = cfg.conf_threshold
                tracker.detector.target_class_ids = (
                    set(cfg.target_class_ids) if cfg.target_class_ids else None
                )
                tracker.fov_radius_px = _required_target_fov(cfg)
                last_gen = gen
            capture_start = time.perf_counter()
            frame = capture.get_frame()
            infer_start = time.perf_counter()
            capture_time_acc += infer_start - capture_start
            if frame is None:
                if stop_event.is_set() or controller.loop_stop.is_set():
                    break
                time.sleep(0.001)
                continue
            target, detections = tracker.process(frame, infer_start - t0)
            infer_end = time.perf_counter()
            infer_time_acc += infer_end - infer_start
            latency.add("capture", (infer_start - capture_start) * 1000.0)
            latency.add("infer", (infer_end - infer_start) * 1000.0)
            shared.set(target, tuple(detections), infer_start)
            vision_frames += 1
            now = time.perf_counter()
            if now - last_vision_report >= 0.5:
                elapsed = now - last_vision_report
                controller.status.vision_fps = vision_frames / elapsed
                controller.status.vision_capture_ms = (
                    1000.0 * capture_time_acc / vision_frames if vision_frames else 0.0
                )
                controller.status.vision_infer_ms = (
                    1000.0 * infer_time_acc / vision_frames if vision_frames else 0.0
                )
                vision_frames = 0
                capture_time_acc = 0.0
                infer_time_acc = 0.0
                last_vision_report = now
    except Exception as exc:
        errors.append(exc)
        ready.set()
    finally:
        if tracker is not None:
            try:
                tracker.close()
            except Exception:
                pass
        tracker_holder.pop("tracker", None)


def _capture_loop(controller: RuntimeController) -> None:
    config = controller.live.get()
    capture = None
    raw_mouse = None
    dispatcher: Any = None
    rp_link: Any = None
    hold_trigger_state = HoldTriggerState()
    tap_assist_state = TapAssistState()
    magnet_state = MagnetState()
    adaptive_aim_state = AdaptiveAimState()
    rp_aim_state = RpAimState()
    module_activity = ModuleActivityLog()
    rp_one_tap_state = RpOneTapState()
    mouse_out_state = MouseOutputState()
    predictor = TargetPredictor()
    # Distil the recorded runs once, off the hot path, before the loop starts.
    humanizer_paths.reload()
    hz_paths = humanizer_paths.get()
    hz_generation = hz_paths.generation
    humanizer_shaper_state = humanizer_shaper.HumanizerShaper()
    vision_thread: Optional[threading.Thread] = None
    vision_stop = threading.Event()
    winmm = None

    try:
        winmm = _begin_1ms_timer()
        _boost_current_thread()
        controller.status.latency.reset()

        if config.input_method == "rp2040":
            from rp2040_link import RP2040Dispatcher, RP2040Link

            rp_link = RP2040Link(config.rp2040_port)

            def _rp_handshake(port: str, step: str) -> None:
                if port:
                    controller.status.rp2040_port = port
                if step == "timeout":
                    controller.status.rp2040_handshake = "fail"
                    return
                controller.status.rp2040_handshake = step
                if step == "ack":
                    controller.status.rp2040_connected = True

            rp_link.connect(progress=_rp_handshake)
            dispatcher = RP2040Dispatcher(rp_link)
            controller.status.rp2040_connected = True
            controller.status.rp2040_port = rp_link.port_used
            controller.status.rp2040_handshake = "ack"
            log("RP2040", f"handshake established on {rp_link.port_used}")
        else:
            dispatcher = MouseDispatcher()
        capture = ScreenCapture(
            roi_size=ROI_SIZE, target_fps=config.target_fps, output_idx=config.output_idx
        )
        capture.start()

        shared = _LatestVision()
        tracker_holder: dict[str, Any] = {}
        vision_errors: list[BaseException] = []
        vision_ready = threading.Event()
        vision_thread = threading.Thread(
            target=_vision_loop,
            args=(
                controller,
                capture,
                shared,
                vision_stop,
                tracker_holder,
                vision_errors,
                vision_ready,
            ),
            name="vision-loop",
            daemon=True,
        )
        vision_thread.start()
        if not vision_ready.wait(timeout=45.0):
            raise RuntimeError("Vision thread failed to start")
        if vision_errors:
            raise vision_errors[0]

        raw_mouse = RawMouseReader()
        raw_mouse.start()

        if not config.dry_run and config.verify_inject:
            _verify_sendinput(dispatcher)

        controller.status.running = True
        controller.ready.set()
        _log_capture_start(config)

        frame_interval = 1.0 / float(config.target_fps)
        t0 = time.perf_counter()
        next_tick = t0
        frames = 0
        last_fps_report = t0
        last_status_log = t0
        status_accum = StatusAccum()
        last_frame_time = t0
        last_gen = -1
        cfg = config
        latency = controller.status.latency
        last_pause_reason = ""

        while not controller.loop_stop.is_set() and not controller.quit_event.is_set():
            if vision_errors:
                raise vision_errors[0]
            scheduled = next_tick
            _wait_for_tick(next_tick)
            now = time.perf_counter()
            latency.add("jitter", (now - scheduled) * 1000.0)
            next_tick += frame_interval
            if next_tick < now - frame_interval:
                next_tick = now + frame_interval
            frame_dt = max(0.0, now - last_frame_time)
            last_frame_time = now

            gen = controller.live.generation()
            if gen != last_gen:
                cfg = controller.live.get()
                last_gen = gen
                new_interval = 1.0 / float(max(1, cfg.target_fps))
                if abs(new_interval - frame_interval) > 1e-9:
                    frame_interval = new_interval
                    next_tick = now + frame_interval

            # A finished Humanizer run republishes the library; pick it up the
            # same lock-free way the live config is picked up.
            if humanizer_paths.generation() != hz_generation:
                hz_paths = humanizer_paths.get()
                hz_generation = hz_paths.generation
                humanizer_shaper_state.reset()

            t = now - t0
            target, detections, vision_updated_at, vision_generation = shared.get()
            ux, uy = raw_mouse.consume_delta()
            if vision_updated_at > 0.0:
                age_s = now - vision_updated_at
                # A still screen delivers no new frames; that is not latency.
                if age_s < 0.25:
                    latency.add("age", age_s * 1000.0)

            # Paused (pause key, or the menu is open): every module sees an
            # empty screen, so nothing aims, clicks or slows the mouse, and
            # whatever was held (a trigger click, an RP2040 slowdown) is let go
            # by the modules' own no-target paths.
            pause_reason = controller.pause_reason(cfg)
            if pause_reason != last_pause_reason:
                _log_pause_change(last_pause_reason, pause_reason)
                last_pause_reason = pause_reason
            paused = bool(pause_reason)
            if paused:
                target = None
                detections = ()

            raw_target = target
            if cfg.target_prediction:
                # Counts pile up per aim tick and are spent when the next vision
                # frame lands, so the camera motion lines up with exactly the
                # interval between the two frames being compared.
                predictor.add_mouse_counts(ux, uy)
                predictor.update(
                    vision_generation,
                    now,
                    _target_bbox(target),
                    detections,
                    1.0 / max(1e-6, _mouse_output_scale(cfg)),
                    cfg.prediction_track_frames,
                )
                # Lead by what Argus can measure of its own delay, plus whatever
                # the user adds for their display and the server.
                pipeline_s = max(0.0, now - vision_updated_at) + 0.001 * (
                    controller.status.vision_capture_ms + controller.status.vision_infer_ms
                )
                lead_s = pipeline_s + 0.001 * cfg.prediction_lead_ms
                lead_dx, lead_dy = predictor.lead(
                    lead_s, cfg.prediction_strength * 0.01, cfg.prediction_max_px
                )
                controller.status.prediction_speed = predictor.speed
                controller.status.prediction_confidence = predictor.confidence
                controller.status.prediction_lead_px = math.hypot(lead_dx, lead_dy)
                if target is not None:
                    target = _lead_target(target, lead_dx, lead_dy)
            elif predictor.state.last_generation >= 0:
                predictor.reset()
                controller.status.prediction_speed = 0.0
                controller.status.prediction_confidence = 0.0
                controller.status.prediction_lead_px = 0.0

            if target is None:
                hold_trigger_state.aim_bone = "chest"
                target_dx = None
                target_dy = None
            else:
                bbox = _target_bbox(target)
                if bbox is None:
                    target_dx, target_dy = float(target.dx), float(target.dy)
                else:
                    target_dx, target_dy, bone = resolve_aim_offset(
                        bbox,
                        cfg.aim_mode,
                        ROI_CENTER,
                        hold_trigger_state.aim_bone,
                    )
                    hold_trigger_state.aim_bone = bone
            _publish_overlay(
                controller,
                capture.region,
                raw_target,
                cfg,
                target if target is not raw_target else None,
            )

            if rp_link is not None:
                if not rp_link.connected and controller.status.rp2040_connected:
                    controller.status.rp2040_connected = False
                    controller.status.rp2040_handshake = "fail"
                    controller.status.error = f"RP2040 handshake dropped: {rp_link.error}"
                    log_error(controller.status.error)
                if rp_link.connected and not cfg.dry_run:
                    _poll_rp_aim_assist(
                        cfg,
                        rp_aim_state,
                        rp_link,
                        _crosshair_over_target(
                            target,
                            cfg.rp_aim_radius_px,
                            now,
                            vision_updated_at,
                            cfg.rp_aim_bones,
                        ),
                        now,
                    )
            one_tap_dx = one_tap_dy = 0
            one_tap_click = False
            if rp_link is not None and rp_link.connected:
                one_tap_dx, one_tap_dy, one_tap_click = _poll_rp_one_tap_assist(
                    cfg,
                    rp_one_tap_state,
                    rp_link,
                    tap_assist_state,
                    adaptive_aim_state,
                    target,
                    now,
                    vision_updated_at,
                    hz_paths,
                    humanizer_shaper_state.rng,
                )
            one_tap_busy = _rp_one_tap_busy(rp_one_tap_state, now)

            spray_active = (
                cfg.spray_acquire
                and not hold_trigger_state.mouse_down
                and not one_tap_busy
                and _is_key_held(VK_LBUTTON)
            )
            aim_result: Optional[HumanAimResult] = None
            if one_tap_busy:
                adaptive_dx, adaptive_dy = 0, 0
                _poll_adaptive_aim(
                    cfg,
                    adaptive_aim_state,
                    None,
                    float(ux),
                    float(uy),
                    now,
                    frame_dt,
                    vision_updated_at,
                    vision_generation,
                    spray_active=False,
                )
            else:
                adaptive_dx, adaptive_dy, aim_result = _poll_adaptive_aim(
                    cfg,
                    adaptive_aim_state,
                    target,
                    float(ux),
                    float(uy),
                    now,
                    frame_dt,
                    vision_updated_at,
                    vision_generation,
                    spray_active=spray_active,
                    hz_gain=humanizer_shaper_state.gain,
                )
            assist_dx, assist_dy = adaptive_dx, adaptive_dy
            if one_tap_dx or one_tap_dy:
                assist_dx += one_tap_dx
                assist_dy += one_tap_dy
            tap_dx, tap_dy = (
                (0, 0)
                if one_tap_busy
                else _poll_tap_assist(cfg, tap_assist_state, target, now)
            )
            mag_dx, mag_dy = _poll_magnet(
                cfg, magnet_state, target, frame_dt, humanizer_shaper_state.gain
            )
            fire, n_dx, n_dy = _hold_trigger_tick(
                cfg,
                dispatcher,
                target,
                target_dx,
                target_dy,
                now,
                hold_trigger_state,
            )
            if tap_dx or tap_dy:
                assist_dx += tap_dx
                assist_dy += tap_dy
            if mag_dx or mag_dy:
                assist_dx += mag_dx
                assist_dy += mag_dy
            if n_dx or n_dy:
                assist_dx += n_dx
                assist_dy += n_dy

            # Lay the user's own hand over whatever the modules asked for.
            # Called every tick, including idle ones, so the shaper can hand
            # back any displacement it still owes.
            shaped_dx, shaped_dy = _shape_humanized(
                humanizer_shaper_state,
                hz_paths,
                cfg,
                assist_dx,
                assist_dy,
                aim_result,
                tap_dx, tap_dy,
                mag_dx, mag_dy,
                n_dx, n_dy,
                one_tap_dx, one_tap_dy,
                adaptive_aim_state,
                magnet_state,
                target_dx,
                target_dy,
                now,
                frame_dt,
            )
            shape_dx = shaped_dx - assist_dx
            shape_dy = shaped_dy - assist_dy
            assist_dx, assist_dy = shaped_dx, shaped_dy

            adaptive_aim_state.record_motion(
                float(tap_dx + mag_dx + n_dx + one_tap_dx + shape_dx),
                float(tap_dy + mag_dy + n_dy + one_tap_dy + shape_dy),
            )
            _log_module_activity(
                module_activity,
                cfg,
                now,
                aim_result,
                adaptive_aim_state,
                magnet_state,
                hold_trigger_state,
                rp_aim_state,
                rp_one_tap_state,
                bool(tap_dx or tap_dy),
                bool(one_tap_click),
            )

            assist_mag = math.hypot(assist_dx, assist_dy) if (assist_dx or assist_dy) else 0.0

            controller.status.target_present = target is not None
            controller.status.last_assist_mag = assist_mag
            status_accum.add_frame(target is not None, assist_mag)

            if not paused:
                compute_end = time.perf_counter()
                latency.add("compute", (compute_end - now) * 1000.0)
                sent = False
                if cfg.dry_run:
                    # Nothing is injected, but the path up to the send is real.
                    sent = bool(assist_dx or assist_dy or one_tap_click or fire)
                else:
                    if assist_dx or assist_dy:
                        send_dx, send_dy = _scale_mouse_output(
                            mouse_out_state, cfg, assist_dx, assist_dy
                        )
                        if send_dx or send_dy:
                            dispatcher.move(send_dx, send_dy)
                            raw_mouse.compensate_injection(send_dx, send_dy)
                            sent = True
                    if one_tap_click:
                        dispatcher.click_left()
                        sent = True
                    if fire:
                        dispatcher.click_left()
                        tap_assist_state.ignore_until = now + TAP_INJECT_IGNORE_S
                        sent = True
                if sent:
                    sent_at = time.perf_counter()
                    if not cfg.dry_run:
                        latency.add("send", (sent_at - compute_end) * 1000.0)
                    latency.add("total", (sent_at - shared.frame_at) * 1000.0)

            frames += 1
            if now - last_fps_report >= 1.0:
                fps = frames / (now - last_fps_report)
                controller.status.loop_fps = fps
                status_accum.add_sample(
                    fps,
                    controller.status.vision_fps,
                    controller.status.vision_capture_ms,
                    controller.status.vision_infer_ms,
                )
                frames = 0
                last_fps_report = now
            if now - last_status_log >= STATUS_INTERVAL_S:
                _log_runtime_status(controller.status, cfg, status_accum)
                status_accum = StatusAccum()
                last_status_log = now
    except Exception as exc:
        controller.error = str(exc)
        controller.status.error = str(exc)
        log_error(str(exc))
        controller.ready.set()
    finally:
        was_running = controller.status.running
        controller.overlay_world.clear()
        controller.status.running = False
        vision_stop.set()
        if vision_thread is not None:
            vision_thread.join(timeout=5.0)
        if hold_trigger_state.mouse_down and dispatcher is not None:
            try:
                dispatcher.left_up()
            except Exception:
                pass
        controller.status.rp2040_connected = False
        controller.status.rp2040_port = ""
        if not controller.error:
            controller.status.rp2040_handshake = ""
        elif controller.status.rp2040_handshake != "fail":
            controller.status.rp2040_handshake = "fail"
        if rp_link is not None:
            # close() also restores scale 1.0, cancels any freeze, and
            # releases injected buttons in the firmware.
            rp_link.close()
        if raw_mouse is not None:
            raw_mouse.stop()
        if capture is not None:
            capture.stop()
        if winmm is not None:
            _end_1ms_timer(winmm)
        if was_running and not controller.error:
            log("Capture", "stopped")


def _menu_hotkey_loop(controller: RuntimeController) -> None:
    was_down = False
    pause_was_down = False
    pause_vk = 0
    while not controller.quit_event.is_set():
        cfg = controller.live.get()
        down = bool(cfg.menu_hotkey_vk) and _is_key_held(cfg.menu_hotkey_vk)
        if down and not was_down:
            controller.toggle_menu()
        was_down = down
        pause_down = bool(cfg.pause_hotkey_vk) and _is_key_held(cfg.pause_hotkey_vk)
        if cfg.pause_hotkey_vk != pause_vk:
            # Just (re)bound: the press that picked the key must not also fire it.
            pause_vk = cfg.pause_hotkey_vk
            pause_was_down = pause_down
        if pause_down and not pause_was_down:
            controller.toggle_pause()
        pause_was_down = pause_down
        # What the dashboard shows; the capture loop decides for itself.
        controller.status.pause_reason = controller.pause_reason(cfg)
        controller.keep_cursor_free()
        # Poll faster while the menu is up so a game that re-clips the cursor
        # every frame cannot make the menu feel sticky.
        time.sleep(0.005 if controller.menu_visible else 0.04)


def run(config: AppConfig) -> int:
    start_logger()
    try:
        import cv2 as _cv2

        _cv2.setNumThreads(1)
        _cv2.ocl.setUseOpenCL(False)
    except Exception:
        pass
    _boost_process_scheduling()
    controller = RuntimeController(config)
    overlay = OverlayRenderer(controller)
    try:
        overlay.start()
        log(
            "Argus",
            f"{'dry-run' if config.dry_run else 'live'} mode, {config.target_fps} mouse FPS",
        )
        if config.ui:
            log("Argus", f"press {config.menu_hotkey_name} to show/hide the settings panel")
        else:
            log("Argus", "press Ctrl+C to stop")

        if config.engine_path:
            result = controller.start_capture()
            if not result.get("ok"):
                if not config.ui:
                    return 1
        elif not config.ui:
            log_error("No game selected. Choose a game in Settings, or pass --engine.")
            return 1

        try:
            if config.ui:
                from ui_bridge import start_ui

                hotkey_thread = threading.Thread(
                    target=_menu_hotkey_loop, args=(controller,), name="menu-hotkey", daemon=True
                )
                hotkey_thread.start()
                start_ui(controller)
            else:
                while not controller.quit_event.is_set() and (
                    controller._loop_thread is None or controller._loop_thread.is_alive()
                ):
                    time.sleep(0.1)
        except KeyboardInterrupt:
            log("Argus", "shutting down")
        return 0
    finally:
        overlay.stop()
        controller.stop()
        controller.stop_capture()
        stop_logger()


def main(argv: Optional[list[str]] = None) -> int:
    config = parse_args(argv)
    return run(config)


if __name__ == "__main__":
    sys.exit(main())
