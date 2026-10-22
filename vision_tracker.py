"""
Vision & detection module: DXCam screen capture, TensorRT YOLO inference,
and HSV marker exclusion filtering for target tracking research.
"""

from __future__ import annotations

import ctypes
import math
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Optional, Sequence, Tuple

import cv2
import numpy as np

cv2.setNumThreads(1)
try:
    cv2.ocl.setUseOpenCL(False)
except Exception:
    pass

# Unique DXGI presents, paced by detection — not the assist-loop FPS slider.
DXGI_FRAME_WAIT_MS = 8

# --- Research tuning constants ------------------------------------------------

ROI_SIZE = 640
ROI_CENTER = (320, 320)
FOV_MIN_PX = 40.0
FOV_MAX_PX = 320.0
DEFAULT_FOV_PX = 280.0

# Aim points as (x, y) fractions of the detection box. y is from the top.
# Geometric box center is ~0.50 (waist/groin); upper-body bones sit well above that.
AIM_MODES = ("head", "neck", "chest", "closest")
AIM_BONE_FROM_TOP = {
    "head": (0.50, 0.12),
    "neck": (0.50, 0.20),
    "spine3": (0.50, 0.26),
    "chest": (0.50, 0.32),
    "stomach": (0.50, 0.46),
    "pelvis": (0.50, 0.56),
    "shoulder_r": (0.28, 0.22),
    "elbow_r": (0.14, 0.34),
    "shoulder_l": (0.72, 0.22),
    "elbow_l": (0.86, 0.34),
    "hip_r": (0.38, 0.56),
    "knee_r": (0.40, 0.74),
    "ankle_r": (0.40, 0.92),
    "hip_l": (0.62, 0.56),
    "knee_l": (0.60, 0.74),
    "ankle_l": (0.60, 0.92),
}
BONE_ALIASES = {
    "spine2": "chest",
    "spine1": "stomach",
    "head_bone": "head",
}
CLOSEST_BONES = ("head", "neck", "chest")
TRIGGER_BONES = tuple(AIM_BONE_FROM_TOP.keys())
AIM_LOCK_PX = 14.0
TARGET_SWITCH_PX = 16.0
TARGET_MATCH_IOU = 0.25
PLAYER_CLASS_ID = 0
HEAD_CLASS_ID = 1
HEAD_TO_BODY_HEIGHT = 6.5

# What each class of a model means to the tracker. A model is recognised by its
# engine's file name; the class order is the one its ONNX export carries.
#   player  a whole (or partly hidden) enemy
#   head    the head alone: refines the head/neck aim point, and stands in for
#           the player when only the head is showing
#   ignore  never a target (own character, weapons, smoke, corpses...)
# Models that are not listed keep the old convention: class 1 is a head,
# everything else is a player.
ROLE_PLAYER = "player"
ROLE_HEAD = "head"
ROLE_IGNORE = "ignore"
_TWO_CLASS = {0: ROLE_PLAYER, 1: ROLE_HEAD}
MODEL_CLASS_ROLES: Dict[str, Dict[int, str]] = {
    "fortnite": {0: ROLE_PLAYER, 1: ROLE_HEAD, 2: ROLE_IGNORE},  # 2 = third_person
    "pubg": {
        0: ROLE_PLAYER,   # player
        1: ROLE_PLAYER,   # bot
        2: ROLE_IGNORE,   # weapon
        3: ROLE_IGNORE,   # outline
        4: ROLE_IGNORE,   # dead_body
        5: ROLE_PLAYER,   # hideout_target_human
        6: ROLE_PLAYER,   # hideout_target_balls
        7: ROLE_HEAD,     # head
        8: ROLE_IGNORE,   # smoke
        9: ROLE_IGNORE,   # fire
        10: ROLE_IGNORE,  # third_person
    },
    "r6": _TWO_CLASS,
    "roblox": _TWO_CLASS,
    "universal": _TWO_CLASS,
    "valorant": _TWO_CLASS,
}
# A head belongs to a player when its centre sits in the upper part of that
# player's box and it is clearly smaller than the box.
HEAD_MAX_DEPTH = 0.55
HEAD_MAX_HEIGHT_FRAC = 0.62
HEAD_MAX_WIDTH_FRAC = 0.90
# A head box that vanishes for a frame or two is carried over, moved along
# with its player, so the aim point does not jump between the real head and
# the box-fraction estimate.
HEAD_HOLD_S = 0.12
HEAD_HOLD_IOU = 0.5

# Default HSV range for saturated red overhead markers (OpenCV H: 0-179).
DEFAULT_MARKER_HSV_LOWER = np.array([0, 120, 120], dtype=np.uint8)
DEFAULT_MARKER_HSV_UPPER = np.array([10, 255, 255], dtype=np.uint8)
MARKER_STRIP_HEIGHT_PX = 20
MARKER_PIXEL_FRACTION_THRESHOLD = 0.25
IOU_THRESHOLD = 0.45


@dataclass(frozen=True)
class Detection:
    """Single YOLO detection before target selection."""

    bbox: Tuple[float, float, float, float]  # x1, y1, x2, y2
    score: float
    class_id: int
    centroid: Tuple[float, float]


@dataclass(frozen=True)
class TargetState:
    """Best target after marker filtering, with offset-compensated vector."""

    dx: float
    dy: float
    confidence: float
    bbox: Tuple[float, float, float, float]
    raw_centroid: Tuple[float, float]
    compensated_centroid: Tuple[float, float]
    class_id: int
    # The real head box when the model saw one, else None.
    head_bbox: Optional[Tuple[float, float, float, float]] = None


class AimBox(tuple):
    """A player box that also remembers where its head was detected.

    Behaves exactly like the (x1, y1, x2, y2) tuple everywhere; `hitbox_point`
    is the only thing that looks at `.head`.
    """

    head: Optional[Tuple[float, float, float, float]]

    def __new__(
        cls,
        bbox: Sequence[float],
        head: Optional[Sequence[float]] = None,
    ) -> "AimBox":
        box = super().__new__(cls, (float(v) for v in bbox))
        box.head = tuple(float(v) for v in head) if head is not None else None
        return box


def bone_xy(bone: str) -> Tuple[float, float]:
    spec = AIM_BONE_FROM_TOP.get(bone, AIM_BONE_FROM_TOP["chest"])
    if isinstance(spec, (int, float)):
        return 0.5, float(spec)
    return float(spec[0]), float(spec[1])


def _head_point(
    head: Tuple[float, float, float, float], bone: str
) -> Tuple[float, float]:
    """Head and neck aim points taken from a detected head box."""
    hx1, hy1, hx2, hy2 = head
    top = min(hy1, hy2)
    height = abs(hy2 - hy1)
    cx = (hx1 + hx2) * 0.5
    if bone == "head":
        return cx, top + height * 0.5
    return cx, top + height * 1.15


def hitbox_point(
    bbox: Tuple[float, float, float, float],
    bone: str,
) -> Tuple[float, float]:
    """Return (x, y) in ROI pixels for a named bone on the bbox."""
    head = getattr(bbox, "head", None)
    if head is not None and bone in ("head", "neck"):
        return _head_point(head, bone)
    x1, y1, x2, y2 = bbox
    xf, yf = bone_xy(bone)
    return x1 + (x2 - x1) * xf, y1 + (y2 - y1) * yf


def class_roles_for_engine(engine_path: str) -> Optional[Dict[int, str]]:
    """Class roles for a known model, or None to use the old convention."""
    stem = str(engine_path or "").replace("\\", "/").rsplit("/", 1)[-1]
    stem = stem.rsplit(".", 1)[0].strip().lower()
    return MODEL_CLASS_ROLES.get(stem)


def head_fits_player(
    head: Tuple[float, float, float, float],
    player: Tuple[float, float, float, float],
) -> bool:
    """True when `head` is plausibly the head of the player in `player`."""
    px1, py1, px2, py2 = player
    pw, ph = px2 - px1, py2 - py1
    hw, hh = abs(head[2] - head[0]), abs(head[3] - head[1])
    if pw <= 1.0 or ph <= 1.0 or hw <= 0.0 or hh <= 0.0:
        return False
    cx = (head[0] + head[2]) * 0.5
    cy = (head[1] + head[3]) * 0.5
    if not (px1 <= cx <= px2 and py1 <= cy <= py1 + ph * HEAD_MAX_DEPTH):
        return False
    return hh <= ph * HEAD_MAX_HEIGHT_FRAC and hw <= pw * HEAD_MAX_WIDTH_FRAC


def hitbox_points(
    bbox: Tuple[float, float, float, float],
    bones: Iterable[str] = CLOSEST_BONES,
) -> Dict[str, Tuple[float, float]]:
    return {bone: hitbox_point(bbox, bone) for bone in bones}


def player_bbox_from_head(
    bbox: Tuple[float, float, float, float],
) -> Tuple[float, float, float, float]:
    """Estimate a full-player box when the model only sees its `head` class."""
    x1, y1, x2, y2 = bbox
    head_w = max(1.0, abs(x2 - x1))
    head_h = max(1.0, abs(y2 - y1))
    body_h = head_h * HEAD_TO_BODY_HEIGHT
    body_w = max(head_w * 2.4, body_h * 0.30)
    center_x = (x1 + x2) * 0.5
    top = min(y1, y2) - head_h * 0.05
    return (
        center_x - body_w * 0.5,
        top,
        center_x + body_w * 0.5,
        top + body_h,
    )


def resolve_aim_offset(
    bbox: Tuple[float, float, float, float],
    aim_mode: str,
    roi_center: Tuple[float, float] = ROI_CENTER,
    locked_bone: Optional[str] = None,
    lock_px: float = AIM_LOCK_PX,
) -> Tuple[float, float, str]:
    """
    Offset from ROI/crosshair center to the selected aim point.

    closest: nearest of head/neck/chest, with hysteresis so it does not flicker.
    """
    mode = aim_mode if aim_mode in AIM_MODES else "closest"
    hx, hy = roi_center
    if mode != "closest":
        px, py = hitbox_point(bbox, mode)
        return px - hx, py - hy, mode

    points = hitbox_points(bbox)
    best_bone = "chest"
    best_dist = float("inf")
    for bone, (px, py) in points.items():
        dist = math.hypot(px - hx, py - hy)
        if dist < best_dist:
            best_dist = dist
            best_bone = bone
    if locked_bone in points:
        lx, ly = points[locked_bone]
        if math.hypot(lx - hx, ly - hy) <= best_dist + lock_px:
            best_bone = locked_bone
    px, py = points[best_bone]
    return px - hx, py - hy, best_bone


def _get_primary_monitor_size() -> Tuple[int, int]:
    """Return (width, height) of the primary display."""
    user32 = ctypes.windll.user32
    width = int(user32.GetSystemMetrics(0))  # SM_CXSCREEN
    height = int(user32.GetSystemMetrics(1))  # SM_CYSCREEN
    return width, height


def clamp_fov_radius(value: float) -> float:
    return max(FOV_MIN_PX, min(float(value), FOV_MAX_PX))


def detection_in_fov(
    detection: Detection,
    center: Tuple[float, float],
    radius: float,
) -> bool:
    # A large close-range player can cover the crosshair while its centroid is
    # well outside a small FOV. Test the nearest point on the whole box so the
    # actual rule is "box touches circle" at every target scale.
    return bbox_crosshair_dist(detection.bbox, center) <= max(0.0, radius)


def bbox_iou(
    a: Tuple[float, float, float, float],
    b: Tuple[float, float, float, float],
) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0.0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0.0 else 0.0


def bbox_crosshair_dist(
    bbox: Tuple[float, float, float, float],
    center: Tuple[float, float],
) -> float:
    """Screen-space distance from the crosshair to the bbox (0 if inside)."""
    cx, cy = center
    x1, y1, x2, y2 = bbox
    left, right = (x1, x2) if x1 <= x2 else (x2, x1)
    top, bottom = (y1, y2) if y1 <= y2 else (y2, y1)
    px = min(max(cx, left), right)
    py = min(max(cy, top), bottom)
    return math.hypot(px - cx, py - cy)


def compute_centered_roi(
    screen_w: int, screen_h: int, roi_size: int = ROI_SIZE
) -> Tuple[int, int, int, int]:
    """Return (left, top, right, bottom) for a centered square ROI."""
    left = max(0, (screen_w - roi_size) // 2)
    top = max(0, (screen_h - roi_size) // 2)
    return left, top, left + roi_size, top + roi_size


def _patch_dxgi_acquire_timeout(timeout_ms: int = DXGI_FRAME_WAIT_MS) -> None:
    """Wait for the next desktop present instead of spinning AcquireNextFrame(0)."""
    from dxcam.core.dxgi_duplicator import DXGIDuplicator

    if getattr(DXGIDuplicator.update_frame, "_argus_wait_ms", None) == timeout_ms:
        return

    from typing import Any, cast

    import comtypes
    from dxcam._libs.d3d11 import ID3D11Texture2D
    from dxcam._libs.dxgi import DXGI_OUTDUPL_FRAME_INFO, IDXGIResource
    from dxcam.core.dxgi_duplicator import (
        DXGI_ERROR_WAIT_TIMEOUT_U32,
        DXGITransientContext,
        com_error_hresult_u32,
        is_transient_hresult,
        logger,
    )

    def update_frame(self: Any, wait_for_frame: bool = False) -> bool:
        del wait_for_frame
        if self._frame_held:
            if not self.release_frame():
                self.updated = False
                return False

        info = DXGI_OUTDUPL_FRAME_INFO()
        res = ctypes.POINTER(IDXGIResource)()
        try:
            self.duplicator.AcquireNextFrame(
                timeout_ms,
                ctypes.byref(info),
                ctypes.byref(res),
            )
        except comtypes.COMError as ce:
            hresult = com_error_hresult_u32(ce)
            if hresult == DXGI_ERROR_WAIT_TIMEOUT_U32:
                self.updated = False
                self.accumulated_frames = 0
                return True
            if is_transient_hresult(
                hresult,
                DXGITransientContext.FRAME_INFO,
                DXGITransientContext.SYSTEM_TRANSITION,
            ):
                logger.warning(
                    "Desktop duplication access loss/system transition detected "
                    "(HRESULT=0x%08X). Triggering output-change recovery.",
                    hresult,
                )
                self.updated = False
                self._frame_held = False
                return False
            raise
        self._frame_held = True
        try:
            resource = cast(Any, res)
            self.texture = resource.QueryInterface(ID3D11Texture2D)
        except comtypes.COMError:
            self.release_frame()
            self.texture = ctypes.POINTER(ID3D11Texture2D)()
            self.updated = False
            return True
        present_ticks = int(info.LastPresentTime)
        self.accumulated_frames = int(info.AccumulatedFrames)
        if present_ticks > 0:
            self.latest_frame_ticks = present_ticks
        else:
            mouse_ticks = int(info.LastMouseUpdateTime)
            if mouse_ticks > 0:
                self.latest_frame_ticks = mouse_ticks
        self.updated = True
        return True

    update_frame._argus_wait_ms = timeout_ms  # type: ignore[attr-defined]
    DXGIDuplicator.update_frame = update_frame  # type: ignore[method-assign]


class ScreenCapture:
    """DXCam-based ROI capture centered on the primary monitor midpoint."""

    def __init__(
        self,
        roi_size: int = ROI_SIZE,
        target_fps: int = 240,
        output_idx: int = 0,
    ) -> None:
        self.roi_size = roi_size
        self.target_fps = target_fps
        screen_w, screen_h = _get_primary_monitor_size()
        self.region = compute_centered_roi(screen_w, screen_h, roi_size)
        _patch_dxgi_acquire_timeout()
        import dxcam

        self._camera = dxcam.create(
            output_idx=output_idx,
            region=self.region,
            output_color="RGB",
            max_buffer_len=2,
        )
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        # One-shot grab on the vision thread: copy a frame only when YOLO
        # is ready. Avoids a 170 Hz DXGI thread stealing the GPU from CS2.
        self._started = True

    def stop(self) -> None:
        self._started = False
        camera = getattr(self, "_camera", None)
        self._camera = None
        if camera is None:
            return
        try:
            camera.release()
        except Exception:
            try:
                if getattr(camera, "is_capturing", False):
                    camera.stop()
            except Exception:
                pass

    def get_frame(self) -> Optional[np.ndarray]:
        """Wait for the next unique RGB desktop frame, then return (H, W, 3)."""
        camera = self._camera
        if not self._started or camera is None:
            return None
        deadline = time.perf_counter() + 0.05
        while self._started:
            try:
                frame = camera.grab(
                    region=self.region,
                    copy=False,
                    new_frame_only=True,
                )
            except Exception:
                return None
            if frame is not None:
                return frame
            if time.perf_counter() >= deadline:
                return None
        return None


class MarkerExclusionFilter:
    """
    Reject detections whose scale-aware overhead strip matches an HSV marker.
    """

    def __init__(
        self,
        hsv_lower: np.ndarray = DEFAULT_MARKER_HSV_LOWER,
        hsv_upper: np.ndarray = DEFAULT_MARKER_HSV_UPPER,
        strip_height: int = MARKER_STRIP_HEIGHT_PX,
        pixel_fraction_threshold: float = MARKER_PIXEL_FRACTION_THRESHOLD,
    ) -> None:
        self.hsv_lower = hsv_lower
        self.hsv_upper = hsv_upper
        self.strip_height = strip_height
        self.pixel_fraction_threshold = pixel_fraction_threshold

    def has_overhead_marker(
        self, frame_rgb: np.ndarray, bbox: Tuple[float, float, float, float]
    ) -> bool:
        x1, y1, x2, y2 = (int(round(v)) for v in bbox)
        x1 = max(0, min(x1, frame_rgb.shape[1] - 1))
        x2 = max(0, min(x2, frame_rgb.shape[1]))
        y1 = max(0, min(y1, frame_rgb.shape[0]))
        y2 = max(0, min(y2, frame_rgb.shape[0]))

        if x2 <= x1 or y2 <= y1:
            return False

        # A fixed 20px strip is disproportionately large for a tiny distant
        # player and can turn unrelated red scenery into a false rejection.
        # Keep the original cap for medium/close targets, but scale down with
        # box height at distance.
        box_height = y2 - y1
        strip_height = min(
            self.strip_height,
            max(4, int(round(box_height * 0.40))),
        )
        strip_top = max(0, y1 - strip_height)
        strip_bottom = y1
        if strip_bottom <= strip_top:
            return False

        crop = frame_rgb[strip_top:strip_bottom, x1:x2]
        if crop.size == 0:
            return False

        hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
        mask = cv2.inRange(hsv, self.hsv_lower, self.hsv_upper)
        fraction = float(np.count_nonzero(mask)) / mask.size
        return fraction >= self.pixel_fraction_threshold

    def filter_detections(
        self, frame_rgb: np.ndarray, detections: Sequence[Detection]
    ) -> list[Detection]:
        return [d for d in detections if not self.has_overhead_marker(frame_rgb, d.bbox)]


def _xywh_to_xyxy(boxes: np.ndarray) -> np.ndarray:
    """Convert center-size boxes (N, 4) to corner boxes (N, 4)."""
    out = np.empty_like(boxes)
    out[:, 0] = boxes[:, 0] - boxes[:, 2] * 0.5
    out[:, 1] = boxes[:, 1] - boxes[:, 3] * 0.5
    out[:, 2] = boxes[:, 0] + boxes[:, 2] * 0.5
    out[:, 3] = boxes[:, 1] + boxes[:, 3] * 0.5
    return out


def _nms_xyxy(
    boxes: np.ndarray,
    scores: np.ndarray,
    iou_threshold: float,
    class_ids: Optional[np.ndarray] = None,
) -> list[int]:
    """Run class-aware OpenCV NMS on xyxy boxes and return kept indices."""
    if boxes.size == 0:
        return []

    # cv2.dnn.NMSBoxes expects Rect boxes as x, y, width, height. Passing
    # x1, y1, x2, y2 (the old behavior) makes IoU vary with screen position
    # and disproportionately affects very small/large boxes.
    xywh = np.column_stack(
        (
            boxes[:, 0],
            boxes[:, 1],
            np.maximum(0.0, boxes[:, 2] - boxes[:, 0]),
            np.maximum(0.0, boxes[:, 3] - boxes[:, 1]),
        )
    )
    valid = np.flatnonzero((xywh[:, 2] > 0.0) & (xywh[:, 3] > 0.0))
    if valid.size == 0:
        return []

    if class_ids is None:
        groups = (valid,)
    else:
        groups = tuple(
            valid[class_ids[valid] == class_id]
            for class_id in np.unique(class_ids[valid])
        )

    kept: list[int] = []
    for group in groups:
        if group.size == 0:
            continue
        local = cv2.dnn.NMSBoxes(
            xywh[group].tolist(),
            scores[group].tolist(),
            score_threshold=0.0,
            nms_threshold=iou_threshold,
        )
        if len(local) == 0:
            continue
        local_indices = np.asarray(local).reshape(-1)
        kept.extend(int(group[int(index)]) for index in local_indices)
    kept.sort(key=lambda index: float(scores[index]), reverse=True)
    return kept


def _postprocess_raw_yolo(
    output: np.ndarray,
    conf_threshold: float,
    iou_threshold: float,
    image_size: int,
    target_class_ids: Optional[set[int]],
    coords_are_normalized: Optional[bool] = None,
) -> Tuple[list[Detection], bool]:
    """
    Decode raw Ultralytics-style YOLO output shaped [1, C, N] or [1, N, C].

    C = 4 + num_classes (box cx,cy,w,h plus per-class scores).
    """
    if output.ndim != 3:
        raise RuntimeError(f"Unexpected raw YOLO output rank: {output.shape}")

    # Normalize to [num_predictions, num_features].
    if output.shape[1] <= output.shape[2]:
        predictions = np.transpose(output[0], (1, 0))
    else:
        predictions = output[0]

    if predictions.shape[1] < 5:
        raise RuntimeError(f"Unexpected raw YOLO feature count: {predictions.shape}")

    boxes_xywh = np.asarray(predictions[:, :4], dtype=np.float32)
    class_scores = np.asarray(predictions[:, 4:], dtype=np.float32)
    class_ids = np.argmax(class_scores, axis=1)
    scores = class_scores[np.arange(class_scores.shape[0]), class_ids]

    # Some exports emit normalized coordinates in [0, 1]. Cache after first frame.
    if coords_are_normalized is None:
        coords_are_normalized = float(np.max(boxes_xywh)) <= 1.5
    if coords_are_normalized:
        boxes_xywh[:, [0, 2]] *= image_size
        boxes_xywh[:, [1, 3]] *= image_size

    keep = scores >= conf_threshold
    if target_class_ids:
        keep &= np.isin(class_ids, list(target_class_ids))
    boxes_xywh = boxes_xywh[keep]
    scores = scores[keep]
    class_ids = class_ids[keep]
    if boxes_xywh.shape[0] == 0:
        return [], bool(coords_are_normalized)

    boxes_xyxy = _xywh_to_xyxy(boxes_xywh)
    boxes_xyxy[:, [0, 2]] = np.clip(boxes_xyxy[:, [0, 2]], 0, image_size - 1)
    boxes_xyxy[:, [1, 3]] = np.clip(boxes_xyxy[:, [1, 3]], 0, image_size - 1)

    kept_indices = _nms_xyxy(
        boxes_xyxy,
        scores,
        iou_threshold,
        class_ids,
    )
    detections: list[Detection] = []
    for idx in kept_indices:
        x1, y1, x2, y2 = boxes_xyxy[idx]
        detections.append(
            Detection(
                bbox=(float(x1), float(y1), float(x2), float(y2)),
                score=float(scores[idx]),
                class_id=int(class_ids[idx]),
                centroid=((x1 + x2) * 0.5, (y1 + y2) * 0.5),
            )
        )
    return detections, bool(coords_are_normalized)


class TensorRTYoloDetector:
    """
    TensorRT YOLO engine wrapper.

    Supports:
      - Ultralytics End2End engines (num_dets, bboxes, scores, labels)
      - Raw Ultralytics ONNX/TRT exports (e.g. output0 shaped [1, 4+C, 8400])
    """

    def __init__(
        self,
        engine_path: str,
        conf_threshold: float = 0.30,
        target_class_ids: Optional[Sequence[int]] = None,
        input_size: int = ROI_SIZE,
        roi_size: int = ROI_SIZE,
    ) -> None:
        import warnings

        # pycuda warns when it can't find nvcc/CUDA_PATH (the full CUDA
        # Toolkit) to add to the DLL search path. That's only needed for
        # runtime kernel compilation (pycuda.compiler.SourceModule), which we
        # never use -- everything here goes through the driver API + a
        # pre-built TensorRT engine. Harmless; silence the noise.
        warnings.filterwarnings(
            "ignore",
            message=r".*Unable to discover CUDA installation directory.*",
            category=UserWarning,
        )
        import pycuda.driver as cuda
        import tensorrt as trt

        self.conf_threshold = conf_threshold
        self.target_class_ids = set(target_class_ids) if target_class_ids else None
        self.input_size = input_size
        self.roi_size = roi_size
        self._coord_scale = 1.0
        self.iou_threshold = IOU_THRESHOLD
        self._cuda = cuda
        cuda.init()
        try:
            # SCHED_SPIN busy-polls for GPU completion instead of parking the
            # thread on an OS wait handle. Measurably lower stream.synchronize()
            # latency (~15-20% more throughput under GPU contention with the
            # game, benchmarked), at the cost of spinning a CPU core. Worth it:
            # this thread is already boosted to HIGH/TIME_CRITICAL priority.
            self._ctx = cuda.Device(0).make_context(
                flags=int(cuda.ctx_flags.SCHED_SPIN)
            )
        except Exception:
            self._ctx = cuda.Device(0).make_context()
        self._output_format = "unknown"
        self._raw_output_name: Optional[str] = None
        self._raw_output_shape: Optional[tuple[int, ...]] = None
        self._thread_attached = False
        self._xywh_normalized: Optional[bool] = None
        self._input_nchw: Optional[np.ndarray] = None
        self._input_f32: Optional[np.ndarray] = None

        logger = trt.Logger(trt.Logger.WARNING)
        runtime = trt.Runtime(logger)
        with open(engine_path, "rb") as f:
            self._engine = runtime.deserialize_cuda_engine(f.read())
        if self._engine is None:
            raise RuntimeError(f"Failed to deserialize TensorRT engine: {engine_path}")

        self._context = self._engine.create_execution_context()
        self._stream = cuda.Stream()

        self._input_binding: Optional[int] = None
        self._output_bindings: dict[str, int] = {}
        self._host_buffers: dict[str, np.ndarray] = {}
        self._device_buffers: dict[str, cuda.DeviceAllocation] = {}
        self._bindings: list[int] = []

        for i in range(self._engine.num_io_tensors):
            name = self._engine.get_tensor_name(i)
            shape = tuple(self._engine.get_tensor_shape(name))
            dtype = trt.nptype(self._engine.get_tensor_dtype(name))
            size = int(np.prod(shape))
            try:
                host = cuda.pagelocked_empty(size, dtype)
            except Exception:
                host = np.empty(size, dtype=dtype)
            device = cuda.mem_alloc(host.nbytes)
            self._host_buffers[name] = host
            self._device_buffers[name] = device
            self._bindings.append(int(device))

            mode = self._engine.get_tensor_mode(name)
            if mode == trt.TensorIOMode.INPUT:
                self._input_binding = i
                self._input_name = name
            else:
                self._output_bindings[name.lower()] = i

        output_names = [
            name
            for name in self._host_buffers
            if name != self._input_name
            and self._engine.get_tensor_mode(name) == trt.TensorIOMode.OUTPUT
        ]
        if self._resolve_output("num_dets", "num", "num_detections"):
            self._output_format = "e2e"
        elif len(output_names) == 1:
            raw_name = output_names[0]
            raw_shape = tuple(self._engine.get_tensor_shape(raw_name))
            if len(raw_shape) == 3 and raw_shape[0] == 1 and raw_shape[1] >= 5:
                self._output_format = "raw_yolo"
                self._raw_output_name = raw_name
                self._raw_output_shape = raw_shape
            else:
                raise RuntimeError(
                    f"Unsupported single output shape {raw_shape} for tensor '{raw_name}'"
                )
        else:
            raise RuntimeError(
                "Engine outputs not recognized. Expected End2End tensors "
                "(num_dets, bboxes, scores, labels) or one raw YOLO output. "
                f"Found: {list(self._host_buffers.keys())}"
            )

        self._input_nchw = None
        self._input_f32 = None
        try:
            in_shape = tuple(self._engine.get_tensor_shape(self._input_name))
            if len(in_shape) == 4:
                self._input_nchw = self._host_buffers[self._input_name].reshape(in_shape)
                if self._input_nchw.dtype == np.float16:
                    self._input_f32 = np.empty(in_shape, dtype=np.float32)
                # Trust the engine's actual fixed input resolution over the
                # constructor default. Lets a smaller/faster engine (e.g. built
                # via `build.py --size 480`) be dropped in as a straight
                # "Choose engine" swap in the UI: we detect it here and rescale
                # decoded coordinates back up to ROI-pixel space below, so
                # everything downstream (FOV math, aim offsets, overlays) keeps
                # working in the capture's native ROI_SIZE coordinate system.
                engine_size = int(in_shape[3])
                if engine_size > 0:
                    self.input_size = engine_size
        except Exception:
            self._input_nchw = None
            self._input_f32 = None

        self._coord_scale = float(self.roi_size) / float(self.input_size)

        self._ctx.pop()

    def attach_thread(self) -> None:
        """Keep the CUDA context current on this thread to avoid per-frame push/pop."""
        if self._ctx is None or self._thread_attached:
            return
        self._ctx.push()
        self._thread_attached = True
        for name, device in self._device_buffers.items():
            self._context.set_tensor_address(name, int(device))

    def detach_thread(self) -> None:
        if self._ctx is None or not self._thread_attached:
            return
        self._ctx.pop()
        self._thread_attached = False

    def close(self) -> None:
        if self._ctx is None:
            return
        if not self._thread_attached:
            try:
                self._ctx.push()
                self._thread_attached = True
            except Exception:
                self._ctx = None
                return
        try:
            for device in list(self._device_buffers.values()):
                try:
                    device.free()
                except Exception:
                    pass
            self._device_buffers.clear()
            self._host_buffers.clear()
            self._context = None
            self._engine = None
        finally:
            if self._thread_attached:
                try:
                    self._ctx.pop()
                except Exception:
                    pass
                self._thread_attached = False
            # Do not detach: pycuda/TRT process-exit already tears the context down.
            self._ctx = None

    def __del__(self) -> None:
        return

    def _resolve_output(self, *candidates: str) -> Optional[str]:
        keys = list(self._host_buffers.keys())
        lowered = {k.lower(): k for k in keys}
        for candidate in candidates:
            if candidate in lowered:
                return lowered[candidate]
        return None

    def _preprocess(self, frame_rgb: np.ndarray) -> None:
        """Write NCHW RGB into the engine input host buffer (FP16 or FP32)."""
        size = self.input_size
        if frame_rgb.shape[0] != size or frame_rgb.shape[1] != size:
            frame_rgb = cv2.resize(frame_rgb, (size, size), interpolation=cv2.INTER_LINEAR)
        if frame_rgb.ndim == 3 and frame_rgb.shape[2] == 4:
            frame_rgb = frame_rgb[:, :, :3]
        src = np.ascontiguousarray(frame_rgb)
        nchw = self._input_nchw
        if nchw is not None and nchw.ndim == 4 and nchw.shape[1] == 3:
            try:
                planes = src.transpose(2, 0, 1)
                if nchw.dtype == np.float16:
                    scratch = self._input_f32
                    if scratch is None:
                        scratch = np.empty(nchw.shape, dtype=np.float32)
                        self._input_f32 = scratch
                    np.multiply(planes, np.float32(1.0 / 255.0), out=scratch[0])
                    np.copyto(nchw, scratch)
                else:
                    np.multiply(
                        planes,
                        np.array(1.0 / 255.0, dtype=nchw.dtype),
                        out=nchw[0],
                    )
                return
            except (ValueError, TypeError):
                pass
        blob = cv2.dnn.blobFromImage(
            src,
            scalefactor=1.0 / 255.0,
            size=(size, size),
            swapRB=False,
            crop=False,
        )
        host = self._host_buffers[self._input_name]
        if blob.dtype != host.dtype:
            blob = blob.astype(host.dtype, copy=False)
        np.copyto(host, np.ravel(blob))

    def _decode_e2e(self) -> list[Detection]:
        num_name = self._resolve_output("num_dets", "num", "num_detections")
        box_name = self._resolve_output("bboxes", "boxes", "det_boxes")
        score_name = self._resolve_output("scores", "score")
        label_name = self._resolve_output("labels", "classes", "class_ids")

        if not all([num_name, box_name, score_name, label_name]):
            raise RuntimeError(
                "End2End engine outputs not recognized. "
                f"Found: {list(self._host_buffers.keys())}"
            )

        num_dets = int(self._host_buffers[num_name].reshape(-1)[0])
        boxes = self._host_buffers[box_name].reshape(-1, 4)[:num_dets]
        scores = self._host_buffers[score_name].reshape(-1)[:num_dets]
        labels = self._host_buffers[label_name].reshape(-1)[:num_dets]

        scale = self._coord_scale
        detections: list[Detection] = []
        for box, score, class_id in zip(boxes, scores, labels):
            score_f = float(score)
            class_i = int(class_id)
            if score_f < self.conf_threshold:
                continue
            if self.target_class_ids and class_i not in self.target_class_ids:
                continue
            x1, y1, x2, y2 = (float(v) * scale for v in box)
            detections.append(
                Detection(
                    bbox=(x1, y1, x2, y2),
                    score=score_f,
                    class_id=class_i,
                    centroid=((x1 + x2) * 0.5, (y1 + y2) * 0.5),
                )
            )
        return detections

    def _decode_raw_yolo(self) -> list[Detection]:
        assert self._raw_output_name is not None
        output = self._host_buffers[self._raw_output_name].reshape(self._raw_output_shape)
        detections, self._xywh_normalized = _postprocess_raw_yolo(
            output,
            conf_threshold=self.conf_threshold,
            iou_threshold=self.iou_threshold,
            image_size=self.input_size,
            target_class_ids=self.target_class_ids,
            coords_are_normalized=self._xywh_normalized,
        )
        scale = self._coord_scale
        if scale != 1.0:
            detections = [
                Detection(
                    bbox=tuple(v * scale for v in d.bbox),
                    score=d.score,
                    class_id=d.class_id,
                    centroid=(d.centroid[0] * scale, d.centroid[1] * scale),
                )
                for d in detections
            ]
        return detections

    def infer(self, frame_rgb: np.ndarray) -> list[Detection]:
        cuda = self._cuda
        attached = self._thread_attached
        if not attached:
            self._ctx.push()
        try:
            self._preprocess(frame_rgb)
            host_in = self._host_buffers[self._input_name]
            cuda.memcpy_htod_async(
                self._device_buffers[self._input_name], host_in, self._stream
            )

            if not attached:
                for name, device in self._device_buffers.items():
                    self._context.set_tensor_address(name, int(device))

            self._context.execute_async_v3(self._stream.handle)

            for name, host in self._host_buffers.items():
                if name == self._input_name:
                    continue
                cuda.memcpy_dtoh_async(host, self._device_buffers[name], self._stream)
            self._stream.synchronize()

            if self._output_format == "e2e":
                return self._decode_e2e()
            return self._decode_raw_yolo()
        finally:
            if not attached:
                self._ctx.pop()


class VisionTracker:
    """
    Facade combining capture, inference, marker filtering, and offset compensation.
    """

    def __init__(
        self,
        engine_path: str,
        conf_threshold: float = 0.30,
        target_class_ids: Optional[Sequence[int]] = None,
        marker_hsv_lower: np.ndarray = DEFAULT_MARKER_HSV_LOWER,
        marker_hsv_upper: np.ndarray = DEFAULT_MARKER_HSV_UPPER,
        offset_fn: Optional[Callable[[float], Tuple[float, float]]] = None,
    ) -> None:
        self.detector = TensorRTYoloDetector(
            engine_path=engine_path,
            conf_threshold=conf_threshold,
            target_class_ids=target_class_ids,
        )
        self.marker_filter = MarkerExclusionFilter(
            hsv_lower=marker_hsv_lower,
            hsv_upper=marker_hsv_upper,
        )
        self.offset_fn = offset_fn
        self.roi_center = ROI_CENTER
        self.fov_radius_px = DEFAULT_FOV_PX
        self.class_roles = class_roles_for_engine(engine_path)
        self._locked_bbox: Optional[Tuple[float, float, float, float]] = None
        # (player box, head box, time) of the last target that had a head.
        self._head_memory: Optional[Tuple[Tuple[float, ...], Tuple[float, ...], float]] = None

    def close(self) -> None:
        self.detector.close()

    def class_role(self, class_id: int) -> str:
        roles = self.class_roles
        if roles is None:
            return ROLE_HEAD if class_id == HEAD_CLASS_ID else ROLE_PLAYER
        return roles.get(int(class_id), ROLE_IGNORE)

    def _pair_heads(
        self, valid: Sequence[Detection]
    ) -> Tuple[list[Detection], Dict[int, Detection], list[Detection]]:
        """Split detections into targets and hand every head to its player.

        Returns (candidates, head_of, loose_heads):
          candidates  players, plus heads that belong to no player
          head_of     id(player detection) -> its best head detection
          loose_heads the heads that stand alone (only the head is showing)
        A "player" box that is really just the head again (the model boxed a
        peeking head twice) is dropped in favour of the head.
        """
        players = [d for d in valid if self.class_role(d.class_id) == ROLE_PLAYER]
        heads = [d for d in valid if self.class_role(d.class_id) == ROLE_HEAD]
        if not heads:
            return players, {}, []

        head_of: Dict[int, Detection] = {}
        loose: list[Detection] = []
        duplicate: set[int] = set()
        for head in heads:
            owners = [p for p in players if head_fits_player(head.bbox, p.bbox)]
            if owners:
                owner = min(
                    owners,
                    key=lambda p: (p.bbox[2] - p.bbox[0]) * (p.bbox[3] - p.bbox[1]),
                )
                best = head_of.get(id(owner))
                if best is None or head.score > best.score:
                    head_of[id(owner)] = head
                continue
            for p in players:
                if bbox_iou(head.bbox, p.bbox) >= 0.5:
                    duplicate.add(id(p))
            loose.append(head)
        kept = [p for p in players if id(p) not in duplicate]
        return kept + loose, head_of, loose

    def _remember_head(
        self,
        player_bbox: Tuple[float, float, float, float],
        head_bbox: Optional[Tuple[float, float, float, float]],
        t: float,
    ) -> Optional[Tuple[float, float, float, float]]:
        """Keep the head across a frame where the model dropped it."""
        if head_bbox is not None:
            self._head_memory = (player_bbox, head_bbox, t)
            return head_bbox
        memory = self._head_memory
        if memory is None:
            return None
        old_player, old_head, seen = memory
        if t - seen > HEAD_HOLD_S or bbox_iou(old_player, player_bbox) < HEAD_HOLD_IOU:
            self._head_memory = None
            return None
        dx = (player_bbox[0] + player_bbox[2]) * 0.5 - (old_player[0] + old_player[2]) * 0.5
        dy = (player_bbox[1] + player_bbox[3]) * 0.5 - (old_player[1] + old_player[3]) * 0.5
        return (old_head[0] + dx, old_head[1] + dy, old_head[2] + dx, old_head[3] + dy)

    def _detection_aim_key(self, detection: Detection) -> Tuple[float, float]:
        cx, cy = self.roi_center
        px, py = detection.centroid
        return (
            bbox_crosshair_dist(detection.bbox, self.roi_center),
            math.hypot(px - cx, py - cy),
        )

    def _pick_crosshair_target(self, candidates: Sequence[Detection]) -> Detection:
        """Prefer the in-FOV player nearest the crosshair, not the highest score."""
        eligible = list(candidates)
        nearest = min(eligible, key=self._detection_aim_key)
        prev = self._locked_bbox
        if prev is not None:
            match = max(eligible, key=lambda item: bbox_iou(item.bbox, prev))
            if bbox_iou(match.bbox, prev) >= TARGET_MATCH_IOU:
                nearest_dist = bbox_crosshair_dist(nearest.bbox, self.roi_center)
                match_dist = bbox_crosshair_dist(match.bbox, self.roi_center)
                if match_dist <= nearest_dist + TARGET_SWITCH_PX:
                    nearest = match
        self._locked_bbox = nearest.bbox
        return nearest

    def process(
        self, frame_rgb: np.ndarray, t: float
    ) -> Tuple[Optional[TargetState], list[Detection]]:
        """
        Run detection + marker filter on a frame.

        Returns the in-FOV player nearest the crosshair (or None) and every
        marker-valid detection for overlay drawing.
        """
        detections = self.detector.infer(frame_rgb)
        valid = self.marker_filter.filter_detections(frame_rgb, detections)
        # Classes that can never be a target (own character, weapons, smoke).
        # A class list chosen by hand is taken as given.
        if not self.detector.target_class_ids:
            valid = [d for d in valid if self.class_role(d.class_id) != ROLE_IGNORE]
        return self.select_target(valid, t)

    def select_target(
        self, valid: list[Detection], t: float
    ) -> Tuple[Optional[TargetState], list[Detection]]:
        """Pick the target from marker-valid detections (no GPU involved)."""
        if not valid:
            self._locked_bbox = None
            return None, []

        fov = max(0.0, float(self.fov_radius_px))
        candidates, head_of, _loose = self._pair_heads(valid)
        in_fov = [item for item in candidates if detection_in_fov(item, self.roi_center, fov)]
        if not in_fov:
            self._locked_bbox = None
            return None, valid

        best = self._pick_crosshair_target(in_fov)
        ox, oy = (0.0, 0.0) if self.offset_fn is None else self.offset_fn(t)

        cx, cy = best.centroid
        compensated_cx = cx - ox
        compensated_cy = cy - oy
        dx = compensated_cx - self.roi_center[0]
        dy = compensated_cy - self.roi_center[1]
        head_bbox: Optional[Tuple[float, float, float, float]] = None
        if self.class_role(best.class_id) == ROLE_HEAD:
            # Only the head is showing: stand a body up behind it.
            target_bbox = player_bbox_from_head(best.bbox)
            head_bbox = best.bbox
            self._head_memory = None
        else:
            target_bbox = best.bbox
            head = head_of.get(id(best))
            head_bbox = self._remember_head(
                best.bbox, head.bbox if head is not None else None, t
            )

        return (
            TargetState(
                dx=dx,
                dy=dy,
                confidence=best.score,
                bbox=target_bbox,
                raw_centroid=best.centroid,
                compensated_centroid=(compensated_cx, compensated_cy),
                class_id=best.class_id,
                head_bbox=head_bbox,
            ),
            valid,
        )
