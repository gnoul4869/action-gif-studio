"""Keyframe tracks: what a pose is at a frame, and what the YAML may leave out.

Inheritance:
  * a keyframe that omits a field keeps the previous keyframe's value
  * the first keyframe cannot inherit, so it must state ``size``, ``x`` and ``y``
  * keyframes arrive in any order and are read in frame order
  * an unknown ``ease`` and a repeated frame are refused by name

Resolution:
  * frames before the first keyframe hold the first pose, frames after the last
    hold the last one, so a short track still covers a long gif
  * ``linear`` moves at a steady rate, ``smooth`` starts and ends slowly
  * ``hold`` keeps the previous pose until the keyframe's own frame
  * ``ease_in``, ``ease_out`` and ``ease_in_out`` each start or end gently
  * every easing reaches its target exactly, and ``size`` comes back whole
  * rotation is interpolated as written, so 0 -> 360 half-ways through is 180

Visibility:
  * a window covers its own frames, inclusive at both ends, and an open one
    runs to the last frame
  * no windows at all means every frame is drawn
"""

from __future__ import annotations

import pytest

from action_studio.track import (
    DEFAULT_EASE,
    EASING,
    Keyframe,
    Window,
    normalize,
    resolve_pose,
    visible,
)

SOURCE = "test.yaml"


def _stops(*keyframes: Keyframe):
    return normalize(keyframes, SOURCE)


def _pose_at(frame: int, *keyframes: Keyframe):
    return resolve_pose(_stops(*keyframes), frame)


def test_normalize_fills_omitted_fields_from_the_previous_keyframe() -> None:
    stops = _stops(
        Keyframe(frame=1, size=20, x=10.0, y=20.0),
        Keyframe(frame=5, size=40),
        Keyframe(frame=9, x=60.0, y=20.0),
    )

    assert [stop.frame for stop in stops] == [1, 5, 9]
    assert stops[1].pose.size == 40
    assert stops[1].pose.x == 10.0, "position is inherited, not restarted"
    assert stops[2].pose.size == 40
    assert stops[2].pose.x == 60.0
    assert stops[0].pose.rotate == 0.0, "rotation and jitter default to nothing"


def test_normalize_orders_keyframes_by_frame() -> None:
    stops = _stops(
        Keyframe(frame=9, x=30.0, y=30.0),
        Keyframe(frame=1, size=12, x=5.0, y=5.0),
    )

    assert [stop.frame for stop in stops] == [1, 9]
    assert stops[1].pose.size == 12, "the earlier keyframe is the one inherited from"


def test_normalize_needs_the_first_keyframe_to_state_a_pose() -> None:
    with pytest.raises(ValueError, match="first keyframe.*needs size, x and y"):
        _stops(Keyframe(frame=3, x=12.0), Keyframe(frame=1, size=10, y=4.0))


def test_normalize_refuses_an_empty_track() -> None:
    with pytest.raises(ValueError, match="track is empty"):
        _stops()


def test_normalize_refuses_a_repeated_frame() -> None:
    with pytest.raises(ValueError, match="frames \\[5\\] appear in more than one"):
        _stops(
            Keyframe(frame=1, size=10, x=1.0, y=1.0),
            Keyframe(frame=5, x=2.0),
            Keyframe(frame=5, x=3.0),
        )


def test_normalize_refuses_an_unknown_ease() -> None:
    with pytest.raises(ValueError, match="unknown ease 'bounce' on frame 5"):
        _stops(
            Keyframe(frame=1, size=10, x=1.0, y=1.0),
            Keyframe(frame=5, ease="bounce"),
        )


def test_frames_outside_the_track_hold_its_ends() -> None:
    keyframes = (
        Keyframe(frame=4, size=10, x=100.0, y=200.0),
        Keyframe(frame=8, size=20, x=200.0, y=200.0),
    )

    assert _pose_at(1, *keyframes) == _pose_at(4, *keyframes)
    assert _pose_at(99, *keyframes) == _pose_at(8, *keyframes)


def test_linear_moves_at_a_steady_rate() -> None:
    keyframes = (
        Keyframe(frame=1, size=10, x=0.0, y=0.0),
        Keyframe(frame=5, x=40.0, y=80.0),
    )

    quarter = _pose_at(2, *keyframes)
    middle = _pose_at(3, *keyframes)

    assert (middle.x, middle.y) == (20.0, 40.0)
    assert (round(quarter.x), round(quarter.y)) == (10, 20)


def test_size_is_whole_pixels() -> None:
    pose = _pose_at(
        2, Keyframe(frame=1, size=10, x=0.0, y=0.0), Keyframe(frame=3, size=15)
    )

    assert isinstance(pose.size, int)
    assert pose.size == 12


def test_smooth_starts_and_ends_slowly() -> None:
    keyframes = (
        Keyframe(frame=1, size=10, x=0.0, y=0.0),
        Keyframe(frame=11, x=100.0, ease="smooth"),
    )

    early = _pose_at(3, *keyframes).x
    middle = _pose_at(6, *keyframes).x

    assert early < 20.0, "less than a steady rate would give this early on"
    assert middle == 50.0, "the middle is still the middle"
    assert _pose_at(10, *keyframes).x > 80.0


def test_hold_keeps_the_previous_pose_until_the_keyframe_itself() -> None:
    keyframes = (
        Keyframe(frame=1, size=10, x=0.0, y=0.0),
        Keyframe(frame=5, size=40, x=100.0, ease="hold"),
    )

    assert _pose_at(4, *keyframes).x == 0.0
    assert _pose_at(5, *keyframes).x == 100.0, "the pose arrives all at once"


def test_each_easing_reaches_its_target_exactly() -> None:
    for ease in EASING:
        pose = _pose_at(
            4,
            Keyframe(frame=1, size=10, x=0.0, y=0.0),
            Keyframe(frame=4, x=100.0, ease=ease),
        )
        assert pose.x == 100.0, ease
        assert pose.size == 10


def test_easing_curves_run_from_nothing_to_everything() -> None:
    for ease, curve in EASING.items():
        assert curve(0.0) == 0.0, ease
        if ease != "hold":
            assert curve(1.0) == 1.0, ease


def test_ease_in_and_ease_out_mirror_each_other() -> None:
    keyframes = (
        Keyframe(frame=1, size=10, x=0.0, y=0.0),
        Keyframe(frame=11, x=100.0, ease="ease_in"),
    )
    mirrored = (
        Keyframe(frame=1, size=10, x=0.0, y=0.0),
        Keyframe(frame=11, x=100.0, ease="ease_out"),
    )

    assert _pose_at(6, *keyframes).x < _pose_at(6, *mirrored).x


def test_ease_in_out_is_gentle_at_both_ends() -> None:
    keyframes = (
        Keyframe(frame=1, size=10, x=0.0, y=0.0),
        Keyframe(frame=11, x=100.0, ease="ease_in_out"),
    )

    assert _pose_at(3, *keyframes).x < 20.0
    assert _pose_at(9, *keyframes).x > 80.0
    assert _pose_at(6, *keyframes).x == 50.0


def test_rotation_is_interpolated_as_written() -> None:
    pose = _pose_at(
        3,
        Keyframe(frame=1, size=10, x=0.0, y=0.0, rotate=0.0),
        Keyframe(frame=5, rotate=360.0),
    )

    assert pose.rotate == 180.0, "not the shortest way round"


def test_jitter_is_interpolated_with_the_pose() -> None:
    pose = _pose_at(
        6,
        Keyframe(frame=1, size=10, x=0.0, y=0.0, jitter=0.0),
        Keyframe(frame=11, jitter=8.0),
    )

    assert pose.jitter == pytest.approx(4.0)


def test_default_ease_is_linear() -> None:
    assert DEFAULT_EASE == "linear"
    assert Keyframe(frame=1).ease == DEFAULT_EASE


def test_a_window_covers_its_own_frames_and_no_others() -> None:
    window = Window(start=1, end=3)

    assert window.covers(1) and window.covers(3)
    assert not window.covers(4)


def test_a_window_with_no_end_runs_to_the_last_frame() -> None:
    assert Window(start=46).covers(46) and Window(start=46).covers(4000)


def test_no_windows_means_every_frame_is_drawn() -> None:
    assert visible((), 1) and visible((), 40)


def test_a_frame_is_drawn_when_any_window_covers_it() -> None:
    windows = (Window(start=20, end=35), Window(start=51, end=90))

    drawn = [frame for frame in (19, 20, 35, 36, 51, 90, 91) if visible(windows, frame)]
    assert drawn == [20, 35, 51, 90]


def test_a_window_that_ends_before_it_starts_covers_nothing() -> None:
    assert not visible((Window(start=9, end=4),), 5)
    assert not visible((Window(start=9, end=4),), 9)
