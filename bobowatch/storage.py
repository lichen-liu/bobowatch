"""SQLite backend; domain/API code consumes a narrow Storage protocol.

A bounded single writer owns its connection. All snapshot state, deltas and events
commit together. Read connections are independent and keep a consistent view until completion.
"""

import hashlib
import json
import queue
import shutil
import sqlite3
import threading
import time
import uuid
from concurrent.futures import Future, TimeoutError
from contextlib import closing
from pathlib import Path
from typing import Protocol

from .history import aggregate_history
from .http_metrics import charts, store_http, summary
from .protocol import BUCKETS_US, Conflict, Invalid, percentile
from .ramdisk import validate_database
from .ranges import validate_bounds
from .retention import admit, footprint, maintain


class Busy(RuntimeError):
    pass


class Storage(Protocol):
    def ingest(self, payload: dict, replica: str) -> dict: ...
    def overview(
        self, filters: dict, window=300, *, start_ms=None, end_ms=None, bucket_ms=None
    ) -> dict: ...
    def backup(self, target: Path) -> None: ...
    def close(self) -> None: ...


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode()
    ).hexdigest()


class SQLiteStore:
    def __init__(self, config):
        self.config, self.path = config, config.database
        validate_database(config, full=config.ram_required)
        self._quota = config.quota_bytes
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._queue = queue.Queue(maxsize=128)
        self._guard, self._accepting = threading.Lock(), True
        self._ready, self._startup_error = threading.Event(), None
        self._thread = threading.Thread(target=self._writer, name="bobowatch-sql", daemon=True)
        self._thread.start()
        if not self._ready.wait(10):
            raise Busy("SQL writer startup timed out")
        if self._startup_error:
            raise self._startup_error
        try:
            self._submit(self._apply_inventory)
        except BaseException:
            self.close()
            raise

    def _check_volume(self):
        try:
            validate_database(self.config)
        except (OSError, ValueError) as error:
            raise Busy("RAM storage unavailable; no disk fallback") from error

    def _connect(self):
        self._check_volume()
        conn = sqlite3.connect(self.path, timeout=2)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA cache_size=-8192")
        return conn

    def _migrate(self, conn):
        if not conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone():
            conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=128")
        migrations = [
            (int(path.stem), path.read_text())
            for path in sorted((Path(__file__).parent / "migrations").glob("*.sql"))
        ]
        expected = {
            version: hashlib.sha256(sql.encode()).hexdigest() for version, sql in migrations
        }
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='schema_migrations'"
        ).fetchone()
        applied = (
            dict(conn.execute("SELECT version,checksum FROM schema_migrations")) if exists else {}
        )
        if any(
            version not in expected or expected[version] != checksum
            for version, checksum in applied.items()
        ):
            raise Invalid("Unsupported database version or migration checksum")
        if sorted(applied) != [version for version, _ in migrations[: len(applied)]]:
            raise Invalid("Database migration history is not contiguous")
        pending = [(version, sql) for version, sql in migrations if version not in applied]
        if applied and pending and not self.config.ram_required:
            backup = self.path.with_name(
                self.path.name
                + ".before-v"
                + str(pending[0][0])
                + "-"
                + uuid.uuid4().hex[:8]
                + ".sqlite3"
            )
            with closing(sqlite3.connect(backup)) as destination:
                backup.chmod(0o600)
                conn.backup(destination)
        for version, migration in pending:
            try:
                conn.executescript("BEGIN IMMEDIATE;\n" + migration)
                conn.execute(
                    "INSERT INTO schema_migrations VALUES (?,?)", (version, expected[version])
                )
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        conn.execute(
            "INSERT OR IGNORE INTO storage_state(id,epoch) VALUES(1,?)", (str(uuid.uuid4()),)
        )
        conn.commit()
        self.path.chmod(0o600)

    def _writer(self):
        try:
            if not self.path.exists():
                with self.path.open("xb"):
                    self.path.chmod(0o600)
            conn = self._connect()
            try:
                self._migrate(conn)
            except BaseException:
                conn.close()
                raise
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
            return
        self._ready.set()
        try:
            while True:
                try:
                    work = self._queue.get(timeout=2)
                except queue.Empty:
                    try:
                        self._check_volume()
                        maintain(conn, self.path, self._quota)
                    except (Busy, OSError, ValueError, sqlite3.Error):
                        pass  # A failed/missing mount rejects ingestion, never falls back.
                    continue
                if work is None:
                    break
                operation, future = work
                try:
                    self._check_volume()
                    maintain(conn, self.path, self._quota)
                    with conn:
                        result = operation(conn)
                    future.set_result(result)
                except Exception as exc:
                    future.set_exception(exc)
        finally:
            conn.close()

    def _submit(self, operation):
        future = Future()
        with self._guard:
            if not self._accepting:
                raise Busy("SQL writer is stopping")
            try:
                self._queue.put_nowait((operation, future))
            except queue.Full:
                raise Busy("SQL queue is full") from None
        try:
            return future.result(timeout=5)
        except TimeoutError:
            # Work may commit later; callers retry using snapshot idempotency.
            raise Busy("SQL acknowledgement timed out") from None

    def close(self):
        with self._guard:
            if not self._accepting:
                return
            self._accepting = False
        self._queue.put(None, timeout=5)
        self._thread.join(10)
        if self._thread.is_alive():
            raise Busy("SQL writer did not finish shutdown")

    def _apply_inventory(self, conn, config=None):
        config = config or self.config
        old = {r["id"] for r in conn.execute("SELECT id FROM replicas")}
        if old - {r["id"] for r in config.replicas}:
            raise Invalid(
                "Missing replica inventory: keep it with retired=true instead of deleting it"
            )
        for table, items in [("projects", config.projects), ("hosts", config.hosts)]:
            for item in items:
                row = conn.execute(f"SELECT id FROM {table} WHERE id=?", (item["id"],)).fetchone()
                if row:
                    conn.execute(
                        f"UPDATE {table} SET title=? WHERE id=?", (item["title"], item["id"])
                    )
                else:
                    conn.execute(f"INSERT INTO {table} VALUES (?,?)", (item["id"], item["title"]))
        for site in config.sites:
            old_site = conn.execute("SELECT * FROM sites WHERE id=?", (site["id"],)).fetchone()
            if old_site:
                if (old_site["project_id"], old_site["environment"]) != (
                    site["project"],
                    site["environment"],
                ):
                    raise Invalid("Changing site project/environment requires a new site ID")
                conn.execute("UPDATE sites SET title=? WHERE id=?", (site["title"], site["id"]))
            else:
                conn.execute(
                    "INSERT INTO sites VALUES (?,?,?,?)",
                    (site["id"], site["project"], site["title"], site["environment"]),
                )
        for replica in config.replicas:
            old_replica = conn.execute(
                "SELECT * FROM replicas WHERE id=?", (replica["id"],)
            ).fetchone()
            if old_replica:
                if (old_replica["site_id"], old_replica["host_id"]) != (
                    replica["site"],
                    replica["host"],
                ):
                    raise Invalid("Moving a replica requires a new replica ID")
                conn.execute(
                    "UPDATE replicas SET title=?,token_hash=?,retired=? WHERE id=?",
                    (
                        replica["title"],
                        replica["token_hash"],
                        replica.get("retired", False),
                        replica["id"],
                    ),
                )
            else:
                conn.execute(
                    "INSERT INTO replicas VALUES (?,?,?,?,?,?)",
                    (
                        replica["id"],
                        replica["site"],
                        replica["host"],
                        replica["title"],
                        replica["token_hash"],
                        replica.get("retired", False),
                    ),
                )

    def reload_inventory(self, config):
        def apply(conn):
            self._apply_inventory(conn, config)

        self._submit(apply)
        self.config, self._quota = config, config.quota_bytes

    def _storage_status(self, conn):
        stats = footprint(conn, self.path)
        state = dict(conn.execute("SELECT * FROM storage_state WHERE id=1").fetchone())
        oldest, newest = conn.execute(
            "SELECT MIN(observed_at_ms),MAX(observed_at_ms) FROM snapshots"
        ).fetchone()
        usage = shutil.disk_usage(self.path.parent)
        return {
            **stats,
            **state,
            "quota_bytes": self._quota,
            "quota_applied": self._quota is None or stats["used_bytes"] <= self._quota,
            "oldest_at_ms": oldest,
            "newest_at_ms": newest,
            "reserve_bytes": max(65536, self._quota // 10) if self._quota else 0,
            "ram_only": self.config.ram_required,
            "volume_bytes": usage.total,
            "volume_free_bytes": usage.free,
            "database": str(self.path),
        }

    def storage_status(self):
        return self._submit(self._storage_status)

    def maintain(self):
        self._submit(lambda conn: maintain(conn, self.path, self._quota, force=True, seconds=0.5))
        return self.storage_status()

    def ingest(self, payload, replica):
        def accept(conn):
            try:
                admit(conn, self.path, self._quota, len(json.dumps(payload)))
            except OSError as error:
                raise Busy(str(error)) from None
            return self._ingest(conn, payload, replica)

        return self._submit(accept)

    def _ingest(self, conn, p, replica, *, demo_received_at_ms=None):
        if p["replica"] != replica:
            raise Invalid("Producer credential does not match replica")
        registered = conn.execute("SELECT * FROM replicas WHERE id=?", (replica,)).fetchone()
        if p["started_at_ms"] > int(time.time() * 1000) + 30_000:
            raise Invalid("Process start time is in the future")
        if not registered or registered["retired"]:
            raise Conflict("Replica is not an active inventory target")
        digest = fingerprint(p)
        duplicate = conn.execute(
            "SELECT * FROM snapshots WHERE run_id=? AND sequence=?", (p["run_id"], p["sequence"])
        ).fetchone()
        if duplicate:
            run = conn.execute("SELECT replica_id FROM runs WHERE id=?", (p["run_id"],)).fetchone()
            if run["replica_id"] != replica or duplicate["payload_hash"] != digest:
                raise Conflict("Sequence already exists with different contents")
            return {"sequence": p["sequence"], "duplicate": True}
        run = conn.execute("SELECT * FROM runs WHERE id=?", (p["run_id"],)).fetchone()
        latest_run = conn.execute(
            "SELECT * FROM runs WHERE replica_id=? ORDER BY started_at_ms DESC LIMIT 1", (replica,)
        ).fetchone()
        if latest_run and (p["started_at_ms"], p["run_id"]) != (
            latest_run["started_at_ms"],
            latest_run["id"],
        ):
            if p["started_at_ms"] <= latest_run["started_at_ms"]:
                raise Conflict("Run was superseded; one producer per replica slot is supported")
        if run:
            if (run["replica_id"], run["started_at_ms"], run["revision"]) != (
                replica,
                p["started_at_ms"],
                p["revision"],
            ):
                raise Conflict("Run identity is immutable")
        else:
            if (
                conn.execute(
                    "SELECT COUNT(*) FROM runs WHERE replica_id=? AND started_at_ms>=?",
                    (replica, int(time.time() * 1000) - 86400000),
                ).fetchone()[0]
                >= 100
            ):
                raise Conflict("Daily process-run budget exceeded")
            conn.execute(
                "INSERT INTO runs VALUES (?,?,?,?)",
                (p["run_id"], replica, p["started_at_ms"], p["revision"]),
            )
        previous = conn.execute(
            "SELECT * FROM snapshots WHERE run_id=? ORDER BY sequence DESC LIMIT 1", (p["run_id"],)
        ).fetchone()
        if previous and (
            p["sequence"] <= previous["sequence"]
            or p["elapsed_ms"] <= previous["elapsed_ms"]
            or p["observed_at_ms"] < previous["observed_at_ms"]
        ):
            raise Conflict("Snapshot sequence/time did not advance")
        baseline = previous is None and self.config.ram_required
        before = previous or (
            p if baseline else {"requests": 0, "errors": 0, "duration_sum_us": 0, "elapsed_ms": 0}
        )
        delta = {key: p[key] - before[key] for key in ("requests", "errors", "duration_sum_us")}
        old_buckets = (
            [
                r["total"]
                for r in conn.execute(
                    "SELECT total FROM histogram_buckets WHERE snapshot_id=? ORDER BY bucket_index",
                    (previous["id"],),
                )
            ]
            if previous
            else p["buckets"]
            if baseline
            else [0] * len(p["buckets"])
        )
        bucket_deltas = [value - old for value, old in zip(p["buckets"], old_buckets, strict=True)]
        if min(delta.values()) < 0 or min(bucket_deltas) < 0 or delta["errors"] > delta["requests"]:
            raise Conflict("Cumulative values decreased or errors exceed interval requests")
        if demo_received_at_ms is not None and not self.config.demo:
            raise Invalid("Historical fixture import is restricted to demo runtimes")
        received = int(time.time() * 1000) if demo_received_at_ms is None else demo_received_at_ms
        interval = p["elapsed_ms"] - before["elapsed_ms"]
        if baseline:
            interval = 1
        if interval <= 0:
            raise Invalid("Snapshot elapsed time must advance beyond zero")
        skewed = abs(received - p["observed_at_ms"]) > 30_000
        partial = baseline or interval > self.config.sample_seconds * 2500
        key = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                key,
                p["run_id"],
                p["sequence"],
                p["observed_at_ms"],
                received,
                p["elapsed_ms"],
                p["requests"],
                p["errors"],
                p["duration_sum_us"],
                delta["requests"],
                delta["errors"],
                delta["duration_sum_us"],
                interval,
                int(partial),
                int(skewed),
                digest,
            ),
        )
        conn.executemany(
            "INSERT INTO histogram_buckets VALUES (?,?,?,?)",
            [(key, i, total, bucket_deltas[i]) for i, total in enumerate(p["buckets"])],
        )
        store_http(conn, key, p, previous, baseline=baseline)
        for event in p["events"]:
            event_hash = fingerprint(event)
            existing = conn.execute(
                "SELECT * FROM event_receipts WHERE id=?", (event["id"],)
            ).fetchone()
            if existing is None:
                existing = conn.execute(
                    "SELECT * FROM events WHERE id=?", (event["id"],)
                ).fetchone()
            if existing:
                if existing["run_id"] != p["run_id"] or existing["payload_hash"] != event_hash:
                    raise Conflict("Event ID already exists with different contents")
            else:
                conn.execute(
                    "INSERT INTO events VALUES (?,?,?,?,?,?,?)",
                    (
                        event["id"],
                        p["run_id"],
                        event["at_ms"],
                        event["severity"],
                        event["message"],
                        event_hash,
                        received,
                    ),
                )
                conn.execute(
                    "INSERT INTO event_receipts VALUES(?,?,?,?,?)",
                    (event["id"], replica, p["run_id"], event_hash, received),
                )
        # At most 256 recent event acknowledgements per replica survive history
        # eviction. Older retained event rows still protect their own IDs.
        conn.execute(
            """DELETE FROM event_receipts WHERE replica_id=? AND id NOT IN (
            SELECT id FROM event_receipts WHERE replica_id=?
            ORDER BY received_at_ms DESC,rowid DESC LIMIT 256)""",
            (replica, replica),
        )
        return {"sequence": p["sequence"], "duplicate": False}

    def overview(
        self, filters, window=300, *, now_ms=None, start_ms=None, end_ms=None, bucket_ms=None
    ):
        # A lock can delay an internal dashboard read; it does not justify an
        # incomplete answer or an artificial query deadline.
        while True:
            try:
                return self._overview(
                    filters,
                    window,
                    now_ms=now_ms,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    bucket_ms=bucket_ms,
                )
            except sqlite3.OperationalError as error:
                code = getattr(error, "sqlite_errorcode", 0) & 255
                if code not in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                    raise
                time.sleep(0.05)

    def _overview(
        self, filters, window=300, *, now_ms=None, start_ms=None, end_ms=None, bucket_ms=None
    ):
        now = int(time.time() * 1000) if now_ms is None else now_ms
        absolute = start_ms is not None or end_ms is not None
        all_history = window == "all" and not absolute
        historical = absolute or all_history
        if absolute:
            validate_bounds(start_ms, end_ms, now)
            since, until = start_ms, end_ms
            window = (until - since) / 1000
        else:
            since, until = 0 if all_history else max(0, now - window * 1000), now
        if bucket_ms is not None and (
            type(bucket_ms) is not int or not 1 <= bucket_ms <= 2**63 - 1
        ):
            raise Invalid("Chart bucket_ms must be a positive SQL integer")
        end_operator = "<" if historical else "<="
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA query_only=ON")
            # One read transaction gives all panels and storage metadata a
            # consistent view. There is no row cap or query time budget.
            with conn:
                conn.execute("BEGIN")
                inventory = [
                    dict(row)
                    for row in conn.execute("""
                    SELECT r.id,r.title,r.site_id,r.host_id,r.retired,s.project_id,s.environment,
                           s.title AS site_title,p.title AS project_title,h.title AS host_title
                    FROM replicas r JOIN sites s ON s.id=r.site_id
                    JOIN projects p ON p.id=s.project_id JOIN hosts h ON h.id=r.host_id
                    ORDER BY s.project_id,s.id,r.id""")
                ]
                inventory = [
                    r
                    for r in inventory
                    if all(not value or str(r.get(key)) == value for key, value in filters.items())
                ]
                replicas, rows, events = [], [], []
                endpoints = {}
                step = bucket_ms or max(5_000, (until - since) // 180)
                for replica in inventory:
                    latest = conn.execute(
                        """SELECT n.*,u.revision FROM snapshots n
                        JOIN runs u ON u.id=n.run_id WHERE u.replica_id=?
                        ORDER BY u.started_at_ms DESC,n.sequence DESC LIMIT 1""",
                        (replica["id"],),
                    ).fetchone()
                    status = "waiting"
                    if latest:
                        age = max(now - latest["received_at_ms"], now - latest["observed_at_ms"])
                        status = (
                            "skewed"
                            if latest["skewed"]
                            else ("fresh" if age <= self.config.stale_seconds * 1000 else "stale")
                        )
                    replicas.append(
                        {
                            **replica,
                            "status": "retired" if replica["retired"] else status,
                            "observed_at_ms": latest["observed_at_ms"] if latest else None,
                            "received_at_ms": latest["received_at_ms"] if latest else None,
                            "revision": latest["revision"] if latest else None,
                        }
                    )
                if replicas:
                    placeholders = ",".join("?" for _ in replicas)
                    ids = [r["id"] for r in replicas]
                    if all_history:
                        first, last = conn.execute(
                            f"""SELECT MIN(n.observed_at_ms),MAX(n.observed_at_ms)
                            FROM snapshots n JOIN runs u ON u.id=n.run_id
                            WHERE u.replica_id IN ({placeholders})""",
                            ids,
                        ).fetchone()
                        since = first if first is not None else now
                        until = max(now + 1, last + 1) if last is not None else now + 1
                    step = bucket_ms or max(5_000, (until - since) // 180)
                    rows, endpoints = aggregate_history(
                        conn, ids, since, until, step, exclusive_end=historical
                    )
                    events = [
                        dict(row)
                        for row in conn.execute(
                            f"""
                        SELECT e.id,e.occurred_at_ms,e.severity,e.message,u.replica_id AS replica,r.site_id AS site
                        FROM events e JOIN runs u ON u.id=e.run_id JOIN replicas r ON r.id=u.replica_id
                        WHERE u.replica_id IN ({placeholders}) AND e.occurred_at_ms >= ? AND e.occurred_at_ms {end_operator} ?
                        ORDER BY e.occurred_at_ms DESC,e.id""",
                            (*ids, since, until),
                        )
                    ]
                    for replica in replicas:
                        replica["history_truncated"] = False

                def http_summary(values, kind, scope=""):
                    metrics = summary(values, include_routes=False)
                    metrics.update(endpoints.get((kind, scope), {"routes": [], "route_count": 0}))
                    return metrics

                # Group every recorded delta; never sample records or average percentiles.
                bins = {}
                for row in rows:
                    time_bin = max(since, row["observed_at_ms"] // step * step)
                    cell = bins.setdefault(
                        (row["site"], time_bin),
                        {
                            "site": row["site"],
                            "at_ms": time_bin,
                            "requests": 0,
                            "errors": 0,
                            "partial": False,
                            "replicas": set(),
                        },
                    )
                    cell["requests"] += row["delta_requests"]
                    cell["errors"] += row["delta_errors"]
                    cell["partial"] |= bool(row["partial"] or row["skewed"])
                    cell["replicas"].add(row["replica"])
                history = [{**cell, "reporting": len(cell["replicas"])} for cell in bins.values()]
                for cell in history:
                    del cell["replicas"]
                sites = []
                for site_id in dict.fromkeys(r["site_id"] for r in replicas):
                    members = [r for r in replicas if r["site_id"] == site_id]
                    selected = [r for r in rows if r["site"] == site_id]
                    buckets = [
                        sum(row["buckets"][i] for row in selected)
                        for i in range(len(BUCKETS_US) + 1)
                    ]
                    sites.append(
                        {
                            "id": site_id,
                            "title": members[0]["site_title"],
                            "project": members[0]["project_id"],
                            "project_title": members[0]["project_title"],
                            "environment": members[0]["environment"],
                            "replicas": members,
                            "snapshots": sum(r.get("snapshot_count", 1) for r in selected),
                            "requests": sum(r["delta_requests"] for r in selected),
                            "errors": sum(r["delta_errors"] for r in selected),
                            "p95": percentile(buckets),
                            "partial": any(r["partial"] or r["skewed"] for r in selected),
                            "history_truncated": any(r["history_truncated"] for r in members),
                        }
                    )
                for site in sites:
                    site_rows = [row for row in rows if row["site"] == site["id"]]
                    site["http"] = http_summary(site_rows, "site", site["id"])
                    site["charts"], site["current"] = charts(
                        site_rows,
                        site["replicas"],
                        step,
                        now,
                        self.config.stale_seconds * 1000,
                        since=since,
                    )
                    for replica in site["replicas"]:
                        values = [row for row in site_rows if row["replica"] == replica["id"]]
                        replica["http"] = http_summary(values, "replica", replica["id"])
                        replica["range_observed_at_ms"] = max(
                            (row["observed_at_ms"] for row in values), default=None
                        )
                        _, replica["current"] = charts(
                            values,
                            [replica],
                            step,
                            now,
                            self.config.stale_seconds * 1000,
                            since=since,
                        )
                combined, current = charts(
                    rows, replicas, step, now, self.config.stale_seconds * 1000, since=since
                )
                if historical:
                    for item in [
                        *sites,
                        *(replica for site in sites for replica in site["replicas"]),
                    ]:
                        item["current"] = {key: None for key in item["current"]}
                    current = {key: None for key in current}
                return {
                    "range_start_ms": since,
                    "range_end_ms": until,
                    "historical": historical,
                    "http": http_summary(rows, "all"),
                    "charts": combined,
                    "current": current,
                    "bucket_seconds": step / 1000,
                    "sample_seconds": self.config.sample_seconds,
                    "schema_version": 3,
                    "now_ms": now,
                    "window_seconds": (until - since) / 1000,
                    "demo": self.config.demo,
                    "sites": sites,
                    "history": sorted(history, key=lambda row: (row["at_ms"], row["site"])),
                    "events": events,
                    "storage": self._storage_status(conn),
                    "queue_depth": self._queue.qsize(),
                    "site_options": [
                        dict(row)
                        for row in conn.execute(
                            "SELECT id,title,project_id FROM sites ORDER BY title"
                        )
                    ],
                    "projects": [
                        dict(r)
                        for r in conn.execute("SELECT id,title FROM projects ORDER BY title")
                    ],
                    "hosts": [
                        dict(r) for r in conn.execute("SELECT id,title FROM hosts ORDER BY title")
                    ],
                    "stale_seconds": self.config.stale_seconds,
                    "storage_bytes": self.path.stat().st_size,
                    "wal_bytes": Path(str(self.path) + "-wal").stat().st_size
                    if Path(str(self.path) + "-wal").exists()
                    else 0,
                }

    def backup(self, target):
        backup_sqlite(self.path, target)


def backup_sqlite(source_path, target):
    """Consistent read-only backup, usable while the daemon owns its writer lock."""
    source_path = Path(source_path).expanduser().resolve()
    target = Path(target).expanduser().resolve()
    if not source_path.is_file() or target == source_path:
        raise Invalid("Source missing or backup would overwrite live data")
    with target.open("xb"):
        target.chmod(0o600)
    try:
        with (
            closing(sqlite3.connect(f"{source_path.as_uri()}?mode=ro", uri=True)) as source,
            closing(sqlite3.connect(target)) as dest,
        ):
            source.backup(dest, pages=128, sleep=0.05)
            if dest.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise Invalid("Backup integrity check failed")
            if dest.execute("PRAGMA foreign_key_check").fetchall():
                raise Invalid("Backup foreign-key check failed")
    except BaseException:
        target.unlink(missing_ok=True)
        raise
