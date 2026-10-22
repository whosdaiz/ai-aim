"""Shapes injected mouse movement to match the user's own motor signature.

The aim modules decide *where* to go. This decides *how the movement gets
there*: the sideways shape of the path, and the hand tremor riding on top of
it. Both come from the user's recorded Humanizer runs by way of
``humanizer_paths``.

Three rules make "this can never make a module worse" a property of the code
rather than a hope:

* **Nothing is ever subtracted along the module's own direction.** Every term
  is either perpendicular to it or a gain of at least 1.0, so the assist can
  never be slowed or reversed.
* **Every term is zero-sum.** The sideways terms are emitted as differences of
  a displacement that starts and ends at zero, so they lengthen the path
  without moving the endpoint.
* **Everything fades out near the target.** Inside the stop radius the output
  is exactly what it would have been with the feature switched off.

Reaction time is deliberately absent: ``humanizer_paths`` trims every recorded
flick to its movement onset, so no perception delay can reach the modules.

No imports from ``main``, so this can be driven from a standalone script.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from humanizer_paths import PathLibrary
from pointer_filter import HUMAN_AIM_STOP_RADIUS_PX

# A gap this much bigger than the last one means a new target, not progress.
RETARGET_JUMP_PX = 40.0
# Idle this long and the next movement starts a fresh engagement.
ENGAGE_GAP_S = 0.12
# Below this there is no path worth shaping, only a small correction.
ENGAGE_MIN_PX = 12.0
# Full shaping at this gap, tapering to nothing at the stop radius.
FADE_START_PX = 12.5
# Tremor: one-pole noise with a ~4.5 Hz corner, hard limited to +-2 sigma.
JITTER_TAU_S = 0.035
JITTER_SIGMA_LIMIT = 2.0
# How far the path may bow out sideways.
ARC_MAX_PX = 8.0
ARC_MAX_FRACTION = 0.25
# Aim swinging further than this means a different target, not the same path.
RELATCH_DEG = 60.0
# How quickly an abandoned engagement settles back onto the module's path.
UNWIND_TAU_S = 0.02
_RELATCH_COS = math.cos(math.radians(RELATCH_DEG))
# A single-tick nudge gets this much sideways tremor at most.
TAP_JITTER_MAX_PX = 1.0
_SEED = 0x41524755


def _smoothstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


@dataclass
class HumanizerShaper:
    """Per-engagement shaping state. Owned by the aim loop, single threaded.

    The RNG is deliberately a private instance: ``main`` draws trigger
    intervals from the global ``random``, and sharing that stream at several
    hundred hertz would silently change how the trigger behaves.
    """

    rng: random.Random = None  # type: ignore[assignment]
    active: bool = False
    last_motion_at: float = 0.0
    last_gap_px: float = 0.0
    # Tremor is a displacement, not a velocity.
    jit_x: float = 0.0
    jit_y: float = 0.0
    # The recorded flick being traced, and the run it is being traced over.
    flick: object = None
    start_gap_px: float = 0.0
    dir_x: float = 0.0
    dir_y: float = 0.0
    # Total sideways displacement currently applied, ramped down when idle.
    disp_x: float = 0.0
    disp_y: float = 0.0
    # Where the aim currently sits relative to the path the module asked for.
    # Everything is emitted as a move from here to the displacement we want,
    # which is what makes the integer output track the float exactly and
    # makes drift impossible however long it runs.
    sent_x: int = 0
    sent_y: int = 0

    def __post_init__(self) -> None:
        if self.rng is None:
            self.rng = random.Random(_SEED)

    def reset(self) -> None:
        """Forget the engagement. Keeps ``sent_*`` so any displacement still
        owed is handed back on the next tick instead of being abandoned."""
        self.active = False
        self.last_motion_at = 0.0
        self.last_gap_px = 0.0
        self.jit_x = 0.0
        self.jit_y = 0.0
        self.flick = None
        self.start_gap_px = 0.0
        self.dir_x = 0.0
        self.dir_y = 0.0

    # ------------------------------------------------------------ lifecycle

    def _begin(
        self, library: PathLibrary, now: float, gap_px: float, dir_x: float, dir_y: float
    ) -> None:
        self.active = True
        self.last_motion_at = now
        self.last_gap_px = gap_px
        self.start_gap_px = gap_px
        self.jit_x = 0.0
        self.jit_y = 0.0
        self.dir_x, self.dir_y = dir_x, dir_y
        self.flick = library.pick(gap_px, math.atan2(dir_y, dir_x), self.rng)

    def _update_engagement(
        self,
        library: PathLibrary,
        now: float,
        moving: bool,
        gap_px: float,
        dir_x: float,
        dir_y: float,
    ) -> bool:
        """Track engagement start/end. True while one is live."""
        if gap_px < ENGAGE_MIN_PX or (dir_x == 0.0 and dir_y == 0.0):
            if self.active:
                self.reset()
            return False
        if self.active:
            swung = self.dir_x * dir_x + self.dir_y * dir_y < _RELATCH_COS
            jumped = gap_px - self.last_gap_px > RETARGET_JUMP_PX
            stale = now - self.last_motion_at > ENGAGE_GAP_S
            if swung or jumped or stale:
                self.reset()
                self._begin(library, now, gap_px, dir_x, dir_y)
        elif moving:
            self._begin(library, now, gap_px, dir_x, dir_y)
        if not self.active:
            return False
        if moving:
            self.last_motion_at = now
        self.last_gap_px = gap_px
        return True

    def gain(self, percent: float) -> float:
        """Speed multiplier for the module asking, at this point in the move.

        Always at least 1.0, so a module can be brought up to its own speed
        limit sooner but never held back. ``percent`` is that module's own
        Humanizer slider.
        """
        if percent <= 0.0 or self.flick is None or self.start_gap_px <= 0.0:
            return 1.0
        progress = 1.0 - self.last_gap_px / self.start_gap_px
        raw = self.flick.gain_at(max(0.0, min(1.0, progress)))
        return 1.0 + (raw - 1.0) * max(0.0, min(1.0, percent * 0.01))

    def _arc(self, weight: float) -> tuple[float, float]:
        """Sideways offset for how far through the move we are."""
        flick = self.flick
        if flick is None or self.start_gap_px <= 0.0:
            return 0.0, 0.0
        progress = 1.0 - self.last_gap_px / self.start_gap_px
        offset = flick.perp_at(max(0.0, min(1.0, progress))) * self.start_gap_px * weight
        limit = min(ARC_MAX_PX, self.start_gap_px * ARC_MAX_FRACTION)
        offset = max(-limit, min(limit, offset))
        # Perpendicular to the way the aim is heading, so it lengthens the
        # path without ever pushing along or against it.
        return -self.dir_y * offset, self.dir_x * offset

    # ---------------------------------------------------------------- terms

    def _step_jitter(self, dt: float, sigma: float) -> None:
        """Advance the tremor. It is a bounded, zero-mean AR(1) displacement,
        so it wanders but always around the module's own path."""
        if sigma <= 0.0 or dt <= 0.0:
            self.jit_x = 0.0
            self.jit_y = 0.0
            return
        decay = math.exp(-dt / JITTER_TAU_S)
        kick = math.sqrt(max(0.0, 1.0 - decay * decay)) * sigma
        limit = JITTER_SIGMA_LIMIT * sigma
        self.jit_x = max(-limit, min(limit, self.jit_x * decay + self.rng.gauss(0.0, 1.0) * kick))
        self.jit_y = max(-limit, min(limit, self.jit_y * decay + self.rng.gauss(0.0, 1.0) * kick))

    def _emit(self, disp_x: float, disp_y: float) -> tuple[int, int]:
        """Move from wherever the shaping currently sits to ``disp``.

        Because the output is the difference of two rounded totals, the
        integer stream never wanders more than half a count from the float
        displacement, and returning ``disp`` to zero always gives back
        exactly what was borrowed.
        """
        want_x = int(round(disp_x))
        want_y = int(round(disp_y))
        out_x = want_x - self.sent_x
        out_y = want_y - self.sent_y
        self.sent_x = want_x
        self.sent_y = want_y
        return out_x, out_y

    # ---------------------------------------------------------------- shape

    def shape(
        self,
        library: PathLibrary,
        assist_dx: int,
        assist_dy: int,
        *,
        blend: float,
        gap_px: float,
        aim_dx: float,
        aim_dy: float,
        px_per_count: float,
        now: float,
        dt: float,
    ) -> tuple[int, int]:
        """Return the assist with the user's own hand laid over it.

        ``blend`` is 0..1, already weighted across the modules contributing
        this tick. ``gap_px`` is how far the aim still has to travel,
        ``aim_dx``/``aim_dy`` point at the target, and ``px_per_count``
        converts the profile's counts into this game's pixels.
        """
        aim_mag = math.hypot(aim_dx, aim_dy)
        if aim_mag > 1e-9:
            aim_dx, aim_dy = aim_dx / aim_mag, aim_dy / aim_mag
        else:
            aim_dx = aim_dy = 0.0
        moving = bool(assist_dx or assist_dy)
        live = (
            blend > 0.0
            and library.ok
            and self._update_engagement(library, now, moving, gap_px, aim_dx, aim_dy)
        )

        if live:
            fade = _smoothstep(
                (gap_px - HUMAN_AIM_STOP_RADIUS_PX)
                / max(1e-6, FADE_START_PX - HUMAN_AIM_STOP_RADIUS_PX)
            )
            weight = max(0.0, min(1.0, blend)) * fade
            sigma = library.jitter_counts * px_per_count * weight
            self._step_jitter(dt, sigma)
            arc_x, arc_y = self._arc(weight)
            self.disp_x = arc_x + self.jit_x
            self.disp_y = arc_y + self.jit_y
        else:
            # Settle back onto the module's own path rather than snapping,
            # handing back exactly what was borrowed.
            if self.active:
                self.reset()
            decay = math.exp(-max(dt, 0.0) / UNWIND_TAU_S)
            self.disp_x *= decay
            self.disp_y *= decay
            if abs(self.disp_x) < 0.01 and abs(self.disp_y) < 0.01:
                self.disp_x = self.disp_y = 0.0

        out_x, out_y = self._emit(self.disp_x, self.disp_y)
        return assist_dx + out_x, assist_dy + out_y


def blend_weight(contributions: tuple[tuple[float, float, float], ...]) -> float:
    """Magnitude-weighted blend across the modules moving the mouse.

    Each entry is ``(dx, dy, percent)``. A module that contributes more of
    this tick's movement has more say in how human it looks.
    """
    numerator = 0.0
    denominator = 0.0
    for dx, dy, percent in contributions:
        magnitude = math.hypot(dx, dy)
        if magnitude > 0.0 and percent > 0.0:
            numerator += magnitude * percent
            denominator += magnitude
        elif magnitude > 0.0:
            denominator += magnitude
    if denominator <= 0.0:
        return 0.0
    return max(0.0, min(1.0, numerator / denominator * 0.01))
