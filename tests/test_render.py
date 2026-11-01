"""Compositing an avatar onto an action gif, and what that render must keep.

What is checked here:
  * the gif is measured and read honestly: one frame per frame, durations
    clamped into a range that still reads as an action
  * the avatar lands where the track says, at the size the track says
  * ``size: 0`` leaves a frame untouched, which is how a gif that only shows the
    character partway through is authored
  * a frame outside the ``show`` ranges is left untouched, and an open range
    reaches the last frame
  * a seed reproduces a render byte for byte; without one, jitter moves
  * an oversized gif is shrunk, and the track's coordinates still land right
  * the frame count and the durations of the source survive the re-encode
  * an upload filename a platform will accept
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from PIL import Image, ImageDraw

from action_studio import render as render_module
from action_studio.render import (
    AVATAR_BASE_SIZE,
    DEFAULT_DURATION_MS,
    MAX_DURATION_MS,
    MAX_FRAMES,
    MIN_DURATION_MS,
    _duration_ms,
    inspect_gif,
    load_frames,
    placeholder_avatar,
    render_action_gif,
    safe_filename,
)
from action_studio.track import Keyframe, Pose, Window, normalize

if TYPE_CHECKING:
    from pathlib import Path

SOURCE_SIZE = (60, 40)
SOURCE_FRAMES = 4
BLACK = (10, 12, 14, 255)
RED = (240, 40, 40, 255)


def _gif(
    path: Path,
    frames: int = SOURCE_FRAMES,
    size: tuple[int, int] = SOURCE_SIZE,
    duration: int = 80,
) -> Path:
    """A real gif whose frames are distinguishable, so counts can be checked."""
    images = [Image.new("RGB", size, (index % 256, 12, 14)) for index in range(frames)]
    images[0].save(
        path,
        save_all=True,
        append_images=images[1:],
        duration=duration,
        loop=0,
        format="GIF",
    )
    return path


def _track(*keyframes: Keyframe) -> tuple:
    return normalize(list(keyframes), "test")


def _solid(size: int, color: tuple[int, int, int, int]) -> Image.Image:
    return Image.new("RGBA", (size, size), color)


def _disc(size: int, color: tuple[int, int, int, int]) -> Image.Image:
    """A soft-edged disc, the shape an avatar crop and the placeholder have."""
    mask = Image.new("L", (size * 3, size * 3), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * 3 - 1, size * 3 - 1), fill=255)
    disc = Image.new("RGBA", (size, size), color)
    disc.putalpha(mask.resize((size, size), Image.LANCZOS))
    return disc


def _frame(path: Path, index: int = 0) -> Image.Image:
    frames, _ = load_frames(path)
    return frames[index]


def test_a_gif_is_measured_without_decoding_its_pixels(tmp_path: Path) -> None:
    info = inspect_gif(_gif(tmp_path / "move.gif", frames=7, size=(80, 50)))

    assert (info.frames, info.width, info.height) == (7, 80, 50)


def test_a_still_image_is_not_an_action_gif(tmp_path: Path) -> None:
    still = tmp_path / "still.gif"
    Image.new("RGB", SOURCE_SIZE, BLACK[:3]).save(still, format="GIF")

    with pytest.raises(ValueError, match="still image"):
        inspect_gif(still)


def test_a_file_that_is_not_an_image_is_said_so(tmp_path: Path) -> None:
    noise = tmp_path / "noise.gif"
    noise.write_bytes(b"not a gif")

    with pytest.raises(ValueError, match="could not be opened"):
        inspect_gif(noise)


def test_a_gif_that_is_not_there_is_said_so(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="could not be opened"):
        inspect_gif(tmp_path / "missing.gif")


def test_reading_a_gif_gives_one_rgba_frame_per_frame(tmp_path: Path) -> None:
    frames, durations = load_frames(_gif(tmp_path / "move.gif", frames=5))

    assert len(frames) == 5
    assert len(durations) == 5
    assert frames[0].mode == "RGBA"
    assert frames[0].size == SOURCE_SIZE


@pytest.mark.parametrize(
    ("stated", "expected"),
    [
        (80, 80),
        (5, MIN_DURATION_MS),
        (9000, MAX_DURATION_MS),
        (0, DEFAULT_DURATION_MS),
        (-10, DEFAULT_DURATION_MS),
        (None, DEFAULT_DURATION_MS),
        ("80", DEFAULT_DURATION_MS),
    ],
)
def test_a_frame_duration_is_clamped_into_a_range_that_reads_as_an_action(
    stated: object, expected: int
) -> None:
    assert _duration_ms(stated) == expected


def test_reading_a_gif_keeps_its_frame_durations(tmp_path: Path) -> None:
    _, durations = load_frames(_gif(tmp_path / "move.gif", frames=3, duration=80))

    assert durations == [80, 80, 80]


def test_a_frame_that_stated_no_duration_gets_the_default(tmp_path: Path) -> None:
    _, durations = load_frames(_gif(tmp_path / "no-duration.gif", frames=2, duration=0))

    assert durations == [DEFAULT_DURATION_MS, DEFAULT_DURATION_MS]


def test_a_gif_over_the_frame_limit_is_refused(tmp_path: Path) -> None:
    path = _gif(tmp_path / "long.gif", frames=MAX_FRAMES + 1, size=(8, 8))

    with pytest.raises(ValueError, match=f"over the {MAX_FRAMES} frame limit"):
        load_frames(path)


def test_the_avatar_is_painted_where_the_track_puts_it(tmp_path: Path) -> None:
    source = _gif(tmp_path / "move.gif")

    rendered = render_action_gif(
        source,
        _track(Keyframe(frame=1, size=20, x=30.0, y=20.0)),
        _solid(AVATAR_BASE_SIZE, RED),
        seed=1,
    )

    out = tmp_path / "out.gif"
    out.write_bytes(rendered)
    assert _frame(out).getpixel((30, 20)) == RED


def test_a_size_of_zero_leaves_the_frame_alone(tmp_path: Path) -> None:
    source = _gif(tmp_path / "move.gif")

    rendered = render_action_gif(
        source,
        _track(Keyframe(frame=1, size=0, x=30.0, y=20.0)),
        _solid(AVATAR_BASE_SIZE, RED),
        seed=1,
    )

    out = tmp_path / "out.gif"
    out.write_bytes(rendered)
    assert _frame(out).getpixel((30, 20)) != RED, "the frame's own colour"


def test_the_avatar_is_painted_at_the_size_the_track_asks_for(tmp_path: Path) -> None:
    source = _gif(tmp_path / "move.gif")

    rendered = render_action_gif(
        source,
        _track(Keyframe(frame=1, size=8, x=30.0, y=20.0)),
        _solid(AVATAR_BASE_SIZE, RED),
        seed=1,
    )

    out = tmp_path / "out.gif"
    out.write_bytes(rendered)
    frame = _frame(out)

    assert frame.getpixel((30, 20)) == RED
    assert frame.getpixel((30, 20 + 6)) != RED, "8px wide, so 6px out is outside it"


def test_a_seed_reproduces_a_render_exactly(tmp_path: Path) -> None:
    source = _gif(tmp_path / "move.gif")
    track = _track(
        Keyframe(frame=1, size=20, x=30.0, y=20.0, jitter=4),
        Keyframe(frame=4, size=20, x=10.0, y=10.0, jitter=4),
    )

    first = render_action_gif(source, track, _solid(AVATAR_BASE_SIZE, RED), seed=7)
    second = render_action_gif(source, track, _solid(AVATAR_BASE_SIZE, RED), seed=7)

    assert first == second


def test_jitter_moves_the_avatar_between_renders(tmp_path: Path) -> None:
    source = _gif(tmp_path / "move.gif")
    track = _track(Keyframe(frame=1, size=16, x=30.0, y=20.0, jitter=8))

    moved = {
        render_action_gif(source, track, _solid(AVATAR_BASE_SIZE, RED), seed=seed)
        for seed in (1, 2, 3)
    }

    assert len(moved) > 1, "the same gif three times should not be identical"


def test_a_gigantic_gif_is_shrunk_and_the_track_still_lands_right(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(render_module, "MAX_GIF_EDGE", 30)
    source = _gif(tmp_path / "big.gif", size=(60, 40))

    rendered = render_action_gif(
        source,
        _track(Keyframe(frame=1, size=20, x=30.0, y=20.0)),
        _solid(AVATAR_BASE_SIZE, RED),
        seed=1,
    )

    out = tmp_path / "out.gif"
    out.write_bytes(rendered)
    frame = _frame(out)

    assert frame.size == (30, 20), "half the source, because 60 is over the edge"
    assert frame.getpixel((15, 10)) == RED, "the authored centre scaled with it"


def test_the_render_keeps_the_source_frames_and_their_durations(tmp_path: Path) -> None:
    source = _gif(tmp_path / "move.gif", frames=6, duration=90)

    rendered = render_action_gif(
        source,
        _track(Keyframe(frame=1, size=16, x=20.0, y=20.0)),
        _solid(AVATAR_BASE_SIZE, RED),
        seed=1,
    )

    out = tmp_path / "out.gif"
    out.write_bytes(rendered)

    assert inspect_gif(out).frames == 6
    assert load_frames(out)[1] == load_frames(source)[1]


def test_a_render_of_a_gif_that_is_not_there_is_said_so(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="could not be opened"):
        render_action_gif(
            tmp_path / "missing.gif",
            _track(Keyframe(frame=1, size=10, x=1.0, y=1.0)),
            _solid(8, RED),
        )


def test_a_missing_avatar_becomes_a_visible_disc() -> None:
    avatar = placeholder_avatar(32)

    assert avatar.mode == "RGBA"
    assert avatar.size == (32, 32)
    assert avatar.getpixel((16, 16))[3] == 255, "opaque, not a hole in the gif"
    assert avatar.getpixel((0, 0))[3] == 0, "a corner is not part of the disc"
    rim = [avatar.getpixel((x, 16))[3] for x in range(32)]
    assert any(0 < value < 255 for value in rim), "the rim is anti-aliased"


def test_a_rotated_avatar_keeps_a_soft_rim() -> None:
    overlay = render_module._overlay(
        _disc(40, RED), Pose(size=40, x=20.0, y=20.0, rotate=30, jitter=0), 1.0
    )

    histogram = overlay.getchannel("A").histogram()
    soft = sum(histogram[1:255])
    assert soft > 400, (
        f"only {soft} rim pixels blend; nearest-neighbour leaves about 300, "
        "which reads as a staircase"
    )


@pytest.mark.parametrize(
    ("actor", "expected"),
    [
        ("someone", "kick_someone.gif"),
        ("someone/../etc", "kick_someone_.._etc.gif"),
        ("", "kick_user.gif"),
        ("  ", "kick_user.gif"),
    ],
)
def test_the_upload_filename_strips_what_a_platform_rejects(
    actor: str, expected: str
) -> None:
    assert safe_filename("kick", actor) == expected


def test_a_long_filename_is_truncated_rather_than_rejected() -> None:
    name = safe_filename("a" * 80, "b" * 80)

    assert name.startswith("a" * 40)
    assert len(name) <= 40 + 1 + 40 + 4


def test_a_frame_outside_the_windows_is_left_alone(tmp_path: Path) -> None:
    """Visibility is a range test, so an uncovered frame is the source's own pixels."""
    source = _gif(tmp_path / "move.gif", frames=5)
    track = _track(Keyframe(frame=1, size=20, x=30.0, y=20.0))

    rendered = render_action_gif(
        source,
        track,
        _solid(AVATAR_BASE_SIZE, RED),
        show=(Window(start=2, end=3),),
        seed=1,
    )

    out = tmp_path / "out.gif"
    out.write_bytes(rendered)
    assert _frame(out, 1).getpixel((30, 20)) == RED
    assert _frame(out, 2).getpixel((30, 20)) == RED
    assert _frame(out, 0).getpixel((30, 20)) != RED, "hidden before the range"
    assert _frame(out, 4).getpixel((30, 20)) != RED, "hidden after the range"


def test_an_open_window_reaches_the_last_frame(tmp_path: Path) -> None:
    source = _gif(tmp_path / "move.gif")
    track = _track(Keyframe(frame=1, size=20, x=30.0, y=20.0))

    rendered = render_action_gif(
        source, track, _solid(AVATAR_BASE_SIZE, RED), show=(Window(start=3),), seed=1
    )

    out = tmp_path / "out.gif"
    out.write_bytes(rendered)
    assert _frame(out, SOURCE_FRAMES - 1).getpixel((30, 20)) == RED
