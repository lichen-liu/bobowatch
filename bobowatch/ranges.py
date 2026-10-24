"""Bounded dashboard query choices and half-open absolute timestamp ranges."""

import re
import time

from .protocol import Invalid

WINDOWS = (30, 60, 300, 900, 1800, 3600, 21600, 43200, 86400, 604800, 2592000, 7776000)


def validate_bounds(start, end, now):
    if type(start) is not int or type(end) is not int or not 0 <= start < end:
        raise Invalid("Use a start time before the end time, on or after 1970-01-01")
    if end > now + 30_000:
        raise Invalid("The end time cannot be in the future")


def overview_range(query, *, now_ms=None):
    if "start_ms" in query or "end_ms" in query:
        if "window" in query or not {"start_ms", "end_ms"} <= query.keys():
            raise Invalid("Use either a rolling window or both start_ms and end_ms")
        values = [query[key][0] for key in ("start_ms", "end_ms")]
        if any(not re.fullmatch(r"[0-9]{1,16}", value) for value in values):
            raise Invalid("Range bounds must be integer Unix timestamps in milliseconds")
        start, end = map(int, values)
        validate_bounds(start, end, int(time.time() * 1000) if now_ms is None else now_ms)
        return {"start_ms": start, "end_ms": end}
    window = query.get("window", ["300"])[0]
    if window not in {str(value) for value in WINDOWS}:
        raise Invalid("Unsupported time window")
    return {"window": int(window)}
