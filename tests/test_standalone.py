"""The packaging promise: this tool needs nothing but Pillow, PyYAML and Tk.

Checked in a fresh interpreter that imports the tool and reports which
top-level packages it pulled in. A chat-platform or HTTP dependency creeping into
the package would show up here instead of in somebody's install.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

CHECK = (
    "import sys, action_studio.ui, action_studio.editor; "
    "names = {m.split('.')[0] for m in sys.modules}; "
    "print(','.join(sorted(names & {'discord', 'httpx', 'aiohttp', 'app'})))"
)


def test_no_network_or_platform_package_is_imported() -> None:
    result = subprocess.run(
        [sys.executable, "-c", CHECK],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.stderr == ""
    assert result.stdout.strip() == ""
