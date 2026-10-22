"""Target prediction: where a moving player will be when the shot lands.

The hard part is not the leading, it is telling a moving *target* apart from a
moving *camera*. What the detector reports is the target's position on screen,
and that position moves for two completely different reasons:

    apparent motion  =  the player actually moving  +  you turning your view

Turning your view moves every pixel on screen, so a standing still player looks
like it is sprinting sideways the moment you flick. Leading on that number would
aim into empty space.

Three independent ways to measure the camera term are used here, cheapest first:

1. Mouse counts. Argus already reads the physical mouse through Raw Input and
   knows its own injected movement, and `_mouse_output_scale` already converts
   counts to vision pixels. Free and exact while the game maps counts to a fixed
   number of pixels.

2. The other players on screen. Turning your view shifts every player by the
   same amount, so the *median* shift across several players is the camera term,
   measured in pixels with no calibration at all. Only works with three or more
   players on screen, and it is what keeps this honest when (1) is wrong.

3. Online scale calibration. Whenever (2) is available it is compared against
   (1) over a sliding window, and the counts-to-pixels scale is nudged toward
   whatever the screen actually did. That is what absorbs in-game mouse
   acceleration, a scoped-in sensitivity change, or simply the wrong DPI typed
   into Settings, and it keeps working with one player on screen once learned.

Anything left over after the camera term is removed is the player's own motion.
Velocity comes from a least-squares fit over a short track rather than from
differencing two frames, because a detection box jitters by a pixel or two every
frame and differencing turns that into hundreds of px/s of noise. Confidence
falls when the estimate cannot be trusted (a hard flick, a target that was just
swapped, a poor fit), and the lead shrinks with it rather than guessing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence, Tuple

Box = Tuple[float, float, float, float]

# A target has to overlap its previous box by this much to count as the same
# player. Below it the box is treated as a different player and the track
# restarts rather than reporting the jump between two players as movement.
MATCH_IOU = 0.25

# Frames kept for the velocity fit. At ~140 fps eight frames is about 57 ms:
# long enough that box jitter averages out, short enough to still catch a
# counter-strafe before the lead points somewhere silly.
TRACK_FRAMES = 8
TRACK_MIN = 4

# A frame pair further apart than this is treated as a break in tracking (the
# capture stalled, or the game was alt-tabbed) instead of a huge velocity.
MAX_FRAME_GAP_S = 0.12

# Below this the target is standing still as far as leading is concerned; it is
# roughly the noise floor of a box that is jittering in place.
STILL_PX_S = 45.0

# Camera motion this large between two vision frames means a hard flick. The
# camera term dominates the arithmetic there, so any residual is mostly error.
FLICK_PX = 55.0

# How far the fit is allowed to miss the track before the estimate is distrusted.
FIT_TOLERANCE_PX = 5.0

# Online scale calibration. Per-frame mouse travel at 140 fps is only a pixel or
# two, so samples are accumulated over a window before a ratio is taken; a single
# frame's ratio would be noise divided by noise.
SCALE_LEARN_RATE = 0.25
SCALE_MIN = 0.35
SCALE_MAX = 2.8
SCALE_WINDOW_PX = 40.0     # accumulated predicted travel before learning
SCALE_WINDOW_FRAMES = 150  # give up and reset if that never arrives


def _center(box: Box) -> Tuple[float, float]:
    return (box[0] + box[2]) * 0.5, (box[1] + box[3]) * 0.5


def _iou(a: Box, b: Box) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = ix2 - ix1, iy2 - iy1
    if iw <= 0.0 or ih <= 0.0:
        return 0.0
    inter = iw * ih
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0.0 else 0.0


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    if not n:
        return 0.0
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) * 0.5


def _detection_boxes(detections: Sequence[Any]) -> list[Box]:
    boxes: list[Box] = []
    for item in detections or ():
        box = getattr(item, "bbox", None)
        if box is None and isinstance(item, (tuple, list)) and len(item) == 4:
            box = tuple(item)
        if box is None:
            continue
        try:
            x1, y1, x2, y2 = (float(v) for v in box)
        except (TypeError, ValueError):
            continue
        if x2 > x1 and y2 > y1:
            boxes.append((x1, y1, x2, y2))
    return boxes


def _fit_velocity(track: Sequence[Tuple[float, float, float]]) -> Tuple[float, float, float]:
    """Least-squares velocity through (t, x, y), plus the RMS miss in pixels.

    Fitting a line through several positions rejects per-frame box jitter far
    better than subtracting the last two positions, which multiplies that jitter
    by the frame rate.
    """
    n = len(track)
    if n < 2:
        return 0.0, 0.0, 0.0
    mean_t = sum(p[0] for p in track) / n
    mean_x = sum(p[1] for p in track) / n
    mean_y = sum(p[2] for p in track) / n
    var_t = sum((p[0] - mean_t) ** 2 for p in track)
    if var_t <= 1e-12:
        return 0.0, 0.0, 0.0
    vx = sum((p[0] - mean_t) * (p[1] - mean_x) for p in track) / var_t
    vy = sum((p[0] - mean_t) * (p[2] - mean_y) for p in track) / var_t
    resid = 0.0
    for t, x, y in track:
        ex = (mean_x + vx * (t - mean_t)) - x
        ey = (mean_y + vy * (t - mean_t)) - y
        resid += ex * ex + ey * ey
    return vx, vy, math.sqrt(resid / n)


@dataclass
class PredictorState:
    """Everything carried between vision frames. One per capture session."""

    pending_dx: float = 0.0          # mouse counts piling up since the last frame
    pending_dy: float = 0.0
    last_generation: int = -1
    last_time: float = 0.0
    last_boxes: list[Box] = field(default_factory=list)
    last_target: Optional[Box] = None

    # Camera-compensated cumulative position, and the fit over it.
    world_x: float = 0.0
    world_y: float = 0.0
    track: list[Tuple[float, float, float]] = field(default_factory=list)
    vx: float = 0.0                  # target velocity, ROI px/s
    vy: float = 0.0
    speed: float = 0.0
    fit_error: float = 0.0
    confidence: float = 0.0

    # Camera estimate for the frame just processed.
    camera_px: Tuple[float, float] = (0.0, 0.0)
    camera_source: str = "mouse"     # "vision" when measured off other players

    # Online counts-to-pixels calibration.
    scale_trim: float = 1.0
    scale_samples: int = 0
    learn_pred: Tuple[float, float] = (0.0, 0.0)
    learn_meas: Tuple[float, float] = (0.0, 0.0)
    learn_frames: int = 0

    def reset_track(self) -> None:
        """Forget the current player but keep what was learned about the mouse."""
        self.track.clear()
        self.world_x = 0.0
        self.world_y = 0.0
        self.vx = 0.0
        self.vy = 0.0
        self.speed = 0.0
        self.fit_error = 0.0
        self.confidence = 0.0
        self.last_target = None


class TargetPredictor:
    """Turns detections plus mouse counts into a lead offset, in ROI pixels."""

    def __init__(self) -> None:
        self.state = PredictorState()

    # -- inputs ------------------------------------------------------------

    def add_mouse_counts(self, dx: float, dy: float) -> None:
        """Feed every mouse delta the aim loop sees, physical and injected.

        The aim loop drains Raw Input each tick, so the counts are accumulated
        here and spent when the next vision frame lands. That pairs the camera
        motion with exactly the interval between the two frames being compared.
        """
        if dx:
            self.state.pending_dx += float(dx)
        if dy:
            self.state.pending_dy += float(dy)

    def reset(self) -> None:
        self.state = PredictorState()

    # -- per vision frame --------------------------------------------------

    def update(
        self,
        generation: int,
        now: float,
        target_box: Optional[Box],
        detections: Sequence[Any],
        px_per_count: float,
        track_frames: int = TRACK_FRAMES,
    ) -> None:
        """Advance the estimate. Call once per new vision generation."""
        state = self.state
        if generation == state.last_generation:
            return

        counts = (state.pending_dx, state.pending_dy)
        state.pending_dx = 0.0
        state.pending_dy = 0.0

        boxes = _detection_boxes(detections)
        dt = now - state.last_time if state.last_time > 0.0 else 0.0
        broke = state.last_generation < 0 or dt <= 0.0 or dt > MAX_FRAME_GAP_S

        # Moving the mouse right turns the view right, which slides the world
        # left across the screen, so a static target's apparent shift is the
        # negative of the mouse travel.
        base = max(0.0, float(px_per_count))
        scale = base * state.scale_trim
        camera = (-counts[0] * scale, -counts[1] * scale)
        source = "mouse"
        if not broke:
            measured = self._camera_from_detections(state.last_boxes, boxes)
            if measured is not None:
                camera = measured
                source = "vision"
                self._learn_scale(counts, measured, base)

        state.camera_px = camera
        state.camera_source = source
        state.last_generation = generation
        state.last_time = now
        state.last_boxes = boxes

        if target_box is None:
            state.reset_track()
            return

        previous = state.last_target
        state.last_target = target_box
        if broke or previous is None or _iou(previous, target_box) < MATCH_IOU:
            # New or different player: restart the track rather than reading the
            # gap between two players as movement.
            state.track.clear()
            state.world_x = 0.0
            state.world_y = 0.0
            state.vx = state.vy = state.speed = 0.0
            state.confidence = 0.0
            return

        px, py = _center(previous)
        cx, cy = _center(target_box)
        # What is left after the camera term is the player's own motion.
        state.world_x += (cx - px) - camera[0]
        state.world_y += (cy - py) - camera[1]
        state.track.append((now, state.world_x, state.world_y))

        keep = max(TRACK_MIN, int(track_frames))
        if len(state.track) > keep:
            del state.track[: len(state.track) - keep]

        state.vx, state.vy, state.fit_error = _fit_velocity(state.track)
        state.speed = math.hypot(state.vx, state.vy)
        state.confidence = self._confidence(camera, source)

    def _camera_from_detections(
        self, previous: Sequence[Box], current: Sequence[Box]
    ) -> Optional[Tuple[float, float]]:
        """Median shift over players matched between two frames.

        Turning the view moves them all together, so the median is the camera
        term. Two matches cannot outvote a moving player, so three are required
        before this overrides the mouse-count estimate.
        """
        if len(previous) < 3 or len(current) < 3:
            return None
        shifts: list[Tuple[float, float]] = []
        for old in previous:
            best: Optional[Box] = None
            best_iou = MATCH_IOU
            for new in current:
                score = _iou(old, new)
                if score > best_iou:
                    best_iou = score
                    best = new
            if best is not None:
                ox, oy = _center(old)
                nx, ny = _center(best)
                shifts.append((nx - ox, ny - oy))
        if len(shifts) < 3:
            return None
        return _median([s[0] for s in shifts]), _median([s[1] for s in shifts])

    def _learn_scale(
        self,
        counts: Tuple[float, float],
        measured: Tuple[float, float],
        base: float,
    ) -> None:
        """Nudge counts-to-pixels toward what the screen actually did.

        Travel is accumulated over a window because a single frame at 140 fps
        moves the view a pixel or two, and a ratio of two small noisy numbers is
        worthless. Once enough travel has built up the ratio is meaningful.
        """
        state = self.state
        if base <= 0.0:
            return
        state.learn_pred = (
            state.learn_pred[0] + counts[0] * base,
            state.learn_pred[1] + counts[1] * base,
        )
        state.learn_meas = (
            state.learn_meas[0] + measured[0],
            state.learn_meas[1] + measured[1],
        )
        state.learn_frames += 1

        for axis in (0, 1):
            predicted = state.learn_pred[axis]
            shift = state.learn_meas[axis]
            if abs(predicted) < SCALE_WINDOW_PX:
                continue
            # The screen moves opposite to the mouse; same sign means the match
            # was bad, so drop the window instead of learning from it.
            if abs(shift) >= 1.0 and (shift > 0.0) != (predicted > 0.0):
                ratio = abs(shift) / abs(predicted)
                if SCALE_MIN <= ratio <= SCALE_MAX:
                    state.scale_trim += (ratio - state.scale_trim) * SCALE_LEARN_RATE
                    state.scale_trim = max(SCALE_MIN, min(SCALE_MAX, state.scale_trim))
                    state.scale_samples += 1
            self._reset_learn()
            return

        if state.learn_frames > SCALE_WINDOW_FRAMES:
            self._reset_learn()

    def _reset_learn(self) -> None:
        self.state.learn_pred = (0.0, 0.0)
        self.state.learn_meas = (0.0, 0.0)
        self.state.learn_frames = 0

    def _confidence(self, camera: Tuple[float, float], source: str) -> float:
        """How much of the lead to actually apply, 0..1."""
        state = self.state
        # Needs a few frames before the fit means anything.
        ready = min(1.0, max(0.0, (len(state.track) - TRACK_MIN + 1) / float(TRACK_MIN)))

        # A hard flick swamps the residual with camera error. Measuring the
        # camera off the other players stays trustworthy even then, so only the
        # mouse-count estimate is penalised.
        if source == "vision":
            motion = 1.0
        else:
            travel = math.hypot(camera[0], camera[1])
            motion = max(0.0, 1.0 - max(0.0, travel - FLICK_PX * 0.4) / FLICK_PX)

        # Standing still is not worth leading and the direction is just noise.
        moving = 0.0 if state.speed < STILL_PX_S else min(
            1.0, (state.speed - STILL_PX_S) / STILL_PX_S
        )

        # A track that does not sit on a straight line is either a player
        # changing direction or a detector that is not settled. Either way the
        # lead should back off.
        fit = max(0.0, 1.0 - state.fit_error / FIT_TOLERANCE_PX)

        return max(0.0, min(1.0, ready * motion * moving * fit))

    # -- output ------------------------------------------------------------

    def lead(self, lead_s: float, strength: float = 1.0, max_px: float = 160.0) -> Tuple[float, float]:
        """Offset to add to the aim point, in ROI pixels."""
        state = self.state
        if state.confidence <= 0.0 or lead_s <= 0.0:
            return 0.0, 0.0
        gain = max(0.0, float(strength)) * state.confidence
        dx = state.vx * lead_s * gain
        dy = state.vy * lead_s * gain
        mag = math.hypot(dx, dy)
        limit = max(0.0, float(max_px))
        if mag > limit and mag > 0.0:
            dx *= limit / mag
            dy *= limit / mag
        return dx, dy

    def shift_box(
        self, box: Box, lead_s: float, strength: float = 1.0, max_px: float = 160.0
    ) -> Box:
        """The target box moved to where it is predicted to be."""
        dx, dy = self.lead(lead_s, strength, max_px)
        if dx == 0.0 and dy == 0.0:
            return box
        return (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)

    # -- reporting ---------------------------------------------------------

    @property
    def velocity(self) -> Tuple[float, float]:
        return self.state.vx, self.state.vy

    @property
    def speed(self) -> float:
        return self.state.speed

    @property
    def confidence(self) -> float:
        return self.state.confidence
