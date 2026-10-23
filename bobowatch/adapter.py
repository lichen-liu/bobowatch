"""Thread-safe counters and bounded, asynchronous delivery; no import-time work."""

import bisect
import json
import logging
import math
import random
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .protocol import BUCKETS_US, identifier

log = logging.getLogger(__name__)


def destination(endpoint, token_file):
    url = urlsplit(endpoint)
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
        raise ValueError("Expected an HTTP(S) ingestion URL without credentials")
    if url.scheme == "http" and url.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("Plain HTTP is only allowed on loopback")
    if url.port is not None and not 1 <= url.port <= 65535:
        raise ValueError("Invalid ingestion port")
    return endpoint, Path(token_file)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass
class Response:
    status: int = 200
    route: str | None = None
    bytes_sent: int | None = None
    bytes_received: int | None = None


class Monitor:
    """One producer per replica slot in v0.1. Construct and start after fork; close at shutdown.

    record_request() is the first milestone's explicit instrumentation API.
    ASGI/WSGI wrappers and application-specific operations are not implemented yet.
    """

    def __init__(
        self, *, replica, endpoint, token_file, revision="unknown", interval=15, routes=()
    ):
        self.replica = identifier(replica)
        if not isinstance(revision, str) or len(revision) > 128:
            raise ValueError("revision must be text of at most 128 characters")
        if not math.isfinite(interval) or interval < 0.1:
            raise ValueError("interval must be at least 0.1 seconds")
        self.endpoint, self.token_file = destination(endpoint, token_file)
        self.revision, self.interval = revision, interval
        self._lock, self._delivery_lock = threading.Lock(), threading.Lock()
        self._stop, self._thread = threading.Event(), None
        self._requests = self._errors = self._duration = self._sequence = 0
        self._buckets = [0] * (len(BUCKETS_US) + 1)
        if len(routes) > 20 or any(
            not isinstance(route, str) or not route or len(route) > 128 or "?" in route
            for route in routes
        ):
            raise ValueError("Register at most 20 bounded route templates without query strings")
        self._status_classes = [0] * 5
        self._in_flight = None
        self._bytes_sent = self._bytes_received = self._sized_requests = 0
        self._routes = {
            name: {"route": name, "requests": 0, "errors": 0, "duration_sum_us": 0}
            for name in sorted(set(routes) | {"unmatched"})
        }

        self._events = deque(maxlen=100)
        self.dropped_events = 0
        self.last_error = None
        self.run_id = str(uuid.uuid4())
        self.started_at_ms = int(time.time() * 1000)
        self._started = time.monotonic()
        # Do not inherit proxy environment settings when delivering private telemetry.
        self._http = build_opener(ProxyHandler({}), _NoRedirect())

    def record_request(
        self,
        duration_seconds,
        status=200,
        *,
        route="unmatched",
        bytes_sent=None,
        bytes_received=None,
    ):
        if not math.isfinite(duration_seconds) or not 0 <= duration_seconds <= 86400:
            raise ValueError("duration_seconds must be finite and between 0 and 86400")
        if type(status) is not int or not 100 <= status <= 599:
            raise ValueError("status must be an HTTP status code")
        if not isinstance(route, str):
            raise ValueError("route must be a registered string template")
        for value in (bytes_sent, bytes_received):
            if value is not None and (type(value) is not int or not 0 <= value <= 2**63 - 1):
                raise ValueError("Body byte sizes must be nonnegative integers")
        duration = round(duration_seconds * 1_000_000)
        with self._lock:
            self._requests += 1
            self._errors += status >= 500
            self._duration += duration
            self._buckets[bisect.bisect_left(BUCKETS_US, duration)] += 1
            self._status_classes[status // 100 - 1] += 1
            target = self._routes.get(route, self._routes["unmatched"])
            target["requests"] += 1
            target["errors"] += status >= 500
            target["duration_sum_us"] += duration
            if bytes_sent is not None and bytes_received is not None:
                self._bytes_sent += bytes_sent
                self._bytes_received += bytes_received
                self._sized_requests += 1

    @contextmanager
    def request(self, route="unmatched"):
        """Explicit lifecycle hook: track in-flight work and finalize once, even on errors."""
        with self._lock:
            self._in_flight = (self._in_flight or 0) + 1
        started = time.monotonic()
        response = Response()
        try:
            yield response
        except BaseException:
            response.status = 500
            raise
        finally:
            try:
                self.record_request(
                    time.monotonic() - started,
                    response.status,
                    route=response.route or route,
                    bytes_sent=response.bytes_sent,
                    bytes_received=response.bytes_received,
                )
            finally:
                with self._lock:
                    self._in_flight -= 1

    def event(self, message, severity="info"):
        """Supply a redacted operational summary, never raw business/request data."""
        if severity not in ("info", "warning", "error") or not isinstance(message, str):
            raise ValueError("Invalid event severity or message")
        with self._lock:
            if len(self._events) == self._events.maxlen:
                self.dropped_events += 1
            self._events.append(
                {
                    "id": str(uuid.uuid4()),
                    "at_ms": int(time.time() * 1000),
                    "severity": severity,
                    "message": message[:256],
                }
            )

    def snapshot(self):
        with self._lock:
            self._sequence += 1
            return {
                "version": 2,
                "replica": self.replica,
                "run_id": self.run_id,
                "revision": self.revision,
                "sequence": self._sequence,
                "started_at_ms": self.started_at_ms,
                "observed_at_ms": int(time.time() * 1000),
                "elapsed_ms": max(1, int((time.monotonic() - self._started) * 1000)),
                "requests": self._requests,
                "errors": self._errors,
                "duration_sum_us": self._duration,
                "buckets": self._buckets.copy(),
                "events": list(self._events),
                "http": {
                    "in_flight": self._in_flight,
                    "bytes_sent": self._bytes_sent,
                    "bytes_received": self._bytes_received,
                    "sized_requests": self._sized_requests,
                    "status_classes": self._status_classes.copy(),
                    "routes": [value.copy() for value in self._routes.values()],
                },
            }

    def set_destination(self, endpoint, token_file):
        target = destination(endpoint, token_file)
        with self._delivery_lock:
            self.endpoint, self.token_file = target

    def flush(self):
        """Explicit sender operation. Never invoked by record_request/event."""
        with self._delivery_lock:
            payload = self.snapshot()
            try:
                token = self.token_file.read_text().strip()
                request = Request(
                    self.endpoint,
                    json.dumps(payload).encode(),
                    method="POST",
                    headers={
                        "Authorization": "Bearer " + token,
                        "Content-Type": "application/json",
                    },
                )
                with self._http.open(request, timeout=2) as response:
                    result = json.loads(response.read(4096))
                if result.get("sequence") != payload["sequence"]:
                    raise ValueError("Unexpected ingestion acknowledgement")
                acknowledged = {event["id"] for event in payload["events"]}
                with self._lock:
                    self._events = deque(
                        (e for e in self._events if e["id"] not in acknowledged), maxlen=100
                    )
                self.last_error = None
                return True
            except (OSError, ValueError) as exc:
                # Export no token, request body or URL into diagnostics.
                if self.last_error is None:
                    log.warning(
                        "Telemetry delivery unavailable (%s); application counters continue",
                        type(exc).__name__,
                    )
                self.last_error = type(exc).__name__
                return False

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        if self._stop.is_set():
            raise RuntimeError("A closed Monitor cannot be restarted; create a new run")
        self._thread = threading.Thread(
            target=self._send_loop, name="bobowatch-sender", daemon=True
        )
        self._thread.start()

    def _send_loop(self):
        failures = 0
        while not self._stop.is_set():
            failures = 0 if self.flush() else min(failures + 1, 6)
            delay = self.interval if not failures else min(60, self.interval * 2**failures)
            self._stop.wait(delay * random.uniform(0.9, 1.1))

    def close(self, timeout=3):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
        # No synchronous final network call: shutdown has a bounded wait.
