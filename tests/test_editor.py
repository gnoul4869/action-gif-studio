"""The studio's editing model: what a frame holds, and what saving writes.

The rules tested here are the ones that decide what a saved file looks like:
  * a frame's pose starts as what the track already resolves there
  * a recorded keyframe stores only what differs from that pose, so a track
    lists what moves and nothing else
  * the first keyframe is filled with what nothing earlier can supply
  * deleting the first keyframe hands its pose to the new first
  * easing belongs to a keyframe, so setting it creates one
  * saving is checked by the loader's own parser, so a saved file cannot be
    something that would fail to load
  * a file the loader would refuse still opens, so it can be repaired
  * hiding the avatar from a frame ends its visible range, and showing it from
    a later frame opens one again - the track is untouched by both
"""

from __future__ import annotations

import shutil
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from PIL import Image

from action_studio.definitions import load_actions
from action_studio.editor import TrackEditor
from action_studio.track import DEFAULT_EASE, EASING, Window

if TYPE_CHECKING:
    from pathlib import Path

GIF_FRAMES = 6


@pytest.fixture(scope="module")
def gif(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A small real animated gif, so frame limits are the real ones."""
    path = tmp_path_factory.mktemp("gifs") / "move.gif"
    frames = [
        Image.new("RGB", (60, 40), (20 * index, 60, 120)) for index in range(GIF_FRAMES)
    ]
    frames[0].save(
        path,
        save_all=True,
        append_images=frames[1:],
        duration=80,
        loop=0,
        format="GIF",
    )
    return path


def _editor(
    gif: Path,
    tmp_path: Path,
    document: dict[str, Any] | None = None,
) -> TrackEditor:
    """An editor over the fixture gif, with whatever track the test wants."""
    return TrackEditor(
        gif_path=gif,
        yaml_path=tmp_path / "kick" / "move.yaml",
        gif_size=(60, 40),
        frame_count=GIF_FRAMES,
        document=document if document is not None else {"action": "kick"},
    )


def _tracked() -> dict[str, Any]:
    """A track with a keyframe on frame 1 and one on frame 5."""
    return {
        "action": "kick",
        "gif": "move.gif",
        "track": [
            {"frame": 1, "size": 30, "x": 10.0, "y": 20.0},
            {"frame": 5, "size": 30, "x": 50.0, "y": 20.0, "ease": "smooth"},
        ],
    }


def test_an_empty_track_starts_with_a_centred_avatar(gif: Path, tmp_path: Path) -> None:
    editor = _editor(gif, tmp_path, document={"action": "kick"})

    assert editor.keyframes == {}
    assert editor.pose.size == 10, "a quarter of the gif's shorter edge"
    assert (editor.pose.x, editor.pose.y) == (30.0, 20.0), "centred"
    assert editor.stops() == ()


def test_opening_a_file_adopts_its_first_keyframe(gif: Path, tmp_path: Path) -> None:
    editor = _editor(gif, tmp_path, document=_tracked())

    assert editor.pose.size == 30
    assert editor.pose.x == 10.0
    assert editor.frame == 1


def test_moving_to_a_frame_adopts_the_pose_the_track_resolves(
    gif: Path, tmp_path: Path
) -> None:
    editor = _editor(gif, tmp_path, document=_tracked())

    pose = editor.go_to(5)

    assert pose.x == 50.0
    assert editor.frame == 5


def test_a_frame_outside_the_gif_is_clamped(gif: Path, tmp_path: Path) -> None:
    editor = _editor(gif, tmp_path)

    editor.go_to(GIF_FRAMES + 4)

    assert editor.frame == GIF_FRAMES


def test_a_pose_that_already_matches_the_track_has_nothing_to_record(
    gif: Path, tmp_path: Path
) -> None:
    editor = _editor(gif, tmp_path, document=_tracked())

    assert editor.record() == "nothing to record: this frame already holds this pose"
    assert editor.keyframes[1] == {"frame": 1, "size": 30, "x": 10.0, "y": 20.0}


def test_a_keyframe_stores_only_what_moved(gif: Path, tmp_path: Path) -> None:
    editor = _editor(gif, tmp_path, document=_tracked())
    editor.go_to(3)

    editor.set_pose(replace(editor.effective(3), x=25.0))
    editor.record()

    assert editor.keyframes[3] == {"frame": 3, "x": 25.0}, (
        "size, y and rotate carried over"
    )


def test_recording_twice_on_one_frame_keeps_both_changes(
    gif: Path, tmp_path: Path
) -> None:
    editor = _editor(gif, tmp_path, document=_tracked())
    editor.go_to(3)

    editor.set_pose(replace(editor.effective(3), x=25.0))
    editor.record()
    editor.set_pose(replace(editor.pose, size=40))
    editor.record()

    assert editor.keyframes[3] == {"frame": 3, "size": 40, "x": 25.0}, (
        "the second record adds to the entry instead of replacing it"
    )
    assert editor.validate() is None


def test_a_fractional_coordinate_is_written_to_one_decimal(
    gif: Path, tmp_path: Path
) -> None:
    editor = _editor(gif, tmp_path)

    editor.set_pose(replace(editor.effective(1), x=30.456))
    editor.record()

    assert editor.keyframes[1]["x"] == 30.5


def test_the_first_keyframe_carries_the_pose_it_starts_on(
    gif: Path, tmp_path: Path
) -> None:
    editor = _editor(gif, tmp_path)

    editor.set_pose(replace(editor.effective(1), size=18))
    editor.record()

    assert editor.keyframes[1] == {"frame": 1, "size": 18, "x": 30.0, "y": 20.0}
    assert editor.validate() is None, "nothing earlier to inherit the centre from"


def test_deleting_a_frame_without_a_keyframe_is_said_so(
    gif: Path, tmp_path: Path
) -> None:
    editor = _editor(gif, tmp_path, document=_tracked())
    editor.go_to(3)

    assert editor.delete() == "frame 3 has no keyframe"


def test_deleting_the_first_keyframe_hands_its_pose_to_the_new_first(
    gif: Path, tmp_path: Path
) -> None:
    editor = _editor(gif, tmp_path, document=_tracked())
    editor.go_to(1)

    assert editor.delete() is None

    first = editor.sorted_keyframes()[0]
    assert first["frame"] == 5
    assert first["size"] == 30 and first["x"] == 50.0, "still savable, not inheriting"
    assert editor.validate() is None


def test_a_first_keyframe_that_never_stated_a_pose_is_filled_in(
    gif: Path, tmp_path: Path
) -> None:
    editor = _editor(
        gif,
        tmp_path,
        document={"action": "kick", "track": [{"frame": 1, "size": 40}]},
    )

    first = editor.sorted_keyframes()[0]

    assert first["x"] == 30.0 and first["y"] == 20.0, "the centre it was drawn at"
    assert editor.validate() is None


def test_setting_an_ease_creates_the_keyframe_it_belongs_to(
    gif: Path, tmp_path: Path
) -> None:
    editor = _editor(gif, tmp_path)

    ease = editor.cycle_ease()

    assert ease == "smooth", "the next name after the default linear"
    assert editor.keyframes[1] == {"frame": 1, "ease": ease}
    assert editor.ease_of(1) == ease


def test_the_ease_cycles_through_every_name(gif: Path, tmp_path: Path) -> None:
    editor = _editor(gif, tmp_path, document=_tracked())

    seen = [editor.cycle_ease() for _ in range(len(EASING))]

    assert seen[0] == "smooth"
    assert len(set(seen)) == len(EASING), "every name is offered, then it wraps"


def test_a_frame_without_a_keyframe_reports_the_pending_ease(
    gif: Path, tmp_path: Path
) -> None:
    editor = _editor(gif, tmp_path, document=_tracked())
    editor.go_to(3)

    assert editor.ease_of(3) == DEFAULT_EASE


def test_a_broken_track_still_opens_and_is_explained_on_save(
    gif: Path, tmp_path: Path
) -> None:
    editor = _editor(
        gif,
        tmp_path,
        document={
            "action": "kick",
            "track": [{"frame": 1, "size": 30, "x": 10.0, "y": 20.0, "ease": "bouncy"}],
        },
    )

    assert editor.pose.size == 10, "the start pose, since nothing resolves"
    assert editor.effective(3).size == 10
    with pytest.raises(ValueError, match="unknown ease"):
        editor.stops()
    assert "unknown ease" in editor.validate()


def test_a_keyframe_past_the_gif_is_found_when_saving(
    gif: Path, tmp_path: Path
) -> None:
    editor = _editor(
        gif,
        tmp_path,
        document={
            "action": "kick",
            "track": [{"frame": 99, "size": 30, "x": 1, "y": 2}],
        },
    )

    assert f"frame must be <= {GIF_FRAMES}" in editor.validate()


def test_the_action_name_comes_from_the_file_then_the_directory(
    gif: Path, tmp_path: Path
) -> None:
    assert _editor(gif, tmp_path, document={"action": "Bonk"}).action_name() == "bonk"
    assert _editor(gif, tmp_path, document={}).action_name() == gif.parent.name.lower()


def test_a_key_the_loader_does_not_read_is_neither_named_nor_written(
    gif: Path, tmp_path: Path
) -> None:
    editor = _editor(gif, tmp_path, document={"name": "Slap", "notes": "todo"})

    assert editor.action_name() == gif.parent.name.lower()
    assert "name" not in editor.draft()
    assert "notes" not in editor.draft()
    assert editor.draft()["action"] == gif.parent.name.lower()


def test_the_draft_keeps_only_what_the_loader_reads(gif: Path, tmp_path: Path) -> None:
    editor = _editor(
        gif,
        tmp_path,
        document={"action": "kick", "message": "kicked {name}", "notes": "todo"},
    )

    draft = editor.draft()

    assert "notes" not in draft
    assert draft["message"] == "kicked {name}"
    assert draft["gif"] == gif.as_posix()
    assert draft["track"] == editor.sorted_keyframes()


def test_a_file_that_never_stated_a_message_gets_one_that_names_the_target(
    gif: Path, tmp_path: Path
) -> None:
    editor = _editor(gif, tmp_path, document={"action": "kick"})

    assert editor.draft()["message"] == "kick {name}"
    assert "needs a 'track'" in editor.validate(), "an empty track is not an action"

    editor.set_pose(replace(editor.effective(1), size=18))
    editor.record()
    assert editor.validate() is None


def test_saving_writes_the_file_and_rereads_it(gif: Path, tmp_path: Path) -> None:
    editor = _editor(gif, tmp_path, document=_tracked())
    editor.go_to(3)
    editor.set_pose(replace(editor.effective(3), x=25.0))
    editor.record()

    path, count = editor.save()

    assert path == tmp_path / "kick" / "move.yaml"
    assert path.is_file(), "the parent directory was created"
    assert count == 3

    written = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert [entry["frame"] for entry in written["track"]] == [1, 3, 5]
    assert written["track"][1] == {"frame": 3, "x": 25.0}, "only what moved"
    assert editor.document == written

    result = load_actions(path.parent)
    assert [action.name for action in result.actions] == ["kick"]
    assert result.problems == ()
    assert [stop.frame for stop in result.actions[0].variants[0].stops] == [1, 3, 5]


def test_saving_repeats_what_the_loader_would_refuse(gif: Path, tmp_path: Path) -> None:
    editor = _editor(
        gif, tmp_path, document={"action": "kick", "message": "no placeholder"}
    )

    result = editor.save()

    assert isinstance(result, str)
    assert "message needs {name}" in result
    assert not (tmp_path / "kick" / "move.yaml").exists()


def _project(tmp_path: Path, gif: Path) -> tuple[Path, Path]:
    """A gif and a definition in the two-tree layout this tool suggests."""
    root = tmp_path / "project"
    gif_target = root / "assets" / "gifs" / "kick" / gif.name
    gif_target.parent.mkdir(parents=True)
    shutil.copyfile(gif, gif_target)
    yaml_target = root / "data" / "actions" / "kick" / f"{gif.stem}.yaml"
    return gif_target, yaml_target


def test_a_definition_in_a_project_names_its_gif_from_the_project_root(
    tmp_path: Path, gif: Path
) -> None:
    """The conventional layout, and the only one worth writing relative.

    A loader resolves a relative ``gif`` from its working directory and from the
    definition's ancestors, and a project root is the single directory both of
    those agree on, so a definition inside the project is portable and readable.
    """
    gif_target, yaml_target = _project(tmp_path, gif)
    editor = TrackEditor(
        gif_path=gif_target,
        yaml_path=yaml_target,
        gif_size=(60, 40),
        frame_count=GIF_FRAMES,
        document={"action": "kick"},
    )
    editor.set_pose(replace(editor.pose, size=30, x=10.0, y=20.0))
    editor.record()

    result = editor.save()

    assert isinstance(result, tuple)
    written = yaml.safe_load(yaml_target.read_text(encoding="utf-8"))
    assert written["gif"] == f"assets/gifs/kick/{gif.name}"
    loaded = load_actions(yaml_target.parent)
    assert loaded.problems == ()
    assert loaded.actions[0].variants[0].gif.path == gif_target


def test_a_definition_written_outside_the_gifs_project_keeps_the_full_path(
    tmp_path: Path, gif: Path
) -> None:
    """An export anywhere else has no shared root, so only the full path finds it."""
    editor = _editor(gif, tmp_path)

    assert editor.gif_reference() == gif.as_posix()


# --- Visibility windows ---------------------------------------------------


def test_hiding_from_a_frame_ends_the_visible_range(gif: Path, tmp_path: Path) -> None:
    editor = _editor(gif, tmp_path, document=_tracked())
    editor.go_to(4)

    assert editor.toggle_hidden() == "hidden from frame 4"
    assert editor.visible(3) and not editor.visible(4)


def test_hiding_frame_one_is_refused_because_nothing_precedes_it(
    gif: Path, tmp_path: Path
) -> None:
    editor = _editor(gif, tmp_path, document=_tracked())

    assert "nothing before it to keep" in editor.toggle_hidden()
    assert editor.windows == []


def test_showing_from_a_later_frame_opens_a_range_again(
    gif: Path, tmp_path: Path
) -> None:
    editor = _editor(gif, tmp_path, document=_tracked())
    editor.go_to(4)
    editor.toggle_hidden()
    editor.go_to(6)

    assert editor.toggle_hidden() == "visible from frame 6"
    assert editor.visible(3) and not editor.visible(4) and editor.visible(6)


def test_a_gap_closed_on_the_spot_needs_no_second_range(
    gif: Path, tmp_path: Path
) -> None:
    editor = _editor(gif, tmp_path, document=_tracked())
    editor.go_to(4)
    editor.toggle_hidden()
    editor.go_to(4)
    editor.toggle_hidden()

    assert editor.visible(1) and editor.visible(3) and editor.visible(6)
    assert editor.window_documents() == [{"from": 1}]


def test_hiding_then_showing_leaves_a_gap_between_two_ranges(
    gif: Path, tmp_path: Path
) -> None:
    editor = _editor(gif, tmp_path, document=_tracked())
    editor.go_to(2)
    editor.toggle_hidden()
    editor.go_to(4)
    editor.toggle_hidden()

    assert editor.visible(1) and not editor.visible(2) and editor.visible(4)
    assert editor.window_documents() == [{"from": 1, "to": 1}, {"from": 4}]


def test_hiding_the_avatar_leaves_the_track_alone(gif: Path, tmp_path: Path) -> None:
    editor = _editor(gif, tmp_path, document=_tracked())
    before = editor.sorted_keyframes()
    editor.go_to(4)
    editor.toggle_hidden()

    assert editor.sorted_keyframes() == before, "hiding does not move the avatar"


def test_opening_a_file_keeps_its_windows(gif: Path, tmp_path: Path) -> None:
    editor = _editor(
        gif, tmp_path, document={**_tracked(), "show": [{"from": 1, "to": 3}]}
    )

    assert editor.visible(2) and not editor.visible(4)
    assert editor.window_documents() == [{"from": 1, "to": 3}]


def test_saving_writes_the_windows_along_with_the_track(
    gif: Path, tmp_path: Path
) -> None:
    editor = _editor(gif, tmp_path, document=_tracked())
    editor.go_to(4)
    editor.toggle_hidden()

    path, _count = editor.save()

    written = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert list(written) == ["action", "message", "gif", "show", "track"]
    assert written["show"] == [{"from": 1, "to": 3}]

    result = load_actions(path.parent)
    assert result.problems == ()
    assert result.actions[0].variants[0].show == (Window(start=1, end=3),)
