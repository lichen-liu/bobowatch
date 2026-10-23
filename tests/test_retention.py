"""Small-quota fill/reuse, counter baselines, event receipts and quota reduction."""

import tempfile
import time
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from bobowatch import Monitor
from bobowatch.config import initialize
from bobowatch.protocol import Conflict
from bobowatch.retention import family_bytes
from bobowatch.storage import Busy, SQLiteStore


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = replace(
            initialize(Path(self.temp.name) / "watch", demo=True), quota_bytes=1024**2
        )
        self.store = SQLiteStore(self.config)
        self.addCleanup(lambda: self.store.close())
        self.monitor = Monitor(
            replica="flights-r01",
            endpoint="http://127.0.0.1:8720/v1/ingest",
            token_file=self.config.replicas[0]["token_file"],
            routes=("/items/{id}",),
        )
        self.at = int(time.time() * 1000)

    def payload(self, step):
        self.monitor.record_request(0.1, route="/items/{id}", bytes_sent=1000, bytes_received=10)
        data = self.monitor.snapshot()
        data.update(elapsed_ms=step * 1000, observed_at_ms=self.at + step)
        return data

    def test_fill_evict_reuse_preserves_baselines_and_event_acknowledgements(self):
        self.monitor.event("Retained event acknowledgement")
        for step in range(1, 801):
            self.store.ingest(self.payload(step), "flights-r01")
            self.assertLessEqual(family_bytes(self.config.database), self.config.quota_bytes)
        status = self.store.storage_status()
        self.assertGreater(status["evicted_snapshots"], 0)
        self.assertEqual(status["evicted_events"], 1)
        data = self.payload(801)
        self.store.ingest(data, "flights-r01")
        self.assertTrue(self.store.ingest(data, "flights-r01")["duplicate"])
        with closing(self.store._connect()) as conn:
            latest = conn.execute(
                "SELECT * FROM snapshots ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            self.assertEqual(latest["delta_requests"], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 0)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        with self.assertRaises(Conflict):
            self.store.ingest(self.payload(1), "flights-r01")

    def test_live_quota_reduction_reclaims_before_continuing(self):
        self.store.reload_inventory(replace(self.config, quota_bytes=3 * 1024**2))
        for step in range(1, 601):
            self.store.ingest(self.payload(step), "flights-r01")
        self.store.reload_inventory(self.config)
        for _ in range(25):
            status = self.store.maintain()
            if status["used_bytes"] < self.config.quota_bytes * 0.75:
                break
        self.assertLess(status["used_bytes"], self.config.quota_bytes)
        self.assertTrue(status["quota_applied"])
        self.store.ingest(self.payload(601), "flights-r01")

    def test_blocked_checkpoint_cannot_consume_reserve_and_later_recovers(self):
        self.store.ingest(self.payload(1), "flights-r01")
        reader = self.store._connect()
        try:
            reader.execute("BEGIN")
            reader.execute("SELECT COUNT(*) FROM snapshots").fetchone()
            rejected = False
            for step in range(2, 200):
                try:
                    self.store.ingest(self.payload(step), "flights-r01")
                except Busy:
                    rejected = True
                    break
            self.assertTrue(rejected)
            self.assertLessEqual(family_bytes(self.config.database), self.config.quota_bytes)
        finally:
            reader.close()
        self.store.maintain()
        self.store.ingest(self.payload(201), "flights-r01")

    def test_ram_epoch_starts_with_a_baseline_and_missing_mount_fails_closed(self):
        # Exercise the volatile epoch semantics using an isolated normal temp file;
        # hardware RAM proof is separately covered by the macOS volume probe.
        self.store.close()
        self.config.database.unlink()
        config = replace(self.config, ram_required=True)
        with patch("bobowatch.storage.validate_database"):
            self.store = SQLiteStore(config)
            self.monitor.record_request(0.2)
            self.store.ingest(self.payload(1), "flights-r01")
            self.store.ingest(self.payload(2), "flights-r01")
            with closing(self.store._connect()) as conn:
                rows = conn.execute(
                    "SELECT delta_requests FROM snapshots ORDER BY sequence"
                ).fetchall()
                self.assertEqual([row[0] for row in rows], [0, 1])
                self.assertEqual(
                    conn.execute("SELECT SUM(delta_sized_requests) FROM http_samples").fetchone()[
                        0
                    ],
                    1,
                )
            old_epoch = self.store.storage_status()["epoch"]
        with self.assertRaises(Busy):
            self.store.ingest(self.payload(3), "flights-r01")
        time.sleep(2.1)  # The idle maintenance tick must survive a missing volume too.
        self.assertTrue(self.store._thread.is_alive())
        self.assertEqual(self.store._quota, config.quota_bytes)
        with patch("bobowatch.storage.validate_database"):
            self.assertEqual(self.store.storage_status()["epoch"], old_epoch)
