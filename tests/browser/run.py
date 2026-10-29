"""Run development-only browser checks against a temporary fictional runtime."""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from bobowatch.access import set_access  # noqa: E402
from bobowatch.adapter import Monitor  # noqa: E402
from bobowatch.config import initialize  # noqa: E402
from bobowatch.protocol import validate  # noqa: E402
from bobowatch.storage import SQLiteStore  # noqa: E402


def seed(config):
    """Forty minutes, four replicas and several request/latency/error patterns."""
    end = int(time.time() * 1000) - 2_000
    routes = ("/", "/api/search", "/api/slow", "/api/health")
    expected = {
        "requests": 0,
        "errors": 0,
        "duration_us": 0,
        "buckets": [0] * 14,
        "snapshots": 4800,
    }
    store = SQLiteStore(config)
    try:
        for index, replica in enumerate(config.replicas):
            monitor = Monitor(
                replica=replica["id"],
                token_file=replica["token_file"],
                endpoint=f"http://127.0.0.1:{config.port}/v1/ingest",
                revision="fictional-browser-fixture",
                routes=routes,
            )
            monitor.started_at_ms = end - 1200 * 2_000 + index * 250
            batch = []
            for step in range(1, 1201):
                for request in range(12 + index):
                    monitor.record_request(
                        (0.012, 0.08, 0.35, 1.5)[(step + request) % 4],
                        503 if (step + request) % 37 == 0 else 200,
                        route=routes[request % len(routes)],
                        bytes_sent=1_000 + request * 50,
                        bytes_received=100,
                    )
                payload = monitor.snapshot()
                at = monitor.started_at_ms + step * 2_000
                payload.update(observed_at_ms=at, elapsed_ms=step * 2_000)
                if step % 100 == 0:
                    payload["events"] = [
                        {
                            "id": f"fixture-{replica['id']}-{step}",
                            "at_ms": at,
                            "severity": "warning" if step % 200 else "info",
                            "message": "Fictional deployment checkpoint",
                        }
                    ]
                batch.append(validate(payload))
                if len(batch) == 50:
                    pending = batch
                    store._submit(
                        lambda connection, pending=pending: [
                            store._ingest(
                                connection,
                                item,
                                item["replica"],
                                demo_received_at_ms=item["observed_at_ms"],
                            )
                            for item in pending
                        ]
                    )
                    batch = []
            expected["requests"] += payload["requests"]
            expected["errors"] += payload["errors"]
            expected["duration_us"] += payload["duration_sum_us"]
            expected["buckets"] = [
                a + b for a, b in zip(expected["buckets"], payload["buckets"], strict=True)
            ]
    finally:
        store.close()
    return {"start_ms": end - 1200 * 2_000, "end_ms": end + 1000, "expected": expected}


def main():
    with tempfile.TemporaryDirectory(prefix="bobowatch-browser-fictional-") as directory:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        config = initialize(Path(directory) / "runtime", demo=True, port=port)
        set_access(config.path.parent, "local")
        fixture = seed(config)
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "bobowatch",
                "demo",
                "--state-dir",
                str(config.path.parent),
                "--port",
                str(port),
            ],
            cwd=REPO,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        url = f"http://127.0.0.1:{port}"
        http = build_opener(ProxyHandler({}))
        try:
            for _ in range(300):
                if process.poll() is not None:
                    raise AssertionError(f"Fictional server exited: {process.communicate()}")
                try:
                    with http.open(url + "/ready", timeout=0.2) as response:
                        if json.load(response) == {"status": "ready"}:
                            break
                except OSError:
                    time.sleep(0.05)
            else:
                raise AssertionError("Fictional server did not become ready")
            result = subprocess.run(
                ["node", str(Path(__file__).with_name("run.cjs"))],
                input=json.dumps(
                    {
                        "url": url,
                        "fictional": True,
                        "fixture": fixture,
                        "output": os.environ.get("BOBOWATCH_BROWSER_OUTPUT"),
                    }
                ),
                cwd=REPO,
                env=os.environ,
                text=True,
                timeout=240,
                check=False,
            )
            if result.returncode:
                raise SystemExit(result.returncode)
        finally:
            process.terminate()
            try:
                process.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)


if __name__ == "__main__":
    main()
