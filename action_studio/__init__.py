"""Action GIF Studio — author the keyframed tracks that drive an action gif.

An action gif is an ordinary animated gif of a character doing something — a
kick, a hug, an arrest — onto which whatever plays the result composites the
*target's* avatar while the gif runs, so the person being acted on rides the
motion without anyone drawing a new gif. What this tool authors is the **track**:
where the avatar sits, how large, on which frames — written out as a small YAML
file that a player loads.

    python -m action_studio              # open the window, browse for a gif
    python -m action_studio kick.gif     # or name the gif on the command line

Place the avatar with a click instead of reading coordinates off a canvas, step
through the frames, record a keyframe wherever the motion changes, and export the
YAML. A keyframe stores only what moved, so a four-frame punch is four lines and
not one row per frame.

The modules, in the order someone reading this package should read them:

* :mod:`~action_studio.__main__` — the command line, and nothing else.
* :mod:`~action_studio.ui` — the window: what a frame looks like, and the keys
  that change it.
* :mod:`~action_studio.editor` — what a keyframe records and what an exported
  file contains.
* :mod:`~action_studio.definitions` — the YAML format, and the parser that reads it
  back.
* :mod:`~action_studio.track` — keyframes and the easing between them.
* :mod:`~action_studio.render` — compositing the avatar onto the gif's frames.
* :mod:`~action_studio.crop` — the avatar as a disc.

The tool needs Pillow, PyYAML and Tkinter and nothing else: no token, no server,
no loader, no renderer, no client of any kind. An exported file is handed through
:func:`~action_studio.definitions.parse_action` before it is written, so a track
cannot be exported in a form that would fail to load.

J.A.D.A.I <https://github.com/gnoul4869/J.A.D.A.I-v2> is one program that reads
these files — it turns each definition into a slash command and an LLM-callable
action — and it keeps its own copy of the loader. Nothing here depends on it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

__version__ = "0.1.0"

# The user-facing help: the epilog of ``--help``, and what the Help menu prints.
HELP = """Keys
    Left / Right      step one frame (Shift: ten)
    Up / Down         avatar 5px larger / smaller (Shift: 1px)
    [ / ]             jitter down / up one pixel
    r / R             rotate +5 / -5 degrees
    click             put the avatar's centre where you clicked
    mouse wheel       size by 5 (Shift: rotate by 5)
    k                 record a keyframe for this frame
    d                 delete this frame's keyframe
    e                 cycle this frame's easing
    c                 re-read the pose the track already has here
    v                 hide the avatar from this frame, or bring it back from here
    space             play / pause the composited gif
    s                 write the YAML to the current target
    Ctrl+O            browse for a gif
    Ctrl+S            write the YAML    Ctrl+Shift+S: choose where first
    Ctrl+Q / q        quit
    h                 print the key list

A keyframe stores only what moved: recording a frame that merely changed position
saves its x and y and nothing else, and the frames between two keyframes are
interpolated. Easing shapes the approach to a keyframe: `ease_in` leaves slowly
and lands fast, `ease_out` lands slowly, `smooth` and `ease_in_out` slow both
ends, `hold` does not interpolate at all, `linear` is the default. `[` and `]` set
the shake: a random offset of up to that many gif pixels per drawn frame, rolled
afresh when the gif is sent, so the preview itself stays still. `v` writes the
optional `show:` ranges, which are the frames the avatar exists on at all — hiding
it never moves it, and the track keeps running behind the gap. Exporting refuses to
write a file that would fail to load.
"""


def project_root(path: Path) -> Path | None:
    """The project directory ``path`` belongs to, when its layout says which.

    A program that keeps definitions under ``<root>/data/actions/`` and gifs
    under ``<root>/assets/gifs/`` names the same root from both, and that root is
    what a relative ``gif`` path in a definition is resolved against. Nothing
    outside such a tree is guessed at: an absolute path is honest where a relative
    one would be a lie.

    Args:
        path: A gif or definition file to locate the project of.

    Returns:
        The nearest ancestor holding ``data/actions`` or ``assets/gifs``, or
        ``None`` when the file sits in no such project.
    """
    for parent in path.resolve().parents:
        actions = (parent / "data" / "actions").is_dir()
        gifs = (parent / "assets" / "gifs").is_dir()
        if actions or gifs:
            return parent
    return None


def suggested_yaml_path(gif_path: Path) -> Path:
    """Where this gif's definition would go by default.

    ``<root>/data/actions/<gif folder>/<gif stem>.yaml`` when the gif sits inside
    a project, otherwise a file beside the gif itself.

    Only a starting point for the export dialog — a track can be written anywhere,
    and a program that reads a folder of definitions treats the folder as the
    action name when the file does not name itself.

    Args:
        gif_path: The gif being authored.

    Returns:
        The conventional path inside the gif's project, or a file beside the gif
        when the gif belongs to no such project.
    """
    root = project_root(gif_path)
    if root is None:
        return gif_path.parent / f"{gif_path.stem}.yaml"
    return root / "data" / "actions" / gif_path.parent.name / f"{gif_path.stem}.yaml"
