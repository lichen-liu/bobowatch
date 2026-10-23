"""Local command/shell client. Stopped-runtime inspection never creates a DB."""

import json
import shlex
import sqlite3
from contextlib import closing
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener

from .access import read_access, set_access
from .config import load
from .policy import read_policy, set_quota, size_bytes
from .protocol import Invalid
from .ramdisk import validate
from .retention import family_bytes

HELP = (
    "storage status | storage quota [5GiB] | storage prune | "
    "dashboard access [local|token] | registrations list | config reload | quit"
)


def request(root, path, data=None):
    config = load(root / "monitor.toml")
    token = config.admin_token_file.read_text().strip()
    req = Request(
        f"http://127.0.0.1:{config.port}{path}",
        None if data is None else json.dumps(data).encode(),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    try:
        with build_opener(ProxyHandler({})).open(req, timeout=10) as response:
            return json.load(response)
    except HTTPError as error:
        error.close()
        raise Invalid(f"Administrator request rejected (HTTP {error.code})") from None


def offline_status(root):
    config = load(root / "monitor.toml")
    result = {"running": False, "runtime": str(root), **read_policy(root), "available": False}
    try:
        state = validate(root)
        result.update(
            available=True,
            mount=state["mount"],
            database=str(config.database),
            used_bytes=family_bytes(config.database),
        )
        if config.database.is_file():
            with closing(sqlite3.connect(config.database.as_uri() + "?mode=ro", uri=True)) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute("SELECT * FROM storage_state WHERE id=1").fetchone()
                if row:
                    result.update(dict(row))
    except (OSError, ValueError, sqlite3.Error):
        pass
    return result


def execute(root, words, *, running=False):
    if words[:2] == ["dashboard", "access"] and len(words) in (2, 3):
        if len(words) == 2:
            return {"dashboard_access": read_access(root)}
        if running:
            return request(
                root, "/api/control", {"command": "dashboard.access", "access": words[2]}
            )
        return set_access(root, words[2]) | {"applies": "next server start"}
    if words == ["storage", "status"]:
        if running:
            return request(root, "/api/storage")
        return offline_status(root)
    if words[:2] == ["storage", "quota"] and len(words) in (2, 3):
        if len(words) == 2:
            return read_policy(root)
        quota = size_bytes(words[2])
        if running:
            return request(root, "/api/control", {"command": "storage.quota", "quota_bytes": quota})
        return set_quota(root, quota) | {"applies": "next server start"}
    if words == ["registrations", "list"]:
        config = load(root / "monitor.toml")
        return [
            {key: row[key] for key in ("id", "title", "site", "host")}
            | {"retired": row.get("retired", False)}
            for row in config.replicas
        ]
    if words in (["config", "reload"], ["storage", "prune"]):
        if not running:
            return {"running": False, "applies": "next server start"}
        return request(root, "/api/control", {"command": ".".join(words)})
    raise Invalid("Unknown admin command. " + HELP)


def shell(root, running):
    print("BoboWatch administrator: " + str(root))
    print(HELP)
    while True:
        try:
            line = input("watch> ").strip()
            if line in ("quit", "exit"):
                return
            if not line:
                continue
            if line == "help":
                print(HELP)
                continue
            print(json.dumps(execute(root, shlex.split(line), running=running()), indent=2))
        except (EOFError, KeyboardInterrupt):
            print()
            return
        except (OSError, ValueError, URLError) as error:
            print("Error: " + str(error))
