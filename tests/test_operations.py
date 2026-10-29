"""Independent readiness, safe diagnostics, HTTP outcomes and scoped retention."""

import io
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from bobowatch import Monitor
from bobowatch.access import set_access
from bobowatch.application import Application
from bobowatch.config import initialize, load
from bobowatch.storage import Busy, SQLiteStore


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = initialize(Path(self.temp.name) / "watch", demo=True)
        self.store = SQLiteStore(self.config)
        self.addCleanup(self.store.close)
        self.app = Application(self.config, self.store)
        self.admin = self.config.admin_token_file.read_text().strip()
        self.producer = self.config.replicas[0]["token_file"].read_text().strip()

    def call(self, path, *, method="GET", token="", body=None, **headers):
        raw = json.dumps(body).encode() if body is not None else b""
        env = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "HTTP_HOST": "127.0.0.1:8720",
            "REMOTE_ADDR": "127.0.0.1",
            "HTTP_AUTHORIZATION": "Bearer " + token,
            "CONTENT_TYPE": "application/json",
            "CONTENT_LENGTH": str(len(raw)),
            "wsgi.input": io.BytesIO(raw),
        } | headers
        statuses = []
        data = b"".join(self.app(env, lambda status, _: statuses.append(int(status.split()[0]))))
        return statuses[0], json.loads(data)

    def monitor(self, replica="flights-r01"):
        entry = next(r for r in self.config.replicas if r["id"] == replica)
        return Monitor(
            replica=replica,
            endpoint="http://127.0.0.1:8720/v1/ingest",
            token_file=entry["token_file"],
        )

    def test_operations_access_matches_dashboard_and_readiness_is_minimal(self):
        self.assertEqual(self.call("/api/operations")[0], 401)
        self.assertEqual(self.call("/api/operations", token=self.producer)[0], 401)
        code, data = self.call("/api/operations", token=self.admin)
        self.assertEqual(code, 200)
        self.assertTrue(data["ready"])
        self.assertEqual(data["dashboard_access"], "token")
        self.assertGreater(data["process"]["started_at_ms"], 0)
        self.assertGreaterEqual(data["process"]["uptime_seconds"], 0)
        self.assertTrue(data["writer"]["alive"])
        self.assertEqual(data["writer"]["queue_capacity"], 128)
        self.assertTrue(data["storage"]["available"])
        self.assertNotIn("database", data["storage"])
        for secret in (self.admin, self.producer, str(self.config.database)):
            self.assertNotIn(secret, json.dumps(data))
        self.assertEqual(self.call("/health"), (200, {"status": "ok"}))
        self.assertEqual(self.call("/ready"), (200, {"status": "ready"}))
        for path in ("/ready", "/api/operations"):
            self.assertEqual(self.call(path, method="POST", token=self.admin)[0], 405)
            for headers in (
                {"REMOTE_ADDR": "8.8.8.8"},
                {"HTTP_HOST": "evil.example:8720"},
                {"HTTP_ORIGIN": "http://evil.example"},
            ):
                self.assertEqual(self.call(path, token=self.admin, **headers)[0], 403)
        set_access(self.config.path.parent, "local")
        self.app.reload()
        self.assertEqual(self.call("/api/operations")[0], 200)
        config_text = self.config.path.read_text().replace("127.0.0.1:8720", "0.0.0.0:8720")
        self.config.path.write_text(config_text)
        # A separate instance represents a listener initially configured for LAN.
        lan_app = Application(load(self.config.path), self.store)
        prior, self.app = self.app, lan_app
        try:
            self.assertEqual(
                self.call(
                    "/api/operations",
                    REMOTE_ADDR="192.168.2.50",
                    HTTP_HOST="192.168.2.23:8720",
                )[0],
                200,
            )
        finally:
            self.app = prior

    def test_ingestion_counts_are_http_process_outcomes_and_thread_safe(self):
        payload = self.monitor().snapshot()
        self.assertEqual(
            self.call("/v1/ingest", method="POST", token=self.producer, body=payload)[0], 200
        )
        self.assertEqual(
            self.call("/v1/ingest", method="POST", token=self.producer, body=payload)[0], 200
        )
        changed = dict(payload, revision="conflicting")
        self.assertEqual(
            self.call("/v1/ingest", method="POST", token=self.producer, body=changed)[0], 409
        )
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.call("/v1/ingest", method="POST")[0], range(64)))
        self.assertEqual(set(results), {401})
        data = self.call("/api/operations", token=self.admin)[1]["ingestion"]
        self.assertEqual((data["accepted"], data["duplicates"], data["rejected"]), (1, 1, 65))
        self.assertEqual(data["last_status"], 401)
        self.assertIsNotNone(data["last_accepted_at_ms"])
        self.assertIsNotNone(data["last_rejected_at_ms"])
        # A new HTTP process starts a fresh counter lifetime while SQL persists.
        self.assertEqual(
            Application(self.config, self.store).operations()["ingestion"]["accepted"], 0
        )

    def test_rejected_configuration_preserves_service_and_exposes_no_error_text(self):
        prior = self.config.path.read_text()
        self.config.path.write_text('secret-token = "' + self.admin)
        with self.assertRaises(ValueError):
            self.app.reload()
        data = self.call("/api/operations", token=self.admin)[1]
        self.assertTrue(data["ready"])
        self.assertEqual(data["configuration"]["status"], "rejected")
        self.assertEqual(data["configuration"]["error_type"], "TOMLDecodeError")
        self.assertIsNotNone(data["configuration"]["last_rejected_at_ms"])
        self.assertNotIn(self.admin, json.dumps(data))
        self.assertNotIn(str(self.config.path), json.dumps(data))
        self.config.path.write_text(prior)
        self.assertFalse(self.app.reload())
        recovered = self.app.operations()["configuration"]
        self.assertEqual(recovered["status"], "current")
        self.assertIsNone(recovered["error_type"])
        self.assertEqual(
            recovered["last_rejected_at_ms"], data["configuration"]["last_rejected_at_ms"]
        )
        self.assertGreaterEqual(
            recovered["last_success_at_ms"], data["configuration"]["last_success_at_ms"]
        )

    def test_diagnostics_bypass_writer_queue_and_long_history_reads(self):
        entered, release = threading.Event(), threading.Event()

        def work(conn):
            entered.set()
            release.wait(3)

        errors = []

        def submit():
            try:
                self.store._submit(work)
            except Exception as error:
                errors.append(error)

        thread = threading.Thread(target=submit)
        thread.start()
        try:
            self.assertTrue(entered.wait(1))
            with (
                patch.object(self.store, "_submit", side_effect=AssertionError("No writer queue")),
                patch.object(
                    self.store, "overview", side_effect=AssertionError("No history query")
                ),
            ):
                code, data = self.call("/api/operations", token=self.admin)
                self.assertEqual(code, 200)
                self.assertTrue(data["ready"])
                self.assertIsNotNone(data["writer"]["busy_since_ms"])
        finally:
            release.set()
            thread.join(3)
        self.assertFalse(errors)

    def test_missing_volume_failed_maintenance_and_writer_stall_are_visible(self):
        with patch.object(self.store, "_check_volume", side_effect=Busy("secret path/token")):
            code, data = self.call("/api/operations", token=self.admin)
            self.assertEqual(code, 200)
            self.assertFalse(data["ready"])
            self.assertEqual(data["storage"], {"available": False, "error_type": "Busy"})
            self.assertEqual(self.call("/ready"), (503, {"status": "unavailable"}))
            self.assertEqual(self.call("/health"), (200, {"status": "ok"}))
            self.assertNotIn("secret", json.dumps(data))
        with patch("bobowatch.storage.maintain", side_effect=OSError("secret path/token")):
            with self.assertRaises(OSError):
                self.store._submit(lambda conn: None)
            data = self.app.operations()
            self.assertFalse(data["ready"])
            self.assertEqual(data["maintenance"]["error_type"], "OSError")
            self.assertIsNotNone(data["maintenance"]["last_error_at_ms"])
            self.assertNotIn("secret", json.dumps(data))
        self.store._submit(lambda conn: None)
        self.assertTrue(self.app.operations()["ready"])
        self.assertIsNone(self.app.operations()["maintenance"]["error_type"])
        with self.store._diagnostics_lock:
            self.store._busy_since = time.monotonic() - 10
            self.store._writer_activity["busy_since_ms"] = int(time.time() * 1000) - 10_000
        try:
            data = self.app.operations()
            self.assertFalse(data["ready"])
            self.assertTrue(data["writer"]["stalled"])
        finally:
            with self.store._diagnostics_lock:
                self.store._busy_since = None
                self.store._writer_activity["busy_since_ms"] = None
        with patch.object(self.store._queue, "qsize", return_value=128):
            self.assertFalse(self.app.operations()["ready"])
        self.store.close()
        data = self.app.operations()
        self.assertFalse(data["ready"])
        self.assertFalse(data["writer"]["alive"])
        self.assertFalse(data["writer"]["accepting"])

    def test_storage_errors_are_read_only_safe_and_do_not_fall_back(self):
        original = self.store.path
        self.store.path = self.config.path.parent / "missing.sqlite3"
        try:
            data = self.app.operations()
            self.assertFalse(data["ready"])
            self.assertFalse(data["storage"]["available"])
            self.assertFalse(self.store.path.exists())
        finally:
            self.store.path = original
        with patch(
            "bobowatch.storage.sqlite3.connect", side_effect=sqlite3.OperationalError("secret")
        ):
            data = self.app.operations()
            self.assertFalse(data["ready"])
            self.assertEqual(data["storage"]["error_type"], "OperationalError")
            self.assertNotIn("secret", json.dumps(data))

    def test_readiness_respects_admission_reserve_and_maintenance_stall(self):
        baseline = self.store.operations_status()["storage"]
        for overrides in (
            {"volume_free_bytes": 0},
            {"quota_bytes": 1024**2, "reserve_bytes": 1024**2 // 10, "used_bytes": 1024**2},
            {"quota_bytes": 1024**2, "reserve_bytes": 1024**2 // 10, "live_bytes": 900_000},
        ):
            with self.subTest(overrides=overrides):
                with patch.object(self.store, "_storage_status", return_value=baseline | overrides):
                    data = self.store.operations_status()
                    self.assertTrue(data["storage"]["available"])
                    self.assertFalse(data["storage"]["admission_available"])
                    self.assertFalse(data["ready"])
        with self.store._diagnostics_lock:
            self.store._maintenance_since = time.monotonic() - 10
            self.store._maintenance["active_since_ms"] = int(time.time() * 1000) - 10_000
        try:
            data = self.store.operations_status()
            self.assertTrue(data["writer"]["stalled"])
            self.assertFalse(data["ready"])
        finally:
            with self.store._diagnostics_lock:
                self.store._maintenance_since = None
                self.store._maintenance["active_since_ms"] = None

    def test_retained_extents_follow_scope_include_events_and_ignore_query_range(self):
        now = int(time.time() * 1000)
        expected = {}
        for replica, before in (("flights-r01", 60_000), ("budget-r01", 120_000)):
            monitor = self.monitor(replica)
            payload = monitor.snapshot()
            payload.update(started_at_ms=now - before - 1000, observed_at_ms=now - before)
            payload["events"] = [
                {
                    "id": "before-" + replica,
                    "at_ms": now - before - 2000,
                    "severity": "info",
                    "message": "fictional extent fixture",
                }
            ]
            self.store.ingest(payload, replica)
            expected[replica] = (now - before, now - before - 2000)
        for site, replica in (("flights", "flights-r01"), ("budget", "budget-r01")):
            data = self.store.overview({"site_id": site}, 1, now_ms=now)
            self.assertEqual(data["http"]["snapshot_count"], 0)
            snapshot, event = expected[replica]
            self.assertEqual(
                data["retained"],
                {
                    "oldest_snapshot_at_ms": snapshot,
                    "newest_snapshot_at_ms": snapshot,
                    "oldest_event_at_ms": event,
                    "newest_event_at_ms": event,
                },
            )
            all_history = self.store.overview({"site_id": site}, "all", now_ms=now)
            self.assertEqual(all_history["range_start_ms"], event)
            self.assertEqual(all_history["retained"], data["retained"])
        empty = self.store.overview({"id": "flights-r02"}, "all", now_ms=now)
        self.assertEqual(set(empty["retained"].values()), {None})
        absent = self.store.overview({"id": "nonexistent"}, "all", now_ms=now)
        self.assertEqual(set(absent["retained"].values()), {None})


if __name__ == "__main__":
    unittest.main()
