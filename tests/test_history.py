"""Full-range SQL aggregation agrees with raw observations, including old traffic."""

import io
import json
import tempfile
import time
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from bobowatch import Monitor
from bobowatch.application import Application
from bobowatch.config import initialize
from bobowatch.history import aggregate_history
from bobowatch.protocol import validate
from bobowatch.storage import HistoryQueryTimeout, SQLiteStore


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = replace(
            initialize(Path(self.tmp.name) / "state", demo=True),
            sample_seconds=15,
            stale_seconds=120,
        )
        self.store = SQLiteStore(self.config)
        self.addCleanup(self.store.close)
        self.now = int(time.time() * 1000)

    def monitor(self, replica="flights-r01", *, started=None, routes=("/busy", "/quiet")):
        monitor = Monitor(
            replica=replica,
            endpoint="http://127.0.0.1:8720/v1/ingest",
            token_file=self.config.replicas[0]["token_file"],
            routes=routes,
        )
        monitor.started_at_ms = self.now - 100000 if started is None else started
        return monitor

    def payload(self, monitor, at, elapsed, *, legacy=False, active=0):
        payload = monitor.snapshot()
        payload["observed_at_ms"], payload["elapsed_ms"] = at, elapsed
        payload["http"]["in_flight"] = active
        if legacy:
            payload.pop("http")
            payload["version"] = 1
        return validate(payload)

    def ingest(self, payloads, *, skew=0):
        for offset in range(0, len(payloads), 200):
            batch = payloads[offset : offset + 200]
            self.store._submit(
                lambda conn, batch=batch: [
                    self.store._ingest(
                        conn, p, p["replica"], demo_received_at_ms=p["observed_at_ms"] + skew
                    )
                    for p in batch
                ]
            )

    def overview(self, filters=None, *, limit=0, window=86400, **bounds):
        with patch("bobowatch.storage.RAW_HISTORY_LIMIT", limit):
            return self.store.overview(filters or {}, window, now_ms=self.now, **bounds)

    def assertEquivalent(self, expected, actual, path="overview"):
        if isinstance(expected, dict):
            self.assertEqual(expected.keys(), actual.keys(), path)
            for key in expected:
                self.assertEquivalent(expected[key], actual[key], f"{path}.{key}")
        elif isinstance(expected, list):
            self.assertEqual(len(expected), len(actual), path)
            for i, item in enumerate(expected):
                self.assertEquivalent(item, actual[i], f"{path}[{i}]")
        elif isinstance(expected, float):
            self.assertAlmostEqual(expected, actual, places=9, msg=path)
        else:
            self.assertEqual(expected, actual, path)

    def test_more_than_4000_observations_preserve_early_errors_and_complete_totals(self):
        count = 1500
        for index, replica in enumerate(("flights-r01", "flights-r02", "budget-r01")):
            started = self.now - count * 15000 - 1000
            monitor = self.monitor(replica, started=started)
            payloads = []
            for step in range(1, count + 1):
                for _ in range(3 if step == count else 1):
                    monitor.record_request(
                        2 if step == 1 else 0.1,
                        503 if step == 1 else 200,
                        route="/quiet" if step == 1 else "/busy",
                        bytes_sent=2000 if step == 1 else 1000,
                        bytes_received=20 if step == 1 else 10,
                    )
                payloads.append(
                    self.payload(
                        monitor,
                        started + step * 15000,
                        step * 15000,
                        active=7 + index if step == count else 1,
                    )
                )
            self.ingest(payloads)
        # The default path must aggregate all 4,500, not the newest 4,000.
        actual = self.store.overview({}, 86400, now_ms=self.now)
        metrics = actual["http"]
        self.assertEqual(metrics["snapshot_count"], 4500)
        self.assertEqual(metrics["requests"], 4506)
        self.assertEqual(metrics["errors"], 3)
        self.assertEqual(metrics["slow_requests"], 3)
        self.assertEqual(metrics["bytes_sent"], 4_509_000)
        self.assertEqual(sum(p["requests"] for p in actual["charts"]), 4506)
        self.assertLessEqual(len(actual["charts"]), 181)
        self.assertEqual(sum(r["errors"] for r in metrics["routes"] if r["route"] == "/quiet"), 3)
        self.assertTrue(all(not s["history_truncated"] for s in actual["sites"]))
        self.assertEquivalent(self.overview(limit=10000), actual)
        scoped = self.overview({"site_id": "flights"})
        self.assertAlmostEqual(scoped["current"]["rps"], 6 / 15)
        self.assertEqual(scoped["current"]["in_flight"], 15)
        self.assertEqual(scoped["http"]["snapshot_count"], 3000)
        self.assertNotEqual(scoped["current"]["rps"], scoped["charts"][-1]["rps"])
        # The exact end sample is excluded for a custom/calendar range.
        historical = self.overview(start_ms=started + 15000, end_ms=self.now - 1000)
        self.assertEqual(historical["http"]["requests"], 4497)
        self.assertEqual(historical["http"]["snapshot_count"], 4497)
        self.assertEqual(historical["http"]["errors"], 3)
        self.assertIsNone(historical["current"]["rps"])

    def test_aggregation_matches_raw_for_versions_resets_partial_sizes_and_gaps(self):
        old = self.monitor(started=self.now - 90000)
        samples = []
        for step, age in enumerate((60000, 45000), 1):
            old.record_request(2, 503, route="/quiet")
            samples.append(self.payload(old, self.now - age, step * 15000, legacy=True))
        self.ingest(samples)
        new = self.monitor(started=self.now - 40000)
        new.record_request(0.1, 404, route="/busy")  # Sizes unknown.
        samples = [self.payload(new, self.now - 30000, 15000)]
        new.record_request(0.2, route="/busy", bytes_sent=200, bytes_received=20)
        samples.append(self.payload(new, self.now - 15000, 30000, active=4))
        self.ingest(samples)
        skewed = self.monitor("flights-r02")
        skewed.record_request(0.1)
        self.ingest([self.payload(skewed, self.now - 1000, 15000)], skew=60000)
        gap = self.monitor("budget-r01", started=self.now - 300000)
        gap.record_request(0.5, bytes_sent=0, bytes_received=0)
        samples = [self.payload(gap, self.now - 16000, 200000)]
        gap.record_request(0.1, bytes_sent=0, bytes_received=0)
        samples.append(self.payload(gap, self.now - 1000, 215000))
        self.ingest(samples)
        for filters in (
            {},
            {"project_id": "travel"},
            {"site_id": "flights"},
            {"id": "flights-r01"},
            {"host_id": "demo-host"},
            {"id": "missing"},
        ):
            for window in (30, 60, 300, 86400):
                with self.subTest(filters=filters, window=window):
                    self.assertEquivalent(
                        self.overview(filters, limit=10000, window=window),
                        self.overview(filters, window=window),
                    )
        bounds = {"start_ms": self.now - 60000, "end_ms": self.now - 15000}
        self.assertEquivalent(self.overview(limit=10000, **bounds), self.overview(**bounds))
        measured = self.overview({"id": "flights-r01"})["http"]
        self.assertEqual(measured["requests"], 4)
        self.assertEqual(measured["http_requests"], 2)
        self.assertEqual(measured["sized_requests"], 1)
        self.assertEqual(measured["mean_response_bytes"], 200)
        self.assertEqual(self.overview({"id": "flights-r01"})["current"]["rps"], 1 / 15)

    def test_endpoint_leaders_are_ranked_after_combining_replicas(self):
        for suffix in (1, 2):
            routes = tuple(f"/busy-{suffix}-{i}" for i in range(19)) + ("/shared",)
            monitor = self.monitor(f"flights-r0{suffix}", routes=routes)
            for route in (*routes[:-1], "unmatched"):
                for _ in range(20):
                    monitor.record_request(0.1, route=route)
            for _ in range(15):
                monitor.record_request(0.1, route="/shared")
            self.ingest([self.payload(monitor, self.now - 1000, 15000)])
        actual = self.overview({"site_id": "flights"})
        self.assertEquivalent(self.overview({"site_id": "flights"}, limit=10000), actual)
        shared = next(row for row in actual["http"]["routes"] if row["route"] == "/shared")
        self.assertEqual(shared["requests"], 30)
        self.assertEqual(actual["http"]["route_count"], 40)
        for replica in actual["sites"][0]["replicas"]:
            self.assertEqual(replica["http"]["route_count"], 21)
            self.assertNotIn("/shared", {r["route"] for r in replica["http"]["routes"]})

    def test_equal_observation_timestamps_choose_the_newest_run_for_live_gauges(self):
        old = self.monitor(started=self.now - 30000)
        for _ in range(5):
            old.record_request(0.1)
        self.ingest([self.payload(old, self.now - 1, 15000, legacy=True)])
        new = self.monitor(started=self.now - 15000)
        for _ in range(2):
            new.record_request(0.1)
        self.ingest([self.payload(new, self.now - 1, 15000, active=7)])
        actual = self.overview({"id": "flights-r01"})
        self.assertEquivalent(self.overview({"id": "flights-r01"}, limit=10000), actual)
        self.assertEqual(actual["http"]["requests"], 7)
        self.assertAlmostEqual(actual["current"]["rps"], 2 / 15)
        self.assertEqual(actual["current"]["in_flight"], 7)

    def test_timeout_returns_no_partial_result_and_reading_recovers(self):
        monitor = self.monitor()
        samples = []
        for step in range(1, 21):
            monitor.record_request(0.1)
            samples.append(self.payload(monitor, self.now - (20 - step) * 1000, step * 1000))
        self.ingest(samples)
        with patch("bobowatch.storage.HISTORY_QUERY_SECONDS", 0):
            with self.assertRaises(HistoryQueryTimeout):
                self.overview()
            app = Application(self.config, self.store)
            env = {
                "REQUEST_METHOD": "GET",
                "PATH_INFO": "/api/overview",
                "QUERY_STRING": "window=86400",
                "HTTP_HOST": "127.0.0.1:8720",
                "REMOTE_ADDR": "127.0.0.1",
                "wsgi.input": io.BytesIO(),
                "HTTP_AUTHORIZATION": "Bearer " + self.config.admin_token_file.read_text().strip(),
            }
            status = []
            body = b"".join(app(env, lambda response, headers: status.append(response)))
            self.assertEqual(status[0], "503 Service Unavailable")
            self.assertEqual(json.loads(body)["code"], "history_query_timeout")
        self.assertEqual(self.overview()["http"]["requests"], 20)

    def test_empty_range_and_inventory_do_not_create_observations(self):
        self.assertEquivalent(self.overview(limit=10000), self.overview())
        with closing(self.store._connect()) as conn:
            self.assertEqual(aggregate_history(conn, [], 0, self.now, 5000), ([], {}))
