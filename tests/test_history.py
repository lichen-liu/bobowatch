"""Full-range SQL aggregation agrees with raw observations, including old traffic."""

import io
import json
import sqlite3
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
from bobowatch.http_metrics import load_http, summary
from bobowatch.protocol import BUCKETS_US, validate
from bobowatch.storage import SQLiteStore


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

    def overview(self, filters=None, *, raw=False, window=86400, **bounds):
        if raw:
            with patch("bobowatch.storage.aggregate_history", side_effect=self.raw_history):
                return self.store.overview(filters or {}, window, now_ms=self.now, **bounds)
        return self.store.overview(filters or {}, window, now_ms=self.now, **bounds)

    def raw_history(self, conn, replicas, since, until, step, *, exclusive_end=False):
        marks = ",".join("?" for _ in replicas)
        end = "<" if exclusive_end else "<="
        rows = [
            dict(row)
            for row in conn.execute(
                f"""
            SELECT n.*,u.replica_id AS replica,r.site_id AS site,u.started_at_ms AS run_started_at_ms
            FROM snapshots n JOIN runs u ON u.id=n.run_id JOIN replicas r ON r.id=u.replica_id
            WHERE u.replica_id IN ({marks}) AND n.observed_at_ms>=? AND n.observed_at_ms {end} ?
            ORDER BY n.observed_at_ms DESC""",
                (*replicas, since, until),
            )
        ]
        by_id = {row["id"]: row for row in rows}
        for row in rows:
            row["buckets"] = [0] * (len(BUCKETS_US) + 1)
        for offset in range(0, len(rows), 200):
            keys = [row["id"] for row in rows[offset : offset + 200]]
            placeholders = ",".join("?" for _ in keys)
            for bucket in conn.execute(
                f"SELECT * FROM histogram_buckets WHERE snapshot_id IN ({placeholders})", keys
            ):
                by_id[bucket["snapshot_id"]]["buckets"][bucket["bucket_index"]] = bucket["delta"]
        load_http(conn, rows)
        endpoints = {}
        scopes = [("all", "", rows)]
        scopes += [
            ("site", site, [r for r in rows if r["site"] == site])
            for site in {r["site"] for r in rows}
        ]
        scopes += [
            ("replica", replica, [r for r in rows if r["replica"] == replica])
            for replica in replicas
        ]
        for kind, scope, values in scopes:
            metrics = summary(values)
            endpoints[kind, scope] = {key: metrics[key] for key in ("routes", "route_count")}
        return rows, endpoints

    def assertEquivalent(self, expected, actual, path="overview"):
        if isinstance(expected, dict):
            self.assertEqual(expected.keys(), actual.keys(), path)
            for key in expected:
                if path == "overview.storage" and key == "volume_free_bytes":
                    continue
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
        self.assertEquivalent(self.overview(raw=True), actual)
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
                        self.overview(filters, raw=True, window=window),
                        self.overview(filters, window=window),
                    )
        bounds = {"start_ms": self.now - 60000, "end_ms": self.now - 15000}
        self.assertEquivalent(self.overview(raw=True, **bounds), self.overview(**bounds))
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
        self.assertEquivalent(self.overview({"site_id": "flights"}, raw=True), actual)
        shared = next(row for row in actual["http"]["routes"] if row["route"] == "/shared")
        self.assertEqual(shared["requests"], 30)
        self.assertEqual(actual["http"]["route_count"], 40)
        for replica in actual["sites"][0]["replicas"]:
            self.assertEqual(replica["http"]["route_count"], 21)
            self.assertIn("/shared", {r["route"] for r in replica["http"]["routes"]})
            self.assertEqual(len(replica["http"]["routes"]), 21)

    def test_diagnostic_source_counts_and_histograms_survive_grouping_and_retirement(self):
        legacy = self.monitor(started=self.now - 200000)
        for duration, age, elapsed in (
            (0.1, 90000, 20000),
            (0.2, 70000, 80000),
            (2, 50000, 140000),
        ):
            legacy.record_request(duration)
            self.ingest([self.payload(legacy, self.now - age, elapsed, legacy=True)])
        extended = self.monitor("flights-r02", started=self.now - 150000)
        for index, (duration, age) in enumerate(((0.005, 60000), (0.5, 40000), (1, 20000)), 1):
            extended.record_request(duration)
            self.ingest(
                [self.payload(extended, self.now - age, index * 15000)],
                skew=60000 if index > 1 else 0,
            )
        retired = self.monitor("budget-r01", started=self.now - 100000)
        retired.record_request(3)
        self.ingest([self.payload(retired, self.now - 15000, 80000)], skew=60000)
        self.store.reload_inventory(
            replace(
                self.config,
                replicas=tuple(
                    dict(r, retired=r["id"] == "budget-r01") for r in self.config.replicas
                ),
            )
        )
        expected_quality = {"snapshots": 7, "partial": 3, "skewed": 3, "extended_http": 4}
        expected_buckets = [1, 0, 0, 0, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0]
        # One large time group must retain three flagged observations, rather
        # than count a boolean MAX(partial) group as one or all of its samples.
        for resolution in (1, 15000, 300000, 86400000):
            with self.subTest(bucket_ms=resolution):
                data = self.overview(window="all", bucket_ms=resolution)
                self.assertEquivalent(
                    self.overview(raw=True, window="all", bucket_ms=resolution), data
                )
                metrics = data["http"]
                self.assertEqual(metrics["duration_us"], 6_805_000)
                self.assertEqual(metrics["quality"], expected_quality)
                self.assertEqual(metrics["snapshot_count"], expected_quality["snapshots"])
                self.assertEqual(metrics["buckets"], expected_buckets)
                self.assertEqual(
                    [
                        sum(point["duration_buckets"][i] for point in data["charts"])
                        for i in range(14)
                    ],
                    expected_buckets,
                )
                for key, count in expected_quality.items():
                    self.assertEqual(sum(point["quality"][key] for point in data["charts"]), count)
                    self.assertEqual(
                        sum(site["http"]["quality"][key] for site in data["sites"]), count
                    )
                    self.assertEqual(
                        sum(
                            replica["http"]["quality"][key]
                            for site in data["sites"]
                            for replica in site["replicas"]
                        ),
                        count,
                    )
                old = next(site for site in data["sites"] if site["id"] == "budget")
                self.assertEqual(old["http"]["duration_us"], 3_000_000)
                self.assertEqual(
                    old["http"]["quality"],
                    {"snapshots": 1, "partial": 1, "skewed": 1, "extended_http": 1},
                )
                self.assertEqual(old["replicas"][0]["status"], "retired")
                self.assertIsNone(old["current"]["rps"])

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
        self.assertEquivalent(self.overview({"id": "flights-r01"}, raw=True), actual)
        self.assertEqual(actual["http"]["requests"], 7)
        self.assertAlmostEqual(actual["current"]["rps"], 2 / 15)
        self.assertEqual(actual["current"]["in_flight"], 7)

    def test_complete_events_and_no_query_deadline(self):
        monitor = self.monitor(started=self.now - 600000)
        samples = []
        for step in range(550):
            monitor.record_request(0.1)
            at = self.now - (550 - step) * 1000
            payload = self.payload(monitor, at, (step + 1) * 1000)
            payload["events"] = [
                {
                    "id": f"event-{step}",
                    "at_ms": at,
                    "severity": "info",
                    "message": f"event number {step}",
                }
            ]
            samples.append(validate(payload))
        self.ingest(samples)
        connection = self.store._connect()
        test = self

        class NoDeadline:
            def __getattr__(self, name):
                return getattr(connection, name)

            def __enter__(self):
                connection.__enter__()
                return self

            def __exit__(self, *args):
                return connection.__exit__(*args)

            def set_progress_handler(self, *args):
                test.fail("Dashboard queries must not install a time budget")

        with patch.object(self.store, "_connect", return_value=NoDeadline()):
            data = self.overview(window="all")
        self.assertEqual(len(data["events"]), 550)
        self.assertEqual(data["events"][-1]["id"], "event-0")
        self.assertEqual(data["http"]["snapshot_count"], 550)
        self.assertEqual(data["http"]["requests"], 550)
        app = Application(self.config, self.store)
        env = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/api/overview",
            "QUERY_STRING": "window=all&bucket_ms=1",
            "HTTP_HOST": "127.0.0.1:8720",
            "REMOTE_ADDR": "127.0.0.1",
            "wsgi.input": io.BytesIO(),
            "HTTP_AUTHORIZATION": "Bearer " + self.config.admin_token_file.read_text().strip(),
        }
        status = []
        body = b"".join(app(env, lambda response, headers: status.append(response)))
        self.assertEqual(status[0], "200 OK")
        full = json.loads(body)
        self.assertEqual(len(full["events"]), 550)
        self.assertEqual(len(full["charts"]), 550)

    def test_every_timestamp_and_grouping_include_identical_totals(self):
        monitor = self.monitor(started=self.now - 120000)
        samples = []
        for step in range(1000):
            monitor.record_request(0.1, route="/busy", bytes_sent=100, bytes_received=10)
            samples.append(self.payload(monitor, self.now - 1000 + step, (step + 1) * 100))
        self.ingest(samples)
        for resolution, points in ((1, 1000), (60000, None), (3600000, None), (86400000, None)):
            data = self.overview(window="all", bucket_ms=resolution)
            self.assertEqual(data["http"]["snapshot_count"], 1000)
            self.assertEqual(data["http"]["requests"], 1000)
            self.assertEqual(sum(point["requests"] for point in data["charts"]), 1000)
            self.assertTrue(
                all(
                    data["range_start_ms"] <= point["at_ms"] < data["range_end_ms"]
                    for point in data["charts"]
                )
            )
            if points:
                self.assertEqual(len(data["charts"]), points)

    def test_retired_replica_history_remains_visible_and_is_not_a_live_gauge(self):
        old = self.monitor()
        old.record_request(2, 503, route="/quiet")
        self.ingest([self.payload(old, self.now - 10000, 15000, active=8)])
        config = replace(
            self.config,
            replicas=tuple(dict(r, retired=r["id"] == "flights-r01") for r in self.config.replicas),
        )
        self.store.reload_inventory(config)
        data = self.overview({"id": "flights-r01"}, window="all")
        self.assertEqual(data["http"]["requests"], 1)
        self.assertEqual(data["http"]["errors"], 1)
        self.assertEqual(data["sites"][0]["replicas"][0]["status"], "retired")
        self.assertIsNone(data["current"]["rps"])
        self.assertIsNone(data["current"]["in_flight"])

    def test_empty_range_and_inventory_do_not_create_observations(self):
        self.assertEquivalent(self.overview(raw=True), self.overview())
        data = self.overview()
        self.assertEqual(data["http"]["duration_us"], 0)
        self.assertEqual(
            data["http"]["quality"],
            {"snapshots": 0, "partial": 0, "skewed": 0, "extended_http": 0},
        )
        self.assertEqual(data["charts"], [])
        with closing(self.store._connect()) as conn:
            self.assertEqual(aggregate_history(conn, [], 0, self.now, 5000), ([], {}))

    def test_lock_wait_retries_and_dashboard_metadata_does_not_wait_on_writer_queue(self):
        expected = self.overview()
        busy = sqlite3.OperationalError("locked")
        busy.sqlite_errorcode = sqlite3.SQLITE_BUSY
        with (
            patch.object(self.store, "_overview", side_effect=[busy, busy, expected]),
            patch("bobowatch.storage.time.sleep") as sleep,
        ):
            self.assertEqual(self.overview(), expected)
            self.assertEqual(sleep.call_count, 2)
        with patch.object(
            self.store,
            "_submit",
            side_effect=AssertionError(
                "Dashboard reads must not use the writer acknowledgement deadline"
            ),
        ):
            self.assertIn("storage", self.overview())

    def test_all_endpoints_are_returned_even_beyond_one_hundred_templates(self):
        for run in range(8):
            monitor = self.monitor(
                started=self.now - 100000 + run * 1000,
                routes=tuple(f"/template-{run}-{i}" for i in range(20)),
            )
            monitor.record_request(0.1, route=f"/template-{run}-0")
            self.ingest([self.payload(monitor, self.now - 20000 + run * 1000, 15000)])
        metrics = self.overview()["http"]
        self.assertEqual(metrics["route_count"], 161)
        self.assertEqual(len(metrics["routes"]), 161)
        self.assertIn("/template-0-19", {r["route"] for r in metrics["routes"]})

    def test_all_history_includes_events_outside_snapshot_time_bounds(self):
        monitor = self.monitor()
        monitor.record_request(0.1)
        payload = self.payload(monitor, self.now - 10000, 15000)
        payload["events"] = [
            {
                "id": "earlier-event",
                "at_ms": self.now - 60000,
                "severity": "info",
                "message": "before first snapshot",
            },
            {
                "id": "later-event",
                "at_ms": self.now + 5000,
                "severity": "info",
                "message": "clock-skewed event",
            },
        ]
        self.ingest([validate(payload)])
        data = self.overview({"id": "flights-r01"}, window="all")
        self.assertEqual(data["range_start_ms"], self.now - 60000)
        self.assertEqual(data["range_end_ms"], self.now + 5001)
        self.assertEqual(
            {event["id"] for event in data["events"]}, {"earlier-event", "later-event"}
        )
        self.assertEqual(data["http"]["snapshot_count"], 1)
