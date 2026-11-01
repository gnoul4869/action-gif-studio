"""The window: the gif, the avatar on it, and the keys and dialogs that change them.

Nothing here decides what a track *means*. :class:`.editor.TrackEditor` holds
those rules; this class shows its answers, turns key presses and clicks into
calls on it, and owns the two dialogs that make the tool usable without a command
line — **Open gif** and **Export YAML**. That is why the rules can be tested
without a display, and why a gif can be browsed to from wherever it landed.

    python -m action_studio              # opens empty; File ▸ Open gif starts
    python -m action_studio kick.gif     # or name the gif on the command line

The keys are listed for the user in :data:`.HELP`; the strip under the canvas is
the compressed version of the same list.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog
from typing import TYPE_CHECKING, Any

import yaml
from PIL import Image, ImageTk

from action_studio import HELP, suggested_yaml_path
from action_studio.crop import circle, decode_image
from action_studio.editor import TrackEditor
from action_studio.render import (
    AVATAR_BASE_SIZE,
    load_frames,
    paste_avatar,
    placeholder_avatar,
)

if TYPE_CHECKING:
    from action_studio.track import Pose

# The canvas shows a gif at its own pixels; only a gif larger than this shrinks,
# so what is authored here is the resolution the player will actually receive.
# ``--zoom`` overrides that for a gif too small to place an avatar precisely in.
CANVAS_WIDTH = 900
CANVAS_HEIGHT = 600

SIZE_STEP = 5
ROTATE_STEP = 5
JITTER_STEP = 1
FRAME_STEP = 1
FRAME_STEP_FAST = 10

# The ring around the avatar and the crosshair through its centre, so the marker
# reads as a marker rather than as part of the artwork being authored.
ACCENT = "#16e39f"

_GIF_TYPES = (("Animated GIF", "*.gif"), ("All files", "*.*"))
_IMAGE_TYPES = (("Images", "*.png *.jpg *.jpeg *.webp *.gif"), ("All files", "*.*"))
_YAML_TYPES = (("YAML", "*.yaml *.yml"), ("All files", "*.*"))

_EMPTY_HINT = (
    "File ▸ Open gif\n\n"
    "then click to place the avatar, and press k on the frames where it moves"
)
_EMPTY_STATUS = "no gif open — File ▸ Open gif, or pass a gif path on the command line"

_KEYS = (
    "Left/Right step frame (Shift = 10)",
    "Up/Down size | [ ] jitter | r/R rotate",
    "click = place centre | wheel = size (Shift = rotate)",
    "k record | d delete | e ease | c copy pose | v hide or show from here",
    "space play | s save | Ctrl+O open | Ctrl+Shift+S export | q quit",
)


class Studio(tk.Tk):
    """The editor window: one gif, one track, and the avatar riding over it."""

    def __init__(
        self,
        gif_path: Path | None = None,
        yaml_path: Path | None = None,
        avatar_path: Path | None = None,
        zoom: float = 0.0,
    ) -> None:
        super().__init__()
        self.zoom = zoom
        self.frames: list[Image.Image] = []
        self.durations: list[int] = []
        self.scale = 1.0
        self.display_size = (CANVAS_WIDTH, CANVAS_HEIGHT)
        self.avatar = self._load_avatar(avatar_path)
        self.edit: TrackEditor | None = None
        self.playing = False
        self._cache: dict[int, ImageTk.PhotoImage] = {}
        # Where the dialogs last looked, so authoring one action does not start
        # from the home directory every time a dialog opens.
        self._gif_dir: Path | None = gif_path.parent if gif_path else None
        self._yaml_dir: Path | None = yaml_path.parent if yaml_path else None

        self.title("Action GIF Studio")
        self.canvas = tk.Canvas(self, width=CANVAS_WIDTH, height=CANVAS_HEIGHT)
        self.canvas.pack()
        self.status = tk.Label(self, text="", anchor="w")
        self.status.pack(fill="x")
        tk.Label(self, text="   ".join(_KEYS), anchor="w").pack(fill="x")

        self._build_menu()
        self._bind_keys()

        if gif_path is None:
            self._show_empty()
        else:
            self.open_gif(gif_path, yaml_path)

    # --- Files --------------------------------------------------------------
    def _build_menu(self) -> None:
        """The menu bar: browse for a gif, choose where to export, quit."""
        menu = tk.Menu(self)
        files = tk.Menu(menu, tearoff=0)
        files.add_command(
            label="Open gif…", command=self.open_gif_dialog, accelerator="Ctrl+O"
        )
        files.add_command(
            label="Choose avatar preview…", command=self.choose_avatar_dialog
        )
        files.add_separator()
        files.add_command(label="Save YAML", command=self._save, accelerator="Ctrl+S")
        files.add_command(
            label="Export YAML…",
            command=self.export_yaml_dialog,
            accelerator="Ctrl+Shift+S",
        )
        files.add_separator()
        files.add_command(label="Quit", command=self.destroy, accelerator="Ctrl+Q")
        menu.add_cascade(label="File", menu=files)

        help_menu = tk.Menu(menu, tearoff=0)
        help_menu.add_command(label="Print the key list", command=self._print_keys)
        help_menu.add_command(label="How this works", command=self._print_about)
        menu.add_cascade(label="Help", menu=help_menu)
        self.config(menu=menu)

    def open_gif(self, gif_path: Path, yaml_path: Path | None = None) -> None:
        """Load a gif into the window and its track into the editor.

        The definition defaults to where this gif's project keeps it, and a file
        already there is loaded rather than quietly replaced: reopening a gif is
        how a track gets edited again rather than started over.

        Args:
            gif_path: The animated gif to author over.
            yaml_path: Definition to read and write, or ``None`` for the default
                place of the project this gif came from.
        """
        try:
            self.frames, self.durations = load_frames(gif_path)
        except (OSError, ValueError) as error:
            self._flash(f"{gif_path.name} could not be opened: {error}")
            return

        target = yaml_path or suggested_yaml_path(gif_path)
        document: dict[str, Any] = {}
        if target.is_file():
            loaded = _load_yaml(target)
            if loaded is not None:
                document = loaded

        self.edit = TrackEditor(
            gif_path=gif_path,
            yaml_path=target,
            gif_size=self.frames[0].size,
            frame_count=len(self.frames),
            document=document,
        )
        self._gif_dir = gif_path.parent
        self.scale = display_scale(self.frames[0].size, self.zoom)
        width, height = self.frames[0].size
        self.display_size = (
            max(1, round(width * self.scale)),
            max(1, round(height * self.scale)),
        )
        self.canvas.config(width=self.display_size[0], height=self.display_size[1])
        self.title(f"{gif_path.name} — {len(self.frames)} frames")
        self.playing = False
        self._invalidate()
        self._draw()

    def open_gif_dialog(self) -> None:
        """Browse for a gif and open it. Cancelling leaves the window as it was."""
        chosen = filedialog.askopenfilename(
            title="Open an action gif",
            initialdir=_directory(self._gif_dir),
            filetypes=_GIF_TYPES,
        )
        if chosen:
            self.open_gif(Path(chosen))

    def export_yaml_dialog(self) -> None:
        """Choose where the track is written, then write it there.

        The dialog opens on the suggested location for this gif — its own name in
        a ``data/actions/<folder>/`` folder when the gif came from a project with
        one — and anywhere else the user picks wins.
        """
        if self.edit is None:
            self._flash("nothing to export: open a gif first")
            return

        suggested = self.edit.yaml_path
        chosen = filedialog.asksaveasfilename(
            title="Export the track as YAML",
            defaultextension=".yaml",
            initialdir=_directory(self._yaml_dir or suggested.parent),
            initialfile=suggested.name,
            filetypes=_YAML_TYPES,
        )
        if not chosen:
            return
        self.edit.yaml_path = Path(chosen)
        self._yaml_dir = Path(chosen).parent
        self._save()

    def choose_avatar_dialog(self) -> None:
        """Preview with a real face instead of the grey placeholder disc.

        Cut exactly as a compositing player cuts one, so what is lined up here is
        what the result shows. Nothing about the track changes: it stays a
        preview.
        """
        chosen = filedialog.askopenfilename(
            title="Choose an avatar preview",
            initialdir=_directory(self._gif_dir),
            filetypes=_IMAGE_TYPES,
        )
        if chosen:
            self.avatar = self._load_avatar(Path(chosen))
            self._invalidate()
            self._draw()

    # --- Setup --------------------------------------------------------------
    def _load_avatar(self, avatar_path: Path | None) -> Image.Image:
        """A real avatar when one was given, otherwise the grey placeholder disc.

        Cut at :data:`.render.AVATAR_BASE_SIZE`, the same crop a compositing player
        applies, so what is previewed here is what the result shows.
        """
        if avatar_path is None:
            return placeholder_avatar(AVATAR_BASE_SIZE)
        image = decode_image(avatar_path.read_bytes())
        if image is None:
            print(f"{avatar_path} is not an image; using the placeholder disc")
            return placeholder_avatar(AVATAR_BASE_SIZE)
        return circle(image, AVATAR_BASE_SIZE)

    def _bind_keys(self) -> None:
        self.bind("<Left>", lambda event: self._step(-FRAME_STEP))
        self.bind("<Right>", lambda event: self._step(FRAME_STEP))
        self.bind("<Shift-Left>", lambda event: self._step(-FRAME_STEP_FAST))
        self.bind("<Shift-Right>", lambda event: self._step(FRAME_STEP_FAST))
        self.bind("<Up>", lambda event: self._resize(SIZE_STEP))
        self.bind("<Down>", lambda event: self._resize(-SIZE_STEP))
        self.bind("<Shift-Up>", lambda event: self._resize(1))
        self.bind("<Shift-Down>", lambda event: self._resize(-1))
        self.bind("<bracketleft>", lambda event: self._jitter(-JITTER_STEP))
        self.bind("<bracketright>", lambda event: self._jitter(JITTER_STEP))
        self.bind("r", lambda event: self._rotate(ROTATE_STEP))
        self.bind("R", lambda event: self._rotate(-ROTATE_STEP))
        self.bind("k", lambda event: self._record())
        self.bind("d", lambda event: self._delete())
        self.bind("e", lambda event: self._cycle_ease())
        self.bind("c", lambda event: self._copy_pose())
        self.bind("v", lambda event: self._toggle_hidden())
        self.bind("space", lambda event: self._toggle_play())
        self.bind("s", lambda event: self._save())
        self.bind("h", lambda event: self._print_keys())
        self.bind("q", lambda event: self.destroy())
        self.bind("<Control-o>", lambda event: self.open_gif_dialog())
        self.bind("<Control-s>", lambda event: self._save())
        self.bind("<Control-Shift-S>", lambda event: self.export_yaml_dialog())
        self.bind("<Control-q>", lambda event: self.destroy())
        self.canvas.bind("<Button-1>", self._place)
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Button-4>", lambda event: self._wheel(event, step=1))
        self.canvas.bind("<Button-5>", lambda event: self._wheel(event, step=-1))

    # --- Editing ------------------------------------------------------------
    def _adjust(self, **changes: float) -> None:
        """Change the pose being edited and redraw it."""
        if self.edit is None:
            return
        self.edit.set_pose(replace(self.edit.pose, **changes))
        self._invalidate()
        self._draw()

    def _step(self, amount: int) -> None:
        if self.edit is None:
            return
        self.edit.go_to(self.edit.frame + amount)
        self._invalidate()
        self._draw()

    def _resize(self, amount: int) -> None:
        if self.edit is None:
            return
        limit = max(self.frames[0].size)
        self._adjust(size=min(max(0, self.edit.pose.size + amount), limit))

    def _rotate(self, amount: int) -> None:
        if self.edit is None:
            return
        self._adjust(rotate=round((self.edit.pose.rotate + amount) % 360.0, 1))

    def _jitter(self, amount: int) -> None:
        if self.edit is None:
            return
        self._adjust(jitter=max(0.0, self.edit.pose.jitter + amount))

    def _place(self, event: tk.Event) -> None:
        """Move the avatar's centre to the clicked point, in gif pixels."""
        if self.edit is None:
            return
        self._adjust(x=round(event.x / self.scale, 1), y=round(event.y / self.scale, 1))

    def _wheel(self, event: tk.Event, step: int | None = None) -> None:
        notches = step if step is not None else event.delta // 120
        if event.state & 1:  # shift held: the wheel turns the avatar instead
            self._rotate(notches * ROTATE_STEP)
        else:
            self._resize(notches * SIZE_STEP)

    def _record(self) -> None:
        if self.edit is None:
            self._flash("nothing to record: open a gif first")
            return
        refusal = self.edit.record()
        if refusal is not None:
            self._flash(refusal)
            return
        self._invalidate()
        self._draw()

    def _delete(self) -> None:
        if self.edit is None:
            return
        refusal = self.edit.delete()
        if refusal is not None:
            self._flash(refusal)
            return
        self._invalidate()
        self._draw()

    def _cycle_ease(self) -> None:
        if self.edit is None:
            return
        self.edit.cycle_ease()
        self._invalidate()
        self._draw()

    def _toggle_hidden(self) -> None:
        """Hide the avatar from this frame, or bring it back from here.

        The frame is redrawn without the avatar, so "gone from here" is visible
        immediately rather than only in the exported file.
        """
        if self.edit is None:
            self._flash("nothing to hide: open a gif first")
            return
        message = self.edit.toggle_hidden()
        self._invalidate()
        self._draw()
        self._flash(message)

    def _copy_pose(self) -> None:
        """Hold this pose: step to the next frame and take the pose with it.

        `c` is a no-op step, and a no-op step inherits the pose it lands on, so
        copying a pose to the next frame is the same move as staying put.
        """
        self._step(0)

    def _save(self) -> None:
        if self.edit is None:
            self._flash("nothing to save: open a gif first")
            return
        result = self.edit.save()
        if isinstance(result, str):
            self._flash(f"not saved: {result}")
            return
        path, count = result
        self._flash(f"saved {count} keyframe(s) to {path}")

    def _print_keys(self) -> None:
        for line in _KEYS:
            print(line)

    def _print_about(self) -> None:
        """The whole key list, plus what the tool is, on the console."""
        print(HELP)

    # --- Playback -----------------------------------------------------------
    def _toggle_play(self) -> None:
        if self.edit is None:
            return
        self.playing = not self.playing
        self._invalidate()
        if self.playing:
            self._tick()
        else:
            self._draw()

    def _tick(self) -> None:
        """Advance one frame on its own duration, so preview timing is the gif's."""
        if not self.playing or self.edit is None:
            return
        self._draw()
        self.edit.go_to(self.edit.frame % len(self.frames) + 1)
        self.after(max(10, self.durations[self.edit.frame - 1]), self._tick)

    # --- Drawing ------------------------------------------------------------
    def _shown_pose(self, frame: int) -> Pose:
        """The pose to draw: the one under edit only on a paused current frame."""
        if self.edit is None:
            raise AssertionError("drawing needs an open gif")
        if frame == self.edit.frame and not self.playing:
            return self.edit.pose
        return self.edit.effective(frame)

    def _invalidate(self) -> None:
        """Drop composited frames, which are only valid for one set of poses."""
        self._cache.clear()

    def _photo(self, frame: int) -> ImageTk.PhotoImage:
        """This frame with the avatar composited, at display size."""
        cached = self._cache.get(frame)
        if cached is not None:
            return cached

        image = self.frames[frame - 1].copy()
        pose = self._shown_pose(frame)
        if pose.size > 0 and self.edit.visible(frame):
            # No rng: the preview places the pose exactly so it can be aimed at.
            paste_avatar(image, self.avatar, pose)

        cached = ImageTk.PhotoImage(image.resize(self.display_size, Image.LANCZOS))
        self._cache[frame] = cached
        return cached

    def _draw(self) -> None:
        """Paint the current frame and mark the avatar's pose on it.

        The marker sits exactly on ``x``/``y`` and no jitter is applied: the shake
        is rolled per frame when the gif is rendered for real, and a preview that
        wobbled on its own could not be aimed at.
        """
        if self.edit is None:
            self._show_empty()
            return

        self.canvas.delete("all")
        # ``anchor="nw"`` because a canvas image item is centred on the point it
        # is given by default: without it the top-left of the frame would sit at
        # (0, 0) and most of the gif would fall outside the canvas, and every
        # click would be read half a frame off.
        self.canvas.create_image(0, 0, image=self._photo(self.edit.frame), anchor="nw")

        pose = self._shown_pose(self.edit.frame)
        if pose.size > 0 and not self.playing and self.edit.visible(self.edit.frame):
            centre_x, centre_y = pose.x * self.scale, pose.y * self.scale
            radius = pose.size * self.scale / 2
            self.canvas.create_oval(
                centre_x - radius,
                centre_y - radius,
                centre_x + radius,
                centre_y + radius,
                outline=ACCENT,
                dash=(3, 3),
            )
            self.canvas.create_line(
                centre_x - 8, centre_y, centre_x + 8, centre_y, fill=ACCENT
            )
            self.canvas.create_line(
                centre_x, centre_y - 8, centre_x, centre_y + 8, fill=ACCENT
            )

        self.status.config(text=self._status(pose))

    def _show_empty(self) -> None:
        """The state before a gif is open: an empty canvas and one instruction."""
        self.canvas.delete("all")
        self.canvas.create_text(
            CANVAS_WIDTH // 2,
            CANVAS_HEIGHT // 2,
            text=_EMPTY_HINT,
            justify="center",
            fill="#8a97a8",
            font=("", 14),
        )
        self.status.config(text=_EMPTY_STATUS)

    def _status(self, pose: Pose) -> str:
        if self.edit is None:
            return _EMPTY_STATUS
        frames = " ".join(
            f"*{frame}" if frame == self.edit.frame else str(frame)
            for frame in sorted(self.edit.keyframes)
        )
        recorded = f"keyframes: {frames}" if frames else "no keyframes yet"
        hidden = "" if self.edit.visible(self.edit.frame) else "avatar hidden | "
        return (
            f"frame {self.edit.frame}/{len(self.frames)}  "
            f"size {pose.size}  x {pose.x:.1f}  y {pose.y:.1f}  "
            f"rot {pose.rotate:.1f}  jitter {pose.jitter:.1f}  "
            f"ease {self.edit.ease_of(self.edit.frame)}  |  {hidden}{recorded}"
        )

    def _flash(self, message: str) -> None:
        """Say something in the status line without disturbing the pose line."""
        self.status.config(text=message)


def _directory(path: Path | None) -> str | None:
    """A directory for a dialog, or ``None`` so Tk picks its own default."""
    return str(path) if path is not None else None


def display_scale(size: tuple[int, int], zoom: float = 0.0) -> float:
    """How many screen pixels one gif pixel gets.

    1.0 whenever the gif fits the canvas: an action gif is a few hundred pixels
    across, and enlarging it would blur the very edge the avatar is being lined
    up against. Only a gif larger than the canvas is scaled down, and ``zoom``
    replaces both answers when a gif is too small to place an avatar in precisely.
    """
    width, height = size
    if zoom > 0:
        return zoom
    return min(1.0, CANVAS_WIDTH / width, CANVAS_HEIGHT / height)


def _load_yaml(path: Path) -> dict[str, Any] | None:
    """The definition file as a mapping, or ``None`` when it cannot be read."""
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"{path} could not be read: {error}")
        return None
    return loaded if isinstance(loaded, dict) else None
