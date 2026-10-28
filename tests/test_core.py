"""Storage/protocol/API contracts, including recovery and authorization boundaries."""

import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from bobowatch import Monitor
from bobowatch.application import Application
from bobowatch.config import initialize, load
from bobowatch.protocol import MAX_BODY, Conflict, Invalid, validate
from bobowatch.storage import SQLiteStore


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name) / "state"
        self.config = initialize(self.directory, demo=True)
        self.store = SQLiteStore(self.config)
        self.addCleanup(lambda: self.store.close())
        self.now = int(time.time() * 1000)
        self.run = str(uuid.uuid4())

    def payload(self, sequence=1, count=10, *, run=None, elapsed=1000):
        return dict(
            version=1,
            replica="flights-r01",
            run_id=run or self.run,
            revision="test-v1",
            sequence=sequence,
            started_at_ms=self.now - 1000,
            observed_at_ms=self.now,
            elapsed_ms=elapsed,
            requests=count,
            errors=0,
            duration_sum_us=count * 1000,
            buckets=[count] + [0] * 13,
            events=[],
        )

    def ingest(self, payload):
        return self.store.ingest(validate(payload), payload["replica"])

    def overview(self, now=None):
        return self.store.overview({}, 3600, now_ms=now or self.now + 1)

    def test_retries_are_atomic_and_do_not_refresh_liveness(self):
        p = self.payload()
        self.assertFalse(self.ingest(p)["duplicate"])
        received = self.overview()["sites"][
            0 if self.overview()["sites"][0]["id"] == "flights" else 1
        ]["replicas"][0]["received_at_ms"]
        self.assertTrue(self.ingest(p)["duplicate"])
        q = self.payload(2, count=13, elapsed=2000)
        self.ingest(q)
        sites = {s["id"]: s for s in self.overview()["sites"]}
        self.assertEqual(sites["flights"]["requests"], 13)
        self.assertEqual(sites["flights"]["p95"]["count"], 13)
        self.assertEqual(sites["budget"]["replicas"][0]["status"], "waiting")
        p["requests"] += 1
        p["buckets"][0] += 1
        p["duration_sum_us"] += 1000
        with self.assertRaises(Conflict):
            self.ingest(p)
        self.assertEqual({s["id"]: s for s in self.overview()["sites"]}["flights"]["requests"], 13)
        self.assertGreaterEqual(received, self.now)

    def test_conflicting_event_rolls_back_whole_snapshot(self):
        p = self.payload()
        p["events"] = [dict(id="event-1", at_ms=self.now, severity="info", message="first")]
        self.ingest(p)
        q = self.payload(2, count=14, elapsed=2000)
        q["events"] = [dict(p["events"][0], message="different")]
        with self.assertRaises(Conflict):
            self.ingest(q)
        data = self.overview()
        self.assertEqual(sum(s["requests"] for s in data["sites"]), 10)
        self.assertEqual(len(data["events"]), 1)
        q["events"] = p["events"]
        self.ingest(q)
        self.assertEqual(len(self.overview()["events"]), 1)

    def test_ordering_resets_and_counter_corruption(self):
        self.ingest(self.payload(sequence=2))
        with self.assertRaises(Conflict):
            self.ingest(self.payload(sequence=1))
        with self.assertRaises(Conflict):
            self.ingest(self.payload(sequence=3, count=9, elapsed=2000))
        p = self.payload(run=str(uuid.uuid4()), count=4)
        p["started_at_ms"] = self.now
        self.ingest(p)
        self.assertEqual(sum(s["requests"] for s in self.overview()["sites"]), 14)
        with self.assertRaises(Conflict):
            self.ingest(self.payload(sequence=3, elapsed=3000))

    def test_restart_and_backup_preserve_history_events_and_dedup(self):
        p = self.payload()
        p["events"] = [dict(id="e", at_ms=self.now, severity="info", message="deploy")]
        self.ingest(p)
        backup = Path(self.tmp.name) / "backup.sqlite3"
        self.store.backup(backup)
        self.store.close()
        self.store = SQLiteStore(self.config)
        self.assertTrue(self.ingest(p)["duplicate"])
        self.assertEqual(sum(s["requests"] for s in self.overview()["sites"]), 10)
        self.assertEqual(self.overview()["events"][0]["message"], "deploy")
        restored = replace(self.config, database=backup)
        other = SQLiteStore(restored)
        try:
            self.assertTrue(other.ingest(p, p["replica"])["duplicate"])
            self.assertEqual(len(other.overview({}, 3600)["events"]), 1)
        finally:
            other.close()
        with self.assertRaises(FileExistsError):
            self.store.backup(backup)

    def test_no_data_stale_skew_and_gap_states(self):
        p = self.payload(elapsed=20000)
        self.ingest(p)
        sites = {s["id"]: s for s in self.overview()["sites"]}
        self.assertTrue(sites["flights"]["partial"])
        stale = {s["id"]: s for s in self.overview(self.now + 20000)["sites"]}
        self.assertEqual(stale["flights"]["replicas"][0]["status"], "stale")
        self.assertEqual(stale["budget"]["replicas"][0]["status"], "waiting")
        p = self.payload(sequence=2, count=12, elapsed=21000)
        p["observed_at_ms"] += 60000
        self.ingest(p)
        data = self.overview(self.now + 60001)
        self.assertEqual(
            {s["id"]: s for s in data["sites"]}["flights"]["replicas"][0]["status"], "skewed"
        )

    def test_scope_and_integer_histogram_validation(self):
        for field, value in [
            ("requests", True),
            ("duration_sum_us", float("nan")),
            ("sequence", 2**70),
        ]:
            p = self.payload()
            p[field] = value
            with self.assertRaises(Invalid):
                validate(p)
        p = self.payload()
        p["buckets"][0] = 9
        with self.assertRaises(Invalid):
            validate(p)
        p = self.payload()
        p["buckets"] = [0] * 14
        p["buckets"][0] = 20
        p["buckets"][1] = -10
        with self.assertRaises(Invalid):
            validate(p)
        with self.assertRaises(Invalid):
            self.store.ingest(self.payload(), "budget-r01")

    def test_filters_and_future_schema_rejection(self):
        self.ingest(self.payload())
        self.assertEqual(
            [s["id"] for s in self.store.overview({"project_id": "travel"}, 3600)["sites"]],
            ["flights"],
        )
        self.assertEqual(self.store.overview({"host_id": "not-here"}, 3600)["sites"], [])
        self.store.close()
        with closing(sqlite3.connect(self.config.database)) as conn, conn:
            conn.execute("UPDATE schema_migrations SET version=999 WHERE version=1")
        with self.assertRaises(Invalid):
            SQLiteStore(self.config)

    def test_inventory_retirement_and_no_silent_target_removal(self):
        self.store.close()
        with self.assertRaises(Invalid):
            SQLiteStore(replace(self.config, replicas=()))
        retired = tuple(dict(r, retired=True) for r in self.config.replicas)
        self.store = SQLiteStore(replace(self.config, replicas=retired))
        data = self.store.overview({}, 3600)
        self.assertEqual(len(data["sites"]), 2)
        self.assertTrue(
            all(
                r["retired"] and r["status"] == "retired"
                for site in data["sites"]
                for r in site["replicas"]
            )
        )
        self.assertIsNone(data["current"]["rps"])
        with self.assertRaises(Conflict):
            self.ingest(self.payload())

    def call(self, path, *, token="", payload=None, method="GET", **overrides):
        app = Application(self.config, self.store)
        raw = json.dumps(payload).encode() if payload is not None else b""
        env = {
            "REQUEST_METHOD": method,
            "PATH_INFO": path,
            "QUERY_STRING": "",
            "HTTP_HOST": "127.0.0.1:8720",
            "REMOTE_ADDR": "127.0.0.1",
            "HTTP_AUTHORIZATION": "Bearer " + token,
            "CONTENT_TYPE": "application/json",
            "CONTENT_LENGTH": str(len(raw)),
            "wsgi.input": io.BytesIO(raw),
        }
        env.update(overrides)
        results = []
        body = b"".join(app(env, lambda status, headers: results.append((status, dict(headers)))))
        return int(results[0][0].split()[0]), body, results[0][1]

    def test_api_auth_origin_body_limits_and_errors(self):
        admin = self.config.admin_token_file.read_text().strip()
        producer = self.config.replicas[0]["token_file"].read_text().strip()
        self.assertEqual(self.call("/")[0], 200)
        self.assertEqual(self.call("/api/overview")[0], 401)
        self.assertEqual(self.call("/api/overview", token=producer)[0], 401)
        self.assertEqual(self.call("/api/overview", token=admin)[0], 200)
        self.assertEqual(
            self.call("/v1/ingest", token=admin, method="POST", payload=self.payload())[0], 401
        )
        self.assertEqual(
            self.call("/v1/ingest", token=producer, method="POST", payload=self.payload())[0], 200
        )
        self.assertEqual(
            self.call("/api/overview", token=admin, HTTP_ORIGIN="http://other.example")[0], 403
        )
        self.assertEqual(
            self.call("/api/overview", token=admin, HTTP_HOST="other.example:8720")[0], 403
        )
        self.assertEqual(
            self.call(
                "/v1/ingest", token=producer, method="POST", CONTENT_LENGTH=str(MAX_BODY + 1)
            )[0],
            413,
        )
        self.assertEqual(self.call("/api/overview", token=admin, QUERY_STRING="window=-1")[0], 400)
        self.assertEqual(
            self.call(
                "/v1/ingest",
                token=producer,
                method="POST",
                CONTENT_LENGTH="7",
                **{"wsgi.input": io.BytesIO(b"{broken")},
            )[0],
            400,
        )
        with patch.object(self.store, "ingest", side_effect=sqlite3.OperationalError("locked")):
            self.assertEqual(
                self.call("/v1/ingest", token=producer, method="POST", payload=self.payload(2))[0],
                503,
            )

    def test_lan_access_preserves_auth_origin_and_host_checks(self):
        self.config = replace(self.config, host="0.0.0.0", allowed_hosts=("bobowatch.local",))
        admin = self.config.admin_token_file.read_text().strip()
        for host in ("192.168.2.23:8720", "10.1.2.3:8720", "bobowatch.local:8720"):
            headers = {"HTTP_HOST": host, "REMOTE_ADDR": "192.168.2.50"}
            self.assertEqual(self.call("/", **headers)[0], 200)
            self.assertEqual(self.call("/api/overview", **headers)[0], 401)
            self.assertEqual(
                self.call("/api/overview", token=admin, HTTP_ORIGIN="http://" + host, **headers)[0],
                200,
            )
            self.assertEqual(
                self.call(
                    "/api/overview", token=admin, HTTP_ORIGIN="http://evil.example", **headers
                )[0],
                403,
            )
        for host in (
            "evil.example:8720",
            "192.168.2.23:80",
            "8.8.8.8:8720",
            "192.168.2.23:8720@evil.example:8720",
            "[broken:8720",
        ):
            self.assertEqual(self.call("/", HTTP_HOST=host, REMOTE_ADDR="192.168.2.50")[0], 403)
        self.assertEqual(
            self.call("/", HTTP_HOST="192.168.2.23:8720", REMOTE_ADDR="8.8.8.8")[0], 403
        )

    def test_lan_configuration_is_explicit_and_validated(self):
        path = self.config.path
        original = path.read_text()
        path.write_text(
            original.replace(
                'listen = "127.0.0.1:8720"',
                'listen = "0.0.0.0:8720"\nallowed_hosts = ["BoboWatch.local"]',
            )
        )
        config = load(path)
        self.assertEqual(config.host, "0.0.0.0")
        self.assertEqual(config.allowed_hosts, ("bobowatch.local",))
        path.write_text(path.read_text().replace("BoboWatch.local", "http://invalid:8720"))
        with self.assertRaises(Invalid):
            load(path)
        path.write_text(original.replace("127.0.0.1:8720", "8.8.8.8:8720"))
        with self.assertRaises(Invalid):
            load(path)

    def test_adapter_no_request_path_io_bounded_events_and_failure(self):
        monitor = Monitor(
            replica="flights-r01",
            endpoint="http://127.0.0.1:1/v1/ingest",
            token_file=self.config.replicas[0]["token_file"],
        )
        with patch(
            "urllib.request.OpenerDirector.open", side_effect=AssertionError("request path network")
        ):
            monitor.record_request(0.02, 503)
            for index in range(120):
                monitor.event(str(index))
        payload = validate(monitor.snapshot())
        self.assertEqual(payload["requests"], 1)
        self.assertEqual(payload["errors"], 1)
        self.assertEqual(len(payload["events"]), 100)
        self.assertEqual(monitor.dropped_events, 20)
        self.assertFalse(monitor.flush())
        monitor.record_request(0.1)
        self.assertEqual(monitor.snapshot()["requests"], 2)
        with self.assertRaises(ValueError):
            Monitor(replica="x", endpoint="http://example.com/v1/ingest", token_file="x")
        # A clean interpreter can import the adapter without site-packages or threads.
        result = subprocess.run(
            [
                sys.executable,
                "-S",
                "-c",
                'import bobowatch,sys,threading; assert "waitress" not in sys.modules; assert threading.active_count()==1',
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_config_validation_and_runtime_secrets(self):
        self.assertEqual(self.config.admin_token_file.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(Invalid):
            initialize(self.directory, demo=False)
        path = self.config.path
        path.write_text(
            path.read_text().replace("sqlite:///monitor.sqlite3", "postgresql://localhost/monitor")
        )
        with self.assertRaises(Invalid):
            load(path)


if __name__ == "__main__":
    unittest.main()
