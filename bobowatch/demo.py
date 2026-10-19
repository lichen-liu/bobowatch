"""Explicitly synthetic workload: SQL-backed history, then live fictional traffic."""

import math
import random
import threading
import time
from contextlib import closing

from .adapter import Monitor
from .protocol import validate

ROUTES = ("/", "/api/search", "/api/items/{id}", "/api/health")


class Simulation:
    def __init__(self, config, store=None):
        if not config.demo:
            raise ValueError("Simulation requires a demo runtime")
        self.config, self.store, self.stop = config, store, threading.Event()
        self.monitors = [
            Monitor(
                replica=r["id"],
                token_file=r["token_file"],
                endpoint=f"http://127.0.0.1:{config.port}/v1/ingest",
                revision="demo-http-v2",
                interval=config.sample_seconds,
                routes=ROUTES,
            )
            for r in config.replicas
            if not r.get("retired")
        ]
        self.thread = threading.Thread(target=self.run, name="bobowatch-demo", daemon=True)
        self.random = random.Random(3427)

    def start(self):
        # Only a fresh demo gets invented history. Never backfill real observations.
        if self.store:
            with closing(self.store._connect()) as conn:
                empty = not conn.execute("SELECT 1 FROM snapshots LIMIT 1").fetchone()
            if empty:
                self.seed_history()
        self.thread.start()

    def workload(self, monitor, index, at, seconds):
        phase = at % 1800 / 1800
        travel = monitor.replica.startswith("flights")
        base = (29 if travel else 13) * (1 - 0.16 * (index % 2))
        wave = (
            1
            + 0.28 * math.sin(phase * math.tau * 3 + index * 0.3)
            + 0.13 * math.cos(phase * math.tau * 7)
        )
        burst = 1.6 if 0.42 < phase < 0.49 else 1
        incident = travel and 0.65 < phase < 0.72
        volume = max(1, round(base * wave * burst * seconds))
        for _ in range(volume):
            route = self.random.choices(ROUTES, weights=(16, 48, 30, 6))[0]
            duration = self.random.lognormvariate(math.log(0.11 if travel else 0.045), 0.75)
            if route == "/api/search":
                duration *= 2.5
            if incident:
                duration *= 3.4
            if self.random.random() < 0.012:
                duration *= 9
            coin = self.random.random()
            status = (
                503
                if coin < (0.065 if incident else 0.004)
                else 404
                if coin < 0.032
                else 400
                if coin < 0.055
                else 304
                if coin < 0.11
                else 200
            )
            monitor.record_request(
                duration,
                status,
                route=route,
                bytes_sent=round(
                    self.random.uniform(700, 8500) * (1.8 if route == "/api/search" else 1)
                ),
                bytes_received=round(self.random.uniform(100, 900)),
            )
        with monitor._lock:
            monitor._in_flight = max(
                0, round(volume / seconds * (0.12 if travel else 0.055) * (3 if incident else 1))
            )

    def seed_history(self):
        duration = 1800
        interval = self.config.sample_seconds
        end = time.time() - interval
        steps = min(900, int(duration / interval))
        start = end - steps * interval
        print(
            f"Demo fixture: generating {steps} historical samples per replica (fictional, not measured).",
            flush=True,
        )
        for index, monitor in enumerate(self.monitors):
            monitor.started_at_ms = int(start * 1000)
            monitor._started = time.monotonic() - (time.time() - start)
            monitor.event(
                "Generated demo history: 30 minutes of fictional HTTP traffic, latency and response mix"
            )
            pending = []
            for step in range(1, steps + 1):
                at = start + step * interval
                self.workload(monitor, index, at, interval)
                payload = monitor.snapshot()
                payload["observed_at_ms"] = round(at * 1000)
                payload["elapsed_ms"] = round(step * interval * 1000)
                # Historical timestamps are deliberate fixture data, not delayed telemetry.
                if payload["events"]:
                    payload["events"][0]["at_ms"] = round(start * 1000)
                pending.append(validate(payload))
                if len(pending) == 25 or step == steps:
                    batch = pending.copy()
                    self.store._submit(
                        lambda conn, batch=batch: [
                            self.store._ingest(
                                conn, p, p["replica"], demo_received_at_ms=p["observed_at_ms"]
                            )
                            for p in batch
                        ]
                    )
                    pending.clear()
            with monitor._lock:
                monitor._events.clear()
        print("Demo history ready in SQLite.", flush=True)

    def run(self):
        for monitor in self.monitors:
            monitor.event("Live synthetic HTTP producer started; all values are fictional")
            monitor.start()
        tick = 0
        while not self.stop.is_set():
            for index, monitor in enumerate(self.monitors):
                self.workload(monitor, index, time.time(), 1)
                if tick and tick % 120 == 0:
                    monitor.event("Synthetic request burst completed")
            tick += 1
            self.stop.wait(1)
        for monitor in self.monitors:
            monitor.close()

    def close(self):
        self.stop.set()
        if self.thread.ident is not None:
            self.thread.join(7)
