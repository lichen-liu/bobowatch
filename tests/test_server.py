"""Real HTTP and independent-process restart/online-backup acceptance test."""

import json
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from urllib.request import Request, urlopen

from bobowatch import Monitor
from bobowatch.config import initialize


class ServerTests(unittest.TestCase):
    def test_process_restart_live_backup_and_single_instance_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            config = initialize(Path(directory) / "state", demo=True, port=port)
            process = None

            def start():
                nonlocal process
                process = subprocess.Popen(
                    [sys.executable, "-m", "bobowatch", "serve", "--config", str(config.path)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for _ in range(80):
                    if process.poll() is not None:
                        raise AssertionError(process.communicate())
                    try:
                        with urlopen(f"http://127.0.0.1:{port}/health", timeout=0.2) as response:
                            if response.status == 200:
                                return
                    except OSError:
                        pass
                    time.sleep(0.05)
                raise AssertionError("Server did not start")

            def stop():
                nonlocal process
                if process and process.poll() is None:
                    process.terminate()
                    try:
                        process.communicate(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()
                        raise
                    self.assertEqual(process.returncode, 0)

            def overview():
                token = config.admin_token_file.read_text().strip()
                req = Request(
                    f"http://127.0.0.1:{port}/api/overview",
                    headers={"Authorization": "Bearer " + token},
                )
                with urlopen(req, timeout=3) as response:
                    return json.load(response)

            try:
                start()
                monitor = Monitor(
                    replica="flights-r01",
                    endpoint=f"http://127.0.0.1:{port}/v1/ingest",
                    token_file=config.replicas[0]["token_file"],
                )
                monitor.record_request(0.1)
                monitor.event("Restart acceptance event")
                self.assertTrue(monitor.flush())
                data = overview()
                self.assertEqual(sum(s["requests"] for s in data["sites"]), 1)
                second = subprocess.run(
                    [sys.executable, "-m", "bobowatch", "serve", "--config", str(config.path)],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertNotEqual(second.returncode, 0)
                self.assertIn("owns this runtime", second.stderr)
                target = Path(directory) / "live-backup.sqlite3"
                backup = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "bobowatch",
                        "backup",
                        "--config",
                        str(config.path),
                        "--output",
                        str(target),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(backup.returncode, 0, backup.stderr)
                with closing(sqlite3.connect(target)) as conn, conn:
                    self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                    self.assertEqual(
                        conn.execute("SELECT SUM(delta_requests) FROM snapshots").fetchone()[0], 1
                    )
                stop()
                start()
                restored = overview()
                self.assertEqual(sum(s["requests"] for s in restored["sites"]), 1)
                self.assertEqual(restored["events"][0]["message"], "Restart acceptance event")
                monitor.record_request(0.2, 503)
                time.sleep(0.01)
                self.assertTrue(monitor.flush())
                restored = overview()
                self.assertEqual(sum(s["requests"] for s in restored["sites"]), 2)
                self.assertEqual(sum(s["errors"] for s in restored["sites"]), 1)
            finally:
                stop()


if __name__ == "__main__":
    unittest.main()


class ReloadServerTests(unittest.TestCase):
    def test_running_server_accepts_offline_registration_without_restart(self):
        from bobowatch.provision import ensure_registration

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            config = initialize(root / "watch", port=port)
            client = root / "flight"
            client.mkdir(mode=0o700)
            process = subprocess.Popen(
                [sys.executable, "-m", "bobowatch", "serve", "--config", str(config.path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                for _ in range(80):
                    try:
                        with urlopen(f"http://127.0.0.1:{port}/health", timeout=0.2) as response:
                            self.assertEqual(response.status, 200)
                        break
                    except OSError:
                        time.sleep(0.05)
                else:
                    self.fail("Server did not start")
                result = ensure_registration(
                    runtime=config.path.parent,
                    project="travel",
                    site="flight-prod",
                    host="mac-01",
                    replica="flight-r01",
                    token_file=client / "producer.token",
                )
                monitor = Monitor(
                    replica=result["replica"],
                    endpoint=result["endpoint"],
                    token_file=result["token_file"],
                )
                monitor.record_request(0.1)
                for _ in range(70):
                    if monitor.flush():
                        break
                    time.sleep(0.05)
                else:
                    self.fail("Live registration was not reloaded")
                self.assertIsNone(process.poll())
                request = Request(
                    f"http://127.0.0.1:{port}/api/overview",
                    headers={
                        "Authorization": "Bearer " + config.admin_token_file.read_text().strip()
                    },
                )
                with urlopen(request, timeout=2) as response:
                    data = json.load(response)
                self.assertEqual(sum(site["requests"] for site in data["sites"]), 1)
            finally:
                process.terminate()
                process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0)
