"""Local dashboard reads do not grant producer or administrator privileges."""

import io
import json
import tempfile
import unittest
from pathlib import Path

from bobowatch import Monitor, admin
from bobowatch.access import read_access, set_access
from bobowatch.application import Application
from bobowatch.config import initialize, load
from bobowatch.protocol import Invalid
from bobowatch.provision import private_write
from bobowatch.storage import SQLiteStore


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        config = initialize(Path(self.temp.name) / "watch", demo=True)
        config.path.write_text(config.path.read_text().replace("127.0.0.1:8720", "0.0.0.0:8720"))
        self.config = load(config.path)
        self.root = self.config.path.parent
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
        status = []
        result = b"".join(self.app(env, lambda code, hdrs: status.append(int(code.split()[0]))))
        return status[0], json.loads(result)

    def local(self):
        set_access(self.root, "local")
        self.assertTrue(self.app.reload())

    def test_local_dashboard_opens_without_login_on_desktop_and_phone(self):
        self.local()
        for remote, host in (
            ("127.0.0.1", "127.0.0.1:8720"),
            ("192.168.2.50", "192.168.2.23:8720"),
        ):
            with self.subTest(remote=remote):
                headers = {"REMOTE_ADDR": remote, "HTTP_HOST": host}
                self.assertEqual(
                    self.call("/api/access", **headers), (200, {"dashboard_access": "local"})
                )
                code, data = self.call("/api/overview", **headers)
                self.assertEqual(code, 200)
                self.assertEqual(data["dashboard_access"], "local")
                self.assertNotIn(self.admin, json.dumps(data))
                self.assertNotIn(self.producer, json.dumps(data))
        self.assertEqual(Application(self.config, self.store).dashboard_access, "local")

    def test_local_access_keeps_network_host_and_origin_boundaries(self):
        self.local()
        for path in ("/api/access", "/api/overview"):
            for headers in (
                {"REMOTE_ADDR": "8.8.8.8"},
                {"HTTP_HOST": "evil.example:8720"},
                {"HTTP_ORIGIN": "http://evil.example"},
                {"HTTP_HOST": "192.168.2.23:80"},
            ):
                with self.subTest(path=path, headers=headers):
                    self.assertEqual(self.call(path, **headers)[0], 403)

    def test_local_dashboard_does_not_authorize_ingestion_or_admin_control(self):
        self.local()
        monitor = Monitor(
            replica="flights-r01",
            endpoint="http://127.0.0.1:8720/v1/ingest",
            token_file=self.config.replicas[0]["token_file"],
        )
        payload = monitor.snapshot()
        for token in ("", "wrong", self.admin):
            self.assertEqual(
                self.call("/v1/ingest", method="POST", token=token, body=payload)[0], 401
            )
        self.assertEqual(
            self.call("/v1/ingest", method="POST", token=self.producer, body=payload)[0], 200
        )
        command = {"command": "dashboard.access", "access": "token"}
        for path, method, body in (
            ("/api/storage", "GET", None),
            ("/api/control", "POST", command),
        ):
            for token, remote in (
                ("", "127.0.0.1"),
                (self.producer, "127.0.0.1"),
                (self.admin, "192.168.2.50"),
            ):
                self.assertEqual(
                    self.call(path, method=method, body=body, token=token, REMOTE_ADDR=remote)[0],
                    403,
                )
        self.assertEqual(self.call("/api/storage", token=self.admin)[0], 200)

    def test_access_changes_live_and_invalid_policy_keeps_last_valid_state(self):
        self.assertEqual(self.call("/api/overview")[0], 401)
        self.local()
        prior = self.app._state
        private_write(self.root / "dashboard-policy.json", b'{"access":"public"}')
        with self.assertRaises(Invalid):
            self.app.reload()
        self.assertIs(self.app._state, prior)
        self.assertEqual(self.call("/api/overview")[0], 200)
        set_access(self.root, "local")
        code, body = self.call(
            "/api/control",
            method="POST",
            token=self.admin,
            body={"command": "dashboard.access", "access": "token"},
        )
        self.assertEqual((code, body), (200, {"dashboard_access": "token"}))
        self.assertEqual(self.call("/api/overview")[0], 401)
        self.assertEqual(self.call("/api/overview", token=self.producer)[0], 401)
        self.assertEqual(self.call("/api/overview", token=self.admin)[0], 200)
        self.assertEqual(read_access(self.root), "token")

    def test_offline_admin_policy_persists_and_rejects_invalid_modes(self):
        self.assertEqual(
            admin.execute(self.root, ["dashboard", "access"]), {"dashboard_access": "token"}
        )
        self.assertEqual(
            admin.execute(self.root, ["dashboard", "access", "local"])["dashboard_access"], "local"
        )
        self.assertEqual((self.root / "dashboard-policy.json").stat().st_mode & 0o077, 0)
        for mode in ("public", "", None, False):
            with self.assertRaises(Invalid):
                set_access(self.root, mode)
        self.assertEqual(read_access(self.root), "local")
        self.assertEqual(self.call("/api/access", method="POST")[0], 405)

    def test_short_overview_windows_and_five_minute_default(self):
        self.local()
        self.assertEqual(self.call("/api/overview")[1]["window_seconds"], 300)
        for window in (
            1,
            30,
            60,
            120,
            300,
            900,
            1800,
            3600,
            21600,
            43200,
            86400,
            604800,
            2592000,
            7776000,
        ):
            with self.subTest(window=window):
                code, data = self.call("/api/overview", QUERY_STRING=f"window={window}")
                self.assertEqual(code, 200)
                self.assertEqual(data["window_seconds"], window)
                self.assertGreaterEqual(data["bucket_seconds"], 5)
                self.assertEqual(data["sample_seconds"], self.config.sample_seconds)
        for window in ("0", "-1", "NaN"):
            with self.subTest(window=window):
                self.assertEqual(
                    self.call("/api/overview", QUERY_STRING=f"window={window}")[0], 400
                )

    def test_custom_overview_ranges_and_invalid_queries(self):
        import time

        self.local()
        end = int(time.time() * 1000) - 1000
        start = end - 60000
        code, data = self.call(
            "/api/overview", QUERY_STRING=f"start_ms={start}&end_ms={end}&project=travel"
        )
        self.assertEqual(code, 200)
        self.assertEqual((data["range_start_ms"], data["range_end_ms"]), (start, end))
        self.assertTrue(data["historical"])
        self.assertIsNone(data["current"]["rps"])
        for query in (
            f"start_ms={start}",
            f"end_ms={end}",
            f"start_ms={start}&end_ms={end}&window=300",
            f"start_ms={start}&start_ms={start}&end_ms={end}",
            f"start_ms={end}&end_ms={start}",
            f"start_ms=0&end_ms={end + 120000}",
        ):
            with self.subTest(query=query):
                self.assertEqual(self.call("/api/overview", QUERY_STRING=query)[0], 400)
