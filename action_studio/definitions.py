"""Action definitions — the YAML file this tool writes.

One file describes one gif and how the avatar rides over it. Files may be
organised however the author likes, and a program that reads a folder of them
recursively can merge by action name, so two files in one folder describe two
gifs of the same action and picking one at random per use is what keeps a command
from repeating itself.

The format is deliberately small, because a new action should be one file:

.. code-block:: yaml

    action: kick                      # the name a player uses
    description: Kick someone across the room
    message: "{name} got kicked into the stratosphere"
    gif: assets/gifs/kick/railgun_kick.gif
    show:                             # optional: the frames the avatar is on
      - {from: 1, to: 9}
    track:                            # keyframes, not one row per frame
      - {frame: 1, size: 60, x: 191, y: 202}
      - {frame: 5, size: 74, x: 322, y: 148, ease: ease_out}
      - {frame: 9, size: 58, x: 96, y: 214, jitter: 4, ease: ease_in}

Every field on a keyframe except ``frame`` is optional and inherits the
previous keyframe's value. ``show`` is a list of frame ranges the avatar is
drawn on, inclusive at both ends, with ``to`` meaning "to the last frame" when
left out; without it the avatar is drawn on every frame. ``gif`` may be relative
to the reader's working directory or absolute.
``message`` needs ``{name}``, which a player fills with whoever is being acted on.

This is the format :class:`~action_studio.editor.TrackEditor` writes, and
:func:`parse_action` is the same reader applied on save: the editor hands a file
through it before it is allowed to write it, so an exported track cannot be a
file that fails to load.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from action_studio.render import GifInfo, inspect_gif
from action_studio.track import (
    DEFAULT_EASE,
    EASING,
    Keyframe,
    Stop,
    Window,
    normalize,
)

# Command-name rules a chat platform accepts: lowercase, digits, underscores, at most
# 32 characters, and it cannot start with a digit.
_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,31}$")

# The target's mention is interpolated into the message, so a message without it
# would send an action aimed at nobody.
_MESSAGE_PLACEHOLDER = "{name}"

DEFAULT_MESSAGE = "{name} was hit by an action"
DEFAULT_DESCRIPTION_SUFFIX = " action gif on another member"

_ACTION_KEYS = frozenset(
    {"action", "description", "message", "gif", "show", "track", "variants"}
)
_VARIANT_KEYS = frozenset({"gif", "message", "show", "track"})
_KEYFRAME_KEYS = frozenset({"frame", "size", "x", "y", "rotate", "jitter", "ease"})
_WINDOW_KEYS = frozenset({"from", "to"})


@dataclass(frozen=True)
class Variant:
    """One gif an action can play, with the track drawn over it."""

    gif: GifInfo
    message: str
    stops: tuple[Stop, ...]
    show: tuple[Window, ...] = ()


@dataclass(frozen=True)
class Action:
    """A named action, i.e. one slash command."""

    name: str
    description: str
    variants: tuple[Variant, ...]


@dataclass(frozen=True)
class LoadResult:
    """What reading an actions directory produced.

    ``problems`` lists files that were skipped and why, so a broken definition
    is reported at boot instead of silently shrinking the command list.
    ``loaded`` is every file that defined at least one action.
    """

    actions: tuple[Action, ...]
    loaded: tuple[Path, ...]
    problems: tuple[str, ...]


def parse_action(raw: Any, source: Path) -> Action:
    """Build one action from a parsed YAML mapping.

    Args:
        raw: The mapping for a single action.
        source: The file it came from, used in error messages.

    Returns:
        The validated action, with its gifs opened and measured.

    Raises:
        ValueError: If a name, message, gif or keyframe is unusable, or the gif
            is not an animation the track can address.
        TypeError: If a value is present but not of a usable type.
    """
    _require_mapping(raw, source, "an action")
    _require_known_keys(raw, _ACTION_KEYS, source, "action")

    name = _name(raw, source)
    description = str(
        raw.get("description") or f"{name}{DEFAULT_DESCRIPTION_SUFFIX}"
    ).strip()
    action_message = str(raw.get("message") or DEFAULT_MESSAGE)

    variants_raw = raw.get("variants")
    if variants_raw is None:
        single = {key: value for key, value in raw.items() if key in _VARIANT_KEYS}
        variants_raw = [single]
    elif not isinstance(variants_raw, list) or not variants_raw:
        raise ValueError(f"{source}: '{name}' needs 'variants' to be a non-empty list")

    variants = tuple(
        _parse_variant(entry, source, name, action_message) for entry in variants_raw
    )
    return Action(name=name, description=description, variants=variants)


def parse_actions(raw: Any, source: Path) -> list[Action]:
    """Parse a YAML file's contents, which may hold one action or a list."""
    if isinstance(raw, dict):
        return [parse_action(raw, source)]
    if isinstance(raw, list):
        return [parse_action(entry, source) for entry in raw]
    raise TypeError(f"{source}: expected a mapping or a list of mappings")


def load_actions(directory: Path) -> LoadResult:
    """Read every YAML file under ``directory`` and merge them by action name.

    A file that cannot be parsed is reported in :attr:`LoadResult.problems` and
    skipped: one mistyped keyframe should not cost the server its other actions.

    Args:
        directory: The configured actions directory, which may not exist.

    Returns:
        The merged actions, the files they came from, and the problems found.
    """
    if not directory.is_dir():
        return LoadResult(actions=(), loaded=(), problems=())

    merged: dict[str, Action] = {}
    loaded: list[Path] = []
    problems: list[str] = []

    for path in sorted(
        [item for item in directory.rglob("*") if item.suffix in {".yaml", ".yml"}]
    ):
        try:
            text = path.read_text(encoding="utf-8")
            actions = parse_actions(yaml.safe_load(text), path)
        except (ValueError, TypeError, KeyError, yaml.YAMLError, OSError) as error:
            # Parse errors already name the file; a YAML or read error does not.
            reason = str(error)
            problems.append(
                reason if reason.startswith(f"{path}:") else f"{path}: {reason}"
            )
            continue

        if not actions:
            problems.append(f"{path}: no actions defined")
            continue

        for action in actions:
            existing = merged.get(action.name)
            if existing is None:
                merged[action.name] = action
            else:
                merged[action.name] = Action(
                    name=existing.name,
                    description=existing.description,
                    variants=(*existing.variants, *action.variants),
                )
        loaded.append(path)

    return LoadResult(
        actions=tuple(merged[name] for name in sorted(merged)),
        loaded=tuple(loaded),
        problems=tuple(problems),
    )


def format_action(
    name: str,
    gif: Path,
    keyframes: tuple[Keyframe, ...],
    *,
    description: str = "",
    message: str = DEFAULT_MESSAGE,
    show: tuple[Window, ...] = (),
    root: Path | None = None,
) -> str:
    """Render an action as YAML text, for the track editor to save.

    Paths are written relative to ``root`` (the project root by default) so the
    saved file stays portable instead of carrying a machine-specific absolute
    path. Keyframes are written in frame order with only the fields that differ
    from the previous pose, which is what makes a hand-edited track readable.
    ``show`` is left out entirely when the avatar is drawn on every frame.
    """
    document: dict[str, Any] = {"action": name}
    if description:
        document["description"] = description
    document["message"] = message
    document["gif"] = _relative(gif, root)
    if show:
        document["show"] = [_window_document(window) for window in show]
    document["track"] = [
        _keyframe_document(keyframe, previous)
        for keyframe, previous in zip(keyframes, (None, *keyframes), strict=False)
    ]
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


def _require_mapping(
    raw: Any, source: Path, what: str, name: str | None = None
) -> None:
    """Refuse anything that is not a mapping, naming the thing that should be one."""
    if isinstance(raw, dict):
        return
    raise TypeError(
        f"{source}: {_owner(name)}{what} needs a mapping, got {type(raw).__name__}"
    )


def _require_known_keys(
    raw: dict[str, Any],
    allowed: frozenset[str],
    source: Path,
    what: str,
    name: str | None = None,
) -> None:
    """Refuse keys the format has no meaning for, naming both lists.

    A typo has to say what was wrong and what is allowed: "degress" is only
    fixable once the reader sees the keys a keyframe actually takes.
    """
    unknown = set(raw) - allowed
    if not unknown:
        return
    raise ValueError(
        f"{source}: {_owner(name)}{what} has unknown key(s) "
        f"{', '.join(sorted(unknown))}; use {', '.join(sorted(allowed))}"
    )


def _owner(name: str | None) -> str:
    """Prefix naming the action a problem belongs to: ``"'kick' "``, or nothing."""
    return f"'{name}' " if name else ""


def _parse_variant(raw: Any, source: Path, name: str, action_message: str) -> Variant:
    _require_mapping(raw, source, "variant", name)
    _require_known_keys(raw, _VARIANT_KEYS, source, "variant", name)

    gif = _gif(raw, source, name)
    message = str(raw.get("message") or action_message)
    if _MESSAGE_PLACEHOLDER not in message:
        raise ValueError(
            f"{source}: '{name}' message needs {_MESSAGE_PLACEHOLDER} so the "
            "target is named"
        )

    track_raw = raw.get("track")
    if not isinstance(track_raw, list) or not track_raw:
        raise ValueError(
            f"{source}: '{name}' needs a 'track' list of keyframes (at least one entry)"
        )

    keyframes = tuple(
        _parse_keyframe(entry, source, name, gif.frames) for entry in track_raw
    )
    stops = normalize(keyframes, f"{source}: '{name}' track")
    return Variant(
        gif=gif,
        message=message,
        stops=stops,
        show=_parse_show(raw.get("show"), source, name, gif.frames),
    )


def _parse_keyframe(raw: Any, source: Path, name: str, gif_frames: int) -> Keyframe:
    _require_mapping(raw, source, "keyframe", name)
    _require_known_keys(raw, _KEYFRAME_KEYS, source, "keyframe", name)

    frame = _number(raw, "frame", source, name, minimum=1, maximum=gif_frames)
    size = _number(raw, "size", source, name, minimum=0)
    ease = str(raw.get("ease") or DEFAULT_EASE).strip()
    if ease not in EASING:
        raise ValueError(
            f"{source}: '{name}' keyframe {int(frame)} has unknown ease {ease!r}; "
            f"use {', '.join(EASING)}"
        )

    return Keyframe(
        frame=int(frame),
        size=None if size is None else int(size),
        x=_number(raw, "x", source, name),
        y=_number(raw, "y", source, name),
        rotate=_number(raw, "rotate", source, name),
        jitter=_number(raw, "jitter", source, name, minimum=0),
        ease=ease,
    )


def _parse_show(
    raw: Any, source: Path, name: str, gif_frames: int
) -> tuple[Window, ...]:
    """The frame ranges the avatar is drawn on, empty when the author left it out.

    Leaving ``show`` out is the common case and means every frame, so the key is
    only present in files that hide the avatar somewhere. An empty list is
    refused rather than read as "hide everything": a gif that draws no avatar is
    not an action, and an author who meant to narrow the frames should not get
    silence for it.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list) or not raw:
        raise ValueError(
            f"{source}: '{name}' 'show' needs a list of {{from, to}} ranges, "
            "or leave it out to draw every frame"
        )

    windows: list[Window] = []
    for entry in raw:
        _require_mapping(entry, source, "show range", name)
        _require_known_keys(entry, _WINDOW_KEYS, source, "show range", name)
        start = _number(
            entry,
            "from",
            source,
            name,
            minimum=1,
            maximum=gif_frames,
            label="show range",
        )
        if start is None:
            raise ValueError(
                f"{source}: '{name}' show range needs a 'from' frame to start at"
            )
        end = _number(
            entry,
            "to",
            source,
            name,
            minimum=int(start),
            maximum=gif_frames,
            label="show range",
        )
        windows.append(Window(start=int(start), end=None if end is None else int(end)))

    # Ordered for readability; whether a frame is drawn only needs the ranges
    # themselves, so overlapping ranges are allowed rather than simplified.
    return tuple(sorted(windows, key=lambda window: window.start))


def _window_document(window: Window) -> dict[str, Any]:
    """One visibility window as YAML. ``to`` is omitted when it runs to the end."""
    document: dict[str, Any] = {"from": window.start}
    if window.end is not None:
        document["to"] = window.end
    return document


def _name(raw: dict[str, Any], source: Path) -> str:
    """The action's command name, validated against the rules a platform accepts."""
    value = raw.get("action")
    if not value:
        raise ValueError(f"{source}: an action needs an 'action' name")

    name = str(value).strip().lower()
    if not _NAME_PATTERN.fullmatch(name):
        raise ValueError(
            f"{source}: action name {value!r} must be 2-32 characters of "
            "lowercase letters, digits and underscores, starting with a letter"
        )
    return name


def _gif(raw: dict[str, Any], source: Path, name: str) -> GifInfo:
    """Locate and measure the variant's gif."""
    value = raw.get("gif")
    if not value:
        raise ValueError(f"{source}: '{name}' needs a 'gif' path")

    path = _resolve_gif(str(value), source)
    if not path.is_file():
        candidates = ", ".join(str(root) for root in _path_roots(source))
        raise ValueError(
            f"{source}: '{name}' gif {value!r} was not found (searched {candidates})"
        )
    return inspect_gif(path)


def _resolve_gif(value: str, source: Path) -> Path:
    """Resolve a gif path, trying the working directory and the file's ancestors.

    Paths are written relative to the project root, which is the working directory
    when a file is read normally. Falling back to the definition's own ancestors keeps
    the files usable from a tool started elsewhere and from wherever the definition
    was exported to, because a path that only works from one directory reads as a
    missing gif.
    """
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate

    roots = _path_roots(source)
    for root in roots:
        hit = root / candidate
        if hit.is_file():
            return hit
    return roots[0] / candidate


def _path_roots(source: Path) -> list[Path]:
    """The directories a relative gif path may be resolved against.

    The working directory first, then every ancestor of the definition, nearest
    first: definitions live at different depths depending on who wrote them, and
    guessing one fixed depth makes a correctly authored file read as missing.
    """
    return [Path.cwd(), *source.parents]


def _relative(path: Path, root: Path | None) -> str:
    """``path`` written relative to ``root`` when that is shorter and honest.

    Forward slashes are written regardless of platform: a definition file is
    shared and read on machines that do not use ``\\``.
    """
    root = root or Path.cwd()
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _number(
    raw: dict[str, Any],
    field: str,
    source: Path,
    name: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    label: str = "keyframe",
) -> float | None:
    """One numeric field, ``None`` when the author left it out.

    ``label`` names the shape being read, so a ``show`` range is not reported as
    a keyframe.
    """
    value = raw.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(
            f"{source}: '{name}' {label} {field} must be a number, got {value!r}"
        )

    if minimum is not None and value < minimum:
        raise ValueError(f"{source}: '{name}' {label} {field} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{source}: '{name}' {label} {field} must be <= {maximum}")
    return float(value)


def _keyframe_document(keyframe: Keyframe, previous: Keyframe | None) -> dict[str, Any]:
    """One keyframe as YAML, omitting the fields it shares with the previous.

    Only the changes are written, which is the point of a track: a growing
    avatar is one line with a ``size``, not four lines repeating the same
    position. ``ease`` is always written except when it is the default.
    """
    document: dict[str, Any] = {"frame": keyframe.frame}
    for field in ("size", "x", "y", "rotate", "jitter"):
        value = getattr(keyframe, field)
        if value is None:
            continue
        if previous is not None and getattr(previous, field) == value:
            continue
        document[field] = round(value, 1) if isinstance(value, float) else value
    if keyframe.ease != DEFAULT_EASE:
        document["ease"] = keyframe.ease
    return document
