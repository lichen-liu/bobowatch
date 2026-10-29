"""Relational HTTP metric storage and bounded dashboard aggregations."""

from .protocol import BUCKETS_US, Conflict, percentile


def store_http(conn, key, payload, previous, *, baseline=False):
    data = payload.get("http")
    before = (
        conn.execute("SELECT * FROM http_samples WHERE snapshot_id=?", (previous["id"],)).fetchone()
        if previous
        else None
    )
    if previous and bool(data) != bool(before):
        raise Conflict("Start a new process run when changing HTTP metric protocol versions")
    if data is None:
        return
    if baseline:
        before = data
    values = {}
    for name in ("bytes_sent", "bytes_received", "sized_requests"):
        values[name] = data[name] - (before[name] if before else 0)
        if values[name] < 0:
            raise Conflict("Cumulative HTTP counters decreased")
    if values["sized_requests"] == 0 and (values["bytes_sent"] or values["bytes_received"]):
        raise Conflict("Body-byte deltas require new sized request observations")
    if values["sized_requests"] > payload["requests"] - (
        previous["requests"] if previous else payload["requests"] if baseline else 0
    ):
        raise Conflict("Sized-request delta exceeds request delta")
    conn.execute(
        "INSERT INTO http_samples VALUES (?,?,?,?,?,?,?,?)",
        (
            key,
            data["in_flight"],
            data["bytes_sent"],
            data["bytes_received"],
            data["sized_requests"],
            values["bytes_sent"],
            values["bytes_received"],
            values["sized_requests"],
        ),
    )
    old_codes = (
        [
            r["total"]
            for r in conn.execute(
                "SELECT total FROM response_classes WHERE snapshot_id=? ORDER BY status_class",
                (previous["id"],),
            )
        ]
        if previous
        else data["status_classes"]
        if baseline
        else [0] * 5
    )
    for index, (total, old) in enumerate(zip(data["status_classes"], old_codes, strict=True)):
        if total < old:
            raise Conflict("Status-class counter decreased")
        conn.execute(
            "INSERT INTO response_classes VALUES (?,?,?,?)", (key, index + 1, total, total - old)
        )
    old_routes = (
        {
            r["route"]: dict(r)
            for r in conn.execute(
                "SELECT * FROM route_totals WHERE snapshot_id=?", (previous["id"],)
            )
        }
        if previous
        else {r["route"]: r for r in data["routes"]}
        if baseline
        else {}
    )
    if previous and set(old_routes) != {r["route"] for r in data["routes"]}:
        raise Conflict("Registered routes changed inside a process run")
    for route in data["routes"]:
        old = old_routes.get(route["route"], {})
        delta = [
            route[name] - old.get(name, 0) for name in ("requests", "errors", "duration_sum_us")
        ]
        if min(delta) < 0 or delta[1] > delta[0]:
            raise Conflict("Route counters decreased or errors exceed interval requests")
        conn.execute(
            "INSERT INTO route_totals VALUES (?,?,?,?,?,?,?,?)",
            (
                key,
                route["route"],
                route["requests"],
                route["errors"],
                route["duration_sum_us"],
                *delta,
            ),
        )


def load_http(conn, rows):
    by_id = {row["id"]: row for row in rows}
    for row in rows:
        row["http"] = None
        row["routes"] = []
    for offset in range(0, len(rows), 200):
        keys = [row["id"] for row in rows[offset : offset + 200]]
        marks = ",".join("?" for _ in keys)
        for data in conn.execute(
            f"SELECT * FROM http_samples WHERE snapshot_id IN ({marks})", keys
        ):
            by_id[data["snapshot_id"]]["http"] = {**dict(data), "codes": [0] * 5}
        for code in conn.execute(
            f"SELECT * FROM response_classes WHERE snapshot_id IN ({marks})", keys
        ):
            by_id[code["snapshot_id"]]["http"]["codes"][code["status_class"] - 1] = code["delta"]
        for route in conn.execute(
            f"SELECT * FROM route_totals WHERE snapshot_id IN ({marks})", keys
        ):
            by_id[route["snapshot_id"]]["routes"].append(dict(route))


def endpoint_totals(routes):
    """Return every recorded template, including idle, quiet or failing routes."""
    return sorted(routes, key=lambda r: (-r["requests"], r["site"], r["route"]))


def summary(rows, *, include_routes=True):
    counts = sum(row["delta_requests"] for row in rows)
    duration = sum(row["delta_duration_us"] for row in rows)
    buckets = [sum(row["buckets"][i] for row in rows) for i in range(len(BUCKETS_US) + 1)]
    extended = [row for row in rows if row["http"] is not None]
    # SQL groups retain source counts. A boolean group flag alone cannot tell
    # whether one or many source observations had incomplete or skewed timing.
    quality = {
        "snapshots": sum(row.get("snapshot_count", 1) for row in rows),
        "partial": sum(
            row.get("partial_snapshot_count", int(bool(row.get("partial")))) for row in rows
        ),
        "skewed": sum(
            row.get("skewed_snapshot_count", int(bool(row.get("skewed")))) for row in rows
        ),
        "extended_http": sum(row.get("snapshot_count", 1) for row in extended),
    }
    codes = [sum(row["http"]["codes"][i] for row in extended) for i in range(5)]
    sized = sum(row["http"]["delta_sized_requests"] for row in extended)
    sent = sum(row["http"]["delta_bytes_sent"] for row in extended)
    received = sum(row["http"]["delta_bytes_received"] for row in extended)
    slow = sum(buckets[BUCKETS_US.index(1_000_000) + 1 :])
    routes = {}
    for row in rows if include_routes else ():
        for route in row["routes"]:
            cell = routes.setdefault(
                (row["site"], route["route"]),
                {
                    "site": row["site"],
                    "route": route["route"],
                    "requests": 0,
                    "errors": 0,
                    "duration_us": 0,
                },
            )
            cell["requests"] += route["delta_requests"]
            cell["errors"] += route["delta_errors"]
            cell["duration_us"] += route["delta_duration_us"]
    return {
        "requests": counts,
        "duration_us": duration,
        "errors": sum(row["delta_errors"] for row in rows),
        "latency": {
            name: percentile(buckets, fraction)
            for name, fraction in [("p50", 0.5), ("p95", 0.95), ("p99", 0.99)]
        },
        "mean_ms": duration / counts / 1000 if counts else None,
        "buckets": buckets,
        "status_classes": codes if extended else None,
        "http_requests": sum(row["delta_requests"] for row in extended),
        "sized_requests": sized,
        "slow_requests": slow,
        "slow_request_pct": slow / counts * 100 if counts else None,
        "bytes_sent": sent,
        "bytes_received": received,
        "mean_response_bytes": sent / sized if sized else None,
        "mean_request_bytes": received / sized if sized else None,
        "routes": endpoint_totals(routes.values()),
        "route_count": len(routes),
        "snapshot_count": quality["snapshots"],
        "quality": quality,
    }


def observation_key(row):
    latest = row.get("latest", row)
    return (
        latest["observed_at_ms"],
        latest["received_at_ms"],
        latest.get("run_started_at_ms", 0),
        latest.get("sequence", 0),
    )


def charts(rows, replicas, step, now, stale_ms, *, since=None):
    cells = {}
    for row in rows:
        at = row["observed_at_ms"] // step * step
        if since is not None:
            at = max(at, since)
        cell = cells.setdefault(at, [])
        cell.append(row)
    result = []
    expected = {replica["id"] for replica in replicas if not replica.get("retired")}
    for at, members in sorted(cells.items()):
        metrics = summary(members, include_routes=False)
        by_replica = {}
        for row in members:
            by_replica.setdefault(row["replica"], []).append(row)
        partial = not expected <= by_replica.keys() or any(
            row["partial"] or row["skewed"] for row in members
        )
        rates = []
        for values in by_replica.values():
            elapsed = sum(row["interval_ms"] for row in values) / 1000
            rates.append(sum(row["delta_requests"] for row in values) / elapsed)
        inflight = []
        sent = received = 0.0
        all_sized = True
        for values in by_replica.values():
            last = max(values, key=observation_key)
            inflight.append(last["http"]["in_flight"] if last["http"] else None)
            elapsed = sum(row["interval_ms"] for row in values) / 1000
            sent += sum(row["http"]["delta_bytes_sent"] for row in values if row["http"]) / elapsed
            received += (
                sum(row["http"]["delta_bytes_received"] for row in values if row["http"]) / elapsed
            )
            all_sized &= all(
                row["http"] and row["http"]["delta_sized_requests"] == row["delta_requests"]
                for row in values
            )
        result.append(
            {
                "at_ms": at,
                "rps": sum(rates) if not partial else None,
                "requests": metrics["requests"],
                "duration_buckets": metrics["buckets"],
                "quality": metrics["quality"],
                "errors": metrics["errors"],
                "client_errors": metrics["status_classes"][3]
                if metrics["status_classes"] is not None
                else None,
                "mean_ms": metrics["mean_ms"],
                "slow_request_pct": metrics["slow_request_pct"],
                "mean_response_bytes": metrics["mean_response_bytes"],
                "mean_request_bytes": metrics["mean_request_bytes"],
                "partial": partial,
                "reporting": len(by_replica),
                "p50": metrics["latency"]["p50"],
                "p95": metrics["latency"]["p95"],
                "p99": metrics["latency"]["p99"],
                "client_error_pct": metrics["status_classes"][3] / metrics["http_requests"] * 100
                if metrics["http_requests"]
                else None,
                "server_error_pct": metrics["errors"] / metrics["requests"] * 100
                if metrics["requests"]
                else None,
                "in_flight": sum(inflight)
                if not partial and all(value is not None for value in inflight)
                else None,
                "bytes_sent_per_sec": sent if not partial and all_sized else None,
                "bytes_received_per_sec": received if not partial and all_sized else None,
            }
        )
    # A current value comes only from the latest fresh interval, never historical carry-forward.
    latest = [
        max(
            (row for row in rows if row["replica"] == replica["id"]),
            key=observation_key,
            default=None,
        )
        for replica in replicas
        if not replica.get("retired")
    ]
    latest = [row.get("latest", row) if row is not None else None for row in latest]
    live = bool(latest) and all(
        row
        and not row["partial"]
        and not row["skewed"]
        and now - row["observed_at_ms"] <= stale_ms
        and now - row["received_at_ms"] <= stale_ms
        for row in latest
    )
    live_http = live and all(row["http"] is not None for row in latest)
    current = {
        "rps": sum(row["delta_requests"] / (row["interval_ms"] / 1000) for row in latest)
        if live
        else None,
        "in_flight": sum(row["http"]["in_flight"] for row in latest)
        if live_http and all(row["http"]["in_flight"] is not None for row in latest)
        else None,
    }
    return result, current
