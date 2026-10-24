"""Explicit stop/install/start/check from this checkout; retain RAM history."""

import argparse
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from bobowatch import admin, manage  # noqa: E402
from bobowatch.config import load  # noqa: E402
from bobowatch.paths import runtime_root  # noqa: E402
from bobowatch.policy import size_bytes  # noqa: E402
from bobowatch.urls import print_dashboard  # noqa: E402


def deploy(root, *, ram_bytes=None, port=None):
    spec = importlib.util.spec_from_file_location(
        "watch_installer", REPO / "installation/install.py"
    )
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    manage.stop(root)
    installer.install(root, repo=REPO, ram_bytes=ram_bytes, port=port)
    try:
        manage.start(root)
        return admin.request(root, "/api/storage")
    except BaseException as error:
        try:
            manage.stop(root)
        except Exception as cleanup_error:
            raise RuntimeError(f"Deployment failed; cleanup incomplete: {cleanup_error}") from error
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["deploy"])
    parser.add_argument("--runtime-dir")
    parser.add_argument("--ram-size", type=size_bytes)
    parser.add_argument("--port", type=int)
    args = parser.parse_args(argv)
    if sys.platform != "darwin" or os.getuid() == 0 or sys.version_info < (3, 11):
        parser.error("Use Python 3.11+ on macOS as your normal user, without sudo")
    if args.port is not None and not 1024 <= args.port <= 65535:
        parser.error("Port must be between 1024 and 65535")
    if args.ram_size is not None and args.ram_size % 512:
        parser.error("RAM size must be a whole multiple of 512 bytes")
    os.umask(0o077)
    try:
        root = runtime_root(args.runtime_dir)
        result = deploy(root, ram_bytes=args.ram_size, port=args.port)
        print("BoboWatch deployed and checked; history epoch: " + result["epoch"])
        print_dashboard(load(root / "monitor.toml"))
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as error:
        parser.exit(1, "Deployment failed: " + str(error) + "\n")


if __name__ == "__main__":
    main()
