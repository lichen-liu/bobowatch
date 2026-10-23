"""Operator-owned inventory and private runtime initialization."""

import copy
import hashlib
import json
import math
import re
import secrets
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .protocol import Invalid, identifier, text


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class Config:
    path: Path
    database: Path
    host: str
    port: int
    stale_seconds: float
    sample_seconds: float
    demo: bool
    admin_token_file: Path
    projects: tuple
    sites: tuple
    hosts: tuple
    replicas: tuple
    allowed_hosts: tuple[str, ...] = ()
    ram_required: bool = False
    quota_bytes: int | None = None


def load(path, *, registry=None):
    path = Path(path).expanduser().resolve()
    with path.open("rb") as file:
        data = tomllib.load(file)
    if set(data) - {"monitor", "projects", "sites", "hosts", "replicas"}:
        raise Invalid("Unknown configuration section")
    registry_path = path.parent / "registrations.json"
    if registry is None:
        registry = json.loads(registry_path.read_text()) if registry_path.exists() else {}
    if not isinstance(registry, dict) or set(registry) - {"projects", "sites", "hosts", "replicas"}:
        raise Invalid("Invalid registration inventory")
    registry = copy.deepcopy(registry)
    for group, entries in registry.items():
        if not isinstance(entries, list):
            raise Invalid("Registration inventory must contain lists")
        data[group] = data.get(group, []) + entries
    monitor = data.get("monitor", {})
    if set(monitor) - {
        "listen",
        "database_url",
        "stale_seconds",
        "sample_seconds",
        "demo",
        "admin_token_file",
        "allowed_hosts",
        "ramdisk",
    }:
        raise Invalid("Unknown monitor setting")
    host, port = monitor.get("listen", "127.0.0.1:8720").rsplit(":", 1)
    if host not in ("0.0.0.0", "127.0.0.1", "localhost") or not 1 <= int(port) <= 65535:
        raise Invalid("Use 127.0.0.1, localhost or 0.0.0.0 as the listen address")
    allowed_hosts = monitor.get("allowed_hosts", [])
    if not isinstance(allowed_hosts, list) or len(allowed_hosts) > 20:
        raise Invalid("allowed_hosts must be a list of at most 20 hostnames")
    for value in allowed_hosts:
        if (
            not isinstance(value, str)
            or len(value) > 253
            or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", value)
            or any(
                not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
                for label in value.split(".")
            )
        ):
            raise Invalid("allowed_hosts entries must be hostnames without ports or URLs")
    url = monitor.get("database_url", "sqlite:///monitor.sqlite3")
    if not url.startswith("sqlite:///"):
        raise Invalid("Only the SQLite backend is implemented")
    database = (path.parent / url[len("sqlite:///") :]).resolve()
    sample, stale = monitor.get("sample_seconds", 15), monitor.get("stale_seconds", 120)
    if (
        type(sample) not in (int, float)
        or type(stale) not in (int, float)
        or not math.isfinite(sample)
        or not math.isfinite(stale)
        or sample < 0.1
        or stale < 2 * sample
    ):
        raise Invalid("Invalid sample/stale intervals; stale must be at least twice sample")
    groups = {}
    allowed = {
        "projects": {"id", "title"},
        "hosts": {"id", "title"},
        "sites": {"id", "title", "project", "environment"},
        "replicas": {"id", "title", "site", "host", "token_file", "retired"},
    }
    for group, keys in allowed.items():
        entries = data.get(group, [])
        if not isinstance(entries, list) or len(entries) > 100:
            raise Invalid(f"{group}: expected at most 100 entries")
        ids = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise Invalid("Inventory entries must be objects")
            if set(entry) - keys:
                raise Invalid(f"Unknown {group} setting")
            key = identifier(entry.get("id"))
            if key in ids:
                raise Invalid(f"Duplicate {group} ID")
            ids.add(key)
            text(entry.get("title"), "title", 256)
        groups[group] = entries
    for site in groups["sites"]:
        if site.get("project") not in {p["id"] for p in groups["projects"]}:
            raise Invalid("Site references an unknown project")
        identifier(site.get("environment"))
    for replica in groups["replicas"]:
        if replica.get("site") not in {s["id"] for s in groups["sites"]} or replica.get(
            "host"
        ) not in {h["id"] for h in groups["hosts"]}:
            raise Invalid("Replica references an unknown site/host")
        if type(replica.get("retired", False)) is not bool:
            raise Invalid("retired must be true or false")
        replica["token_file"] = (path.parent / replica["token_file"]).resolve()
        token = replica["token_file"].read_text().strip()
        if len(token) < 32:
            raise Invalid("Producer tokens must contain at least 32 characters")
        replica["token_hash"] = token_hash(token)
    admin = (path.parent / monitor.get("admin_token_file", "admin.token")).resolve()
    if len(admin.read_text().strip()) < 32:
        raise Invalid("Admin token is missing or too short")
    hashes = [r["token_hash"] for r in groups["replicas"]] + [token_hash(admin.read_text().strip())]
    if len(set(hashes)) != len(hashes):
        raise Invalid("Admin and each producer must have distinct tokens")
    if type(monitor.get("demo", False)) is not bool:
        raise Invalid("demo must be true or false")
    ram_required = monitor.get("ramdisk", False)
    if type(ram_required) is not bool:
        raise Invalid("ramdisk must be a boolean")
    policy_path = path.parent / "storage-policy.json"
    quota = None
    if policy_path.exists():
        policy = json.loads(policy_path.read_text())
        quota = policy.get("quota_bytes")
        capacity = policy.get("ram_bytes")
        if (
            type(quota) is not int
            or type(capacity) is not int
            or quota < 1024**2
            or quota > capacity
            or capacity % 512
        ):
            raise Invalid("Invalid storage quota/RAM capacity")
    elif ram_required:
        raise Invalid("RAM storage requires persistent storage-policy.json")
    return Config(
        path,
        database,
        host,
        int(port),
        stale,
        sample,
        monitor.get("demo", False),
        admin,
        *(tuple(groups[name]) for name in ("projects", "sites", "hosts", "replicas")),
        allowed_hosts=tuple(value.lower() for value in allowed_hosts),
        ram_required=ram_required,
        quota_bytes=quota,
    )


def initialize(directory, *, demo=False, port=8720):
    directory = Path(directory).expanduser().resolve()
    source = Path(__file__).resolve().parent.parent
    if directory.is_relative_to(source):
        raise Invalid("Keep runtime data outside the source checkout")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / "monitor.toml"
    if path.exists():
        config = load(path)
        if config.demo != demo:
            raise Invalid("Choose a separate runtime directory for demo and real data")
        if config.port != port:
            raise Invalid("Existing runtime uses a different port; edit monitor.toml explicitly")
        return config
    if any(directory.iterdir()):
        raise Invalid("Initialize in an empty directory to avoid overwriting existing state")

    def secret(name):
        target = directory / name
        with target.open("x") as file:
            target.chmod(0o600)
            file.write(secrets.token_urlsafe(32) + "\n")

    secret("admin.token")
    contents = f"""[monitor]
listen = "127.0.0.1:{port}"
database_url = "sqlite:///monitor.sqlite3"
admin_token_file = "admin.token"
sample_seconds = {2 if demo else 15}
stale_seconds = {10 if demo else 120}
demo = {str(demo).lower()}
"""
    if demo:
        for project, title in [("travel", "Travel tools"), ("household", "Household tools")]:
            contents += f'\n[[projects]]\nid = "{project}"\ntitle = "{title}"\n'
        contents += '\n[[hosts]]\nid = "demo-host"\ntitle = "Simulated host"\n'
        for project, site, title in [
            ("travel", "flights", "Flight site"),
            ("household", "budget", "Budget site"),
        ]:
            contents += f'''\n[[sites]]
id = "{site}"
title = "{title}"
project = "{project}"
environment = "demo"
'''
            for number in (1, 2):
                token_name = site + ("" if number == 1 else "-r02") + ".token"
                secret(token_name)
                contents += f'''\n[[replicas]]
id = "{site}-r{number:02d}"
title = "Replica {number:02d}"
site = "{site}"
host = "demo-host"
token_file = "{token_name}"
'''
    with path.open("x") as file:
        path.chmod(0o600)
        file.write(contents)
    return load(path)
