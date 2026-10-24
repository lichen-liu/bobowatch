"""HTTP extensions, migration compatibility and correct multi-replica aggregation."""

import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from bobowatch import Monitor
from bobowatch.config import initialize
from bobowatch.protocol import Conflict, Invalid, validate
from bobowatch.storage import SQLiteStore


class HTTPMetricsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = initialize(Path(self.tmp.name) / "state", demo=True)
        self.store = SQLiteStore(self.config)
        self.addCleanup(lambda: self.store.close())
        self.monitor = Monitor(
            replica="flights-r01",
            endpoint="http://127.0.0.1:8720/v1/ingest",
            token_file=self.config.replicas[0]["token_file"],
            routes=("/api/search",),
        )
        self.now = int(time.time() * 1000)
        self.monitor.started_at_ms = self.now - 1000

    def snapshot(self, elapsed):
        p = self.monitor.snapshot()
        p["elapsed_ms"] = elapsed
        return validate(p)

    def ingest(self, p):
        return self.store.ingest(p, p["replica"])

    def data(self):
        return self.store.overview({"site_id": "flights", "id": "flights-r01"}, 1800)

    def test_extended_metrics_and_deduplication(self):
        self.monitor.record_request(
            0.08, 200, route="/api/search", bytes_sent=1000, bytes_received=100
        )
        self.monitor.record_request(
            0.30, 404, route="/api/search", bytes_sent=200, bytes_received=20
        )
        p = self.snapshot(1000)
        self.ingest(p)
        self.ingest(p)
        self.monitor.record_request(
            0.40, 503, route="/api/search", bytes_sent=400, bytes_received=40
        )
        self.ingest(self.snapshot(2000))
        data = self.data()
        http = data["http"]
        self.assertEqual(http["requests"], 3)
        self.assertEqual(http["status_classes"], [0, 1, 0, 1, 1])
        self.assertEqual(http["sized_requests"], 3)
        self.assertEqual(http["routes"][0]["requests"], 3)
        self.assertEqual(http["routes"][0]["errors"], 1)
        self.assertEqual(http["latency"]["p50"]["upper_ms"], 500)
        self.assertAlmostEqual(http["mean_ms"], 260)
        self.assertIsNone(data["current"]["in_flight"])
        self.assertGreater(data["charts"][-1]["bytes_sent_per_sec"], 0)

    def test_request_lifecycle_and_unknown_route(self):
        with self.assertRaises(RuntimeError):
            with self.monitor.request("/api/search"):
                self.assertEqual(self.monitor.snapshot()["http"]["in_flight"], 1)
                raise RuntimeError("app exception")
        p = self.snapshot(1000)
        self.assertEqual(p["errors"], 1)
        self.assertEqual(p["http"]["in_flight"], 0)
        self.assertEqual(p["http"]["status_classes"], [0, 0, 0, 0, 1])
        self.monitor.record_request(0.02, route="/raw/path/123?secret=123")
        p = self.snapshot(2000)
        self.assertEqual(
            next(r for r in p["http"]["routes"] if r["route"] == "unmatched")["requests"], 1
        )

    def test_unknown_body_sizes_cannot_report_phantom_throughput(self):
        self.monitor.record_request(0.1)
        p = self.snapshot(1000)
        p["http"]["bytes_sent"] = 10
        with self.assertRaises(Invalid):
            validate(p)

    def test_counter_conflicts_and_histogram_validation(self):
        self.monitor.record_request(0.1, bytes_sent=10, bytes_received=1)
        p = self.snapshot(1000)
        self.ingest(p)
        self.monitor.record_request(0.1, bytes_sent=10, bytes_received=1)
        p = self.snapshot(2000)
        p["http"]["bytes_sent"] = 0
        with self.assertRaises(Conflict):
            self.ingest(validate(p))
        self.assertEqual(self.data()["http"]["requests"], 1)
        p = self.snapshot(3000)
        p["http"]["status_classes"][1] = 1
        with self.assertRaises(Invalid):
            validate(p)
        p = self.snapshot(4000)
        p["http"]["routes"][0]["requests"] += 1
        with self.assertRaises(Invalid):
            validate(p)

    def test_multi_replica_rates_are_summed_not_averaged(self):
        for replica, count, elapsed in [("flights-r01", 10, 1000), ("flights-r02", 20, 2000)]:
            monitor = Monitor(
                replica=replica,
                endpoint="http://127.0.0.1:8720/v1/ingest",
                token_file=self.config.replicas[0]["token_file"],
            )
            monitor.started_at_ms = self.now - 3000
            for _ in range(count):
                monitor.record_request(0.1)
            p = monitor.snapshot()
            p["elapsed_ms"] = elapsed
            self.ingest(validate(p))
        data = self.store.overview({"site_id": "flights"}, 1800)
        self.assertAlmostEqual(data["current"]["rps"], 20)
        self.assertAlmostEqual(data["charts"][-1]["rps"], 20)
        self.assertIsNone(data["charts"][-1]["bytes_sent_per_sec"])

    def test_short_windows_select_recent_intervals_and_preserve_totals(self):
        self.monitor.started_at_ms = self.now - 90_000
        for age_seconds, count in ((75, 1), (45, 3), (15, 5)):
            for _ in range(count):
                self.monitor.record_request(0.1, route="/api/search")
            payload = self.snapshot((90 - age_seconds) * 1000)
            payload["observed_at_ms"] = self.now - age_seconds * 1000
            self.store._submit(
                lambda conn, payload=payload: self.store._ingest(
                    conn,
                    payload,
                    payload["replica"],
                    demo_received_at_ms=payload["observed_at_ms"],
                )
            )
        for window, expected in ((30, 5), (60, 8), (300, 9)):
            with self.subTest(window=window):
                data = self.store.overview({"id": "flights-r01"}, window, now_ms=self.now)
                self.assertEqual(data["http"]["requests"], expected)
                self.assertEqual(sum(point["requests"] for point in data["charts"]), expected)
                self.assertEqual(data["bucket_seconds"], 5)
                self.assertTrue(
                    all(
                        self.now - window * 1000 <= point["at_ms"] <= self.now
                        for point in data["charts"]
                    )
                )

    def test_v1_database_upgrade_preserves_history_and_makes_backup(self):
        self.monitor.record_request(0.1)
        p = self.snapshot(1000)
        p.pop("http")
        p["version"] = 1
        self.ingest(validate(p))
        self.store.close()
        with closing(sqlite3.connect(self.config.database)) as conn, conn:
            for table in ("route_totals", "response_classes", "http_samples"):
                conn.execute(f"DROP TABLE {table}")
            conn.execute("DROP TABLE event_receipts")
            conn.execute("DROP TABLE storage_state")
            conn.execute("DROP INDEX snapshots_receipt")
            conn.execute("DROP INDEX events_receipt")
            conn.execute("ALTER TABLE events DROP COLUMN received_at_ms")
            conn.execute("DELETE FROM schema_migrations WHERE version>=2")
        self.store = SQLiteStore(self.config)
        self.assertEqual(self.data()["http"]["requests"], 1)
        self.assertIsNone(self.data()["http"]["status_classes"])
        self.assertEqual(len(list(self.config.database.parent.glob("*.before-v2-*.sqlite3"))), 1)
        self.assertTrue(self.ingest(p)["duplicate"])
        p = self.snapshot(2000)
        with self.assertRaises(Conflict):
            self.ingest(p)

    def test_standalone_simulate_cli_does_not_require_a_storage_instance(self):
        from bobowatch.__main__ import main

        with (
            patch("sys.argv", ["bobowatch", "simulate", "--config", str(self.config.path)]),
            patch("bobowatch.demo.Simulation") as simulation,
        ):
            simulation.return_value.thread.is_alive.return_value = False
            main()
            simulation.assert_called_once_with(self.config)
            simulation.return_value.close.assert_called_once()

    def test_fixture_import_cannot_target_real_runtime(self):
        self.store.close()
        self.store = SQLiteStore(replace(self.config, demo=False))
        p = self.snapshot(1000)
        with self.assertRaises(Invalid):
            self.store._submit(
                lambda conn: self.store._ingest(conn, p, p["replica"], demo_received_at_ms=self.now)
            )
        self.assertEqual(self.data()["http"]["requests"], 0)


if __name__ == "__main__":
    unittest.main()
