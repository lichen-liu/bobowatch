"""Small local administrator API. No arbitrary SQL or public provisioning."""

import json

from .policy import set_quota
from .protocol import Invalid


def dispatch(application, env, path):
    if path == "/api/storage":
        if env["REQUEST_METHOD"] != "GET":
            return 405, {"error": "Use GET"}, "application/json"
        return 200, application.store.storage_status(), "application/json"
    if env["REQUEST_METHOD"] != "POST":
        return 405, {"error": "Use POST"}, "application/json"
    try:
        length = int(env.get("CONTENT_LENGTH", "0"))
        if not 0 < length <= 4096:
            raise Invalid("Control request must be between 1 and 4096 bytes")
        data = json.loads(env["wsgi.input"].read(length))
    except (ValueError, UnicodeError):
        raise Invalid("Invalid control request") from None
    if not isinstance(data, dict) or set(data) - {"command", "quota_bytes"}:
        raise Invalid("Invalid control fields")
    command = data.get("command")
    with application._reload_lock:
        if command == "storage.quota":
            if not application.config.ram_required:
                raise Invalid("Persistent quota changes require an installed RAM runtime")
            set_quota(application.config.path.parent, data.get("quota_bytes"))
            application.reload()
            return 202, application.store.maintain(), "application/json"
        if command == "storage.prune":
            return 200, application.store.maintain(), "application/json"
        if command == "config.reload":
            return 200, {"reloaded": application.reload()}, "application/json"
    raise Invalid("Unknown control command")
