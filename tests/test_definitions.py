"""Action definitions: the YAML in ``data/actions/``, parsed and measured.

One file per gif:
  * a file naming an action, a gif, and a track becomes one command's variant
  * ``description`` and ``message`` fall back to something usable as text
  * a file may hold several gifs under ``variants``, each with its own message
  * files are merged by action name, so two gifs can share ``/kick``
  * the actions come back sorted by name, with per-file counts

What is refused:
  * names outside the allowed shape, and a file with no name
  * a message that cannot name the target
  * a missing gif, a still image, and a gif path that does not resolve
  * an empty track, a frame outside the gif's own frames, a negative size
  * a value that is not a number, including ``true``, which YAML happily reads
    as one
  * an unknown key anywhere, so a typo is said rather than ignored

Visibility windows:
  * ``show`` lists the frames the avatar is drawn on; no ``show`` means all of
    them, and a range with no ``to`` runs to the gif's last frame
  * a range outside the gif's frames, one ending before it starts, an empty
    list, and a key that is neither ``from`` nor ``to`` are refused
  * ``show`` is written before ``track`` and left out when nothing is hidden

What survives a broken file:
  * a bad file is reported and skipped; the rest of the directory still loads
  * a file that is not a mapping, or holds no actions, is reported too
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from PIL import Image

from action_studio.definitions import (
    DEFAULT_MESSAGE,
    format_action,
    load_actions,
    parse_action,
    parse_actions,
)
from action_studio.track import Keyframe, Window

GIF_FRAMES = 6
DESCRIPTION_SUFFIX = " action gif on another member"


@pytest.fixture(scope="module")
def gif(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A small real animated gif, so measuring it is the real thing."""
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


def _definition(gif_path: Path, **fields: Any) -> dict[str, Any]:
    """A minimal valid definition, with whatever the test wants to change."""
    raw: dict[str, Any] = {
        "action": "kick",
        "gif": gif_path.as_posix(),
        "track": [{"frame": 1, "size": 30, "x": 10.0, "y": 20.0}],
    }
    raw.update(fields)
    return raw


def _source() -> Path:
    return Path("data/actions/kick/test.yaml")


def _written(tmp_path: Path, name: str, raw: dict[str, Any]) -> Path:
    """A definition as a file on disk, for the loader to find."""
    path = tmp_path / name
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


def test_a_file_becomes_one_action_with_one_variant(gif: Path) -> None:
    action = parse_action(_definition(gif), _source())

    assert action.name == "kick"
    assert len(action.variants) == 1
    assert action.variants[0].gif.frames == GIF_FRAMES
    assert action.variants[0].gif.width == 60
    assert action.description == f"kick{DESCRIPTION_SUFFIX}"
    assert action.variants[0].message == DEFAULT_MESSAGE


def test_the_description_and_message_a_author_gives_are_used(gif: Path) -> None:
    action = parse_action(
        _definition(gif, description="Kick someone", message="kicked {name}"), _source()
    )

    assert action.description == "Kick someone"
    assert action.variants[0].message == "kicked {name}"


def test_a_file_may_carry_several_gifs(gif: Path, tmp_path: Path) -> None:
    other = tmp_path / "second.gif"
    other.write_bytes(gif.read_bytes())
    track = [{"frame": 1, "size": 30, "x": 10.0, "y": 20.0}]

    action = parse_action(
        {
            "action": "kick",
            "variants": [
                {"gif": gif.as_posix(), "message": "first {name}", "track": track},
                {"gif": other.as_posix(), "message": "second {name}", "track": track},
            ],
        },
        _source(),
    )

    assert len(action.variants) == 2
    assert [variant.message for variant in action.variants] == [
        "first {name}",
        "second {name}",
    ]


def test_a_key_beyond_the_format_is_refused(gif: Path) -> None:
    raw = _definition(gif)
    del raw["action"]
    raw["name"] = "Bonk"

    with pytest.raises(ValueError, match="unknown key"):
        parse_action(raw, _source())


def test_the_track_is_normalized_into_stops(gif: Path) -> None:
    action = parse_action(
        _definition(
            gif,
            track=[
                {"frame": 1, "size": 30, "x": 10.0, "y": 20.0},
                {"frame": 4, "size": 50},
            ],
        ),
        _source(),
    )

    stops = action.variants[0].stops
    assert [stop.frame for stop in stops] == [1, 4]
    assert stops[1].pose.size == 50
    assert stops[1].pose.x == 10.0


@pytest.mark.parametrize(
    "name", ["kick!", "1kick", "a", "Kick My Friend", "kick-space"]
)
def test_names_outside_the_allowed_shape_are_refused(gif: Path, name: str) -> None:
    with pytest.raises(ValueError, match="must be 2-32 characters"):
        parse_action(_definition(gif, action=name), _source())


def test_a_file_without_a_name_is_refused(gif: Path) -> None:
    raw = _definition(gif)
    del raw["action"]

    with pytest.raises(ValueError, match="needs an 'action' name"):
        parse_action(raw, _source())


def test_an_unknown_action_key_is_named(gif: Path) -> None:
    with pytest.raises(ValueError, match="unknown key\\(s\\) notes"):
        parse_action(_definition(gif, notes="remember to fix"), _source())


def test_an_unknown_keyframe_key_is_named(gif: Path) -> None:
    with pytest.raises(ValueError, match="keyframe has unknown key\\(s\\) degress"):
        parse_action(
            _definition(
                gif, track=[{"frame": 1, "size": 30, "x": 1, "y": 2, "degress": 3}]
            ),
            _source(),
        )


def test_a_message_that_cannot_name_the_target_is_refused(gif: Path) -> None:
    with pytest.raises(ValueError, match=r"message needs \{name\}"):
        parse_action(_definition(gif, message="someone got kicked"), _source())


def test_a_missing_gif_path_is_refused(gif: Path) -> None:
    raw = _definition(gif)
    del raw["gif"]

    with pytest.raises(ValueError, match="'kick' needs a 'gif' path"):
        parse_action(raw, _source())


def test_a_gif_that_is_not_on_disk_is_said_so(gif: Path) -> None:
    with pytest.raises(ValueError, match="gif .* was not found"):
        parse_action(_definition(gif, gif="assets/gifs/nope.gif"), _source())


def test_a_still_image_is_not_an_action_gif(gif: Path, tmp_path: Path) -> None:
    still = tmp_path / "still.gif"
    Image.new("RGB", (40, 30), (10, 20, 30)).save(still, format="GIF")

    with pytest.raises(ValueError, match="still image, not an animated gif"):
        parse_action(_definition(gif, gif=still.as_posix()), _source())


def test_an_empty_track_is_refused(gif: Path) -> None:
    with pytest.raises(ValueError, match="needs a 'track' list of keyframes"):
        parse_action(_definition(gif, track=[]), _source())


def test_a_keyframe_past_the_gif_is_refused(gif: Path) -> None:
    with pytest.raises(ValueError, match=f"frame must be <= {GIF_FRAMES}"):
        parse_action(
            _definition(
                gif, track=[{"frame": GIF_FRAMES + 1, "size": 30, "x": 1, "y": 2}]
            ),
            _source(),
        )


def test_a_keyframe_before_the_first_frame_is_refused(gif: Path) -> None:
    with pytest.raises(ValueError, match="frame must be >= 1"):
        parse_action(
            _definition(gif, track=[{"frame": 0, "size": 30, "x": 1, "y": 2}]),
            _source(),
        )


def test_size_may_be_zero_to_hide_the_avatar(gif: Path) -> None:
    action = parse_action(
        _definition(gif, track=[{"frame": 1, "size": 0, "x": 10.0, "y": 20.0}]),
        _source(),
    )

    assert action.variants[0].stops[0].pose.size == 0


def test_a_negative_size_is_refused(gif: Path) -> None:
    with pytest.raises(ValueError, match="size must be >= 0"):
        parse_action(
            _definition(gif, track=[{"frame": 1, "size": -1, "x": 1, "y": 2}]),
            _source(),
        )


@pytest.mark.parametrize("value", ["big", [1], True])
def test_a_value_that_is_not_a_number_is_refused(gif: Path, value: Any) -> None:
    with pytest.raises(TypeError, match="size must be a number"):
        parse_action(
            _definition(gif, track=[{"frame": 1, "size": value, "x": 1, "y": 2}]),
            _source(),
        )


def test_an_unknown_ease_is_named(gif: Path) -> None:
    with pytest.raises(ValueError, match="unknown ease 'bouncy'"):
        parse_action(
            _definition(
                gif, track=[{"frame": 1, "size": 1, "x": 1, "y": 2, "ease": "bouncy"}]
            ),
            _source(),
        )


def test_a_keyframe_that_is_not_a_mapping_is_refused(gif: Path) -> None:
    with pytest.raises(TypeError, match="keyframe needs a mapping"):
        parse_action(_definition(gif, track=["frame: 1"]), _source())


def test_a_file_may_hold_several_actions(gif: Path) -> None:
    actions = parse_actions(
        [_definition(gif, action="kick"), _definition(gif, action="slap")], _source()
    )

    assert [action.name for action in actions] == ["kick", "slap"]


def test_a_file_that_is_neither_a_mapping_nor_a_list_is_refused(gif: Path) -> None:
    with pytest.raises(TypeError, match="expected a mapping or a list of mappings"):
        parse_actions("kick", _source())


def test_the_loader_merges_files_by_action_name(gif: Path, tmp_path: Path) -> None:
    other = tmp_path / "second.gif"
    other.write_bytes(gif.read_bytes())
    _written(tmp_path, "one.yaml", _definition(gif))
    _written(
        tmp_path,
        "two.yaml",
        {
            "action": "kick",
            "gif": other.as_posix(),
            "track": [{"frame": 2, "size": 20, "x": 5.0, "y": 5.0}],
        },
    )
    _written(tmp_path, "other.yaml", _definition(gif, action="slap"))

    result = load_actions(tmp_path)

    assert [action.name for action in result.actions] == ["kick", "slap"]
    assert len(result.actions[0].variants) == 2, "both gifs belong to /kick"
    assert len(result.actions[1].variants) == 1
    assert [path.name for path in result.loaded] == [
        "one.yaml",
        "other.yaml",
        "two.yaml",
    ], "every file that defined something is listed"


def test_the_loader_skips_a_broken_file_and_keeps_the_rest(
    gif: Path, tmp_path: Path
) -> None:
    _written(tmp_path, "good.yaml", _definition(gif))
    _written(tmp_path, "bad.yaml", _definition(gif, action="nope!"))
    _written(tmp_path, "empty.yaml", [])

    result = load_actions(tmp_path)

    assert [action.name for action in result.actions] == ["kick"]
    assert len(result.problems) == 2
    assert any("must be 2-32 characters" in problem for problem in result.problems)
    assert any("no actions defined" in problem for problem in result.problems)


def test_a_file_that_is_not_yaml_at_all_is_reported(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("action: kick\n  bad indent: [", encoding="utf-8")

    result = load_actions(tmp_path)

    assert result.actions == ()
    assert len(result.problems) == 1


def test_a_directory_that_is_not_there_loads_nothing(tmp_path: Path) -> None:
    result = load_actions(tmp_path / "actions")

    assert result.actions == ()
    assert result.loaded == ()
    assert result.problems == ()


def test_the_loader_reads_files_in_subdirectories(gif: Path, tmp_path: Path) -> None:
    nested = tmp_path / "kick"
    nested.mkdir()
    _written(nested, "one.yaml", _definition(gif))

    assert [action.name for action in load_actions(tmp_path).actions] == ["kick"]


def test_format_action_writes_a_file_the_loader_accepts(
    gif: Path, tmp_path: Path
) -> None:
    text = format_action(
        "kick",
        gif,
        (
            Keyframe(frame=1, size=30, x=10.0, y=20.0),
            Keyframe(frame=3, size=50),
            Keyframe(frame=5, x=20.0, ease="ease_out"),
        ),
        description="Kick someone",
        message="kicked {name}",
        root=gif.parent,
    )

    assert yaml.safe_load(text)["gif"] == "move.gif", "written relative, not absolute"

    document = yaml.safe_load(text)
    document["gif"] = gif.as_posix()
    action = parse_action(document, _source())
    assert [stop.frame for stop in action.variants[0].stops] == [1, 3, 5]
    assert action.variants[0].stops[2].pose.size == 50, "size carries forward"
    assert action.description == "Kick someone"


def test_format_action_writes_only_what_changes(gif: Path) -> None:
    written = format_action(
        "kick",
        gif,
        (
            Keyframe(frame=1, size=30, x=10.0, y=20.0),
            Keyframe(frame=2, size=30, x=10.0, y=20.0),
            Keyframe(frame=3, size=30, x=10.0, y=20.0, ease="linear"),
        ),
    )

    track = yaml.safe_load(written)["track"]

    assert track[1] == {"frame": 2}, "an unchanged pose is an empty keyframe"
    assert track[2] == {"frame": 3}, "the default ease is not written out"


def test_a_relative_gif_is_found_through_the_definitions_own_ancestors(
    tmp_path: Path, gif: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Definitions arrive from wherever they were authored, not from one depth.

    A relative path is tried against every ancestor of the file rather than a
    guessed fixed depth, so a correctly written definition is not read as a
    missing gif just because the loader was started from somewhere else.
    """
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    root = tmp_path / "project"
    target = root / "assets" / "gifs" / "kick" / gif.name
    target.parent.mkdir(parents=True)
    shutil.copyfile(gif, target)
    definition = root / "data" / "actions" / "kick" / "test.yaml"
    definition.parent.mkdir(parents=True)
    definition.write_text(
        yaml.safe_dump(
            {
                "action": "kick",
                "gif": f"assets/gifs/kick/{gif.name}",
                "track": [{"frame": 1, "size": 30, "x": 10.0, "y": 10.0}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    result = load_actions(root / "data" / "actions")

    assert result.problems == ()
    assert result.actions[0].variants[0].gif.path == target


# --- Visibility windows ---------------------------------------------------


def test_show_lists_the_frames_the_avatar_is_drawn_on(gif: Path) -> None:
    action = parse_action(_definition(gif, show=[{"from": 1, "to": 5}]), _source())

    assert action.variants[0].show == (Window(start=1, end=5),)


def test_a_file_without_show_draws_every_frame(gif: Path) -> None:
    assert parse_action(_definition(gif), _source()).variants[0].show == ()


def test_a_show_range_with_no_to_runs_to_the_last_frame(gif: Path) -> None:
    action = parse_action(_definition(gif, show=[{"from": 4}]), _source())

    assert action.variants[0].show == (Window(start=4),)


def test_show_ranges_are_kept_in_frame_order(gif: Path) -> None:
    action = parse_action(
        _definition(gif, show=[{"from": 4, "to": 6}, {"from": 1, "to": 2}]), _source()
    )

    assert action.variants[0].show == (
        Window(start=1, end=2),
        Window(start=4, end=6),
    )


def test_a_show_range_past_the_gifs_frames_is_refused(gif: Path) -> None:
    with pytest.raises(ValueError, match="show range to must be <= 6"):
        parse_action(
            _definition(gif, show=[{"from": 1, "to": GIF_FRAMES + 1}]), _source()
        )


def test_a_show_range_ending_before_it_starts_is_refused(gif: Path) -> None:
    with pytest.raises(ValueError, match="show range to must be >= 4"):
        parse_action(_definition(gif, show=[{"from": 4, "to": 2}]), _source())


def test_a_show_range_without_a_from_is_refused(gif: Path) -> None:
    with pytest.raises(ValueError, match="needs a 'from' frame"):
        parse_action(_definition(gif, show=[{"to": 3}]), _source())


def test_an_empty_show_is_refused_rather_than_hiding_everything(gif: Path) -> None:
    with pytest.raises(ValueError, match="needs a list"):
        parse_action(_definition(gif, show=[]), _source())


def test_a_show_key_that_is_neither_from_nor_to_is_refused(gif: Path) -> None:
    with pytest.raises(ValueError, match="unknown key"):
        parse_action(_definition(gif, show=[{"from": 1, "until": 3}]), _source())


def test_a_variant_can_narrow_the_frames_it_is_drawn_on(
    gif: Path, tmp_path: Path
) -> None:
    other = tmp_path / "second.gif"
    other.write_bytes(gif.read_bytes())
    track = [{"frame": 1, "size": 30, "x": 10.0, "y": 20.0}]

    action = parse_action(
        {
            "action": "kick",
            "variants": [
                {"gif": gif.as_posix(), "track": track, "show": [{"from": 1, "to": 3}]},
                {"gif": other.as_posix(), "track": track},
            ],
        },
        _source(),
    )

    assert action.variants[0].show == (Window(start=1, end=3),)
    assert action.variants[1].show == (), "the other gif keeps every frame"


def test_show_is_written_before_the_track(tmp_path: Path, gif: Path) -> None:
    text = format_action(
        "kick",
        gif,
        [Keyframe(1, 30, 10.0, 20.0)],
        show=(Window(start=1, end=3),),
        root=tmp_path,
    )

    document = yaml.safe_load(text)
    assert list(document) == ["action", "message", "gif", "show", "track"]
    assert document["show"] == [{"from": 1, "to": 3}]


def test_show_is_left_out_when_every_frame_is_drawn(tmp_path: Path, gif: Path) -> None:
    text = format_action("kick", gif, [Keyframe(1, 30, 10.0, 20.0)], root=tmp_path)

    assert "show" not in yaml.safe_load(text)


def test_written_show_survives_the_round_trip(tmp_path: Path, gif: Path) -> None:
    windows = (Window(start=1, end=3), Window(start=5))
    text = format_action(
        "kick",
        gif,
        [Keyframe(1, 30, 10.0, 20.0)],
        show=windows,
        root=tmp_path,
    )

    parsed = parse_action(yaml.safe_load(text), tmp_path / "kick.yaml")

    assert parsed.variants[0].show == windows
