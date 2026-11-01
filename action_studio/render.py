"""Composite an avatar onto every frame of an action gif.

The heavy part is Pillow work, so everything here is synchronous and the caller
runs it in a worker thread. Frames are pasted from the source gif's own pixels,
which is what keeps the authored coordinates meaningful.

Two boundaries here are worth stating:

* The source frames' durations are carried into the encoded gif, so a 30 ms gif
  plays at 30 ms rather than at Pillow's 100 ms default, which would turn every
  action into a slow slideshow.
* A frame is resolved from the track rather than from a cursor that only
  advances on an exact frame match, so a track shorter than the gif cannot stall
  the pose on the last entry.
"""

from __future__ import annotations

import io
import random
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageSequence

from action_studio.track import Pose, Stop, Window, resolve_pose, visible

if TYPE_CHECKING:
    from pathlib import Path

# A duration of 0 in a gif means "as fast as the viewer allows", which reads as
# a flicker; anything below this looks like a strobe, and players floor it.
MIN_DURATION_MS = 20
# Longer than this and one frame becomes a pause rather than part of an action.
MAX_DURATION_MS = 500
DEFAULT_DURATION_MS = 100

# Gifs get uploaded by whoever plays them, and every decoded frame is held while
# the gif is rebuilt, so an oversized source is refused instead of rendered.
MAX_GIF_EDGE = 1280
MAX_FRAMES = 200

# The avatar is cropped once at this size and scaled per frame. Scaling a
# 256px circle down to the ~40px poses most gifs use costs a resample per frame
# and keeps the edge smooth; cropping at each frame's size would multiply the
# work for no visible gain.
AVATAR_BASE_SIZE = 256

_UNSAFE_FILENAME = re.compile(r"[^\w. -]")


@dataclass(frozen=True)
class GifInfo:
    """What an action's source gif looks like, read from its header.

    ``frames`` is what a track is checked against, so a keyframe pointing past
    the end of the gif is reported at boot instead of at the moment someone
    uses the command.
    """

    path: Path
    frames: int
    width: int
    height: int


def inspect_gif(path: Path) -> GifInfo:
    """Read an animated gif's size and frame count without decoding pixels.

    Raises:
        ValueError: If the file is not an animated gif.
    """
    try:
        with Image.open(path) as image:
            if not getattr(image, "is_animated", False):
                raise ValueError(f"{path} is a still image, not an animated gif")
            return GifInfo(
                path=path, frames=image.n_frames, width=image.width, height=image.height
            )
    except OSError as error:
        raise ValueError(f"{path} could not be opened as an image: {error}") from error


def load_frames(path: Path) -> tuple[list[Image.Image], list[int]]:
    """The gif's frames as RGBA images and each frame's duration in milliseconds.

    Frames are copied out of the decoder because ``ImageSequence`` reuses one
    image object while seeking, and a reused object would render every frame as
    the last one.

    Raises:
        ValueError: If the file is not a readable image, or has too many frames
            to encode back out within an upload's limits.
    """
    frames: list[Image.Image] = []
    durations: list[int] = []

    try:
        with Image.open(path) as gif:
            for frame in ImageSequence.Iterator(gif):
                frames.append(frame.convert("RGBA"))
                durations.append(_duration_ms(frame.info.get("duration")))
    except OSError as error:
        raise ValueError(f"{path} could not be opened as an image: {error}") from error

    if len(frames) > MAX_FRAMES:
        raise ValueError(
            f"{path} has {len(frames)} frames, over the {MAX_FRAMES} frame limit"
        )
    return frames, durations


def render_action_gif(
    path: Path,
    stops: tuple[Stop, ...],
    avatar: Image.Image,
    *,
    show: tuple[Window, ...] = (),
    seed: int | None = None,
) -> bytes:
    """Paste ``avatar`` over ``path``'s gif along ``stops`` and re-encode it.

    Args:
        path: The source gif.
        stops: The normalized track, in frame order.
        avatar: A circular RGBA avatar, ideally :data:`AVATAR_BASE_SIZE` wide.
        show: The frame ranges the avatar is drawn on. Empty draws every frame,
            which is what a definition without a ``show`` key means.
        seed: Fix the jitter offsets so a render can be reproduced; ``None``
            randomizes them, which is what makes repeated actions feel alive.
            The stream advances only on frames the avatar is drawn on, so a
            ``show`` key moves the offsets of the frames after a gap too.

    Returns:
        The encoded gif bytes.

    Raises:
        ValueError: If the source is too large to render, or a pose lands
            entirely outside the frame.
    """
    rng = random.Random(seed)
    frames, durations = load_frames(path)
    scale = _scale_to(frames[0].size)

    if scale < 1.0:
        width, height = frames[0].size
        size = (round(width * scale), round(height * scale))
        frames = [frame.resize(size) for frame in frames]

    for index, frame in enumerate(frames):
        # Frames are counted from 1 because that is how the track is authored.
        frame_number = index + 1
        if not visible(show, frame_number):
            # Outside every window the avatar is not drawn. The track still
            # advances, so the next window carries the motion on from where the
            # gap began rather than restarting it.
            continue
        pose = resolve_pose(stops, frame_number)
        if pose.size <= 0:
            # ``size: 0`` is a pose that has shrunk to nothing on a frame that is
            # otherwise drawn — visibility is the separate question ``show``
            # answers.
            continue
        paste_avatar(frame, avatar, pose, scale=scale, rng=rng)

    buffer = io.BytesIO()
    frames[0].save(
        buffer,
        "GIF",
        save_all=True,
        append_images=frames[1:],
        duration=durations,
        loop=0,
        optimize=False,
    )
    return buffer.getvalue()


def paste_avatar(
    frame: Image.Image,
    avatar: Image.Image,
    pose: Pose,
    *,
    scale: float = 1.0,
    rng: random.Random | None = None,
) -> None:
    """Paste ``avatar`` onto one frame at ``pose``, in that frame's pixels.

    This is the one place a pose becomes pixels: resized to the pose's diameter,
    rotated about its own centre, then pasted with its centre on the pose. An
    editor preview and a rendered gif go through here so they cannot drift apart.

    Args:
        frame: The frame to draw on, modified in place.
        avatar: A circular RGBA avatar at :data:`AVATAR_BASE_SIZE`.
        pose: Where the avatar belongs on this frame.
        scale: How much the frame was shrunk from the source's pixels; poses are
            authored in the source's pixels, so they meet the scale here.
        rng: Rolls the jitter shake. ``None`` places the pose exactly, which is
            what a preview needs to aim with.
    """
    overlay = _overlay(avatar, pose, scale)
    center_x = pose.x * scale
    center_y = pose.y * scale
    if rng is not None and pose.jitter:
        # The shake: its own offset on each axis for every drawn frame. The track
        # interpolates jitter, so a track can build it rather than switch it on.
        center_x += rng.uniform(-pose.jitter, pose.jitter) * scale
        center_y += rng.uniform(-pose.jitter, pose.jitter) * scale
    _composite(frame, overlay, center_x, center_y)


_PLACEHOLDER_COLOR = (176, 176, 176, 255)
_MASK_SCALE = 3


def placeholder_avatar(size: int = AVATAR_BASE_SIZE) -> Image.Image:
    """A grey disc standing in for an avatar that could not be fetched.

    A blank circle would read as a hole in the gif, and refusing the action over
    a dead avatar would refuse it for a reason nobody can see. The mask is drawn
    at three times the size and shrunk with LANCZOS, the way
    ``action_studio.crop.circle`` cuts a real avatar, so a placeholder has the
    same soft rim as one instead of a hard-edged block.
    """
    mask = Image.new("L", (size * _MASK_SCALE, size * _MASK_SCALE), 0)
    ImageDraw.Draw(mask).ellipse(
        (0, 0, size * _MASK_SCALE - 1, size * _MASK_SCALE - 1), fill=255
    )
    disc = Image.new("RGBA", (size, size), _PLACEHOLDER_COLOR)
    disc.putalpha(mask.resize((size, size), Image.LANCZOS))
    return disc


def safe_filename(stem: str, actor: str) -> str:
    """An upload filename a chat platform accepts.

    Display names are arbitrary text, and platforms reject control characters and
    path separators, so anything outside ``\\w . -`` and a space becomes an
    underscore. The action's file stem comes first so a saved gif is still
    recognisable as, say, ``kick_something.gif``.
    """
    cleaned = _UNSAFE_FILENAME.sub("_", actor).strip() or "user"
    return f"{stem[:40]}_{cleaned[:40]}.gif"


def _duration_ms(value: object) -> int:
    """One frame's duration, clamped into a range that reads as an action."""
    if not isinstance(value, int) or value <= 0:
        return DEFAULT_DURATION_MS
    return max(MIN_DURATION_MS, min(value, MAX_DURATION_MS))


def _scale_to(size: tuple[int, int]) -> float:
    """Shrink an oversized gif to :data:`MAX_GIF_EDGE` while keeping its pixels.

    The scale is returned rather than applied to the track because the authored
    coordinates stay in the source's pixels and only meet the scale here, so a
    track authored against the original file keeps working after a resize.
    """
    longest = max(size)
    if longest <= MAX_GIF_EDGE:
        return 1.0
    return MAX_GIF_EDGE / longest


def _overlay(avatar: Image.Image, pose: Pose, scale: float) -> Image.Image:
    """The avatar at this frame's size and rotation, centred on its own origin."""
    diameter = max(1, round(pose.size * scale))
    overlay = avatar.resize((diameter, diameter), Image.LANCZOS)
    if pose.rotate:
        # ``expand`` keeps the whole disc instead of clipping its corners, and
        # the composite centres on the pose anyway. Bilinear resampling matters
        # here: the default nearest-neighbour turns every rotated rim into a
        # staircase of unchanged pixels.
        overlay = overlay.rotate(
            pose.rotate, resample=Image.Resampling.BILINEAR, expand=True
        )
    return overlay


def _composite(frame: Image.Image, overlay: Image.Image, x: float, y: float) -> None:
    """Paste ``overlay`` with its centre at ``(x, y)``, clipping at the edges.

    ``paste`` and ``alpha_composite`` both clip a box that runs past the frame,
    which is what a jittered or partly off-screen pose needs.
    """
    frame.alpha_composite(
        overlay, (round(x - overlay.width / 2), round(y - overlay.height / 2))
    )
