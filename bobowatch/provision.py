"""Idempotent local registration. No HTTP, database access or process startup."""

import fcntl
import json
import os
import secrets
import stat
import tempfile
from pathlib import Path

from .config import load
from .paths import runtime_root
from .protocol import Invalid, identifier


def require_private(path, *, directory=False):
    info = path.lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not kind(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise Invalid("Provisioning paths must be owned by you, private and not symlinks")


def private_write(path, content):
    path = Path(path)
    require_private(path.parent, directory=True)
    if path.exists() or path.is_symlink():
        require_private(path)
    fd, temporary = tempfile.mkstemp(prefix=".watch-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def ensure_registration(
    *, runtime=None, project, site, host, replica, token_file, environment="production"
):
    """Return public connection settings; write secrets directly to private files.

    Both local runtimes must belong to the same operating-system user. Cross-host
    provisioning is intentionally outside this first interface.
    """
    root = runtime_root(runtime)
    require_private(root, directory=True)
    require_private(root / "monitor.toml")
    for value in (project, site, host, replica, environment):
        identifier(value)
    output = Path(token_file).expanduser()
    if not output.is_absolute():
        raise Invalid("Producer token destination must be absolute")
    require_private(output.parent, directory=True)
    lock_path = root / ".provision.lock"
    if lock_path.exists() or lock_path.is_symlink():
        require_private(lock_path)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        config = load(root / "monitor.toml")
        if config.demo:
            raise Invalid("Register real applications in a non-demo runtime")
        registry_path = root / "registrations.json"
        if registry_path.exists() or registry_path.is_symlink():
            require_private(registry_path)
            registry = json.loads(registry_path.read_text())
        else:
            registry = {name: [] for name in ("projects", "sites", "hosts", "replicas")}
        definitions = {
            "projects": {"id": project, "title": project},
            "hosts": {"id": host, "title": host},
            "sites": {"id": site, "title": site, "project": project, "environment": environment},
        }
        for group, desired in definitions.items():
            existing = next(
                (row for row in getattr(config, group) if row["id"] == desired["id"]), None
            )
            if existing:
                for key in ("project", "environment"):
                    if key in desired and existing[key] != desired[key]:
                        raise Invalid("Existing site identity does not match registration")
            else:
                registry.setdefault(group, []).append(desired)
        existing = next((row for row in config.replicas if row["id"] == replica), None)
        if existing:
            if existing["site"] != site or existing["host"] != host or existing.get("retired"):
                raise Invalid("Existing replica identity does not match registration")
            source = existing["token_file"]
            require_private(source)
            token = source.read_bytes()
        else:
            folder = root / "tokens"
            folder.mkdir(mode=0o700, exist_ok=True)
            require_private(folder, directory=True)
            source = folder / (replica + ".token")
            if source.exists() or source.is_symlink():
                require_private(source)
                token = source.read_bytes()
            else:
                token = (secrets.token_urlsafe(32) + "\n").encode()
                private_write(source, token)
            registry.setdefault("replicas", []).append(
                {
                    "id": replica,
                    "title": replica,
                    "site": site,
                    "host": host,
                    "token_file": str(source.relative_to(root)),
                }
            )
        # Validate the complete candidate before publishing a single registry file.
        candidate = load(root / "monitor.toml", registry=registry)
        private_write(output, token)
        private_write(registry_path, (json.dumps(registry, indent=2) + "\n").encode())
        return {
            "enabled": True,
            "endpoint": f"http://127.0.0.1:{candidate.port}/v1/ingest",
            "replica": replica,
            "token_file": str(output),
            "interval": candidate.sample_seconds,
        }
