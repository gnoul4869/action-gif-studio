"""Keyframe tracks — how the avatar moves across an action gif.

An action used to be authored with one entry per gif frame, which meant reading
every frame's coordinates out of an image editor by hand. A track instead
declares a pose at a few frames and interpolates the frames in between, so a
kick is four keyframes rather than twenty-five rows.

Three shapes, in the order they appear while an action is used:

* :class:`Keyframe` — what the YAML says. Any field may be omitted to keep the
  previous keyframe's value, so only what changes is written down.
* :class:`Stop` — the same keyframe with every value filled in, produced by
  :func:`normalize`. Rendering and validation work on stops.
* :class:`Pose` — one frame's resolved pose, produced by :func:`resolve_pose`.

``ease`` belongs to the keyframe being *arrived at*: it describes how the motion
into that pose behaves, which is how an author reads it ("snap to this pose",
"drift into this pose").

A track says where the avatar is. :class:`Window` ranges say something separate:
whether it is drawn at all on a given frame. Keeping the two apart is the point
— hiding the avatar in the middle of a motion should not cost a pose, and the
track keeps running behind the gap so the reappearance carries on where the
motion left off.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


# Each curve takes the fraction of a segment elapsed and returns the fraction of
# the pose difference to apply. ``t`` is 0 leaving the previous keyframe and 1
# arriving at the keyframe the easing is written on, so "in" slows the departure
# and "out" slows the arrival — the naming a gif author reads it by.
def _linear(t: float) -> float:
    """The same speed the whole way."""
    return t


def _smooth(t: float) -> float:
    """Leaves slowly, arrives slowly, fastest in the middle."""
    return t * t * (3.0 - 2.0 * t)


def _ease_in(t: float) -> float:
    """Leaves slowly and lands fast: a wind-up into a hit."""
    return t * t


def _ease_out(t: float) -> float:
    """Leaves fast and lands slowly: a recoil coming to rest."""
    return t * (2.0 - t)


def _ease_in_out(t: float) -> float:
    """Slower at both ends than :func:`_smooth`."""
    return 4.0 * t * t * t if t < 0.5 else 1.0 - (-2.0 * t + 2.0) ** 3 / 2.0


def _hold(t: float) -> float:
    """No interpolation: the previous pose is kept until this keyframe's frame."""
    return 0.0


EASING: dict[str, Callable[[float], float]] = {
    "hold": _hold,
    "linear": _linear,
    "smooth": _smooth,
    "ease_in": _ease_in,
    "ease_out": _ease_out,
    "ease_in_out": _ease_in_out,
}

# A keyframe that says nothing about easing moves this way, and the file written
# out leaves ``ease`` off when it is this value.
DEFAULT_EASE = "linear"


@dataclass(frozen=True)
class Keyframe:
    """One authored pose at ``frame``, counted from 1 like a gif viewer does.

    A field left as ``None`` inherits the previous keyframe's value, so a track
    reads as the changes rather than as a full table. ``size`` is the avatar's
    diameter in the gif's own pixels, where ``0`` means the avatar is not drawn
    at all; ``x``/``y`` its centre, ``rotate`` the degrees to turn it,
    ``jitter`` the random pixel offset applied per frame, and ``ease`` how the
    segment arriving at this keyframe moves.
    """

    frame: int
    size: int | None = None
    x: float | None = None
    y: float | None = None
    rotate: float | None = None
    jitter: float | None = None
    ease: str = DEFAULT_EASE


@dataclass(frozen=True)
class Pose:
    """A fully specified pose — the numbers the renderer actually pastes with."""

    size: int
    x: float
    y: float
    rotate: float
    jitter: float


@dataclass(frozen=True)
class Stop:
    """A keyframe whose values are all resolved, i.e. a frame safe to hold."""

    frame: int
    pose: Pose
    ease: str


def normalize(keyframes: Sequence[Keyframe], source: str) -> tuple[Stop, ...]:
    """Fill in inherited values and order the track by frame.

    Args:
        keyframes: The authored keyframes, in any order.
        source: Where the track came from, used in error messages.

    Returns:
        Stops sorted by frame, each carrying a complete pose.

    Raises:
        ValueError: If the track is empty, repeats a frame, names an unknown
            easing, or starts without a pose to inherit from.
    """
    if not keyframes:
        raise ValueError(f"{source}: the track is empty")

    ordered = sorted(keyframes, key=lambda keyframe: keyframe.frame)
    first = ordered[0]

    if first.size is None or first.x is None or first.y is None:
        raise ValueError(
            f"{source}: the first keyframe (frame {first.frame}) needs size, x and y "
            "— there is no earlier pose to inherit them from"
        )

    duplicates = [
        ordered[index].frame
        for index in range(1, len(ordered))
        if ordered[index].frame == ordered[index - 1].frame
    ]
    if duplicates:
        raise ValueError(
            f"{source}: frames {duplicates} appear in more than one keyframe"
        )

    stops: list[Stop] = []
    carried = Pose(
        size=first.size,
        x=first.x,
        y=first.y,
        rotate=first.rotate or 0.0,
        jitter=first.jitter or 0.0,
    )

    for keyframe in ordered:
        pose = Pose(
            size=carried.size if keyframe.size is None else keyframe.size,
            x=carried.x if keyframe.x is None else keyframe.x,
            y=carried.y if keyframe.y is None else keyframe.y,
            rotate=carried.rotate if keyframe.rotate is None else keyframe.rotate,
            jitter=carried.jitter if keyframe.jitter is None else keyframe.jitter,
        )
        _require_ease(keyframe.ease, source, keyframe.frame)
        stops.append(Stop(frame=keyframe.frame, pose=pose, ease=keyframe.ease))
        carried = pose

    return tuple(stops)


def resolve_pose(stops: Sequence[Stop], frame: int) -> Pose:
    """The pose at ``frame`` (counted from 1), interpolated between stops.

    Frames before the first keyframe hold the first pose and frames after the
    last hold the last one, so a track shorter than the gif still covers every
    frame.
    """
    if frame <= stops[0].frame:
        return stops[0].pose
    if frame >= stops[-1].frame:
        return stops[-1].pose

    # The frame sits between two stops: the segment is the first pair whose end
    # is past it, and ``ease`` belongs to the stop being arrived at.
    for start, end in itertools.pairwise(stops):
        if frame >= end.frame:
            continue
        elapsed = (frame - start.frame) / (end.frame - start.frame)
        progress = EASING[end.ease](elapsed)
        if progress <= 0.0:
            return start.pose
        return Pose(
            size=round(_lerp(start.pose.size, end.pose.size, progress)),
            x=_lerp(start.pose.x, end.pose.x, progress),
            y=_lerp(start.pose.y, end.pose.y, progress),
            rotate=_lerp(start.pose.rotate, end.pose.rotate, progress),
            jitter=_lerp(start.pose.jitter, end.pose.jitter, progress),
        )

    return stops[-1].pose  # pragma: no cover - the two checks above cover it


def _lerp(start: float, end: float, progress: float) -> float:
    """``start`` moved ``progress`` of the way towards ``end``.

    Rotation is interpolated as written rather than around the shortest way, so
    an author can spin a pose through more than half a turn (a 0 -> 360 track)
    without the renderer deciding to turn the other way.
    """
    return start + (end - start) * progress


@dataclass(frozen=True)
class Window:
    """The frames the avatar is drawn on, inclusive at both ends.

    ``end`` left as ``None`` runs to the gif's last frame, which keeps a
    definition correct when the gif gains a trailing frame.
    """

    start: int
    end: int | None = None

    def covers(self, frame: int) -> bool:
        """Whether ``frame`` (counted from 1) is inside this window."""
        return frame >= self.start and (self.end is None or frame <= self.end)


def visible(windows: Sequence[Window], frame: int) -> bool:
    """Whether the avatar belongs on ``frame``.

    An empty list means the author never restricted visibility, so every frame
    is drawn — that is what makes the key optional.
    """
    if not windows:
        return True
    return any(window.covers(frame) for window in windows)


def _require_ease(ease: str, source: str, frame: int) -> None:
    if ease not in EASING:
        raise ValueError(
            f"{source}: unknown ease {ease!r} on frame {frame}; "
            f"use one of {', '.join(EASING)}"
        )
