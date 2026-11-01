# Action GIF Studio

Take an animated gif, place an avatar holder on it frame by frame — moving,
scaling, rotating, wobbling it along a motion path — and export the keyframe track
as a YAML definition file.

The output is a track, not a rendered file. Whatever plays the result composites
the gif with the real avatar of whoever is being acted on, at send time, so one
gif works for every avatar. This tool never renders anything and never connects
anywhere: what you see in the window is a preview, cut the same way a player cuts
an avatar.

## Install

Needs Python 3.12+ with Tkinter (on Debian/Ubuntu: `sudo apt install python3-tk`).

From a checkout:

```bash
pip install -e .
action-gif-studio            # or: python -m action_studio
```

It is not on PyPI yet, so a checkout is how it is installed for now. Nothing needs
installing to try it: clone the repo and run `python -m action_studio` from its
folder, provided Pillow, PyYAML and Tkinter are already available.

## Use it

```bash
python -m action_studio                # opens empty; File ▸ Open gif
python -m action_studio kick.gif       # or name the gif
python -m action_studio kick.gif --avatar face.png
```

An action gif usually arrives in a downloads folder, so the window opens without
one: **File ▸ Open gif** browses for it, and **File ▸ Export YAML…** chooses where
the result goes. The dialog starts where you saved last, or — when the gif came
from a project that keeps its definitions under `data/actions/` — in the folder
for that gif's own subfolder, named after the gif.

The keys that do almost all the work:

| Key | What it does |
| --- | --- |
| click | Move the avatar's centre to the click |
| `Up` / `Down` | Change its size |
| `Left` / `Right` | Step frames (`Shift` = 10) |
| `k` | Record this frame's pose as a keyframe |
| `v` | Hide the avatar from this frame, or bring it back from here |
| `space` | Play the preview |
| `s` | Save the YAML |

The rest:

| Key | What it does |
| --- | --- |
| `[` / `]` | Shake amount down / up one pixel |
| `r` / `R` | Rotate +5 / -5 degrees |
| mouse wheel | Size by 5 (`Shift` = rotate by 5) |
| `e` | Cycle this frame's easing |
| `d` | Delete this frame's keyframe |
| `c` | Re-read the pose the track already has here |
| `h` | Print the key list |
| `Ctrl+O` | Browse for a gif |
| `Ctrl+S` / `Ctrl+Shift+S` | Save / save somewhere else |
| `q` / `Ctrl+Q` | Quit |

That is the whole list. `h` prints it inside the window, **Help** in the menu shows
it, and `--help` shows it beside the flags.

The usual pass through a punch gif: step to frame 1, click where the fist starts,
press `k`; step forward, move the avatar to follow the fist, `k` at each frame
where the motion changes; `space` to watch; `s`. Keyframes are the frames you
mark — the frames between them are interpolated, so a track of six keyframes
drives an animation of forty frames.

## The file it writes

```yaml
action: kick
description: Kick a member across the screen.
message: "{name} got kicked across the room."
gif: assets/gifs/kick/genos_kick.gif
track:
  - frame: 1
    size: 60
    x: 191.0
    y: 202.0
  - frame: 4
    x: 250.0
    y: 174.0
    ease: smooth
```

- `action` — the name, also the folder name and, where a player builds commands
  from these files, the command name.
- `description` — what a player shows when picking the action.
- `message` — sent alongside the gif; must contain `{name}`, which the player
  fills in with whoever is being acted on.
- `gif` — the gif this track runs over.
- `track` — the keyframes: `frame` (1-based), plus `size`, `x`, `y`, `rotate`,
  `jitter`, `ease`.
- `show` — optional: the frame ranges the avatar is drawn on, inclusive, e.g.
  `- {from: 1, to: 22}`. Outside every range nothing is pasted at all, and a
  range with no `to` runs to the gif's last frame. Leaving the key out draws
  every frame.

**Only what changed is stored.** A keyframe holds the fields that differ from the
previous one; the rest carry over, which is why frame 4 above stores only a new
position. `x`/`y` are the avatar's centre in gif pixels, `size` is its diameter,
`ease` is how the motion arrives from the previous keyframe, `jitter` is how much
it shakes. The first keyframe holds everything.

**Hiding it is a range, not a size.** `show` says which frames the avatar exists
on, and `v` in the window cuts a range at the frame you are standing on or opens
one back up. The track keeps running behind the gap, so the avatar comes back
where the motion would have put it. `size: 0` still means shrink to nothing,
which fades rather than cuts — use `show` when the character leaves the frame.

Gif limits: animated, at most 200 frames, no edge longer than 1280 pixels, frame
timings preserved.

A relative `gif` path resolves against the reader's working directory and the
definition's own folder upwards; a full path resolves anywhere. The editor writes
the relative form when the gif sits inside the same project as the file being
written, and the full path otherwise.

## Easing

`ease` sits on the keyframe being arrived at and shapes the frames leading into
it. `in` slows the departure from the previous keyframe, `out` slows the arrival
at this one:

| ease | leaving the previous keyframe | arriving at this one |
| --- | --- | --- |
| `hold` | frozen | jumps on the frame |
| `linear` | steady | steady — the default |
| `smooth` | slow | slow, fastest in the middle |
| `ease_in` | slow | fast |
| `ease_out` | fast | slow |
| `ease_in_out` | slowest | slowest |

Size gained per frame on a `size: 10 → 100` segment, which makes it concrete:

```
hold         0, 0, 0, 0, 0, 0, 0, 0, 90   <- no interpolation at all
linear      10, 10, 10, 10, 10, 10, 10, 10, 10
smooth       3, 8, 12, 15, 14, 15, 12, 8, 3
ease_in      1, 3, 6, 8, 10, 12, 14, 17, 19   <- creeps, then lands hard
ease_out    19, 17, 14, 12, 10, 8, 6, 3, 1    <- rushes in, settles
ease_in_out  0, 4, 9, 19, 26, 19, 9, 4, 0
```

`e` cycles through these for the frame you are on and the status line shows the
current one. A keyframe with no `ease` is `linear`, and saving leaves `ease` off
when it is `linear`, so most tracks never mention it. `ease` on the first keyframe
does nothing — the frames before it already hold that pose.

**What to reach for.**

| Situation | Use |
| --- | --- |
| A pose travelling at one speed — a thrown avatar, a walk across the frame | nothing, `linear` |
| The pose must not move, then move all at once — a cut, a held pose before a snap | `hold` |
| Approaching the frame of a hit — slow wind-up, fast contact | `ease_in` |
| Leaving a hit — fast away, settling to rest | `ease_out` |
| A drift or a turn that looks mechanical at constant speed | `smooth` |
| A long travel that should start and finish gently | `ease_in_out` |

The gif files shipped with [J.A.D.A.I](https://github.com/gnoul4869/J.A.D.A.I-v2)
use only `hold` and the default, because the timeline they were migrated from
stepped one pose per frame. The four curves are for the gifs you author: they are
what lets a track of six keyframes look like twenty-five rows of motion.

## Jitter

`jitter` is a random offset, in gif pixels, applied to the avatar's centre on
every frame it is drawn. Each axis moves independently by up to that many pixels,
so a shake of 3 can land the avatar up to about 4 pixels off diagonally. It is
what makes a slap look hand-held rather than pasted on.

It is a field like any other, so it inherits and it interpolates: one keyframe of
`jitter: 1` keeps shaking to the end of the track unless another keyframe changes
it, and a track that writes 1.0, then 2.5, then 7.5 builds the shake as the avatar
gets crushed.

`[` and `]` step it one pixel at a time; `0`, the default, is no shake. The values
in the shipped gifs run from 1.0 (a wobble) to 9.5 (a flail).

**The preview does not shake.** The window puts the avatar exactly where `x` and
`y` say, because a preview that moved on its own could not be aimed at. The shake
is applied when the gif is sent, and it is rolled afresh every time — the same
slap twice never shakes the same way.

## An example of what reads this

The format is a plain file with nothing attached to it.
[J.A.D.A.I](https://github.com/gnoul4869/J.A.D.A.I-v2) is one thing that reads it:
it loads every definition at startup, turns the action name into a slash command
and an LLM-callable action, fills `{name}` with whoever is being acted on, and
composites the gif with that member's avatar at send time.

Nothing in this repo depends on it. The tool ships no loader, no renderer and no
client, and any program that reads the fields above can play the result.

## What this tool does not do

It does not render a gif, upload anything, or read a token. It reads a gif and
writes YAML, and needs only Pillow, PyYAML and Tkinter. The format rules are
enforced on save by the parser that reads these files back, so a file that saved
cleanly is a file that loads.

## Development

```bash
python -m pytest -q      # rules and files, no display needed
ruff check action_studio tests
```

`action_studio/editor.py` holds the rules (what a keyframe records, what a save
writes) and imports no UI; `action_studio/ui.py` holds the window, its keys and
its dialogs. The tests for the rules run without a display.

The preview pastes the avatar through `paste_avatar` in `action_studio/render.py`,
the same function a player uses to composite the sent gif, so the frame on screen
is the frame that gets drawn rather than a guess at it.

## License

MIT. See [LICENSE](LICENSE).
