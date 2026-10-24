"""Dashboard URL reporting respects listener policy and tolerates discovery failure."""

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bobowatch import manage, urls
from bobowatch.config import initialize

INTERFACES = """lo0: flags=8049<UP,LOOPBACK,RUNNING> mtu 16384
    inet 127.0.0.1 netmask 0xff000000
en0: flags=8863<UP,BROADCAST,RUNNING> mtu 1500
    inet 192.168.1.10 netmask 0xffffff00
    inet6 fe80::1 prefixlen 64
    status: active
en1: flags=8863<UP,BROADCAST,RUNNING> mtu 1500
    inet 10.0.0.20 netmask 0xffffff00
    status: active
en2: flags=8863<UP,BROADCAST,RUNNING> mtu 1500
    inet 192.168.2.30 netmask 0xffffff00
    status: inactive
en3: flags=0<> mtu 1500
    inet 192.168.3.40 netmask 0xffffff00
en4: flags=8863<UP,BROADCAST,RUNNING> mtu 1500
    inet 203.0.113.10 netmask 0xffffff00
    inet invalid netmask 0xffffff00
    inet 10.0.0.20 netmask 0xffffff00
"""


class DashboardURLTests(unittest.TestCase):
    def config(self, host="0.0.0.0", port=9123):
        return SimpleNamespace(host=host, port=port, allowed_hosts=("watch.local",))

    def test_discovery_filters_down_inactive_loopback_public_and_duplicate_addresses(self):
        with (
            patch.object(urls.sys, "platform", "darwin"),
            patch.object(
                urls.subprocess,
                "run",
                return_value=SimpleNamespace(returncode=0, stdout=INTERFACES),
            ) as run,
        ):
            self.assertEqual(
                urls.lan_interfaces(),
                [
                    {"interface": "en0", "address": "192.168.1.10"},
                    {"interface": "en1", "address": "10.0.0.20"},
                ],
            )
        run.assert_called_once_with(
            ["/sbin/ifconfig", "-a"], capture_output=True, text=True, timeout=2
        )

    def test_links_use_configured_port_and_never_advertise_wildcard(self):
        with patch.object(
            urls, "lan_interfaces", return_value=[{"interface": "en1", "address": "10.1.2.3"}]
        ):
            links = urls.dashboard_urls(self.config())
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                urls.print_dashboard(self.config())
        self.assertEqual(links["local"], "http://127.0.0.1:9123/")
        self.assertEqual(links["lan"], [{"interface": "en1", "url": "http://10.1.2.3:9123/"}])
        self.assertEqual(links["hostnames"], ["http://watch.local:9123/"])
        self.assertIn("Dashboard (this Mac): http://127.0.0.1:9123/", output.getvalue())
        self.assertIn("Dashboard (local network, en1): http://10.1.2.3:9123/", output.getvalue())
        self.assertNotIn("0.0.0.0", output.getvalue())

    def test_loopback_listener_does_not_advertise_lan_or_hostnames(self):
        for host in ("127.0.0.1", "localhost"):
            with patch.object(urls, "lan_interfaces") as discover:
                links = urls.dashboard_urls(self.config(host))
            discover.assert_not_called()
            self.assertEqual(links["lan"], [])
            self.assertNotIn("hostnames", links)
            self.assertIn("loopback-only", links["lan_note"])

    def test_discovery_failure_keeps_local_link_and_hint(self):
        failures = (FileNotFoundError(), subprocess.TimeoutExpired("ifconfig", 2), None)
        for failure in failures:
            with (
                self.subTest(failure=failure),
                patch.object(urls.sys, "platform", "darwin"),
                patch.object(
                    urls.subprocess,
                    "run",
                    side_effect=failure,
                    return_value=SimpleNamespace(returncode=1),
                ),
            ):
                links = urls.dashboard_urls(self.config())
                self.assertEqual(links["local"], "http://127.0.0.1:9123/")
                self.assertEqual(links["lan"], [])
                self.assertIn("port 9123", links["lan_note"])
        with (
            patch.object(urls.sys, "platform", "linux"),
            patch.object(urls.subprocess, "run") as run,
        ):
            self.assertEqual(urls.lan_interfaces(), [])
            run.assert_not_called()

    def test_start_check_and_stopped_status_keep_valid_json_and_show_urls(self):
        with tempfile.TemporaryDirectory() as directory:
            config = initialize(Path(directory) / "runtime", port=9123)
            for command in ("start", "check", "status"):
                with (
                    self.subTest(command=command),
                    patch.object(manage.sys, "platform", "darwin"),
                    patch.object(manage.os, "getuid", return_value=501),
                    patch.object(manage, "runtime_root", return_value=config.path.parent),
                    patch.object(manage, "start", return_value={"running": True}),
                    patch.object(manage, "busy", return_value=False),
                    patch.object(manage.admin, "request", return_value={"available": True}),
                    patch.object(manage.admin, "offline_status", return_value={"running": False}),
                    contextlib.redirect_stdout(io.StringIO()) as output,
                ):
                    manage.main([command])
                result = json.loads(output.getvalue())
                self.assertEqual(result["dashboard"]["local"], "http://127.0.0.1:9123/")
                if command == "status":
                    self.assertFalse(result["running"])
                self.assertNotIn(config.admin_token_file.read_text().strip(), output.getvalue())
