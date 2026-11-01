"""Collection settings shared by the tests.

The window test imports Tkinter at module level, and some Pythons — the ones on
minimal Linux images in particular — are built without it. Skipping that file is
honest; failing it would only say the machine has no Tk.
"""

import importlib.util

collect_ignore = (
    [] if importlib.util.find_spec("tkinter") is not None else ["test_studio.py"]
)
