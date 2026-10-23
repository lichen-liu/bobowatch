"""Writer-owned bounded history, physical-family admission and SQLite reclamation."""

import shutil
import time
from pathlib import Path

from .protocol import Invalid

TABLES = ("histogram_buckets", "http_samples", "response_classes", "route_totals")


def family_bytes(path):
    return sum(
        p.stat().st_size
        for p in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm"))
        if p.exists()
    )


def footprint(conn, path):
    page = conn.execute("PRAGMA page_size").fetchone()[0]
    pages = conn.execute("PRAGMA page_count").fetchone()[0]
    free = conn.execute("PRAGMA freelist_count").fetchone()[0]
    sizes = {
        name: file.stat().st_size if file.exists() else 0
        for name, file in (
            ("main_bytes", path),
            ("wal_bytes", Path(str(path) + "-wal")),
            ("shm_bytes", Path(str(path) + "-shm")),
        )
    }
    return {
        **sizes,
        "used_bytes": sum(sizes.values()),
        "live_bytes": (pages - free) * page,
        "allocated_bytes": pages * page,
        "reusable_bytes": free * page,
        "page_size": page,
    }


def checkpoint(conn):
    result = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
    if result[1] == result[2]:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()


def prune(conn, *, limit=256):
    # Preserve one newest snapshot per replica's newest run, independent of the
    # dashboard's retained history. These bounded rows are the cumulative baseline.
    keys = [
        row[0]
        for row in conn.execute(
            """
        SELECT s.id FROM snapshots s WHERE s.id NOT IN (
          SELECT (SELECT n.id FROM snapshots n WHERE n.run_id=r.id
                  ORDER BY n.sequence DESC LIMIT 1)
          FROM replicas p JOIN runs r ON r.id=(SELECT u.id FROM runs u
             WHERE u.replica_id=p.id ORDER BY u.started_at_ms DESC LIMIT 1)

        ) ORDER BY s.received_at_ms,s.rowid LIMIT ?""",
            (limit,),
        )
    ]
    if keys:
        marks = ",".join("?" for _ in keys)
        for table in TABLES:
            conn.execute(f"DELETE FROM {table} WHERE snapshot_id IN ({marks})", keys)
        conn.execute(f"DELETE FROM snapshots WHERE id IN ({marks})", keys)
    events = conn.execute(
        """DELETE FROM events WHERE id IN (
        SELECT id FROM events ORDER BY received_at_ms,rowid LIMIT ?
    )""",
        (limit,),
    ).rowcount
    conn.execute("""DELETE FROM runs WHERE id NOT IN (SELECT run_id FROM snapshots)
        AND id NOT IN (SELECT run_id FROM events)
        AND id NOT IN (SELECT u.id FROM runs u WHERE u.id=(SELECT r.id FROM runs r
            WHERE r.replica_id=u.replica_id ORDER BY r.started_at_ms DESC LIMIT 1))""")
    conn.execute(
        """UPDATE storage_state SET evicted_snapshots=evicted_snapshots+?,
        evicted_events=evicted_events+? WHERE id=1""",
        (len(keys), events),
    )
    return len(keys) + events


def maintain(conn, path, quota, *, force=False, seconds=0.15):
    if quota is None:
        return
    if type(quota) is not int or quota < 1024**2:
        raise Invalid("Quota must be at least one MiB")
    deadline = time.monotonic() + seconds
    stats = footprint(conn, path)
    if force or stats["used_bytes"] >= quota * 0.70:
        checkpoint(conn)
    while time.monotonic() < deadline:
        stats = footprint(conn, path)
        if stats["live_bytes"] < quota * 0.60 and stats["used_bytes"] < quota * 0.80:
            break
        # Delete only when live history needs space. Existing free pages should be
        # reclaimed/reused, not cause repeated deletion of already-small history.
        if stats["live_bytes"] >= quota * 0.60:
            with conn:
                removed = prune(conn)
            if not removed:
                break
        conn.execute("PRAGMA incremental_vacuum(256)").fetchall()
        checkpoint(conn)
        if stats == footprint(conn, path):
            break
    page = conn.execute("PRAGMA page_size").fetchone()[0]
    # SQLite cannot lower below current allocation. Admission still blocks growth
    # while a reduced requested quota is being reclaimed.
    conn.execute(f"PRAGMA max_page_count={max(1, int(quota * 0.80) // page)}").fetchone()


def admit(conn, path, quota, payload_bytes):
    if quota is None:
        return
    stats = footprint(conn, path)
    # Bounded SQL fanout/index updates have substantially more bytes than JSON.
    # Keep a second reserve for checkpointing and eviction, and a hard RAM-volume
    # boundary underneath this application policy.
    estimate = max(65536, payload_bytes * 16)
    reserve = max(65536, quota // 10)
    free = shutil.disk_usage(path.parent).free
    if (
        stats["used_bytes"] + estimate + reserve > quota
        or free < estimate + reserve
        or stats["live_bytes"] + estimate > quota * 0.80
    ):
        raise OSError("Storage reserve unavailable; retry after retention/checkpoint maintenance")
