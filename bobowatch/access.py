"""Dashboard-only access policy, separate from producer and control credentials."""

import json

from .protocol import Invalid
from .provision import private_write, require_private


def read_access(root):
    path = root / "dashboard-policy.json"
    if not path.exists() and not path.is_symlink():
        return "token"  # Legacy init/demo runtimes retain their existing behavior.
    require_private(path)
    data = json.loads(path.read_text())
    if (
        not isinstance(data, dict)
        or set(data) != {"access"}
        or data["access"]
        not in (
            "local",
            "token",
        )
    ):
        raise Invalid("Dashboard access must be local or token")
    return data["access"]


def set_access(root, mode):
    require_private(root, directory=True)
    if mode not in ("local", "token"):
        raise Invalid("Dashboard access must be local or token")
    private_write(root / "dashboard-policy.json", (json.dumps({"access": mode}) + "\n").encode())
    return {"dashboard_access": mode}
