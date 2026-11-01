"""Tests for the command line and the parts of the window that are arithmetic.

The window itself is not driven here: it needs a display, and a Python built
without Tk is skipped wholesale by ``conftest.py``. What is tested is the scale a
gif is shown at, where a definition is suggested to go, which directory a gif
belongs to, and what the command line accepts — the decisions a user sees.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from action_studio import project_root, suggested_yaml_path
from action_studio.__main__ import main
from action_studio.ui import CANVAS_HEIGHT, CANVAS_WIDTH, display_scale


def test_a_gif_smaller_than_the_canvas_is_shown_at_its_own_pixels() -> None:
    assert display_scale((400, 300)) == 1.0


def test_a_gif_larger_than_the_canvas_is_fitted_into_it() -> None:
    scale = display_scale((2 * CANVAS_WIDTH, CANVAS_HEIGHT))
    assert scale == pytest.approx(0.5)


def test_zoom_replaces_the_fit() -> None:
    assert display_scale((400, 300), 4.0) == 4.0


def test_a_gif_inside_a_project_is_written_where_a_bot_looks(tmp_path: Path) -> None:
    (tmp_path / "data" / "actions").mkdir(parents=True)
    gif = tmp_path / "assets" / "gifs" / "kick"
    gif.mkdir(parents=True)
    target = gif / "genos_kick.gif"

    assert suggested_yaml_path(target) == (
        tmp_path / "data" / "actions" / "kick" / "genos_kick.yaml"
    )


def test_a_gif_in_no_project_is_written_beside_itself(tmp_path: Path) -> None:
    assert suggested_yaml_path(tmp_path / "punch.gif") == tmp_path / "punch.yaml"


def test_only_a_project_layout_names_a_project(tmp_path: Path) -> None:
    gif = tmp_path / "kick" / "genos_kick.gif"
    assert project_root(gif) is None

    (tmp_path / "assets" / "gifs").mkdir(parents=True)
    assert project_root(gif) == tmp_path


def test_a_gif_that_is_not_there_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(Path("nowhere.gif") / "missing.gif")]) == 1
    assert "is not a file" in capsys.readouterr().err


def test_the_help_names_the_keys_and_the_files(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["action-gif-studio", "--help"])
    with pytest.raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == 0


def test_the_version_flag_reports_a_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["action-gif-studio", "--version"])
    with pytest.raises(SystemExit) as exit_info:
        main()

    assert exit_info.value.code == 0
