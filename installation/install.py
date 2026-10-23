"""Install a stopped BoboWatch snapshot; never mount RAM or start a service."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from bobowatch.access import read_access, set_access  # noqa: E402
from bobowatch.config import load  # noqa: E402
from bobowatch.manage import busy, control_lock, loaded  # noqa: E402
from bobowatch.paths import runtime_root  # noqa: E402
from bobowatch.policy import DEFAULT_BYTES, read_policy, size_bytes  # noqa: E402
from bobowatch.provision import private_write, require_private  # noqa: E402
from bobowatch.ramdisk import mount_path  # noqa: E402


def install(root, *, repo=REPO, ram_bytes=None, port=None):
    if ram_bytes is not None and (
        type(ram_bytes) is not int or ram_bytes < 1024**2 or ram_bytes % 512
    ):
        raise ValueError("RAM size must be at least one MiB and a whole multiple of 512 bytes")
    if root.is_relative_to(repo):
        raise ValueError("Install outside the source checkout")
    if root.exists() or root.is_symlink():
        require_private(root, directory=True)
    else:
        root.mkdir(parents=True, mode=0o700)
    with control_lock(root):
        if loaded(root) or busy(root):
            raise ValueError("Stop BoboWatch before installing/updating")
        for name in ("logs", "jobs", "tokens"):
            folder = root / name
            folder.mkdir(mode=0o700, exist_ok=True)
            require_private(folder, directory=True)
        policy_path = root / "storage-policy.json"
        if not policy_path.exists():
            capacity = ram_bytes or DEFAULT_BYTES
            private_write(
                policy_path, json.dumps({"quota_bytes": capacity, "ram_bytes": capacity}).encode()
            )
        else:
            policy = read_policy(root)
            if ram_bytes is not None and ram_bytes != policy["ram_bytes"]:
                if (root / "ramdisk.json").exists():
                    raise ValueError("Release the stopped RAM volume before changing its size")
                private_write(
                    policy_path,
                    json.dumps(
                        {
                            "quota_bytes": min(policy["quota_bytes"], ram_bytes),
                            "ram_bytes": ram_bytes,
                        }
                    ).encode(),
                )
        config_path = root / "monitor.toml"
        if not config_path.exists():
            import secrets

            private_write(root / "tokens/admin.token", (secrets.token_urlsafe(32) + "\n").encode())
            # Bind all local IPv4 interfaces for phone access; ingestion from the
            # colocated adapter still uses loopback. Existing settings are preserved.
            text = (
                '[monitor]\nlisten = "0.0.0.0:' + str(port or 8720) + '"\n'
                'database_url = "sqlite:///'
                + str(mount_path(root) / "data/monitor.sqlite3")
                + '"\n'
                'admin_token_file = "tokens/admin.token"\nramdisk = true\n'
                "sample_seconds = 15\nstale_seconds = 120\ndemo = false\n"
            )
            private_write(config_path, text.encode())
        config = load(config_path)
        if not config.ram_required or config.demo:
            raise ValueError(
                "Choose a new runtime; old disk/demo history is never converted implicitly"
            )
        if port is not None and port != config.port:
            raise ValueError("Existing runtime port differs; edit monitor.toml explicitly")
        access_path = root / "dashboard-policy.json"
        if not access_path.exists() and not access_path.is_symlink():
            set_access(root, "local")
        read_access(root)  # Validate an existing explicit choice before updating.
        private_write(root / "installing", b"Installation incomplete until receipt is written.\n")
        venv.EnvBuilder(with_pip=True).create(root / "venv")
        python = root / "venv/bin/python"
        subprocess.run(
            [str(python), "-m", "pip", "install", "-r", str(repo / "requirements-core.txt")],
            check=True,
        )
        staging = root / ".app-staging"
        if staging.exists():
            require_private(staging, directory=True)
            shutil.rmtree(staging)
        staging.mkdir(mode=0o700)
        shutil.copytree(
            repo / "bobowatch",
            staging / "bobowatch",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        app = root / "app"
        if app.exists():
            require_private(app, directory=True)
            shutil.rmtree(app)
        staging.rename(app)
        private_write(root / "manage.py", (repo / "deployment/manage.py").read_bytes())
        files = {
            str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in app.rglob("*")
            if p.is_file()
        }
        files["manage.py"] = hashlib.sha256((root / "manage.py").read_bytes()).hexdigest()
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
        receipt = {"source_commit": commit, "files_sha256": files}
        private_write(root / "installation.json", (json.dumps(receipt, indent=2) + "\n").encode())
        (root / "installing").unlink()
    print("Installed BoboWatch at " + str(root))
    print("Start when wanted: ./deployment/watch start (or serve for foreground mode)")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir")
    parser.add_argument("--ram-size", type=size_bytes)
    parser.add_argument("--port", type=int)
    args = parser.parse_args(argv)
    if sys.platform != "darwin" or os.getuid() == 0 or sys.version_info < (3, 11):
        parser.error("Use Python 3.11+ on macOS as your normal user, without sudo")
    if args.port is not None and not 1024 <= args.port <= 65535:
        parser.error("Port must be between 1024 and 65535")
    os.umask(0o077)
    try:
        install(runtime_root(args.runtime_dir), ram_bytes=args.ram_size, port=args.port)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, "Installation failed: " + str(error) + "\n")


if __name__ == "__main__":
    main()
