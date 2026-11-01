"""The command line: ``python -m action_studio``.

The gif is optional on purpose. An action gif usually lands in a downloads
folder, and browsing to it beats typing its path, so the window can open empty
and File ▸ Open gif takes it from there. Everything after that happens in the
window, including where the YAML is written.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from action_studio import HELP, __version__
from action_studio.ui import Studio

DESCRIPTION = (
    "Author an action gif: place an avatar on an animated gif frame by frame, "
    "and export the keyframe track a program such as J.A.D.A.I can play."
)


def main(argv: list[str] | None = None) -> int:
    """Parse the arguments, open the window, and return the exit code.

    Args:
        argv: The arguments to parse; ``None`` reads the process's own.

    Returns:
        ``0`` after the window closes, ``1`` when a named gif is not a file.
    """
    parser = argparse.ArgumentParser(
        prog="action-gif-studio", description=DESCRIPTION, epilog=HELP
    )
    parser.add_argument(
        "gif",
        nargs="?",
        type=Path,
        help="the gif to open; omit it to browse for one from the window",
    )
    parser.add_argument(
        "--yaml",
        type=Path,
        help="definition file to read and write; defaults to this gif's project",
    )
    parser.add_argument(
        "--avatar",
        type=Path,
        help="preview with this image instead of the grey placeholder disc",
    )
    parser.add_argument(
        "--zoom",
        type=float,
        default=0.0,
        help="screen pixels per gif pixel, for a gif too small to place precisely",
    )
    parser.add_argument(
        "--version", action="version", version=f"action-gif-studio {__version__}"
    )

    args = parser.parse_args(argv)
    if args.gif is not None and not args.gif.is_file():
        print(f"{args.gif} is not a file", file=sys.stderr)
        return 1

    Studio(args.gif, args.yaml, args.avatar, args.zoom).mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
