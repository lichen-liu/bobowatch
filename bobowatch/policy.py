"""Persistent storage policy; telemetry itself never leaves the RAM volume."""

import json
import re
from decimal import Decimal

from .protocol import Invalid
from .provision import private_write

DEFAULT_BYTES = 5 * 2**30
MIN_BYTES = 2**20


def size_bytes(value):
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(B|KiB|MiB|GiB)?", value, re.I)
    if not match:
        raise Invalid("Use a size such as 5GiB, 500MiB or integer bytes")
    units = {"b": 1, "kib": 2**10, "mib": 2**20, "gib": 2**30}
    size = int(Decimal(match[1]) * units[(match[2] or "B").lower()])
    if size < MIN_BYTES:
        raise Invalid("Storage must be at least 1 MiB; unlimited RAM storage is not supported")
    return size


def read_policy(root):
    path = root / "storage-policy.json"
    data = json.loads(path.read_text())
    if set(data) != {"quota_bytes", "ram_bytes"}:
        raise Invalid("Invalid storage policy")
    if any(type(data[key]) is not int or data[key] < MIN_BYTES for key in data):
        raise Invalid("Storage policy sizes must be positive integer bytes of at least 1 MiB")
    if data["quota_bytes"] > data["ram_bytes"] or data["ram_bytes"] % 512:
        raise Invalid("Quota must fit the RAM volume, whose size must be a multiple of 512 bytes")
    return data


def set_quota(root, size):
    data = read_policy(root)
    if type(size) is not int or not MIN_BYTES <= size <= data["ram_bytes"]:
        raise Invalid("Quota must be at least 1 MiB and fit the configured RAM volume")
    data["quota_bytes"] = size
    private_write(root / "storage-policy.json", (json.dumps(data, indent=2) + "\n").encode())
    return data
