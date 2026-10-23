"""Real provisioning/auth boundaries and ASGI lifecycle integration contracts."""

import asyncio
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bobowatch import Monitor
from bobowatch.application import Application
from bobowatch.asgi import InstrumentedApp
from bobowatch.client import ConfiguredMonitor
from bobowatch.config import initialize, load
from bobowatch.paths import runtime_root
from bobowatch.protocol import Invalid
from bobowatch.provision import ensure_registration
from bobowatch.storage import SQLiteStore


class ProvisioningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.watch = self.root / "watch"
        self.config = initialize(self.watch)
        self.client = self.root / "flight"
        self.client.mkdir(mode=0o700)
        self.args = dict(
            runtime=self.watch,
            project="travel",
            site="flight-prod",
            host="mac-01",
            replica="flight-r01",
            token_file=self.client / "watch.token",
        )

    def request(self, app, token, payload):
        body = json.dumps(payload).encode()
        result = []
        response = app(
            {
                "REQUEST_METHOD": "POST",
                "PATH_INFO": "/v1/ingest",
                "REMOTE_ADDR": "127.0.0.1",
                "HTTP_HOST": "127.0.0.1:8720",
                "HTTP_AUTHORIZATION": "Bearer " + token,
                "CONTENT_TYPE": "application/json",
                "CONTENT_LENGTH": str(len(body)),
                "wsgi.input": io.BytesIO(body),
            },
            lambda status, headers: result.append(status),
        )
        return int(result[0].split()[0]), json.loads(b"".join(response))

    def test_offline_idempotent_registration_and_live_reload(self):
        store = SQLiteStore(self.config)
        self.addCleanup(store.close)
        app = Application(self.config, store)
        result = ensure_registration(**self.args)
        token = self.args["token_file"].read_text().strip()
        before = (self.watch / "registrations.json").read_bytes()
        self.assertEqual(ensure_registration(**self.args), result)
        self.assertEqual((self.watch / "registrations.json").read_bytes(), before)
        self.assertEqual(self.args["token_file"].stat().st_mode & 0o077, 0)
        self.assertNotIn(token, json.dumps(result))
        monitor = Monitor(
            replica="flight-r01", endpoint=result["endpoint"], token_file=self.args["token_file"]
        )
        monitor.record_request(0.1)
        payload = monitor.snapshot()
        self.assertEqual(self.request(app, token, payload)[0], 401)
        self.assertTrue(app.reload())
        self.assertEqual(self.request(app, token, payload)[0], 200)
        self.assertFalse(app.reload())
        self.assertEqual(self.request(app, token + "wrong", monitor.snapshot())[0], 401)
        self.assertFalse((self.client / "monitor.sqlite3").exists())

    def test_registration_does_not_open_sql_or_need_server(self):
        with patch("sqlite3.connect", side_effect=AssertionError("No database registration")):
            result = ensure_registration(**self.args)
        self.assertEqual(result["replica"], "flight-r01")
        self.assertFalse(self.config.database.exists())
        self.assertEqual(len(load(self.config.path).replicas), 1)

    def test_identity_conflict_keeps_registry_and_token(self):
        ensure_registration(**self.args)
        before = (self.watch / "registrations.json").read_bytes()
        token = self.args["token_file"].read_bytes()
        with self.assertRaises(Invalid):
            ensure_registration(**(self.args | {"host": "other-host"}))
        self.assertEqual((self.watch / "registrations.json").read_bytes(), before)
        self.assertEqual(self.args["token_file"].read_bytes(), token)

    def test_invalid_reload_keeps_prior_auth_and_sql_inventory(self):
        ensure_registration(**self.args)
        store = SQLiteStore(load(self.config.path))
        self.addCleanup(store.close)
        app = Application(load(self.config.path), store)
        previous = app._state
        registry = json.loads((self.watch / "registrations.json").read_text())
        registry["replicas"][0]["host"] = "unknown"
        (self.watch / "registrations.json").write_text(json.dumps(registry))
        with self.assertRaises(Invalid):
            app.reload()
        self.assertIs(app._state, previous)
        self.assertEqual(store.config.replicas[0]["host"], "mac-01")

    def test_token_rotation_and_listener_change(self):
        ensure_registration(**self.args)
        config = load(self.config.path)
        store = SQLiteStore(config)
        self.addCleanup(store.close)
        app = Application(config, store)
        old = config.replicas[0]["token_file"].read_text().strip()
        new = "new-random-test-secret-" + "x" * 32
        config.replicas[0]["token_file"].write_text(new)
        app.reload()
        monitor = Monitor(
            replica="flight-r01",
            endpoint="http://127.0.0.1:8720/v1/ingest",
            token_file=self.args["token_file"],
        )
        monitor.record_request(0.1)
        payload = monitor.snapshot()
        self.assertEqual(self.request(app, old, payload)[0], 401)
        self.assertEqual(self.request(app, new, payload)[0], 200)
        original = self.config.path.read_text()
        self.config.path.write_text(original.replace(":8720", ":8721"))
        with self.assertRaisesRegex(Invalid, "restart"):
            app.reload()
        self.assertEqual(app.config.port, 8720)

    def test_unsafe_destination_and_demo_refused(self):
        self.args["token_file"].write_text("existing")
        self.args["token_file"].chmod(0o644)
        with self.assertRaises(Invalid):
            ensure_registration(**self.args)
        self.assertFalse((self.watch / "registrations.json").exists())
        demo = initialize(self.root / "demo", demo=True)
        with self.assertRaisesRegex(Invalid, "non-demo"):
            ensure_registration(**(self.args | {"runtime": demo.path.parent}))

    def test_named_runtime_and_override(self):
        with patch.dict(os.environ, {"BOBOWATCH_RUNTIME_DIR": str(self.watch)}):
            self.assertEqual(runtime_root(), self.watch)
            self.assertEqual(runtime_root(self.client), self.client)
        with self.assertRaises(ValueError):
            runtime_root("relative")
        with (
            patch("bobowatch.paths.sys.platform", "darwin"),
            patch.dict(os.environ, {}, clear=True),
        ):
            self.assertEqual(runtime_root().name, "fyi.boboji.watch")


class ASGITests(unittest.TestCase):
    def monitor(self):
        monitor = Monitor(
            replica="flight-r01",
            endpoint="http://127.0.0.1:8720/v1/ingest",
            token_file="unused",
            routes=("/v1/items/{id}",),
        )
        monitor.start, monitor.close = Mock(), Mock()
        return monitor

    def invoke(
        self,
        app,
        monitor,
        *,
        path="/v1/items/123?private",
        messages=None,
        method="POST",
        headers=(),
    ):
        scope = {"type": "http", "method": method, "path": path, "headers": list(headers)}
        incoming = iter(messages or [{"type": "http.request", "body": b"abc", "more_body": False}])
        sent = []

        async def receive():
            return next(incoming, {"type": "http.disconnect"})

        async def send(message):
            sent.append(message)

        wrapped = InstrumentedApp(app, lambda: monitor)
        asyncio.run(wrapped(scope, receive, send))
        return monitor.snapshot(), sent

    def test_streaming_counts_once_at_last_body_before_background_work(self):
        monitor = self.monitor()

        async def app(scope, receive, send):
            scope["route"] = SimpleNamespace(path="/v1/items/{id}")
            await receive()
            self.assertEqual(monitor.snapshot()["http"]["in_flight"], 1)
            await send({"type": "http.response.start", "status": 201})
            await send({"type": "http.response.body", "body": b"hello", "more_body": True})
            self.assertEqual(monitor.snapshot()["requests"], 0)
            await send({"type": "http.response.body", "body": b"world"})
            self.assertEqual(monitor.snapshot()["http"]["in_flight"], 0)
            self.assertEqual(monitor.snapshot()["requests"], 1)

        data, sent = self.invoke(app, monitor)
        self.assertEqual(data["requests"], 1)
        self.assertEqual(data["http"]["bytes_sent"], 10)
        self.assertEqual(data["http"]["bytes_received"], 3)
        self.assertEqual(data["http"]["sized_requests"], 1)
        self.assertNotIn("private", json.dumps(data))
        self.assertEqual(len(sent), 3)

    def test_failure_propagates_and_concurrency_is_cleared(self):
        monitor = self.monitor()

        async def broken(scope, receive, send):
            raise RuntimeError("original app failure")

        with self.assertRaisesRegex(RuntimeError, "original app"):
            self.invoke(broken, monitor)
        data = monitor.snapshot()
        self.assertEqual((data["requests"], data["errors"], data["http"]["in_flight"]), (1, 1, 0))
        self.assertEqual(data["http"]["sized_requests"], 0)

    def test_monitor_factory_failure_does_not_break_app(self):
        sent = []

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 204})

        async def receive():
            return {"type": "http.request"}

        async def send(message):
            sent.append(message)

        wrapped = InstrumentedApp(app, Mock(side_effect=OSError("unavailable")))
        asyncio.run(wrapped({"type": "http", "method": "GET"}, receive, send))
        self.assertEqual(sent[0]["status"], 204)

    def test_lifespan_starts_after_fork_and_closes_sender(self):
        monitor = self.monitor()
        factory = Mock(return_value=monitor)

        async def app(scope, receive, send):
            self.assertEqual((await receive())["type"], "lifespan.startup")
            await send({"type": "lifespan.startup.complete"})
            self.assertEqual((await receive())["type"], "lifespan.shutdown")
            await send({"type": "lifespan.shutdown.complete"})

        wrapped = InstrumentedApp(app, factory)
        factory.assert_not_called()
        incoming = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])

        async def receive():
            return next(incoming)

        async def send(message):
            pass

        asyncio.run(wrapped({"type": "lifespan"}, receive, send))
        monitor.start.assert_called_once()
        monitor.close.assert_called_once()


class ClientReloadTests(unittest.TestCase):
    def test_endpoint_token_change_preserves_run_and_counters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "settings.json"
            (root / "first.token").write_text("first-secret")
            (root / "second.token").write_text("second-secret")
            config = {
                "enabled": True,
                "replica": "flight-r01",
                "endpoint": "http://127.0.0.1:8720/v1/ingest",
                "token_file": "first.token",
            }
            path.write_text(json.dumps({"monitoring": config}))
            monitor = ConfiguredMonitor(path)
            identity = monitor.run_id
            monitor.record_request(0.1)
            deliveries = []

            def opened(request, **kwargs):
                deliveries.append(request)
                sequence = json.loads(request.data)["sequence"]
                response = Mock()
                response.read.return_value = json.dumps({"sequence": sequence}).encode()
                response.__enter__ = Mock(return_value=response)
                response.__exit__ = Mock(return_value=False)
                return response

            monitor._http.open = opened
            self.assertTrue(monitor.flush())
            config.update(endpoint="http://127.0.0.1:8721/v1/ingest", token_file="second.token")
            path.write_text(json.dumps({"monitoring": config}))
            self.assertTrue(monitor.flush())
            self.assertEqual(deliveries[-1].full_url, config["endpoint"])
            self.assertEqual(deliveries[-1].get_header("Authorization"), "Bearer second-secret")
            path.write_text("invalid JSON")
            self.assertTrue(monitor.flush())
            self.assertEqual(deliveries[-1].full_url, config["endpoint"])
            self.assertEqual(monitor.run_id, identity)
            self.assertEqual(monitor.snapshot()["requests"], 1)


class RequestBodyCoverageTests(unittest.TestCase):
    def test_unconsumed_get_body_is_unknown(self):
        monitor = ASGITests().monitor()

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200})
            await send({"type": "http.response.body", "body": b"ok"})

        data, _ = ASGITests().invoke(
            app, monitor, method="GET", headers=[(b"content-length", b"3")]
        )
        self.assertEqual(data["http"]["sized_requests"], 0)
