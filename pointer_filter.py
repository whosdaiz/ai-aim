"""
Dynamic vector alignment and gain module: tremor reduction, dot-product intent
gating, and aligned speed scaling for adaptive pointer assistance research.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Tuple

# --- Research tuning constants ------------------------------------------------

COS_ALIGN_THRESHOLD = 0.707  # cos(45°): movement must align within 45° of target
GAIN_COEFF = 0.15
LPF_CUTOFF_HZ = 12.0
TARGET_EPSILON = 1e-6
TARGET_DEADZONE_PX = 25.0
TARGET_FADE_RADIUS_PX = 90.0
ALIGN_HYSTERESIS = 0.05
TARGET_SMOOTH_HZ = 24.0
ASSIST_SMOOTH_HZ = 36.0
VELOCITY_REF_PX = 35.0
OVERSHOOT_LIMIT = 0.22
TRACK_COEFF = 0.09
TRACK_RADIUS_PX = 120.0
APPROACH_FADE_START_PX = 18.0
APPROACH_FADE_END_PX = 90.0
CLOSE_LIMIT = 0.32
STICKINESS = 1.0
STICKY_RADIUS_PX = 56.0
STICKY_BREAKAWAY_PX = 16.0
SIT_PX = 2.5
ALIGN_EPS_PX = 0.12
ACQUIRE_RAMP_MS = 45.0
TRACK_ROT = 2.4
STICKY_ROT = 0.34
STICKY_SPEED = 0.20
MAX_ROT = 0.95


@dataclass(frozen=True)
class FilterResult:
    """Output of one pointer-filter iteration."""

    out_dx: float
    out_dy: float
    assist_dx: float
    assist_dy: float
    cos_theta: Optional[float]
    gain: float
    filtered_user_dx: float
    filtered_user_dy: float
    speed_gain: float = 0.0
    aim_dx: float = 0.0
    aim_dy: float = 0.0


class LowPassFilter2D:
    """
    First-order IIR low-pass filter applied independently to x and y deltas.

    Cutoff frequency defaults to 12 Hz to dampen high-frequency input jitter.
    """

    def __init__(self, sample_rate_hz: float = 240.0, cutoff_hz: float = LPF_CUTOFF_HZ) -> None:
        if sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        if cutoff_hz <= 0:
            raise ValueError("cutoff_hz must be positive")

        self.sample_rate_hz = sample_rate_hz
        self.cutoff_hz = cutoff_hz
        dt = 1.0 / sample_rate_hz
        tau = 1.0 / (2.0 * math.pi * cutoff_hz)
        self.alpha = dt / (tau + dt)
        self._fx = 0.0
        self._fy = 0.0
        self._initialized = False

    def reset(self) -> None:
        self._fx = 0.0
        self._fy = 0.0
        self._initialized = False

    def reset_from_zero(self) -> None:
        """Start filtering from (0, 0) so values ease in instead of jumping."""
        self._fx = 0.0
        self._fy = 0.0
        self._initialized = True

    def reset_to(self, dx: float, dy: float) -> None:
        """Start filtering from a known sample so the aim point does not lag in from zero."""
        self._fx = dx
        self._fy = dy
        self._initialized = True

    def _alpha_for_dt(self, dt_seconds: Optional[float]) -> float:
        if dt_seconds is None:
            return self.alpha
        dt = max(1e-4, min(dt_seconds, 0.05))
        tau = 1.0 / (2.0 * math.pi * self.cutoff_hz)
        return dt / (tau + dt)

    def decay_to_zero(self, dt_seconds: Optional[float] = None) -> None:
        """Ease internal state to zero without a hard reset pop."""
        if not self._initialized:
            return
        alpha = self._alpha_for_dt(dt_seconds)
        self._fx += alpha * (0.0 - self._fx)
        self._fy += alpha * (0.0 - self._fy)

    def filter(
        self, dx: float, dy: float, dt_seconds: Optional[float] = None
    ) -> Tuple[float, float]:
        if not self._initialized:
            self._fx = dx
            self._fy = dy
            self._initialized = True
            return dx, dy

        alpha = self._alpha_for_dt(dt_seconds)
        self._fx += alpha * (dx - self._fx)
        self._fy += alpha * (dy - self._fy)
        return self._fx, self._fy


class CriticallyDamped2D:
    """
    Second-order critically damped follow filter.

    Smoother than a 1-pole lowpass: less stepping, less overshoot on direction changes.
    """

    def __init__(self, sample_rate_hz: float = 240.0, cutoff_hz: float = ASSIST_SMOOTH_HZ) -> None:
        if sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be positive")
        if cutoff_hz <= 0:
            raise ValueError("cutoff_hz must be positive")
        self.cutoff_hz = cutoff_hz
        self._dt_default = 1.0 / sample_rate_hz
        self._x = 0.0
        self._y = 0.0
        self._vx = 0.0
        self._vy = 0.0
        self._initialized = False

    def reset(self) -> None:
        self._x = 0.0
        self._y = 0.0
        self._vx = 0.0
        self._vy = 0.0
        self._initialized = False

    def reset_from_zero(self) -> None:
        self.reset()
        self._initialized = True

    def decay_to_zero(self, dt_seconds: Optional[float] = None) -> None:
        self.filter(0.0, 0.0, dt_seconds)

    def filter(
        self, dx: float, dy: float, dt_seconds: Optional[float] = None
    ) -> Tuple[float, float]:
        dt = self._dt_default if dt_seconds is None else max(1e-4, min(dt_seconds, 0.05))
        if not self._initialized:
            self._x = dx
            self._y = dy
            self._vx = 0.0
            self._vy = 0.0
            self._initialized = True
            return dx, dy

        omega = 2.0 * math.pi * self.cutoff_hz
        ax = omega * omega * (dx - self._x) - 2.0 * omega * self._vx
        ay = omega * omega * (dy - self._y) - 2.0 * omega * self._vy
        self._vx += ax * dt
        self._vy += ay * dt
        self._x += self._vx * dt
        self._y += self._vy * dt
        return self._x, self._y


def _smoothstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def _proximity_scale(
    target_mag: float,
    deadzone_px: float,
    fade_radius_px: float,
) -> float:
    """
    Legacy fade helper; kept for compatibility with outer-range tuning.
    """
    if target_mag <= deadzone_px:
        return 0.0
    if fade_radius_px <= deadzone_px:
        return 1.0
    if target_mag >= fade_radius_px:
        return 1.0
    linear = (target_mag - deadzone_px) / (fade_radius_px - deadzone_px)
    return _smoothstep(linear)


def _approach_scale(
    target_mag: float,
    fade_start_px: float,
    fade_end_px: float,
) -> float:
    """Extra speed only while close. Far away stays at the user's throw speed."""
    if target_mag <= SIT_PX:
        return 0.0
    if fade_end_px <= fade_start_px:
        return 1.0 if target_mag > SIT_PX else 0.0
    if target_mag >= fade_end_px:
        return 0.0
    if target_mag <= fade_start_px:
        return 1.0
    t = (fade_end_px - target_mag) / max(fade_end_px - fade_start_px, TARGET_EPSILON)
    return _smoothstep(t)


def _tracking_scale(
    target_mag: float,
    deadzone_px: float,
    track_radius_px: float,
) -> float:
    """Steer the throw toward the bone. Zero outside steer range so a sweep stays yours."""
    del deadzone_px
    if target_mag <= SIT_PX:
        return 0.0
    if track_radius_px <= SIT_PX:
        return 1.0
    if target_mag >= track_radius_px:
        return 0.0
    t = (target_mag - SIT_PX) / max(track_radius_px - SIT_PX, TARGET_EPSILON)
    return 1.0 - _smoothstep(t)


def _sticky_scale(
    target_mag: float,
    deadzone_px: float,
    sticky_radius_px: float,
) -> float:
    """Extra on-target steer, strongest nearest the bone."""
    if sticky_radius_px <= SIT_PX:
        return 0.0
    if target_mag <= SIT_PX:
        return 0.0
    if target_mag >= sticky_radius_px:
        return 0.0
    t = (target_mag - SIT_PX) / max(sticky_radius_px - SIT_PX, TARGET_EPSILON)
    return 1.0 - _smoothstep(t)


def _sticky_engagement(
    user_dx: float,
    user_dy: float,
    target_dx: float,
    target_dy: float,
    sticky_radius_px: float,
    breakaway_px: float,
) -> float:
    """Hold while on the player; yield only on a real flick away."""
    target_mag = math.hypot(target_dx, target_dy)
    if target_mag > sticky_radius_px * 1.15:
        return 0.0
    if target_mag <= TARGET_EPSILON or breakaway_px <= 0.0:
        return 1.0
    unit_x = target_dx / target_mag
    unit_y = target_dy / target_mag
    user_away = max(0.0, -(user_dx * unit_x + user_dy * unit_y))
    if user_away >= breakaway_px:
        return 0.0
    return 1.0 - _smoothstep(user_away / breakaway_px)


def _steer_move(
    user_dx: float,
    user_dy: float,
    target_dx: float,
    target_dy: float,
    blend: float,
) -> Tuple[float, float]:
    """Same speed as the user, rotated toward the bone. blend=0 keeps the throw."""
    user_mag = math.hypot(user_dx, user_dy)
    target_mag = math.hypot(target_dx, target_dy)
    if user_mag <= TARGET_EPSILON or target_mag <= TARGET_EPSILON or blend <= 0.0:
        return user_dx, user_dy
    blend = max(0.0, min(1.0, blend))
    udx = user_dx / user_mag
    udy = user_dy / user_mag
    tdx = target_dx / target_mag
    tdy = target_dy / target_mag
    dx = udx + (tdx - udx) * blend
    dy = udy + (tdy - udy) * blend
    mag = math.hypot(dx, dy)
    if mag <= TARGET_EPSILON:
        return user_dx, user_dy
    scale = user_mag / mag
    return dx * scale, dy * scale


def _cooperative_assist(
    user_dx: float,
    user_dy: float,
    target_dx: float,
    target_dy: float,
    speed_gain: float,
    rot: float,
) -> Tuple[float, float]:
    """
    Extra movement that only amplifies and steers the user's throw.

    Never slows the original move, never injects a magnet, never reverses the stick.
    """
    user_mag = math.hypot(user_dx, user_dy)
    if user_mag <= TARGET_EPSILON:
        return 0.0, 0.0
    steered_x, steered_y = _steer_move(user_dx, user_dy, target_dx, target_dy, rot)
    gain = max(0.0, speed_gain)
    out_x = steered_x * (1.0 + gain)
    out_y = steered_y * (1.0 + gain)
    uhx = user_dx / user_mag
    uhy = user_dy / user_mag
    along = out_x * uhx + out_y * uhy
    if along < user_mag:
        out_x += uhx * (user_mag - along)
        out_y += uhy * (user_mag - along)
    return out_x - user_dx, out_y - user_dy


def _sanitize_assist(
    assist_dx: float,
    assist_dy: float,
    user_dx: float,
    user_dy: float,
    target_dx: float,
    target_dy: float,
) -> Tuple[float, float]:
    """Drop push-away and any leftover that would reverse the throw."""
    user_mag = math.hypot(user_dx, user_dy)
    if user_mag <= TARGET_EPSILON:
        return 0.0, 0.0
    target_mag = math.hypot(target_dx, target_dy)
    if target_mag > SIT_PX:
        thx = target_dx / target_mag
        thy = target_dy / target_mag
        toward = assist_dx * thx + assist_dy * thy
        if toward < 0.0:
            assist_dx -= thx * toward
            assist_dy -= thy * toward
    out_x = user_dx + assist_dx
    out_y = user_dy + assist_dy
    if out_x * user_dx + out_y * user_dy < 0.0:
        return 0.0, 0.0
    uhx = user_dx / user_mag
    uhy = user_dy / user_mag
    along = out_x * uhx + out_y * uhy
    if along < user_mag:
        out_x += uhx * (user_mag - along)
        out_y += uhy * (user_mag - along)
    return out_x - user_dx, out_y - user_dy


def _cap_overshoot(
    assist_dx: float,
    assist_dy: float,
    user_dx: float,
    user_dy: float,
    target_dx: float,
    target_dy: float,
    close_limit: float,
) -> Tuple[float, float]:
    """Scale assist down if user+assist would pass the bone. Never pull back."""
    target_mag = math.hypot(target_dx, target_dy)
    if target_mag <= SIT_PX:
        return 0.0, 0.0
    thx = target_dx / target_mag
    thy = target_dy / target_mag
    remaining = target_mag - SIT_PX
    user_toward = max(0.0, user_dx * thx + user_dy * thy)
    budget = max(0.0, remaining - user_toward)
    if close_limit > 0.0:
        budget = min(budget, max(remaining * close_limit, remaining * 0.5))
    assist_toward = assist_dx * thx + assist_dy * thy
    if assist_toward <= budget or assist_toward <= TARGET_EPSILON:
        return assist_dx, assist_dy
    scale = budget / assist_toward
    return assist_dx * scale, assist_dy * scale


def _acquire_scale(ramp: float) -> float:
    """Ease assist in on new target acquisition."""
    return _smoothstep(ramp)


def _velocity_scale(user_mag: float, ref_px: float) -> float:
    """Reduce assist on fast flicks to limit overshoot."""
    if ref_px <= 0.0 or user_mag <= ref_px:
        return 1.0
    return ref_px / user_mag


def _clamp_vector_magnitude(dx: float, dy: float, max_mag: float) -> Tuple[float, float]:
    if max_mag <= 0.0:
        return 0.0, 0.0
    mag = math.hypot(dx, dy)
    if mag <= max_mag or mag == 0.0:
        return dx, dy
    scale = max_mag / mag
    return dx * scale, dy * scale


def _limit_extra_speed(
    assist_dx: float,
    assist_dy: float,
    user_dx: float,
    user_dy: float,
    extra_gain: float,
) -> Tuple[float, float]:
    """Keep |user+assist| from exceeding the throw speed plus allowed extra gain."""
    user_mag = math.hypot(user_dx, user_dy)
    if user_mag <= TARGET_EPSILON:
        return 0.0, 0.0
    out_x = user_dx + assist_dx
    out_y = user_dy + assist_dy
    out_mag = math.hypot(out_x, out_y)
    cap = user_mag * (1.0 + max(0.0, extra_gain))
    if out_mag <= cap or out_mag <= TARGET_EPSILON:
        return assist_dx, assist_dy
    scale = cap / out_mag
    return out_x * scale - user_dx, out_y * scale - user_dy


def finalize_assist(
    assist_dx: float,
    assist_dy: float,
    user_dx: float,
    user_dy: float,
    aim_dx: float,
    aim_dy: float,
    speed_gain: float,
    assist_scale: float,
    close_limit: float,
) -> Tuple[float, float]:
    """Apply the UI multiplier without letting scale turn a sweep into a speed-up."""
    ax = assist_dx * max(0.0, assist_scale)
    ay = assist_dy * max(0.0, assist_scale)
    ax, ay = _limit_extra_speed(ax, ay, user_dx, user_dy, speed_gain)
    ax, ay = _sanitize_assist(ax, ay, user_dx, user_dy, aim_dx, aim_dy)
    return _cap_overshoot(ax, ay, user_dx, user_dy, aim_dx, aim_dy, close_limit)


class PointerFilter:
    """
    Tremor filter + intent gating + cooperative assist.

    Assist only amplifies and steers the user's own move toward the target.
    Zero user input → zero assist. Assist never reverses or slows the throw.
    """

    def __init__(
        self,
        sample_rate_hz: float = 240.0,
        cutoff_hz: float = LPF_CUTOFF_HZ,
        cos_align_threshold: float = COS_ALIGN_THRESHOLD,
        gain_coeff: float = GAIN_COEFF,
        target_deadzone_px: float = TARGET_DEADZONE_PX,
        target_fade_radius_px: float = TARGET_FADE_RADIUS_PX,
        align_hysteresis: float = ALIGN_HYSTERESIS,
        target_smooth_hz: float = TARGET_SMOOTH_HZ,
        assist_smooth_hz: float = ASSIST_SMOOTH_HZ,
        velocity_ref_px: float = VELOCITY_REF_PX,
        overshoot_limit: float = OVERSHOOT_LIMIT,
        track_coeff: float = TRACK_COEFF,
        track_radius_px: float = TRACK_RADIUS_PX,
        approach_fade_start_px: float = APPROACH_FADE_START_PX,
        approach_fade_end_px: float = APPROACH_FADE_END_PX,
        close_limit: float = CLOSE_LIMIT,
        stickiness: float = STICKINESS,
        sticky_radius_px: float = STICKY_RADIUS_PX,
        sticky_breakaway_px: float = STICKY_BREAKAWAY_PX,
        acquire_ramp_ms: float = ACQUIRE_RAMP_MS,
    ) -> None:
        self.tremor_filter = LowPassFilter2D(
            sample_rate_hz=sample_rate_hz,
            cutoff_hz=cutoff_hz,
        )
        self.target_smoother = LowPassFilter2D(
            sample_rate_hz=sample_rate_hz,
            cutoff_hz=target_smooth_hz,
        )
        self.assist_smoother = LowPassFilter2D(
            sample_rate_hz=sample_rate_hz,
            cutoff_hz=assist_smooth_hz,
        )
        self.cos_align_threshold = cos_align_threshold
        self.gain_coeff = gain_coeff
        self.target_deadzone_px = target_deadzone_px
        self.target_fade_radius_px = target_fade_radius_px
        self.align_hysteresis = align_hysteresis
        self.velocity_ref_px = velocity_ref_px
        self.overshoot_limit = max(0.0, min(overshoot_limit, 1.0))
        self.track_coeff = max(0.0, track_coeff)
        self.track_radius_px = track_radius_px
        self.approach_fade_start_px = approach_fade_start_px
        self.approach_fade_end_px = approach_fade_end_px
        self.close_limit = max(0.0, min(close_limit, 1.0))
        self.stickiness = max(0.0, stickiness)
        self.sticky_radius_px = sticky_radius_px
        self.sticky_breakaway_px = max(0.0, sticky_breakaway_px)
        self.acquire_ramp_ms = max(0.0, acquire_ramp_ms)
        self._sample_dt = 1.0 / sample_rate_hz if sample_rate_hz > 0.0 else 1.0 / 240.0
        self._had_target = False
        self._acquire_ramp = 1.0
        self._alignment_gate_open = False

    def apply_settings(
        self,
        *,
        gain_coeff: Optional[float] = None,
        target_deadzone_px: Optional[float] = None,
        target_fade_radius_px: Optional[float] = None,
        velocity_ref_px: Optional[float] = None,
        overshoot_limit: Optional[float] = None,
        track_coeff: Optional[float] = None,
        track_radius_px: Optional[float] = None,
        approach_fade_start_px: Optional[float] = None,
        approach_fade_end_px: Optional[float] = None,
        close_limit: Optional[float] = None,
        stickiness: Optional[float] = None,
        sticky_radius_px: Optional[float] = None,
        acquire_ramp_ms: Optional[float] = None,
        cos_align_threshold: Optional[float] = None,
    ) -> None:
        """Update live-tunable fields without resetting filter state."""
        if gain_coeff is not None:
            self.gain_coeff = gain_coeff
        if target_deadzone_px is not None:
            self.target_deadzone_px = target_deadzone_px
        if target_fade_radius_px is not None:
            self.target_fade_radius_px = target_fade_radius_px
        if velocity_ref_px is not None:
            self.velocity_ref_px = velocity_ref_px
        if overshoot_limit is not None:
            self.overshoot_limit = max(0.0, min(overshoot_limit, 1.0))
        if track_coeff is not None:
            self.track_coeff = max(0.0, track_coeff)
        if track_radius_px is not None:
            self.track_radius_px = track_radius_px
        if approach_fade_start_px is not None:
            self.approach_fade_start_px = approach_fade_start_px
        if approach_fade_end_px is not None:
            self.approach_fade_end_px = approach_fade_end_px
        if close_limit is not None:
            self.close_limit = max(0.0, min(close_limit, 1.0))
        if stickiness is not None:
            self.stickiness = max(0.0, stickiness)
        if sticky_radius_px is not None:
            self.sticky_radius_px = sticky_radius_px
        if acquire_ramp_ms is not None:
            self.acquire_ramp_ms = max(0.0, acquire_ramp_ms)
        if cos_align_threshold is not None:
            self.cos_align_threshold = cos_align_threshold

    def reset(self) -> None:
        self.tremor_filter.reset()
        self.target_smoother.reset()
        self.assist_smoother.reset()
        self._alignment_gate_open = False
        self._had_target = False
        self._acquire_ramp = 1.0

    def _begin_target_acquisition(self, target_dx: float, target_dy: float) -> None:
        self.target_smoother.reset_to(target_dx, target_dy)
        self.assist_smoother.reset_from_zero()
        self._alignment_gate_open = False
        self._acquire_ramp = 0.0

    @staticmethod
    def _magnitude(x: float, y: float) -> float:
        return math.hypot(x, y)

    @staticmethod
    def _cosine_similarity(
        ux: float, uy: float, tx: float, ty: float, eps: float = TARGET_EPSILON
    ) -> Optional[float]:
        user_mag = math.hypot(ux, uy)
        target_mag = math.hypot(tx, ty)
        if user_mag < eps or target_mag < eps:
            return None
        cos_theta = (ux * tx + uy * ty) / (user_mag * target_mag)
        return max(-1.0, min(1.0, cos_theta))

    def apply(
        self,
        user_dx: float,
        user_dy: float,
        target_dx: Optional[float] = None,
        target_dy: Optional[float] = None,
        dt_seconds: Optional[float] = None,
    ) -> FilterResult:
        """
        Apply tremor filtering, intent gating, and gain scaling.

        Args:
            user_dx, user_dy: Raw manual mouse deltas for this frame.
            target_dx, target_dy: Target offset vector from ROI center; None if no target.
        """
        # Zero manual input must produce zero output (check raw deltas, not filtered).
        dt = self._sample_dt if dt_seconds is None else max(0.0, dt_seconds)
        if user_dx == 0.0 and user_dy == 0.0:
            self.tremor_filter.reset()
            self.assist_smoother.decay_to_zero(dt)
            return FilterResult(
                out_dx=0.0,
                out_dy=0.0,
                assist_dx=0.0,
                assist_dy=0.0,
                cos_theta=None,
                gain=0.0,
                filtered_user_dx=0.0,
                filtered_user_dy=0.0,
            )

        fx, fy = self.tremor_filter.filter(user_dx, user_dy, dt)

        if target_dx is None or target_dy is None:
            self.target_smoother.reset()
            self.assist_smoother.reset()
            self._alignment_gate_open = False
            self._had_target = False
            self._acquire_ramp = 1.0
            return FilterResult(
                out_dx=fx,
                out_dy=fy,
                assist_dx=0.0,
                assist_dy=0.0,
                cos_theta=None,
                gain=1.0,
                filtered_user_dx=fx,
                filtered_user_dy=fy,
            )

        if not self._had_target:
            self._begin_target_acquisition(target_dx, target_dy)
            self._had_target = True
        if self._acquire_ramp < 1.0:
            if self.acquire_ramp_ms > 0.0:
                self._acquire_ramp = min(1.0, self._acquire_ramp + dt / (self.acquire_ramp_ms * 0.001))
            else:
                self._acquire_ramp = 1.0
        acquire_scale = _acquire_scale(self._acquire_ramp)

        raw_tx = float(target_dx)
        raw_ty = float(target_dy)
        raw_mag = math.hypot(raw_tx, raw_ty)
        sx, sy = self.target_smoother.filter(raw_tx, raw_ty, dt)
        if raw_mag > TARGET_EPSILON:
            # Aim along the live bone. Only mix smoothed length when already close
            # so bbox jitter cannot rotate assist into an orbit.
            dirx = raw_tx / raw_mag
            diry = raw_ty / raw_mag
            close = 1.0 - _smoothstep(min(1.0, raw_mag / 28.0))
            mag = raw_mag * (1.0 - close) + math.hypot(sx, sy) * close
            tx, ty = dirx * mag, diry * mag
        else:
            tx, ty = sx, sy
        target_mag = math.hypot(tx, ty)

        cos_theta = self._cosine_similarity(user_dx, user_dy, tx, ty, eps=ALIGN_EPS_PX)
        if cos_theta is None:
            cos_theta = self._cosine_similarity(fx, fy, tx, ty, eps=ALIGN_EPS_PX)
        if cos_theta is None:
            self._alignment_gate_open = False
            return FilterResult(
                out_dx=fx,
                out_dy=fy,
                assist_dx=0.0,
                assist_dy=0.0,
                cos_theta=None,
                gain=1.0,
                filtered_user_dx=fx,
                filtered_user_dy=fy,
            )

        enter_threshold = self.cos_align_threshold + self.align_hysteresis
        exit_threshold = self.cos_align_threshold - self.align_hysteresis
        if self._alignment_gate_open:
            if cos_theta < exit_threshold:
                self._alignment_gate_open = False
        elif cos_theta >= enter_threshold:
            self._alignment_gate_open = True

        align = max(0.0, cos_theta)
        gain = 1.0 + self.gain_coeff * align if align > 0.0 else 1.0

        if align <= 0.0 or target_mag <= SIT_PX:
            self.assist_smoother.decay_to_zero(dt)
            return FilterResult(
                out_dx=fx,
                out_dy=fy,
                assist_dx=0.0,
                assist_dy=0.0,
                cos_theta=cos_theta,
                gain=gain,
                filtered_user_dx=fx,
                filtered_user_dy=fy,
            )

        sticky_scale = 0.0
        sticky_engagement = 1.0
        if self.stickiness > 0.0:
            sticky_scale = _sticky_scale(
                target_mag, self.target_deadzone_px, self.sticky_radius_px
            )
            sticky_engagement = _sticky_engagement(
                user_dx,
                user_dy,
                tx,
                ty,
                self.sticky_radius_px,
                self.sticky_breakaway_px,
            )
        if sticky_engagement <= 0.0:
            sticky_scale = 0.0

        approach_scale = _approach_scale(
            target_mag, self.approach_fade_start_px, self.approach_fade_end_px
        )
        tracking_scale = _tracking_scale(
            target_mag, self.target_deadzone_px, self.track_radius_px
        )
        user_mag = math.hypot(user_dx, user_dy)
        velocity = _velocity_scale(user_mag, self.velocity_ref_px)

        rot = min(
            MAX_ROT,
            (
                self.track_coeff * TRACK_ROT * tracking_scale
                + self.stickiness * STICKY_ROT * sticky_scale
            )
            * align
            * acquire_scale,
        )
        speed_gain = (
            (self.gain_coeff + self.stickiness * STICKY_SPEED * sticky_scale)
            * align
            * approach_scale
            * velocity
            * acquire_scale
        )
        raw_assist_dx, raw_assist_dy = _cooperative_assist(
            user_dx, user_dy, tx, ty, speed_gain, rot
        )
        raw_assist_dx, raw_assist_dy = _limit_extra_speed(
            raw_assist_dx, raw_assist_dy, user_dx, user_dy, speed_gain
        )
        max_boost = max(target_mag * self.overshoot_limit, min(3.6, target_mag * 0.85))
        raw_assist_dx, raw_assist_dy = _clamp_vector_magnitude(
            raw_assist_dx, raw_assist_dy, max_boost
        )
        raw_assist_dx, raw_assist_dy = _cap_overshoot(
            raw_assist_dx,
            raw_assist_dy,
            user_dx,
            user_dy,
            tx,
            ty,
            self.close_limit,
        )

        if raw_assist_dx == 0.0 and raw_assist_dy == 0.0:
            self.assist_smoother.decay_to_zero(dt)
            assist_dx, assist_dy = 0.0, 0.0
        else:
            assist_dx, assist_dy = self.assist_smoother.filter(raw_assist_dx, raw_assist_dy, dt)
        assist_dx, assist_dy = _sanitize_assist(assist_dx, assist_dy, user_dx, user_dy, tx, ty)
        assist_dx, assist_dy = _limit_extra_speed(
            assist_dx, assist_dy, user_dx, user_dy, speed_gain
        )
        assist_dx, assist_dy = _cap_overshoot(
            assist_dx,
            assist_dy,
            user_dx,
            user_dy,
            tx,
            ty,
            self.close_limit,
        )

        return FilterResult(
            out_dx=fx * gain,
            out_dy=fy * gain,
            assist_dx=assist_dx,
            assist_dy=assist_dy,
            cos_theta=cos_theta,
            gain=gain,
            filtered_user_dx=fx,
            filtered_user_dy=fy,
            speed_gain=speed_gain,
            aim_dx=tx,
            aim_dy=ty,
        )


# --- Independent, cooperative aim modules ------------------------------------

HUMAN_AIM_EPSILON = 1e-6
HUMAN_AIM_STOP_RADIUS_PX = 2.5
# Slider 100% matches the previous slider at 50%.
AIM_DIRECTOR_STRENGTH_SCALE = 0.20
AIM_DIRECTOR_CURVES = (
    "linear",
    "ease_in",
    "ease_out",
    "ease_in_out",
    "ease_in_cubic",
    "ease_out_cubic",
    "exponential",
    "ease_out_expo",
    "sigmoid",
)


def apply_aim_curve(t: float, name: str) -> float:
    """Map 0..1 through an Omega Dash easing curve. t=0 is the start."""
    t = max(0.0, min(1.0, float(t)))
    if name == "ease_in":
        return t * t
    if name == "ease_out":
        return 1.0 - (1.0 - t) * (1.0 - t)
    if name == "exponential":
        return 0.0 if t <= 0.0 else math.pow(2.0, 10.0 * t - 10.0)
    if name == "ease_out_expo":
        return 1.0 if t >= 1.0 else 1.0 - math.pow(2.0, -10.0 * t)
    if name == "sigmoid":
        return t * t * (3.0 - 2.0 * t)
    if name == "ease_in_out":
        if t < 0.5:
            return 2.0 * t * t
        return 1.0 - ((-2.0 * t + 2.0) ** 2) / 2.0
    if name == "ease_in_cubic":
        return t * t * t
    if name == "ease_out_cubic":
        return 1.0 - (1.0 - t) ** 3
    return t


def resolve_aim_curve(name: str) -> str:
    key = str(name or "").strip().lower().replace(" ", "_").replace("-", "_")
    return key if key in AIM_DIRECTOR_CURVES else "ease_out"


CORRECTOR_PATH_WINDOW_DEG = 60.0
CORRECTOR_HARD_CAP_PX = 2.0
SPRAY_ACQUIRE_RAMP_S = 0.12
SPRAY_YIELD_PX = 6.0


@dataclass(frozen=True)
class HumanAimResult:
    """Per-module additions to the user's physical mouse movement."""

    director_dx: float = 0.0
    director_dy: float = 0.0
    corrector_dx: float = 0.0
    corrector_dy: float = 0.0
    tracking_dx: float = 0.0
    tracking_dy: float = 0.0
    spray_dx: float = 0.0
    spray_dy: float = 0.0

    @property
    def total_dx(self) -> float:
        return self.director_dx + self.corrector_dx + self.tracking_dx + self.spray_dx

    @property
    def total_dy(self) -> float:
        return self.director_dy + self.corrector_dy + self.tracking_dy + self.spray_dy


def _fov_entry_scale(target_mag: float, radius_px: float) -> float:
    """Ease in across the outer 20% of a module FOV."""
    if radius_px <= 0.0 or target_mag >= radius_px:
        return 0.0
    feather = max(8.0, radius_px * 0.20)
    return _smoothstep((radius_px - target_mag) / feather)


def aim_director_vector(
    target_dx: float,
    target_dy: float,
    *,
    strength: float,
    radius_px: float,
    max_step_px: float,
    dt_seconds: float,
    curve: str = "ease_out",
    user_dx: float = 0.0,
    user_dy: float = 0.0,
    deadzone_px: float = 0.0,
    humanize_gain: float = 1.0,
) -> Tuple[float, float]:
    """
    Apply Aim Director's continuous magnetic pull.

    Far from the bone the pull closes a fraction of the remaining gap.
    The curve blends toward a cruise step as you get closer. Force is
    remapped so slider 100% matches the previous 50%. An optional
    deadzone kills pull on the bone. Moving away yields immediately.
    """
    stop_r = HUMAN_AIM_STOP_RADIUS_PX
    if deadzone_px > 0.0:
        stop_r = max(stop_r, min(float(deadzone_px), max(0.0, radius_px - 1.0)))
    target_mag = math.hypot(target_dx, target_dy)
    if (
        target_mag <= stop_r
        or target_mag >= radius_px
        or strength <= 0.0
        or max_step_px <= 0.0
        or dt_seconds <= 0.0
    ):
        return 0.0, 0.0

    user_mag = math.hypot(user_dx, user_dy)
    if user_mag > 0.35:
        align = (user_dx * target_dx + user_dy * target_dy) / (user_mag * target_mag)
        if align < 0.0:
            return 0.0, 0.0

    strength = max(0.0, min(1.0, strength)) * AIM_DIRECTOR_STRENGTH_SCALE
    entry = _fov_entry_scale(target_mag, radius_px)
    remaining = max(0.0, target_mag - stop_r)
    span = max(radius_px - stop_r, 1e-6)
    # t=0 at the FOV edge, t=1 at the deadzone / bone.
    t = 1.0 - max(0.0, min(1.0, remaining / span))
    blend = apply_aim_curve(t, resolve_aim_curve(curve))

    rate_per_second = 1.4 * strength
    # 8 px matches the old per-update cap. Higher Max force closes faster;
    # at 8 px and below the rate is unchanged.
    if max_step_px > 8.0:
        rate_per_second *= max_step_px / 8.0
    fraction = 1.0 - math.exp(-rate_per_second * min(dt_seconds, 0.05))
    gap_close = target_mag * fraction * entry
    cruise = min(max_step_px, remaining) * strength * entry
    amount = gap_close * (1.0 - blend) + cruise * blend
    # The recorded flick's speed at this point in the move. Always >= 1.0,
    # and the clamp below still holds it to the configured max step, so this
    # can bring the pull up to its own ceiling sooner but never past it.
    amount *= max(1.0, humanize_gain)
    amount = min(amount, max_step_px, remaining)
    return target_dx / target_mag * amount, target_dy / target_mag * amount


def spray_acquire_vector(
    target_dx: float,
    target_dy: float,
    *,
    strength: float,
    radius_px: float,
    max_step_px: float,
    dt_seconds: float,
    user_dx: float = 0.0,
    held_seconds: float = 1.0,
) -> Tuple[float, float]:
    """
    Horizontal-only pull toward the closest target while spraying.

    Intended to run only while the left mouse button is physically held. It
    closes the remaining horizontal gap after the user's own move, feathers
    in at the FOV edge, eases in after mouse-down, yields if the user aims
    away, and never moves vertically. Once the crosshair is horizontally on
    target the pull is exactly zero, so spray control stays with the user.
    """
    lateral = float(target_dx)
    remaining = lateral - float(user_dx)
    target_mag = math.hypot(target_dx, target_dy)
    if (
        abs(lateral) <= HUMAN_AIM_STOP_RADIUS_PX
        or abs(remaining) <= HUMAN_AIM_STOP_RADIUS_PX
        or remaining * lateral <= 0.0
        or target_mag >= radius_px
        or strength <= 0.0
        or max_step_px <= 0.0
        or dt_seconds <= 0.0
    ):
        return 0.0, 0.0

    entry = _fov_entry_scale(target_mag, radius_px)
    near = _smoothstep(
        (abs(remaining) - HUMAN_AIM_STOP_RADIUS_PX)
        / max(6.0, radius_px * 0.12)
    )
    ramp = (
        1.0
        if held_seconds >= SPRAY_ACQUIRE_RAMP_S
        else _smoothstep(max(0.0, held_seconds) / SPRAY_ACQUIRE_RAMP_S)
    )
    toward = max(0.0, float(user_dx) * math.copysign(1.0, lateral))
    against = max(0.0, -float(user_dx) * math.copysign(1.0, lateral))
    yield_scale = 1.0 - _smoothstep(against / SPRAY_YIELD_PX)
    covered = min(1.0, toward / max(abs(lateral), HUMAN_AIM_EPSILON))
    user_blend = 1.0 - 0.75 * covered
    rate_per_second = 1.7 * max(0.0, min(1.0, strength))
    # 8 px matches the old per-update cap. Higher Max force closes faster;
    # at 8 px and below the rate is unchanged.
    if max_step_px > 8.0:
        rate_per_second *= max_step_px / 8.0
    fraction = 1.0 - math.exp(-rate_per_second * min(dt_seconds, 0.05))
    amount = (
        abs(remaining) * fraction * entry * near * ramp * yield_scale * user_blend
    )
    amount = min(
        amount,
        max_step_px,
        max(0.0, abs(remaining) - HUMAN_AIM_STOP_RADIUS_PX) * 0.35,
    )
    if amount <= HUMAN_AIM_EPSILON:
        return 0.0, 0.0
    return math.copysign(amount, remaining), 0.0


def aim_corrector_vector(
    user_dx: float,
    user_dy: float,
    target_dx: float,
    target_dy: float,
    *,
    strength: float,
    radius_px: float,
    max_bend_deg: float,
) -> Tuple[float, float]:
    """
    Bend the path laterally without adding a forward force toward the target.

    The correction is perpendicular to the user's physical movement (dot
    product == 0), so it changes pathing rather than accelerating the throw.
    Targets behind the path, very wide turns, and already-passing steps are
    ignored.
    """
    user_mag = math.hypot(user_dx, user_dy)
    target_mag = math.hypot(target_dx, target_dy)
    if (
        user_mag <= HUMAN_AIM_EPSILON
        or target_mag <= HUMAN_AIM_STOP_RADIUS_PX
        or target_mag >= radius_px
        or strength <= 0.0
        or max_bend_deg <= 0.0
    ):
        return 0.0, 0.0

    uhx, uhy = user_dx / user_mag, user_dy / user_mag
    along = target_dx * uhx + target_dy * uhy
    if along <= max(HUMAN_AIM_STOP_RADIUS_PX, user_mag * 0.5):
        return 0.0, 0.0

    lateral_x = target_dx - uhx * along
    lateral_y = target_dy - uhy * along
    lateral_mag = math.hypot(lateral_x, lateral_y)
    if lateral_mag <= HUMAN_AIM_EPSILON:
        return 0.0, 0.0

    path_angle = math.degrees(math.atan2(lateral_mag, along))
    if path_angle >= CORRECTOR_PATH_WINDOW_DEG:
        return 0.0, 0.0

    # Once the crosshair is at the bone's height, leave the flick straight.
    height_err = abs(target_dy)
    if height_err <= HUMAN_AIM_STOP_RADIUS_PX:
        return 0.0, 0.0
    # Bend only the part of the path that changes height, so the flick
    # does not gain forward speed.
    parallel = target_dy * uhy
    px = -uhx * parallel
    py = target_dy - uhy * parallel
    pmag = math.hypot(px, py)
    if pmag <= HUMAN_AIM_EPSILON:
        return 0.0, 0.0

    strength_u = max(0.0, min(0.50, strength))
    # A few pixels inside the edge, not a fraction of a large FOV.
    # That fraction stayed weak and then jumped.
    enter = _smoothstep(min(1.0, (radius_px - target_mag) / 8.0))
    # Full bend while the height miss is clear, easing to zero as the
    # crosshair meets the bone so the rest of the flick stays straight.
    release = _smoothstep(
        min(1.0, (height_err - HUMAN_AIM_STOP_RADIUS_PX) / 16.0)
    )
    bend = (
        math.radians(max(0.0, min(45.0, max_bend_deg)))
        * (strength_u / 0.50)
        * enter
        * release
    )
    correction_mag = user_mag * math.tan(bend)
    correction_mag = min(
        correction_mag,
        max(0.0, height_err - HUMAN_AIM_STOP_RADIUS_PX) * 0.45,
        CORRECTOR_HARD_CAP_PX,
    )
    if correction_mag <= HUMAN_AIM_EPSILON:
        return 0.0, 0.0
    return px / pmag * correction_mag, py / pmag * correction_mag


def tracking_retention_vector(
    user_dx: float,
    user_dy: float,
    target_dx: float,
    target_dy: float,
    *,
    strength: float,
    radius_px: float,
    breakaway_px: float,
) -> Tuple[float, float]:
    """
    Cancel only a small fraction of low-speed outward drift near an acquired aim.

    It never creates movement at rest, never helps acquisition, never reverses
    an outward move, and fully yields at the configured breakaway speed.
    """
    user_mag = math.hypot(user_dx, user_dy)
    target_mag = math.hypot(target_dx, target_dy)
    if (
        user_mag <= HUMAN_AIM_EPSILON
        or target_mag <= HUMAN_AIM_EPSILON
        or target_mag >= radius_px
        or strength <= 0.0
        or breakaway_px <= 0.0
        or user_mag >= breakaway_px
    ):
        return 0.0, 0.0

    thx, thy = target_dx / target_mag, target_dy / target_mag
    outward = max(0.0, -(user_dx * thx + user_dy * thy))
    if outward <= HUMAN_AIM_EPSILON:
        return 0.0, 0.0

    proximity = 1.0 - _smoothstep(
        (target_mag - HUMAN_AIM_STOP_RADIUS_PX)
        / max(radius_px - HUMAN_AIM_STOP_RADIUS_PX, HUMAN_AIM_EPSILON)
    )
    fade_start = breakaway_px * 0.55
    if user_mag <= fade_start:
        velocity = 1.0
    else:
        velocity = 1.0 - _smoothstep(
            (user_mag - fade_start)
            / max(breakaway_px - fade_start, HUMAN_AIM_EPSILON)
        )
    retention = max(0.0, min(0.50, strength)) * proximity * velocity
    amount = min(outward * retention, outward * 0.50)
    return thx * amount, thy * amount
