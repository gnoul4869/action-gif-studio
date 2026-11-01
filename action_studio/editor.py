"""What a track is, and what saving one writes.

``ui.py`` in this package draws frames and translates key presses. Every
decision about a track lives here instead: what a frame's pose currently is,
what a recorded keyframe contains, what the saved YAML looks like. Keeping it
apart from the window means the rules are testable without a display, and means
a file can be handed to :func:`action_studio.definitions.parse_action` before it
is written — the same reading a program applies when it loads the track.

The rule that keeps the files readable is :meth:`TrackEditor.record`: a
keyframe stores only the fields that differ from the pose the track already has
at that frame, which is the inverse of the inheritance
:func:`action_studio.track.normalize` applies when reading it back.

Only ``ui.py`` uses this. Nothing that plays the result imports it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import yaml

from action_studio import project_root
from action_studio.definitions import format_action, parse_action
from action_studio.track import (
    DEFAULT_EASE,
    EASING,
    Keyframe,
    Pose,
    Window,
    normalize,
    resolve_pose,
)
from action_studio.track import visible as track_visible

if TYPE_CHECKING:
    from pathlib import Path

    from action_studio.track import Stop

# How big the first avatar of an empty track is, as a fraction of the gif's
# shorter edge: big enough to see where it lands, small enough to still see the
# frame it sits on.
_START_SCALE = 4

# The definition keys the studio keeps. It manages the track and the gif; a
# file's own notes would be keys the loader refuses, so they are not written.
_KEPT_KEYS = ("action", "description", "message")


def _number(entry: dict[str, Any], key: str) -> float | None:
    """A keyframe's numeric field, or ``None`` when it is absent or not a number.

    ``None`` means "inherit from the previous keyframe", so a field a human
    mistyped degrades to an inherited value rather than aborting the whole file.
    """
    value = entry.get(key)
    return float(value) if isinstance(value, int | float) else None


def _to_keyframe(entry: dict[str, Any]) -> Keyframe:
    """One YAML entry as a :class:`Keyframe`, tolerating what it omits."""
    ease = entry.get("ease")
    size = entry.get("size")
    return Keyframe(
        frame=int(entry["frame"]),
        size=size if isinstance(size, int) else None,
        x=_number(entry, "x"),
        y=_number(entry, "y"),
        rotate=_number(entry, "rotate"),
        jitter=_number(entry, "jitter"),
        ease=ease if isinstance(ease, str) else DEFAULT_EASE,
    )


def _keyframes_from(document: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """The document's ``track`` as ``{frame: entry}``, ignoring malformed rows.

    Opening a file never rewrites it: entries are kept as written and only
    validated when saving, so a file the loader would reject can still be
    repaired rather than being silently normalised on open.
    """
    track = document.get("track")
    if not isinstance(track, list):
        return {}

    keyframes: dict[int, dict[str, Any]] = {}
    for entry in track:
        if not isinstance(entry, dict) or not isinstance(entry.get("frame"), int):
            continue
        keyframes[entry["frame"]] = dict(entry)
    return keyframes


def _windows_from(document: dict[str, Any]) -> list[Window]:
    """The document's ``show`` ranges, ignoring malformed rows.

    Tolerant on open for the same reason the track is: a file the loader would
    refuse can still be repaired here, and it is validated when saving.
    """
    raw = document.get("show")
    if not isinstance(raw, list):
        return []

    windows: list[Window] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        start = entry.get("from")
        if not isinstance(start, int) or isinstance(start, bool):
            continue
        end = entry.get("to")
        windows.append(
            Window(
                start=start,
                end=end if isinstance(end, int) and not isinstance(end, bool) else None,
            )
        )
    return sorted(windows, key=lambda window: window.start)


@dataclass
class TrackEditor:
    """The keyframes of one gif while they are being authored.

    Attributes:
        frame: The frame under edit, counted from 1 like a gif viewer.
        pose: The pose being edited on this frame, not yet recorded. It starts
            as whatever the track already says about this frame.
        ease: The easing to use for the next keyframe recorded here.
        windows: The frame ranges the avatar is drawn on, empty when it is drawn
            on every frame. Separate from the track: hiding the avatar does not
            move it, and the track keeps running behind the gap.
    """

    gif_path: Path
    yaml_path: Path
    gif_size: tuple[int, int]
    frame_count: int
    document: dict[str, Any] = field(default_factory=dict)
    frame: int = 1
    ease: str = DEFAULT_EASE
    pose: Pose = field(init=False)
    keyframes: dict[int, dict[str, Any]] = field(init=False)
    windows: list[Window] = field(init=False)

    def __post_init__(self) -> None:
        self.keyframes = _keyframes_from(self.document)
        self.windows = _windows_from(self.document)
        self.frame = min(max(1, self.frame), self.frame_count)
        self.pose = self._blank_pose()
        self._fill_first()
        self.pose = self._start_pose()

    # --- Reading ------------------------------------------------------------
    def stops(self) -> tuple[Stop, ...]:
        """The recorded keyframes as stops, or empty when nothing is recorded."""
        if not self.keyframes:
            return ()
        return normalize(
            [_to_keyframe(entry) for entry in self.keyframes.values()],
            str(self.yaml_path),
        )

    def _stops_or_none(self) -> tuple[Stop, ...] | None:
        """``stops()``, or ``None`` when the track does not normalize yet.

        A file is often opened precisely because it is broken; resolving such a
        track to the pose under edit keeps the studio usable, and
        :meth:`validate` says what is wrong when saving.
        """
        try:
            return self.stops()
        except ValueError:
            return None

    def effective(self, frame: int) -> Pose:
        """What the track resolves at ``frame``.

        When nothing is recorded — or when the track does not normalize yet —
        there is nothing to resolve, so this is the start pose rather than the
        pose being edited; that is what a new keyframe is compared against.
        """
        stops = self._stops_or_none()
        if not stops:
            return self._blank_pose()
        return resolve_pose(stops, frame)

    def ease_of(self, frame: int) -> str:
        """The easing that frame's keyframe carries, or the pending one."""
        entry = self.keyframes.get(frame)
        ease = entry.get("ease") if entry is not None else None
        return ease if isinstance(ease, str) and ease in EASING else self.ease

    def visible(self, frame: int) -> bool:
        """Whether the avatar is drawn on ``frame``.

        With no windows recorded the avatar is drawn everywhere, which is what a
        file without a ``show`` key means.
        """
        return track_visible(self.windows, frame)

    def _blank_pose(self) -> Pose:
        """A centred avatar at the start size: what an empty track shows."""
        width, height = self.gif_size
        return Pose(
            size=max(8, min(width, height) // _START_SCALE),
            x=width / 2,
            y=height / 2,
            rotate=0.0,
            jitter=0.0,
        )

    def _start_pose(self) -> Pose:
        """The pose to begin from: the track's first, or a centred grey disc."""
        return (
            self.effective(min(self.keyframes))
            if self.keyframes
            else self._blank_pose()
        )

    def _fill_first(self, source: Pose | None = None) -> None:
        """Give the first keyframe the fields nothing earlier can supply.

        Only the first keyframe has nothing to inherit from, so deleting it — or
        opening a file that never stated these — would leave the rest of the
        track resolving to nothing. ``source`` is the pose to copy across, which
        is the one the deleted keyframe carried; without one, a centred start.
        """
        if not self.keyframes:
            return
        entry = self.keyframes[min(self.keyframes)]
        origin = source if source is not None else self._blank_pose()
        for key, value in (("size", origin.size), ("x", origin.x), ("y", origin.y)):
            if not isinstance(entry.get(key), int | float):
                entry[key] = round(value, 1)

    # --- Editing ------------------------------------------------------------
    def go_to(self, frame: int) -> Pose:
        """Move to ``frame`` (clamped) and adopt the pose already there."""
        self.frame = min(max(1, frame), self.frame_count)
        self.pose = self.effective(self.frame)
        return self.pose

    def set_pose(self, pose: Pose) -> None:
        self.pose = pose

    def record(self) -> str | None:
        """Write this frame's pose down; return a refusal when there is nothing to say.

        Only fields that differ from the pose the track would have here are
        stored, so a track lists what moves and nothing else. If this is the
        first keyframe, the fields nothing earlier can carry are filled in from
        the pose too, so the track stays savable.
        """
        reference = self.effective(self.frame)
        entry: dict[str, Any] = {"frame": self.frame}
        compared = (
            ("size", self.pose.size, float(reference.size)),
            ("x", self.pose.x, round(reference.x, 1)),
            ("y", self.pose.y, round(reference.y, 1)),
            ("rotate", self.pose.rotate, round(reference.rotate, 1)),
            ("jitter", self.pose.jitter, round(reference.jitter, 1)),
        )
        for name, value, shown in compared:
            if abs(value - shown) > 0.05:
                entry[name] = round(value, 1) if name != "size" else int(value)
        if self.ease_of(self.frame) != DEFAULT_EASE:
            entry["ease"] = self.ease_of(self.frame)

        if set(entry) == {"frame"}:
            return "nothing to record: this frame already holds this pose"
        if self.frame in self.keyframes:
            # Recording twice on one frame adds to what is already written there:
            # changing a size, recording, then moving the avatar would otherwise
            # replace the entry and drop the size.
            entry = {**self.keyframes[self.frame], **entry}
        self.keyframes[self.frame] = entry
        if self.frame == min(self.keyframes):
            self._fill_first(self.pose)
        return None

    def delete(self) -> str | None:
        """Drop this frame's keyframe; return a refusal when there was none.

        Deleting the *first* keyframe moves the inheritance problem onto the new
        first: it may have been inheriting from the one just deleted. The pose
        the viewer saw at that frame is written into it so the animation keeps
        its shape rather than becoming unsavable.
        """
        if self.frame not in self.keyframes:
            return f"frame {self.frame} has no keyframe"

        frames = sorted(self.keyframes)
        was_first = self.frame == frames[0]
        carried = self.effective(frames[1]) if was_first and len(frames) > 1 else None
        del self.keyframes[self.frame]
        if was_first:
            self._fill_first(carried)
        self.pose = self.effective(self.frame)
        return None

    def cycle_ease(self) -> str:
        """Move to the next easing and record it on this frame's keyframe.

        ``ease`` describes the motion arriving at a keyframe, so it is only
        meaningful on one: setting it creates the keyframe.
        """
        names = list(EASING)
        ease = names[(names.index(self.ease_of(self.frame)) + 1) % len(names)]
        self.ease = ease
        self.keyframes.setdefault(self.frame, {"frame": self.frame})["ease"] = ease
        return ease

    def toggle_hidden(self) -> str:
        """Hide the avatar from this frame, or bring it back from here.

        Visibility is a step, so one edit says "gone from here" and another says
        "back from here"; neither touches the pose, which keeps moving underneath.
        Cutting at the current frame is enough to end a window, so "visible until
        here" is ``v`` on the next frame rather than a third command.

        Returns:
            What happened, for the status line.
        """
        if self.visible(self.frame):
            return self._hide_from(self.frame)
        return self._show_from(self.frame)

    def _hide_from(self, frame: int) -> str:
        """End every window that covers ``frame`` one frame earlier."""
        if frame <= 1:
            # A gif with no avatar on any frame is not an action, so there is
            # nothing to do here; the author meant a later frame.
            return "frame 1 is already the first frame — nothing before it to keep"

        if not self.windows:
            # Visible everywhere until now: keep the frames before this one.
            self.windows = [Window(start=1, end=frame - 1)]
        else:
            self.windows = [
                Window(start=window.start, end=frame - 1)
                if window.covers(frame)
                else window
                for window in self.windows
            ]
        self.windows = sorted(self.windows, key=lambda window: window.start)
        return f"hidden from frame {frame}"

    def _show_from(self, frame: int) -> str:
        """Open a window at ``frame``, dropping the ones that reached past it."""
        kept = [
            window
            for window in self.windows
            if window.end is not None and window.end < frame
        ]
        if kept and kept[-1].end == frame - 1:
            # The gap ends where this frame starts, so the window simply
            # reaches over it rather than becoming a second range.
            kept[-1] = Window(start=kept[-1].start)
        else:
            kept.append(Window(start=frame))
        self.windows = kept
        return f"visible from frame {frame}"

    # --- Writing ------------------------------------------------------------
    def sorted_keyframes(self) -> list[dict[str, Any]]:
        """The recorded keyframes in frame order, as they were written."""
        return [self.keyframes[frame] for frame in sorted(self.keyframes)]

    def as_keyframes(self) -> list[Keyframe]:
        """The recorded keyframes in frame order, as :class:`Keyframe` objects."""
        return [_to_keyframe(entry) for entry in self.sorted_keyframes()]

    def window_documents(self) -> list[dict[str, Any]]:
        """The visibility windows as YAML entries, empty when nothing is hidden."""
        return [
            {"from": window.start, "to": window.end}
            if window.end is not None
            else {"from": window.start}
            for window in self.windows
        ]

    def action_name(self) -> str:
        """What the file calls the action, or the directory it lives in.

        A program reading a folder of definitions takes the action's name from
        its ``action`` key, and falls back to the folder when the file does not
        name itself.
        """
        stated = self.document.get("action", "")
        return str(stated or self.gif_path.parent.name).strip().lower()

    def reference_root(self) -> Path | None:
        """The project both this definition and its gif belong to, when there is one.

        A relative ``gif`` path is only worth writing when the file that contains
        it is inside the same project as the gif: a relative path resolves
        against the reader's working directory and the definition's own ancestors, and a
        project root is the one directory both answers agree on. An export
        somewhere else has no such directory, so the full path is the only honest
        reference and a relative one would read as a missing gif.

        Returns:
            That project root, or ``None``.
        """
        root = project_root(self.gif_path)
        if root is None:
            return None
        try:
            self.yaml_path.resolve().relative_to(root)
        except ValueError:
            return None
        return root

    def gif_reference(self) -> str:
        """How the file should point at this gif.

        Relative to :meth:`reference_root` when there is one, forward slashes
        regardless of platform because a definition is shared and read on machines
        that do not use ``\\``; the full path when there is not.
        """
        root = self.reference_root()
        if root is None:
            return self.gif_path.as_posix()
        try:
            return self.gif_path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return self.gif_path.as_posix()

    def draft(self) -> dict[str, Any]:
        """The file as it would be written: the fields it keeps, plus the track."""
        draft = {
            key: value for key, value in self.document.items() if key in _KEPT_KEYS
        }
        name = self.action_name()
        draft.setdefault("action", name)
        draft.setdefault("message", f"{name} {{name}}")
        draft["gif"] = self.gif_reference()
        if self.windows:
            draft["show"] = self.window_documents()
        draft["track"] = self.sorted_keyframes()
        return draft

    def validate(self) -> str | None:
        """Why loading this file would fail, or ``None`` when it would work."""
        return self._validate(self.draft())

    def _validate(self, draft: dict[str, Any]) -> str | None:
        """Why loading ``draft`` would fail, or ``None`` when it would work.

        Checked by handing the draft to the same parser a reader uses, so saving
        cannot produce a track that fails on load: the name, the message, the gif
        and every keyframe — including a frame past the gif's last — are checked
        exactly as a reader checks them.
        """
        try:
            parse_action(draft, self.yaml_path)
        except (TypeError, ValueError) as error:
            return str(error)
        return None

    def save(self) -> tuple[Path, int] | str:
        """Write the file, or return why it could not be written.

        The text comes from :func:`~action_studio.definitions.format_action`, the
        writer the rest of the feature shares, so a saved file carries the gif's
        project-relative path and only the fields that change between keyframes.
        The draft is built once and written from it, so what was checked is what
        lands on disk.

        Returns:
            ``(path, keyframe count)`` on success, otherwise the refusal text.
        """
        draft = self.draft()
        problem = self._validate(draft)
        if problem is not None:
            return problem

        text = format_action(
            str(draft["action"]),
            self.gif_path,
            self.as_keyframes(),
            description=str(draft.get("description") or ""),
            message=str(draft["message"]),
            show=tuple(self.windows),
            root=self.reference_root(),
        )
        self.yaml_path.parent.mkdir(parents=True, exist_ok=True)
        self.yaml_path.write_text(text, encoding="utf-8")
        self.document = yaml.safe_load(text)
        return self.yaml_path, len(draft["track"])
