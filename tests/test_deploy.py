"""Deployment failures preserve private state and never leave failed jobs loaded."""

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("watch_deploy_test", REPO / "deployment/deploy.py")
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)


class DeployTests(unittest.TestCase):
    def test_order_failure_cleanup_and_state_preservation(self):
        for failure in (None, "stop", "install", "start", "check"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                state = {
                    "monitor.toml": b"config",
                    "admin.token": b"secret",
                    "history.sqlite3": b"existing history",
                }
                for name, value in state.items():
                    (root / name).write_bytes(value)
                calls = []

                def stage(name, *, calls=calls, failure=failure):
                    calls.append(name)
                    if name == failure:
                        raise ValueError("failed " + name)
                    return {"epoch": "retained"}

                installer = SimpleNamespace(install=lambda *a, **kw: stage("install"))
                with (
                    patch.object(
                        deploy.importlib.util,
                        "spec_from_file_location",
                        return_value=SimpleNamespace(
                            loader=SimpleNamespace(exec_module=lambda target: None)
                        ),
                    ),
                    patch.object(deploy.importlib.util, "module_from_spec", return_value=installer),
                    patch.object(deploy.manage, "stop", side_effect=lambda root: stage("stop")),
                    patch.object(deploy.manage, "start", side_effect=lambda root: stage("start")),
                    patch.object(deploy.admin, "request", side_effect=lambda *a: stage("check")),
                    patch.object(
                        deploy.manage.ramdisk,
                        "release",
                        side_effect=AssertionError("Never discard history"),
                    ),
                ):
                    if failure:
                        with self.assertRaisesRegex(ValueError, "failed " + failure):
                            deploy.deploy(root)
                    else:
                        self.assertEqual(deploy.deploy(root)["epoch"], "retained")
                expected = {
                    None: ["stop", "install", "start", "check"],
                    "stop": ["stop"],
                    "install": ["stop", "install"],
                    "start": ["stop", "install", "start", "stop"],
                    "check": ["stop", "install", "start", "check", "stop"],
                }
                self.assertEqual(calls, expected[failure])
                self.assertEqual(state, {name: (root / name).read_bytes() for name in state})

    def test_help_before_install_has_no_runtime_side_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "absent"
            result = subprocess.run(
                [str(REPO / "deployment/watch"), "deploy", "--runtime-dir", str(root), "--help"],
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("--ram-size", result.stdout)
            self.assertFalse(root.exists())

    def test_successful_deploy_reports_dashboard_urls_after_check(self):
        import contextlib
        import io

        from bobowatch.config import initialize

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "runtime"
            config = initialize(root, port=9123)
            config.path.write_text(config.path.read_text().replace("127.0.0.1:", "0.0.0.0:"))
            with (
                patch.object(deploy.sys, "platform", "darwin"),
                patch.object(deploy.os, "getuid", return_value=501),
                patch.object(deploy, "deploy", return_value={"epoch": "retained"}),
                patch(
                    "bobowatch.urls.lan_interfaces",
                    return_value=[{"interface": "en1", "address": "10.1.2.3"}],
                ),
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                deploy.main(["deploy", "--runtime-dir", str(root)])
            self.assertIn("deployed and checked; history epoch: retained", output.getvalue())
            self.assertIn("Dashboard (this Mac): http://127.0.0.1:9123/", output.getvalue())
            self.assertIn(
                "Dashboard (local network, en1): http://10.1.2.3:9123/", output.getvalue()
            )
            self.assertNotIn(config.admin_token_file.read_text().strip(), output.getvalue())

    def test_failed_deploy_does_not_report_dashboard_ready(self):
        import contextlib
        import io

        with (
            patch.object(deploy.sys, "platform", "darwin"),
            patch.object(deploy.os, "getuid", return_value=501),
            patch.object(deploy, "deploy", side_effect=ValueError("failed check")),
            patch.object(deploy, "print_dashboard") as show,
            contextlib.redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit) as error,
        ):
            deploy.main(["deploy"])
        self.assertEqual(error.exception.code, 1)
        show.assert_not_called()
