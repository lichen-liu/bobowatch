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
