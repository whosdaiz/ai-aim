"""Click-through game overlay for module FOV circles and target points."""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any, Optional, Tuple

import cv2
import numpy as np

cv2.setNumThreads(1)
try:
    cv2.ocl.setUseOpenCL(False)
except Exception:
    pass

from vision_tracker import ROI_SIZE, compute_centered_roi
from logger import error as log_error

WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_POPUP = 0x80000000
HWND_TOPMOST = -1
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
BI_RGB = 0
DIB_RGB_COLORS = 0
PM_REMOVE = 0x0001
WM_DESTROY = 0x0002
WDA_EXCLUDEFROMCAPTURE = 0x00000011

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM
)

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32
HANDLE_PTR = ctypes.c_void_p

user32.DefWindowProcW.argtypes = [
    wintypes.HWND,
    ctypes.c_uint,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
user32.DefWindowProcW.restype = LRESULT
user32.CreateWindowExW.restype = HANDLE_PTR
user32.GetDC.restype = HANDLE_PTR
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, HANDLE_PTR]
user32.ReleaseDC.restype = ctypes.c_int
gdi32.CreateCompatibleDC.restype = HANDLE_PTR
gdi32.CreateCompatibleDC.argtypes = [HANDLE_PTR]
gdi32.SelectObject.restype = HANDLE_PTR
gdi32.SelectObject.argtypes = [HANDLE_PTR, HANDLE_PTR]
gdi32.DeleteObject.argtypes = [HANDLE_PTR]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.DeleteDC.argtypes = [HANDLE_PTR]
gdi32.DeleteDC.restype = wintypes.BOOL

def _premul(color: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
    b, g, r, a = color
    return (b * a // 255, g * a // 255, r * a // 255, a)


COLOR_AIM_DIRECTOR = _premul((255, 140, 181, 205))
COLOR_AIM_CORRECTOR = _premul((190, 230, 99, 205))
COLOR_TRACKING_ASSIST = _premul((177, 143, 255, 205))
COLOR_SPRAY_ACQUIRE = _premul((99, 230, 190, 205))
COLOR_TAP = _premul((255, 167, 105, 210))
COLOR_RP_ONE_TAP = _premul((72, 96, 255, 210))
COLOR_RP_ONE_TAP_RESET = _premul((72, 96, 255, 140))
COLOR_TRIGGER = _premul((120, 107, 255, 210))
COLOR_PREDICTION = _premul((255, 214, 64, 215))
COLOR_MAGNET = _premul((74, 193, 255, 210))
COLOR_AIM_DIRECTOR_SPOT = _premul((255, 140, 181, 235))
COLOR_AIM_CORRECTOR_SPOT = _premul((190, 230, 99, 235))
COLOR_TRACKING_ASSIST_SPOT = _premul((177, 143, 255, 235))
COLOR_SPRAY_ACQUIRE_SPOT = _premul((99, 230, 190, 235))
COLOR_TAP_SPOT = _premul((255, 167, 105, 235))
COLOR_RP_ONE_TAP_SPOT = _premul((72, 96, 255, 235))
COLOR_RP_AIM = _premul((255, 159, 67, 210))
COLOR_RP_AIM_SPOT = _premul((255, 159, 67, 235))
COLOR_TRIGGER_SPOT = _premul((120, 107, 255, 235))
COLOR_MAGNET_SPOT = _premul((74, 193, 255, 235))
COLOR_MAGNET_FIRE_SPOT = COLOR_TRIGGER_SPOT
COLOR_TARGET_BOX = _premul((244, 238, 232, 220))
OVERLAY_MAX_HZ = 90.0


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", ctypes.c_uint),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_byte),
        ("BlendFlags", ctypes.c_byte),
        ("SourceConstantAlpha", ctypes.c_byte),
        ("AlphaFormat", ctypes.c_byte),
    ]


user32.UpdateLayeredWindow.argtypes = [
    HANDLE_PTR,
    HANDLE_PTR,
    ctypes.POINTER(POINT),
    ctypes.POINTER(SIZE),
    HANDLE_PTR,
    ctypes.POINTER(POINT),
    wintypes.COLORREF,
    ctypes.POINTER(BLENDFUNCTION),
    wintypes.DWORD,
]
user32.UpdateLayeredWindow.restype = wintypes.BOOL
gdi32.CreateDIBSection.restype = HANDLE_PTR
gdi32.CreateDIBSection.argtypes = [
    HANDLE_PTR,
    ctypes.POINTER(BITMAPINFO),
    ctypes.c_uint,
    ctypes.POINTER(ctypes.c_void_p),
    HANDLE_PTR,
    wintypes.DWORD,
]


@dataclass
class OverlayWorld:
    roi: Tuple[int, int, int, int] = (0, 0, 0, 0)
    target_box: Optional[Tuple[float, float, float, float]] = None
    aim_director_spots: Tuple[Tuple[float, float], ...] = ()
    aim_corrector_spot: Optional[Tuple[float, float]] = None
    tracking_assist_spot: Optional[Tuple[float, float]] = None
    spray_acquire_spot: Optional[Tuple[float, float]] = None
    tap_spot: Optional[Tuple[float, float]] = None
    rp_one_tap_spot: Optional[Tuple[float, float]] = None
    rp_aim_spots: Tuple[Tuple[float, float], ...] = ()
    trigger_spots: Tuple[Tuple[float, float], ...] = ()
    magnet_pull_spots: Tuple[Tuple[float, float], ...] = ()
    magnet_fire_spots: Tuple[Tuple[float, float], ...] = ()
    prediction_box: Optional[Tuple[float, float, float, float]] = None


class OverlayBuffer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._frame = OverlayWorld()
        self._gen = 0
        self._event = threading.Event()

    def set(self, frame: OverlayWorld) -> None:
        with self._lock:
            if frame == self._frame:
                return
            self._frame = frame
            self._gen += 1
        self._event.set()

    def get(self) -> OverlayWorld:
        with self._lock:
            return self._frame

    def snapshot(self) -> Tuple[OverlayWorld, int]:
        with self._lock:
            return self._frame, self._gen

    def wait(self, timeout: float) -> None:
        if self._event.wait(timeout):
            self._event.clear()

    def clear(self) -> None:
        self.set(OverlayWorld())


def overlay_wanted(cfg: Any) -> bool:
    if not getattr(cfg, "visuals", True):
        return False
    if getattr(cfg, "vis_target_box", False):
        return True
    if getattr(cfg, "target_prediction", False) and getattr(cfg, "vis_prediction", False):
        return True
    return (
        (
            getattr(cfg, "aim_director", False)
            and (
                getattr(cfg, "vis_aim_director_fov", False)
                or getattr(cfg, "vis_aim_director_spot", False)
            )
        )
        or (
            getattr(cfg, "aim_corrector", False)
            and (
                getattr(cfg, "vis_aim_corrector_fov", False)
                or getattr(cfg, "vis_aim_corrector_spot", False)
            )
        )
        or (
            getattr(cfg, "tracking_assist", False)
            and (
                getattr(cfg, "vis_tracking_assist_fov", False)
                or getattr(cfg, "vis_tracking_assist_spot", False)
            )
        )
        or (
            getattr(cfg, "spray_acquire", False)
            and (
                getattr(cfg, "vis_spray_acquire_fov", False)
                or getattr(cfg, "vis_spray_acquire_spot", False)
            )
        )
        or (
            getattr(cfg, "tap_assist", False)
            and (
                getattr(cfg, "vis_tap_fov", False)
                or getattr(cfg, "vis_tap_spot", False)
            )
        )
        or (
            getattr(cfg, "rp_aim_assist", False)
            and (
                getattr(cfg, "vis_rp_aim_fov", False)
                or getattr(cfg, "vis_rp_aim_spot", False)
            )
        )
        or (
            getattr(cfg, "rp_one_tap_assist", False)
            and (
                getattr(cfg, "vis_rp_one_tap_fov", False)
                or getattr(cfg, "vis_rp_one_tap_reset", False)
                or getattr(cfg, "vis_rp_one_tap_spot", False)
            )
        )
        or (
            getattr(cfg, "trigger", False)
            and (
                getattr(cfg, "vis_trigger_fov", False)
                or getattr(cfg, "vis_trigger_spot", False)
            )
        )
        or (
            getattr(cfg, "magnet", False)
            and (
                getattr(cfg, "vis_magnet_fov", False)
                or getattr(cfg, "vis_magnet_spot", False)
            )
        )
    )


def _monitor_size() -> Tuple[int, int]:
    return int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))


def _clip_pt(x: float, y: float, width: int, height: int) -> Tuple[int, int]:
    return (
        max(0, min(width - 1, int(round(x)))),
        max(0, min(height - 1, int(round(y)))),
    )


def _draw_center_box(
    buf: np.ndarray,
    box: Optional[Tuple[float, float, float, float]],
    color: Tuple[int, int, int, int],
    *,
    inset: float = 0.0,
    cross: str = "both",
) -> None:
    """Outline the detection box and mark its center, instead of a bone dot."""
    if box is None:
        return
    height, width = buf.shape[:2]
    x1, y1, x2, y2 = box
    left, top = min(x1, x2) + inset, min(y1, y2) + inset
    right, bottom = max(x1, x2) - inset, max(y1, y2) - inset
    if right - left < 4.0 or bottom - top < 4.0:
        return
    p1 = _clip_pt(left, top, width, height)
    p2 = _clip_pt(right, bottom, width, height)
    if p1 != p2:
        cv2.rectangle(buf, p1, p2, color, 1, cv2.LINE_AA)
    cx = (left + right) * 0.5
    cy = (top + bottom) * 0.5
    if cross in ("both", "x"):
        cv2.line(
            buf,
            _clip_pt(left, cy, width, height),
            _clip_pt(right, cy, width, height),
            color,
            1,
            cv2.LINE_AA,
        )
    if cross in ("both", "y"):
        arm = max(6.0, (bottom - top) * 0.16)
        cv2.line(
            buf,
            _clip_pt(cx, cy - arm, width, height),
            _clip_pt(cx, cy + arm, width, height),
            color,
            1,
            cv2.LINE_AA,
        )


def render_overlay(
    buf: np.ndarray,
    cfg: Any,
    world: OverlayWorld,
) -> None:
    """Draw in ROI pixel space. `buf` is the capture window, not the full monitor."""
    buf.fill(0)
    if not getattr(cfg, "visuals", True):
        return
    height, width = buf.shape[:2]
    cx, cy = width // 2, height // 2

    if getattr(cfg, "vis_prediction", False) and world.prediction_box is not None:
        # Where the modules are actually aiming once the lead is applied.
        x1, y1, x2, y2 = world.prediction_box
        p1 = _clip_pt(min(x1, x2), min(y1, y2), width, height)
        p2 = _clip_pt(max(x1, x2), max(y1, y2), width, height)
        if p1 != p2:
            cv2.rectangle(buf, p1, p2, COLOR_PREDICTION, 1, cv2.LINE_AA)
            if world.target_box is not None:
                bx1, by1, bx2, by2 = world.target_box
                a = _clip_pt((bx1 + bx2) * 0.5, (by1 + by2) * 0.5, width, height)
                b = _clip_pt((x1 + x2) * 0.5, (y1 + y2) * 0.5, width, height)
                if a != b:
                    cv2.line(buf, a, b, COLOR_PREDICTION, 1, cv2.LINE_AA)

    if getattr(cfg, "vis_target_box", False) and world.target_box is not None:
        x1, y1, x2, y2 = world.target_box
        p1 = _clip_pt(min(x1, x2), min(y1, y2), width, height)
        p2 = _clip_pt(max(x1, x2), max(y1, y2), width, height)
        if p1 != p2:
            cv2.rectangle(buf, p1, p2, COLOR_TARGET_BOX, 1, cv2.LINE_AA)

    if getattr(cfg, "aim_director", False) and getattr(cfg, "vis_aim_director_fov", False):
        director_r = max(
            1,
            int(round(float(getattr(cfg, "aim_director_radius_px", 0.0)))),
        )
        cv2.circle(buf, (cx, cy), director_r, COLOR_AIM_DIRECTOR, 1, cv2.LINE_AA)
        if getattr(cfg, "aim_director_deadzone", False):
            deadzone_r = max(
                1,
                int(round(float(getattr(cfg, "aim_director_deadzone_px", 0.0)))),
            )
            if deadzone_r < director_r:
                cv2.circle(buf, (cx, cy), deadzone_r, COLOR_AIM_DIRECTOR, 1, cv2.LINE_AA)
    if getattr(cfg, "aim_corrector", False) and getattr(cfg, "vis_aim_corrector_fov", False):
        corrector_r = max(
            1,
            int(round(float(getattr(cfg, "aim_corrector_radius_px", 0.0)))),
        )
        cv2.circle(buf, (cx, cy), corrector_r, COLOR_AIM_CORRECTOR, 1, cv2.LINE_AA)
    if getattr(cfg, "tracking_assist", False) and getattr(
        cfg, "vis_tracking_assist_fov", False
    ):
        tracking_r = max(
            1,
            int(round(float(getattr(cfg, "tracking_assist_radius_px", 0.0)))),
        )
        cv2.circle(buf, (cx, cy), tracking_r, COLOR_TRACKING_ASSIST, 1, cv2.LINE_AA)
    if getattr(cfg, "spray_acquire", False) and getattr(
        cfg, "vis_spray_acquire_fov", False
    ):
        spray_r = max(
            1,
            int(round(float(getattr(cfg, "spray_acquire_radius_px", 0.0)))),
        )
        cv2.circle(buf, (cx, cy), spray_r, COLOR_SPRAY_ACQUIRE, 1, cv2.LINE_AA)
    if getattr(cfg, "tap_assist", False) and getattr(cfg, "vis_tap_fov", False):
        tap_r = max(1, int(round(float(getattr(cfg, "tap_range_px", 0.0)))))
        cv2.circle(buf, (cx, cy), tap_r, COLOR_TAP, 1, cv2.LINE_AA)
    if getattr(cfg, "rp_aim_assist", False) and getattr(cfg, "vis_rp_aim_fov", False):
        aim_r = max(2, int(round(float(getattr(cfg, "rp_aim_radius_px", 0.0)))))
        for spot in world.rp_aim_spots:
            cv2.circle(
                buf,
                _clip_pt(spot[0], spot[1], width, height),
                aim_r,
                COLOR_RP_AIM,
                1,
                cv2.LINE_AA,
            )
    if getattr(cfg, "rp_one_tap_assist", False):
        if getattr(cfg, "vis_rp_one_tap_reset", False):
            reset_r = max(
                1,
                int(round(float(getattr(cfg, "rp_one_tap_reset_fov_px", 0.0)))),
            )
            cv2.circle(buf, (cx, cy), reset_r, COLOR_RP_ONE_TAP_RESET, 1, cv2.LINE_AA)
        if getattr(cfg, "vis_rp_one_tap_fov", False):
            one_tap_r = max(
                1,
                int(round(float(getattr(cfg, "rp_one_tap_fov_px", 0.0)))),
            )
            cv2.circle(buf, (cx, cy), one_tap_r, COLOR_RP_ONE_TAP, 1, cv2.LINE_AA)
    if getattr(cfg, "trigger", False) and getattr(cfg, "vis_trigger_fov", False):
        trig_r = max(2, int(round(float(getattr(cfg, "trigger_radius_px", 0.0)))))
        cv2.circle(buf, (cx, cy), trig_r, COLOR_TRIGGER, 1, cv2.LINE_AA)
    if getattr(cfg, "magnet", False) and getattr(cfg, "vis_magnet_fov", False):
        mag_r = max(2, int(round(float(getattr(cfg, "magnet_radius_px", 0.0)))))
        cv2.circle(buf, (cx, cy), mag_r, COLOR_MAGNET, 1, cv2.LINE_AA)
        mag_fire_r = max(2, int(round(float(getattr(cfg, "magnet_trigger_radius_px", 0.0)))))
        cv2.circle(buf, (cx, cy), mag_fire_r, COLOR_TRIGGER, 1, cv2.LINE_AA)

    if getattr(cfg, "aim_director", False) and getattr(cfg, "vis_aim_director_spot", False):
        for spot in world.aim_director_spots:
            cv2.circle(
                buf,
                _clip_pt(spot[0], spot[1], width, height),
                4,
                COLOR_AIM_DIRECTOR_SPOT,
                -1,
                cv2.LINE_AA,
            )
    if (
        getattr(cfg, "aim_corrector", False)
        and getattr(cfg, "vis_aim_corrector_spot", False)
        and world.aim_corrector_spot is not None
    ):
        cv2.circle(
            buf,
            _clip_pt(*world.aim_corrector_spot, width, height),
            4,
            COLOR_AIM_CORRECTOR_SPOT,
            -1,
            cv2.LINE_AA,
        )
    if (
        getattr(cfg, "tracking_assist", False)
        and getattr(cfg, "vis_tracking_assist_spot", False)
        and world.target_box is not None
    ):
        _draw_center_box(buf, world.target_box, COLOR_TRACKING_ASSIST_SPOT, cross="both")
    if (
        getattr(cfg, "spray_acquire", False)
        and getattr(cfg, "vis_spray_acquire_spot", False)
        and world.target_box is not None
    ):
        _draw_center_box(
            buf,
            world.target_box,
            COLOR_SPRAY_ACQUIRE_SPOT,
            inset=3.0,
            cross="x",
        )
    if (
        getattr(cfg, "tap_assist", False)
        and getattr(cfg, "vis_tap_spot", False)
        and world.tap_spot is not None
    ):
        cv2.circle(
            buf,
            _clip_pt(*world.tap_spot, width, height),
            4,
            COLOR_TAP_SPOT,
            -1,
            cv2.LINE_AA,
        )
    if getattr(cfg, "rp_aim_assist", False) and getattr(cfg, "vis_rp_aim_spot", False):
        for spot in world.rp_aim_spots:
            cv2.circle(
                buf,
                _clip_pt(spot[0], spot[1], width, height),
                3,
                COLOR_RP_AIM_SPOT,
                -1,
                cv2.LINE_AA,
            )
    if (
        getattr(cfg, "rp_one_tap_assist", False)
        and getattr(cfg, "vis_rp_one_tap_spot", False)
        and world.rp_one_tap_spot is not None
    ):
        cv2.circle(
            buf,
            _clip_pt(*world.rp_one_tap_spot, width, height),
            4,
            COLOR_RP_ONE_TAP_SPOT,
            -1,
            cv2.LINE_AA,
        )
    if getattr(cfg, "trigger", False) and getattr(cfg, "vis_trigger_spot", False):
        for spot in world.trigger_spots:
            cv2.circle(buf, _clip_pt(spot[0], spot[1], width, height), 3, COLOR_TRIGGER_SPOT, -1, cv2.LINE_AA)
    if getattr(cfg, "magnet", False) and getattr(cfg, "vis_magnet_spot", False):
        for spot in world.magnet_pull_spots:
            cv2.circle(buf, _clip_pt(spot[0], spot[1], width, height), 4, COLOR_MAGNET_SPOT, -1, cv2.LINE_AA)
        for spot in world.magnet_fire_spots:
            cv2.circle(buf, _clip_pt(spot[0], spot[1], width, height), 2, COLOR_MAGNET_FIRE_SPOT, -1, cv2.LINE_AA)


class OverlayRenderer:
    """Fullscreen layered window, updated on a background thread."""

    def __init__(self, controller: Any) -> None:
        self._controller = controller
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._hwnd = 0
        self._wndproc = None
        self._class_atom = 0
        self._class_name = "ArgusOverlay"
        self._hdc_screen = 0
        self._hdc_mem = 0
        self._hbm = 0
        self._old_bmp = 0
        self._bits: Optional[np.ndarray] = None
        self._size = (0, 0)
        self._geom = (0, 0, 0, 0)
        self._visible = False
        self._menu_raise_at = 0.0

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="overlay", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._hwnd:
            user32.PostMessageW(self._hwnd, WM_DESTROY, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _create_window(self, x: int, y: int, width: int, height: int) -> None:
        hinstance = kernel32.GetModuleHandleW(None)
        self._wndproc = WNDPROC(self._wnd_proc)
        self._wndclass = WNDCLASSW()
        self._wndclass.style = 0
        self._wndclass.lpfnWndProc = self._wndproc
        self._wndclass.hInstance = hinstance
        self._wndclass.lpszClassName = self._class_name
        atom = user32.RegisterClassW(ctypes.byref(self._wndclass))
        self._class_atom = int(atom or 0)
        ex = (
            WS_EX_LAYERED
            | WS_EX_TRANSPARENT
            | WS_EX_TOPMOST
            | WS_EX_TOOLWINDOW
            | WS_EX_NOACTIVATE
        )
        hwnd = user32.CreateWindowExW(
            ex,
            self._class_name,
            "Argus Overlay",
            WS_POPUP,
            x,
            y,
            width,
            height,
            None,
            None,
            hinstance,
            None,
        )
        if not hwnd:
            raise RuntimeError("Failed to create overlay window")
        self._hwnd = int(hwnd)
        try:
            user32.SetWindowDisplayAffinity(self._hwnd, WDA_EXCLUDEFROMCAPTURE)
        except Exception:
            pass
        self._alloc_buffer(width, height)

    def _alloc_buffer(self, width: int, height: int) -> None:
        self._release_buffer()
        hdc_screen = user32.GetDC(None)
        if not hdc_screen:
            raise RuntimeError("GetDC failed")
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        if not hdc_mem:
            user32.ReleaseDC(None, hdc_screen)
            raise RuntimeError("CreateCompatibleDC failed")
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB
        bits_ptr = ctypes.c_void_p()
        hbm = gdi32.CreateDIBSection(
            hdc_mem,
            ctypes.byref(bmi),
            DIB_RGB_COLORS,
            ctypes.byref(bits_ptr),
            None,
            0,
        )
        if not hbm or not bits_ptr.value:
            gdi32.DeleteDC(hdc_mem)
            user32.ReleaseDC(None, hdc_screen)
            raise RuntimeError(f"CreateDIBSection failed ({ctypes.GetLastError()})")
        self._hdc_screen = hdc_screen
        self._hdc_mem = hdc_mem
        self._hbm = hbm
        self._old_bmp = gdi32.SelectObject(hdc_mem, hbm)
        buf = (ctypes.c_char * (width * height * 4)).from_address(bits_ptr.value)
        array = np.frombuffer(buf, dtype=np.uint8).reshape((height, width, 4))
        array.flags.writeable = True
        self._bits = array
        self._size = (width, height)

    def _release_buffer(self) -> None:
        try:
            if self._hdc_mem and self._old_bmp:
                gdi32.SelectObject(self._hdc_mem, self._old_bmp)
        except Exception:
            pass
        self._old_bmp = 0
        try:
            if self._hbm:
                gdi32.DeleteObject(self._hbm)
        except Exception:
            pass
        self._hbm = 0
        try:
            if self._hdc_mem:
                gdi32.DeleteDC(self._hdc_mem)
        except Exception:
            pass
        self._hdc_mem = 0
        try:
            if self._hdc_screen:
                user32.ReleaseDC(None, self._hdc_screen)
        except Exception:
            pass
        self._hdc_screen = 0
        self._bits = None

    def _destroy_window(self) -> None:
        self._release_buffer()
        if self._hwnd:
            user32.DestroyWindow(self._hwnd)
            self._hwnd = 0

    def _pump(self) -> None:
        msg = wintypes.MSG()
        while user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, PM_REMOVE):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _present(self, left: int, top: int) -> None:
        if not self._hwnd or self._bits is None:
            return
        width, height = self._size
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        src = POINT(0, 0)
        dst = POINT(left, top)
        size = SIZE(width, height)
        user32.UpdateLayeredWindow(
            self._hwnd,
            self._hdc_screen,
            ctypes.byref(dst),
            ctypes.byref(size),
            self._hdc_mem,
            ctypes.byref(src),
            0,
            ctypes.byref(blend),
            ULW_ALPHA,
        )

    def _raise_menu(self) -> None:
        hwnd = int(getattr(self._controller, "hwnd", 0) or 0)
        if not hwnd or not getattr(self._controller, "menu_visible", False):
            return
        now = time.perf_counter()
        if now < self._menu_raise_at:
            return
        self._menu_raise_at = now + 0.2
        user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
        )

    def _set_visible(self, visible: bool) -> None:
        if not self._hwnd or visible == self._visible:
            return
        user32.ShowWindow(self._hwnd, SW_SHOWNOACTIVATE if visible else SW_HIDE)
        self._visible = visible

    def _roi_rect(self, world: OverlayWorld) -> Tuple[int, int, int, int]:
        roi = world.roi
        if roi[2] > roi[0] and roi[3] > roi[1]:
            return int(roi[0]), int(roi[1]), int(roi[2] - roi[0]), int(roi[3] - roi[1])
        sw, sh = _monitor_size()
        left, top, right, bottom = compute_centered_roi(sw, sh, ROI_SIZE)
        return left, top, right - left, bottom - top

    def _loop(self) -> None:
        try:
            sw, sh = _monitor_size()
            left, top, right, bottom = compute_centered_roi(sw, sh, ROI_SIZE)
            width, height = right - left, bottom - top
            self._create_window(left, top, width, height)
            self._geom = (left, top, width, height)
            min_interval = 1.0 / OVERLAY_MAX_HZ
            next_present = 0.0
            last_world_gen = -1
            last_cfg_gen = -1
            while self._running:
                self._pump()
                cfg = self._controller.live.get()
                cfg_gen = self._controller.live.generation()
                wanted = overlay_wanted(cfg)
                self._set_visible(wanted)
                world, world_gen = self._controller.overlay_world.snapshot()
                now = time.perf_counter()
                dirty = world_gen != last_world_gen or cfg_gen != last_cfg_gen
                if wanted and self._bits is not None and dirty and now >= next_present:
                    left, top, width, height = self._roi_rect(world)
                    if (width, height) != self._size:
                        self._alloc_buffer(width, height)
                    self._geom = (left, top, width, height)
                    render_overlay(self._bits, cfg, world)
                    self._present(left, top)
                    self._raise_menu()
                    last_world_gen = world_gen
                    last_cfg_gen = cfg_gen
                    next_present = now + min_interval
                    continue
                if not wanted:
                    last_world_gen = -1
                    last_cfg_gen = -1
                    time.sleep(0.05)
                    continue
                wait_for = min_interval
                if dirty:
                    wait_for = max(0.0, next_present - now)
                self._controller.overlay_world.wait(wait_for)
        except Exception as exc:
            log_error(f"Overlay failed: {exc}")
        finally:
            self._destroy_window()
            self._running = False
