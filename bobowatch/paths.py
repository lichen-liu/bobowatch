"""Explicit installed-runtime resolution; no filesystem or process side effects."""

import os
import sys
from pathlib import Path


def runtime_root(value=None):
    default = (
        Path.home() / "Library/Application Support/fyi.boboji.watch"
        if sys.platform == "darwin"
        else Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "bobowatch"
    )
    path = Path(value or os.environ.get("BOBOWATCH_RUNTIME_DIR", default)).expanduser()
    if not path.is_absolute():
        raise ValueError("BoboWatch runtime directory must be absolute")
    return path.resolve()
