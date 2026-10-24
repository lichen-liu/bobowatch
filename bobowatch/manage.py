"""Explicit on-demand macOS lifecycle; no login/boot installation or auto-load."""

import argparse
import fcntl
import hashlib
import json
import logging
import logging.handlers
import os
import plistlib
import subprocess
import sys
import time
from contextlib import contextmanager

from . import admin, ramdisk
from .config import load
from .paths import runtime_root
from .protocol import Invalid
from .provision import private_write, require_private
from .urls import dashboard_urls


def label(root):
    return "fyi.boboji.watch." + hashlib.sha256(str(root).encode()).hexdigest()[:12]


def launchctl(*args):
    return subprocess.run(["/bin/launchctl", *args], capture_output=True, text=True, timeout=20)


def loaded(root):
    return launchctl("print", f"gui/{os.getuid()}/{label(root)}").returncode == 0


def busy(root):
    path = root / "server.lock"
    if not path.exists():
        return False
    require_private(path)
    with path.open("rb") as file:
        try:
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(file, fcntl.LOCK_UN)
    return False


@contextmanager
def control_lock(root):
    require_private(root, directory=True)
    path = root / ".lifecycle.lock"
    if path.exists() or path.is_symlink():
        require_private(path)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a+b") as file:
        fcntl.flock(file, fcntl.LOCK_EX)
        yield


def verify_installation(root):
    require_private(root, directory=True)
    if (root / "installing").exists():
        raise Invalid("Installation incomplete; rerun installer while stopped")
    receipt = root / "installation.json"
    require_private(receipt)
    for name, expected in json.loads(receipt.read_text())["files_sha256"].items():
        path = root / name
        if not path.resolve().is_relative_to(root) or path.is_symlink():
            raise Invalid("Invalid installed source path")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise Invalid("Installed source differs from its receipt; reinstall while stopped")
    config = load(root / "monitor.toml")
    if not config.ram_required or config.demo:
        raise Invalid("Installed runtime requires non-demo RAM-only storage")
    return config


def job_definition(root):
    return {
        "Label": label(root),
        "ProgramArguments": [
            str(root / "venv/bin/python"),
            str(root / "manage.py"),
            "serve",
            "--runtime-dir",
            str(root),
        ],
        "WorkingDirectory": str(root),
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "ExitTimeOut": 15,
        "Umask": 0o077,
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1", "PYTHONDONTWRITEBYTECODE": "1"},
        "StandardOutPath": "/dev/null",
        "StandardErrorPath": "/dev/null",
    }


def start(root):
    with control_lock(root):
        verify_installation(root)
        if loaded(root):
            return admin.request(root, "/api/storage")
        if busy(root):
            raise Invalid("A foreground server is already running; stop it with Ctrl-C")
        ramdisk.ensure(root)
        require_private(root / "jobs", directory=True)
        path = root / "jobs" / (label(root) + ".plist")
        private_write(path, plistlib.dumps(job_definition(root)))
        result = launchctl("bootstrap", f"gui/{os.getuid()}", str(path))
        if result.returncode:
            raise Invalid("Could not load the explicitly started job: " + result.stderr.strip())
    # The child can now acquire its own short lifecycle lock before serving.
    for _ in range(100):
        try:
            return admin.request(root, "/api/storage")
        except (OSError, Invalid):
            time.sleep(0.1)
    launchctl("bootout", f"gui/{os.getuid()}/{label(root)}")
    raise Invalid("Server did not become ready; inspect logs/watch.log")


def stop(root):
    if not root.exists():
        return {"running": False}
    with control_lock(root):
        if loaded(root):
            result = launchctl("bootout", f"gui/{os.getuid()}/{label(root)}")
            if result.returncode:
                raise Invalid("Could not unload BoboWatch; inspect launchd status")
        elif busy(root):
            raise Invalid("Foreground mode is active; stop that terminal with Ctrl-C")
    for _ in range(150):
        if not busy(root):
            return {
                "running": False,
                "history": "RAM volume retained until explicitly released or lost",
            }
        time.sleep(0.1)
    raise Invalid("Server has not exited; do not reinstall until its lock is released")


def serve(root):
    logs = root / "logs"
    require_private(logs, directory=True)
    handler = logging.handlers.RotatingFileHandler(
        logs / "watch.log", maxBytes=10 * 1024**2, backupCount=3
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    with control_lock(root):
        config = verify_installation(root)
        if busy(root):
            raise Invalid("Another BoboWatch server owns this runtime")
        ramdisk.ensure(root)
    from .__main__ import serve as run

    run(config)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=["start", "stop", "serve", "status", "check", "admin", "release", "token"],
    )
    parser.add_argument("--runtime-dir")
    parser.add_argument("--yes", action="store_true", help="confirm deliberate RAM history release")
    args, words = parser.parse_known_args(argv)
    if args.command != "admin" and words:
        parser.error("Unexpected arguments")
    root = runtime_root(args.runtime_dir)
    if args.command in ("start", "stop", "serve", "release") and (
        sys.platform != "darwin" or os.getuid() == 0
    ):
        parser.error("Use macOS as your normal user, without sudo")
    os.umask(0o077)
    try:
        if args.command == "start":
            result = start(root)
        elif args.command == "stop":
            result = stop(root)
        elif args.command == "serve":
            serve(root)
            return
        elif args.command == "token":
            print(load(root / "monitor.toml").admin_token_file.read_text().strip())
            return
        elif args.command == "status":
            if not (root / "monitor.toml").exists():
                result = {"installed": False, "runtime": str(root)}
            elif busy(root):
                result = admin.request(root, "/api/storage")
            else:
                result = admin.offline_status(root)
        elif args.command == "check":
            result = admin.request(root, "/api/storage")
        elif args.command == "release":
            if not args.yes:
                raise Invalid("Use release --yes to discard the RAM-only monitoring history")
            with control_lock(root):
                if loaded(root) or busy(root):
                    raise Invalid("Stop BoboWatch before releasing its RAM volume")
                ramdisk.release(root)
                result = {"released": True, "history": "discarded"}
        else:
            require_private(root, directory=True)
            if not words:
                admin.shell(root, lambda: busy(root))
                return
            with control_lock(root):
                result = admin.execute(root, words, running=busy(root))
        if args.command in ("start", "status", "check") and (root / "monitor.toml").exists():
            result = {**result, "dashboard": dashboard_urls(load(root / "monitor.toml"))}
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        logging.getLogger(__name__).error("%s", error)
        parser.exit(1, "BoboWatch: " + str(error) + "\n")
