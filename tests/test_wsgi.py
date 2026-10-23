"""WSGI streaming, privacy and threaded lifecycle contracts."""

import io
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

from bobowatch import Monitor
from bobowatch.wsgi import InstrumentedApp


class WSGITests(unittest.TestCase):
    def setUp(self):
        self.monitor = Monitor(
            replica="split-r01",
            endpoint="http://127.0.0.1:8720/v1/ingest",
            token_file="unused-test-token",
            routes=("/calculate",),
        )
        self.monitor.start = Mock()
        self.monitor.close = Mock()
        self.factory = Mock(return_value=self.monitor)

    def environ(self, body=b"", **values):
        return {
            "REQUEST_METHOD": "POST",
            "CONTENT_LENGTH": str(len(body)),
            "wsgi.input": io.BytesIO(body),
            "PATH_INFO": "/person/private-name",
            "QUERY_STRING": "secret=private",
            **values,
        }

    def run_app(self, app, environ=None):
        output = []
        wrapped = InstrumentedApp(app, self.factory)
        response = wrapped(self.environ() if environ is None else environ, lambda *a: output.append)
        try:
            output.extend(response)
        finally:
            response.close()
        return wrapped, b"".join(output)

    def test_streamed_body_and_write_are_measured_without_buffering(self):
        def app(env, start):
            self.assertEqual(env["wsgi.input"].read(2), b"pr")
            self.assertEqual(env["wsgi.input"].read(), b"ivate-body")
            env["bobowatch.route"] = "/calculate"
            write = start("201 Created", [])
            write(b"a")
            yield b"bc"
            self.assertEqual(self.monitor.snapshot()["requests"], 0)
            yield b"d"

        wrapped, output = self.run_app(app, self.environ(b"private-body"))
        self.assertEqual(output, b"abcd")
        snapshot = self.monitor.snapshot()
        self.assertEqual(snapshot["http"]["bytes_received"], 12)
        self.assertEqual(snapshot["http"]["bytes_sent"], 4)
        self.assertEqual(snapshot["http"]["sized_requests"], 1)
        self.assertEqual(snapshot["http"]["in_flight"], 0)
        self.assertEqual(snapshot["http"]["status_classes"], [0, 1, 0, 0, 0])
        self.assertNotIn("private", json.dumps(snapshot))
        wrapped.close()
        wrapped.close()
        self.monitor.close.assert_called_once()

    def test_unconsumed_body_stays_unknown(self):
        def app(env, start):
            start("413 Payload Too Large", [])
            return [b"rejected"]

        self.run_app(app, self.environ(b"unread"))
        self.assertEqual(self.monitor.snapshot()["http"]["sized_requests"], 0)
        self.assertEqual(self.monitor.snapshot()["http"]["status_classes"][3], 1)

    def test_close_before_iteration_releases_request_and_underlying_iterable_once(self):
        iterable = Mock()
        iterable.__iter__ = Mock(return_value=iter([b"not sent"]))
        wrapped = InstrumentedApp(lambda env, start: iterable, self.factory)
        response = wrapped(self.environ(), lambda *a: None)
        self.assertEqual(self.monitor.snapshot()["http"]["in_flight"], 1)
        response.close()
        response.close()
        iterable.close.assert_called_once()
        self.assertEqual(self.monitor.snapshot()["requests"], 1)
        self.assertEqual(self.monitor.snapshot()["http"]["in_flight"], 0)
        self.assertEqual(self.monitor.snapshot()["http"]["sized_requests"], 0)

    def test_application_and_iteration_errors_propagate_and_count_once(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):

                def app(env, start, streaming=streaming):
                    start("200 OK", [])
                    if not streaming:
                        raise RuntimeError("private failure")

                    def generate():
                        yield b"partial"
                        raise RuntimeError("private failure")

                    return generate()

                with self.assertRaisesRegex(RuntimeError, "private failure"):
                    self.run_app(app)
        snapshot = self.monitor.snapshot()
        self.assertEqual(snapshot["requests"], 2)
        self.assertEqual(snapshot["errors"], 2)
        self.assertEqual(snapshot["http"]["in_flight"], 0)
        self.assertEqual(snapshot["http"]["sized_requests"], 0)

    def test_input_readinto_lines_and_terminated_stream(self):
        def app(env, start):
            stream = env["wsgi.input"]
            buffer = bytearray(2)
            self.assertEqual(stream.readinto(buffer), 2)
            self.assertEqual(stream.readlines(), [b"b\n", b"c\n"])
            self.assertEqual(list(stream), [])
            start("200 OK", [])
            return [b"ok"]

        self.run_app(
            app, self.environ(b"a\nb\nc\n", CONTENT_LENGTH="", **{"wsgi.input_terminated": True})
        )
        self.assertEqual(self.monitor.snapshot()["http"]["bytes_received"], 6)
        self.assertEqual(self.monitor.snapshot()["http"]["sized_requests"], 1)

    def test_raw_route_is_collapsed_and_empty_get_is_sized(self):
        def app(env, start):
            env["bobowatch.route"] = env["PATH_INFO"]
            start("404 Not Found", [])
            return [b"missing"]

        self.run_app(app, self.environ(REQUEST_METHOD="GET", CONTENT_LENGTH=""))
        snapshot = self.monitor.snapshot()
        self.assertNotIn("private-name", json.dumps(snapshot))
        self.assertEqual(snapshot["http"]["sized_requests"], 1)
        self.assertEqual(
            next(r for r in snapshot["http"]["routes"] if r["route"] == "unmatched")["requests"], 1
        )

    def test_concurrent_first_requests_start_one_monitor(self):
        barrier = threading.Barrier(8)

        def app(env, start):
            start("200 OK", [])
            return [b"ok"]

        wrapped = InstrumentedApp(app, self.factory)
        self.factory.assert_not_called()

        def request(_):
            barrier.wait(timeout=5)
            response = wrapped(self.environ(), lambda *a: None)
            try:
                self.assertEqual(b"".join(response), b"ok")
            finally:
                response.close()

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(request, range(8)))
        self.factory.assert_called_once()
        self.monitor.start.assert_called_once()
        self.assertEqual(self.monitor.snapshot()["requests"], 8)

    def test_unavailable_monitor_does_not_break_app_or_leak_details(self):
        def app(env, start):
            start("200 OK", [])
            return [b"works"]

        self.factory.side_effect = RuntimeError("secret-token-and-url")
        wrapped = InstrumentedApp(app, self.factory)
        with self.assertLogs("bobowatch.wsgi", level="WARNING") as logs:
            self.assertEqual(wrapped(self.environ(), lambda *a: None), [b"works"])
        self.assertNotIn("secret-token", " ".join(logs.output))
        self.assertEqual(wrapped(self.environ(), lambda *a: None), [b"works"])
        self.factory.assert_called_once()


if __name__ == "__main__":
    unittest.main()
