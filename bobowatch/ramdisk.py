"""Owned macOS RAM volumes; absent/mismatched mounts never fall back to disk."""

import hashlib
import json
import os
import plistlib
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

from .policy import read_policy
from .protocol import Invalid
from .provision import private_write, require_private


def mount_path(root):
    identity = hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:12]
    return Path("/Volumes") / ("BoboWatch-" + identity)


def images():
    return plistlib.loads(subprocess.check_output(["hdiutil", "info", "-plist"]))["images"]


def prove_image(device, size, *, mounted=None):
    matches = [
        image
        for image in images()
        if any(entity.get("dev-entry") == device for entity in image.get("system-entities", []))
    ]
    if len(matches) != 1:
        raise Invalid("Recorded RAM device is missing or ambiguous")
    image = matches[0]
    if (
        image.get("image-path") != f"ram://{size // 512}"
        or image.get("blockcount", 0) * image.get("blocksize", 0) != size
        or image.get("owner-uid") != os.getuid()
    ):
        raise Invalid("Refusing an operation on an unverified RAM device")
    if mounted is not None and not any(
        entity.get("dev-entry") == device and entity.get("mount-point") == str(mounted)
        for entity in image.get("system-entities", [])
    ):
        raise Invalid("RAM device does not match its recorded mount")
    return image


def validate(root, *, full=False):
    require_private(root, directory=True)
    record = root / "ramdisk.json"
    require_private(record)
    state = json.loads(record.read_text())
    mount = Path(state["mount"])
    if mount != mount_path(root) or not os.path.ismount(mount):
        raise Invalid("BoboWatch RAM volume is unavailable; no disk fallback")
    if mount.stat().st_dev != state["st_dev"]:
        raise Invalid("RAM volume device changed")
    require_private(mount, directory=True)
    marker = mount / ".watch-session"
    require_private(marker)
    if marker.read_text() != state["session"] or state["root"] != str(root.resolve()):
        raise Invalid("RAM volume session mismatch")
    usage = shutil.disk_usage(mount)
    if usage.total != state["capacity_bytes"]:
        raise Invalid("RAM volume capacity changed")
    if full:
        if sys.platform != "darwin":
            raise Invalid("Managed RAM volumes currently require macOS")
        prove_image(state["device"], state["capacity_bytes"], mounted=mount)
    return state


def validate_database(config, *, full=False):
    if not config.ram_required:
        return None
    state = validate(config.path.parent, full=full)
    expected = Path(state["mount"]) / "data/monitor.sqlite3"
    if config.database != expected or expected.is_symlink() or expected.parent.is_symlink():
        raise Invalid("Monitoring database must be inside its verified RAM volume")
    return state


def memory_check(size):
    physical = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True))
    pressure = subprocess.check_output(["memory_pressure", "-Q"], text=True)
    match = re.search(r"free percentage:\s*(\d+)%", pressure)
    if size > physical // 4 or not match or int(match[1]) < 20 + size * 100 / physical:
        raise Invalid("Insufficient RAM headroom for the requested volume")


def ensure(root):
    if sys.platform != "darwin":
        raise Invalid("Managed RAM volumes currently require macOS")
    policy = read_policy(root)
    record = root / "ramdisk.json"
    if record.exists() or record.is_symlink():
        require_private(record)
        state = json.loads(record.read_text())
        # If the device still exists, a failed proof is an error, never a reason
        # to format another disk. Reuse a correct existing session after crashes.
        present = any(
            any(e.get("dev-entry") == state["device"] for e in image.get("system-entities", []))
            for image in images()
        )
        if present:
            if not os.path.ismount(mount_path(root)):
                prove_image(state["device"], state["capacity_bytes"])
                subprocess.run(
                    ["diskutil", "mount", state["device"]], check=True, stdout=subprocess.DEVNULL
                )
            validate(root, full=True)
            if state["capacity_bytes"] != policy["ram_bytes"]:
                raise Invalid("Stop and release the old RAM volume before changing its size")
            return state
        if os.path.ismount(mount_path(root)):
            raise Invalid("An unrelated device occupies the requested mount")
    mount = mount_path(root)
    if mount.exists():
        raise Invalid("RAM mount path already exists; inspect it before retrying")
    size = policy["ram_bytes"]
    memory_check(size)
    output = subprocess.check_output(
        ["hdiutil", "attach", "-nomount", f"ram://{size // 512}"], text=True
    )
    devices = re.findall(r"^(/dev/disk\d+)\s*$", output, re.M)
    if len(devices) != 1:
        raise Invalid("Unexpected RAM device creation response")
    device = devices[0]
    prove_image(device, size)
    try:
        subprocess.run(
            ["diskutil", "eraseVolume", "HFS+", mount.name, device],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        if not os.path.ismount(mount) or shutil.disk_usage(mount).total != size:
            raise Invalid("Formatted RAM capacity/mount does not match the request")
        mount.chmod(0o700)
        (mount / "data").mkdir(mode=0o700)
        session = secrets.token_hex(32)
        private_write(mount / ".watch-session", session.encode())
        state = dict(
            root=str(root.resolve()),
            mount=str(mount),
            device=device,
            capacity_bytes=size,
            st_dev=mount.stat().st_dev,
            session=session,
        )
        private_write(record, (json.dumps(state, indent=2) + "\n").encode())
        validate(root, full=True)
        return state
    except BaseException:
        prove_image(device, size)
        subprocess.run(["hdiutil", "detach", device], check=True, stdout=subprocess.DEVNULL)
        raise


def release(root):
    # A reboot can already have destroyed the device. Forget only its private
    # metadata in that case; never detach an unrelated replacement device.
    record = root / "ramdisk.json"
    if not os.path.ismount(mount_path(root)):
        require_private(record)
        state = json.loads(record.read_text())
        present = any(
            any(e.get("dev-entry") == state["device"] for e in image.get("system-entities", []))
            for image in images()
        )
        if not present:
            record.unlink()
            return
        prove_image(state["device"], state["capacity_bytes"])
        subprocess.run(
            ["hdiutil", "detach", state["device"]], check=True, stdout=subprocess.DEVNULL
        )
        record.unlink()
        return
    state = validate(root, full=True)
    subprocess.run(["hdiutil", "detach", state["device"]], check=True, stdout=subprocess.DEVNULL)
    (root / "ramdisk.json").unlink()
