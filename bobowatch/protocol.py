"""Small versioned wire contract shared by producer and server."""

import math
import re
import uuid

BUCKETS_US = (
    5_000,
    10_000,
    25_000,
    50_000,
    100_000,
    250_000,
    500_000,
    1_000_000,
    2_500_000,
    5_000_000,
    10_000_000,
    30_000_000,
    120_000_000,
)
MAX_BODY = 262_144
MAX_INT = 2**63 - 1
ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}\Z")


class Invalid(ValueError):
    pass


class Conflict(ValueError):
    pass


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise Invalid("IDs must contain 1–128 letters, numbers, dots, dashes or underscores")
    return value


def integer(value, name, minimum=0):
    if type(value) is not int or not minimum <= value <= MAX_INT:
        raise Invalid(f"{name} must be an integer between {minimum} and {MAX_INT}")
    return value


def text(value, name, limit):
    if not isinstance(value, str) or len(value) > limit:
        raise Invalid(f"{name} must be text with at most {limit} characters")
    return value


def validate(payload):
    if not isinstance(payload, dict):
        raise Invalid("Expected a JSON object")
    required = {
        "version",
        "replica",
        "run_id",
        "revision",
        "sequence",
        "started_at_ms",
        "observed_at_ms",
        "elapsed_ms",
        "requests",
        "errors",
        "duration_sum_us",
        "buckets",
        "events",
    }
    version = payload.get("version")
    if version == 2:
        required.add("http")
    if set(payload) != required or type(version) is not int or version not in (1, 2):
        raise Invalid("Unsupported version or unexpected snapshot fields")
    identifier(payload["replica"])
    try:
        if str(uuid.UUID(payload["run_id"])) != payload["run_id"]:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise Invalid("run_id must be a canonical UUID") from None
    text(payload["revision"], "revision", 128)
    for name in (
        "sequence",
        "started_at_ms",
        "observed_at_ms",
        "elapsed_ms",
        "requests",
        "errors",
        "duration_sum_us",
    ):
        integer(payload[name], name)
    if payload["errors"] > payload["requests"]:
        raise Invalid("Errors cannot exceed requests")
    if payload["observed_at_ms"] < payload["started_at_ms"]:
        raise Invalid("Observation precedes process start")
    buckets = payload["buckets"]
    if not isinstance(buckets, list) or len(buckets) != len(BUCKETS_US) + 1:
        raise Invalid("Incompatible histogram schema")
    for value in buckets:
        integer(value, "bucket")
    if sum(buckets) != payload["requests"]:
        raise Invalid("Histogram count does not equal request count")
    events = payload["events"]
    if not isinstance(events, list) or len(events) > 100:
        raise Invalid("At most 100 events per snapshot")
    ids = set()
    for event in events:
        if not isinstance(event, dict) or set(event) != {"id", "at_ms", "severity", "message"}:
            raise Invalid("Invalid event fields")
        identifier(event["id"])
        integer(event["at_ms"], "event timestamp")
        if event["severity"] not in ("info", "warning", "error"):
            raise Invalid("Invalid event severity")
        text(event["message"], "event message", 1024)
        if event["id"] in ids:
            raise Invalid("Duplicate event IDs in snapshot")
        ids.add(event["id"])
    if version == 2:
        validate_http(payload)
    return payload


def percentile(buckets, fraction=0.95):
    total = sum(buckets)
    if not total:
        return None
    target, count = math.ceil(total * fraction), 0
    for index, value in enumerate(buckets):
        count += value
        if count >= target:
            return {
                "upper_ms": BUCKETS_US[index] / 1000 if index < len(BUCKETS_US) else None,
                "overflow": index == len(BUCKETS_US),
                "count": total,
            }


def validate_http(payload):
    data = payload["http"]
    fields = {
        "in_flight",
        "bytes_sent",
        "bytes_received",
        "sized_requests",
        "status_classes",
        "routes",
    }
    if not isinstance(data, dict) or set(data) != fields:
        raise Invalid("Invalid HTTP metric fields")
    if data["in_flight"] is not None:
        integer(data["in_flight"], "in_flight")
    for key in ("bytes_sent", "bytes_received", "sized_requests"):
        integer(data[key], key)
    if data["sized_requests"] == 0 and (data["bytes_sent"] or data["bytes_received"]):
        raise Invalid("Body bytes require sized request observations")
    if data["sized_requests"] > payload["requests"]:
        raise Invalid("Sized request count exceeds requests")
    codes = data["status_classes"]
    if not isinstance(codes, list) or len(codes) != 5:
        raise Invalid("Expected five HTTP status classes")
    for value in codes:
        integer(value, "status class")
    if sum(codes) != payload["requests"] or codes[4] != payload["errors"]:
        raise Invalid("HTTP status totals do not match request/error counts")
    routes = data["routes"]
    if not isinstance(routes, list) or not 1 <= len(routes) <= 21:
        raise Invalid("Expected at most 20 registered routes plus unmatched")
    seen = set()
    for route in routes:
        if not isinstance(route, dict) or set(route) != {
            "route",
            "requests",
            "errors",
            "duration_sum_us",
        }:
            raise Invalid("Invalid route metric fields")
        name = text(route["route"], "route", 128)
        if not name or name in seen or "?" in name:
            raise Invalid("Duplicate, empty or query-bearing route name")
        seen.add(name)
        for key in ("requests", "errors", "duration_sum_us"):
            integer(route[key], key)
        if route["errors"] > route["requests"]:
            raise Invalid("Route errors exceed requests")
    for key in ("requests", "errors", "duration_sum_us"):
        if sum(route[key] for route in routes) != payload[key]:
            raise Invalid("Route totals do not match snapshot totals")
