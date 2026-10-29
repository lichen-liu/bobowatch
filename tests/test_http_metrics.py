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
from bobowatch.http_metrics import endpoint_totals
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
        p["observed_at_ms"] = self.now  # Keep multi-snapshot assertions in the same chart bucket.
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
        self.assertEqual(http["duration_us"], 780_000)
        self.assertEqual(
            http["quality"], {"snapshots": 2, "partial": 0, "skewed": 0, "extended_http": 2}
        )
        self.assertEqual(data["charts"][-1]["duration_buckets"], http["buckets"])
        self.assertEqual(data["charts"][-1]["quality"], http["quality"])
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

    def test_weighted_means_slow_boundary_and_measured_body_coverage(self):
        for _ in range(9):
            self.monitor.record_request(0.1, bytes_sent=100, bytes_received=10)
        self.ingest(self.snapshot(1000))
        self.monitor.record_request(1.0)  # Exactly one second is not over one second.
        self.monitor.record_request(1.1, bytes_sent=300, bytes_received=30)
        self.ingest(self.snapshot(2000))
        data = self.data()
        metrics = data["http"]
        self.assertAlmostEqual(metrics["mean_ms"], 3000 / 11)
        self.assertEqual(metrics["slow_requests"], 1)
        self.assertAlmostEqual(metrics["slow_request_pct"], 100 / 11)
        self.assertEqual(metrics["sized_requests"], 10)
        self.assertEqual(metrics["bytes_sent"], 1200)
        self.assertEqual(metrics["bytes_received"], 120)
        self.assertEqual(metrics["mean_response_bytes"], 120)
        self.assertEqual(metrics["mean_request_bytes"], 12)
        # Chart means use request counts as weights, too; partial sizes are explicit.
        self.assertAlmostEqual(data["charts"][-1]["mean_ms"], 3000 / 11)
        self.assertEqual(data["charts"][-1]["mean_response_bytes"], 120)
        self.assertIsNone(data["charts"][-1]["bytes_sent_per_sec"])
        self.assertEqual(data["charts"][-1]["errors"], 0)
        self.assertEqual(data["charts"][-1]["client_errors"], 0)

    def test_empty_and_v1_metrics_do_not_invent_body_sizes_or_error_classes(self):
        empty = self.data()["http"]
        for key in ("mean_ms", "slow_request_pct", "mean_response_bytes", "mean_request_bytes"):
            self.assertIsNone(empty[key])
        self.assertEqual(empty["slow_requests"], 0)
        self.monitor.record_request(2, 503)
        payload = self.snapshot(1000)
        payload.pop("http")
        payload["version"] = 1
        self.ingest(validate(payload))
        data = self.data()
        self.assertEqual(data["http"]["slow_request_pct"], 100)
        self.assertEqual(data["http"]["errors"], 1)
        self.assertEqual(data["http"]["route_count"], 0)
        self.assertIsNone(data["http"]["mean_response_bytes"])
        self.assertIsNone(data["charts"][-1]["client_errors"])
        self.assertIsNone(data["charts"][-1]["mean_request_bytes"])
        self.assertEqual(data["charts"][-1]["errors"], 1)
        self.assertEqual(data["http"]["duration_us"], 2_000_000)
        self.assertEqual(
            data["http"]["quality"],
            {"snapshots": 1, "partial": 0, "skewed": 0, "extended_http": 0},
        )
        self.assertEqual(sum(data["charts"][-1]["duration_buckets"]), 1)

    def test_zero_request_samples_differ_from_no_observations_and_fleet_gaps(self):
        empty = self.data()
        self.assertEqual(empty["charts"], [])
        self.assertEqual(empty["http"]["quality"]["snapshots"], 0)
        self.ingest(self.snapshot(1000))
        # Include another expected replica, which has not reported. The sample
        # itself has complete timing even though the fleet chart is partial.
        data = self.store.overview({"site_id": "flights"}, 1800)
        point = data["charts"][-1]
        expected = {"snapshots": 1, "partial": 0, "skewed": 0, "extended_http": 1}
        self.assertEqual(data["http"]["quality"], expected)
        self.assertEqual(point["quality"], expected)
        self.assertTrue(point["partial"])
        self.assertEqual(point["duration_buckets"], [0] * 14)
        self.assertEqual(data["http"]["duration_us"], 0)
        self.assertIsNone(point["mean_ms"])

    def test_endpoint_totals_include_quiet_and_idle_routes_without_a_cap(self):
        busy = [
            {
                "site": "one",
                "route": f"/busy-{i}",
                "requests": 1000,
                "errors": 0,
                "duration_us": 1000,
            }
            for i in range(100)
        ]
        quiet = {
            "site": "two",
            "route": "/quiet-failing",
            "requests": 1,
            "errors": 1,
            "duration_us": 2_000_000,
        }
        candidates = endpoint_totals([*busy, quiet])
        self.assertIn(quiet, candidates)
        self.assertEqual(len(candidates), 101)
        self.assertEqual(candidates, endpoint_totals([quiet, *reversed(busy)]))
        idle = {**quiet, "requests": 0, "errors": 0}
        self.assertEqual(endpoint_totals([idle]), [idle])

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

    def test_absolute_range_has_exclusive_end_and_does_not_expose_live_gauges(self):
        self.monitor.started_at_ms = self.now - 90000
        for age, count in ((60, 1), (30, 3), (0, 5)):
            for _ in range(count):
                self.monitor.record_request(0.1, route="/api/search")
            payload = self.snapshot((90 - age) * 1000)
            payload["observed_at_ms"] = self.now - age * 1000
            payload["events"] = [
                {
                    "id": f"event-{age}",
                    "at_ms": payload["observed_at_ms"],
                    "severity": "info",
                    "message": f"at {age}",
                }
            ]
            self.store._submit(
                lambda conn, payload=payload: self.store._ingest(
                    conn, payload, payload["replica"], demo_received_at_ms=payload["observed_at_ms"]
                )
            )
        for start_age, end_age, count in ((60, 30, 1), (30, 0, 3)):
            data = self.store.overview(
                {"id": "flights-r01"},
                now_ms=self.now,
                start_ms=self.now - start_age * 1000,
                end_ms=self.now - end_age * 1000,
            )
            self.assertEqual(data["http"]["requests"], count)
            self.assertEqual(sum(point["requests"] for point in data["charts"]), count)
            self.assertEqual([event["id"] for event in data["events"]], [f"event-{start_age}"])
            self.assertEqual(data["window_seconds"], 30)
            self.assertEqual(data["range_start_ms"], self.now - start_age * 1000)
            self.assertEqual(data["range_end_ms"], self.now - end_age * 1000)
            self.assertTrue(data["historical"])
            self.assertEqual(
                data["sites"][0]["replicas"][0]["range_observed_at_ms"], self.now - start_age * 1000
            )
            for item in [
                data,
                *data["sites"],
                *(r for site in data["sites"] for r in site["replicas"]),
            ]:
                self.assertTrue(all(value is None for value in item["current"].values()))
        empty = self.store.overview(
            {}, now_ms=self.now, start_ms=self.now - 120000, end_ms=self.now - 90000
        )
        self.assertEqual(empty["http"]["requests"], 0)
        self.assertEqual(empty["charts"], [])

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
