"""Installer preservation, owned-volume proof and explicit lifecycle contracts."""

import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bobowatch import admin, manage, ramdisk
from bobowatch.access import read_access, set_access
from bobowatch.application import Application
from bobowatch.config import initialize, load
from bobowatch.policy import DEFAULT_BYTES, read_policy, set_quota
from bobowatch.protocol import Invalid
from bobowatch.provision import private_write
from bobowatch.storage import SQLiteStore

SPEC = importlib.util.spec_from_file_location(
    "watch_installer_test", Path(__file__).resolve().parents[1] / "installation/install.py"
)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "runtime"

    def install(self):
        with (
            patch.object(installer, "loaded", return_value=False),
            patch.object(installer, "busy", return_value=False),
            patch.object(installer.venv.EnvBuilder, "create"),
            patch.object(installer.subprocess, "run") as run,
            patch.object(installer.subprocess, "check_output", return_value="test-commit\n"),
            patch("builtins.print"),
        ):
            installer.install(self.root)
            self.assertEqual(run.call_count, 1)
            self.assertIn("pip", run.call_args.args[0])
        return load(self.root / "monitor.toml")

    def test_install_and_reinstall_preserve_controls_without_starting(self):
        config = self.install()
        self.assertTrue(config.ram_required)
        self.assertFalse(config.demo)
        self.assertEqual(config.host, "0.0.0.0")
        self.assertEqual(read_access(self.root), "local")
        set_access(self.root, "token")
        self.assertEqual(read_policy(self.root)["ram_bytes"], DEFAULT_BYTES)
        set_quota(self.root, 3 * 1024**3)
        private_write(
            self.root / "registrations.json", b'{"projects":[],"sites":[],"hosts":[],"replicas":[]}'
        )
        files = [
            "monitor.toml",
            "tokens/admin.token",
            "registrations.json",
            "storage-policy.json",
            "dashboard-policy.json",
        ]
        before = {name: (self.root / name).read_bytes() for name in files}
        self.install()
        self.assertEqual(before, {name: (self.root / name).read_bytes() for name in files})
        self.assertEqual(read_access(self.root), "token")
        self.assertFalse((self.root / "ramdisk.json").exists())
        self.assertFalse((self.root / "installing").exists())
        manage.verify_installation(self.root)
        (self.root / "app/bobowatch/manage.py").write_text("changed")
        with self.assertRaisesRegex(Invalid, "receipt"):
            manage.verify_installation(self.root)

    def test_invalid_ram_size_is_rejected_before_creating_runtime(self):
        with self.assertRaisesRegex(ValueError, "512"):
            installer.install(self.root, ram_bytes=1024**3 + 1)
        self.assertFalse(self.root.exists())

    def test_active_install_and_failed_update_refuse_to_start(self):
        self.install()
        with patch.object(installer, "loaded", return_value=True):
            with self.assertRaisesRegex(ValueError, "Stop"):
                installer.install(self.root)
        private_write(self.root / "installing", b"incomplete")
        with self.assertRaisesRegex(Invalid, "incomplete"):
            manage.verify_installation(self.root)

    def test_quota_offline_is_persistent_bounded_and_does_not_open_database(self):
        self.install()
        with patch("sqlite3.connect", side_effect=AssertionError("No SQL while stopped")):
            self.assertFalse(admin.offline_status(self.root)["available"])
            result = admin.execute(self.root, ["storage", "quota", "2GiB"])
            self.assertEqual(result["quota_bytes"], 2 * 1024**3)
        for value in ("0", "unlimited", "6GiB", "NaN", "0.5MiB"):
            with self.assertRaises((ValueError, Invalid)):
                set_quota(self.root, value)
        self.assertEqual(read_policy(self.root)["quota_bytes"], 2 * 1024**3)

    def test_unavailable_volume_never_creates_fallback_database(self):
        config = self.install()
        with self.assertRaises((OSError, Invalid)):
            SQLiteStore(config)
        self.assertFalse(config.database.parent.exists())

    def test_explicit_job_has_restart_but_no_login_or_boot_registration(self):
        job = manage.job_definition(self.root)
        self.assertTrue(job["KeepAlive"])
        self.assertNotIn("RunAtLoad", job)
        self.assertNotIn("LaunchAgents", str(job))
        self.assertIn(str(self.root / "manage.py"), job["ProgramArguments"])
        self.assertNotEqual(manage.label(self.root), manage.label(self.root / "other"))

    def test_readiness_failure_unloads_only_its_job_and_stop_waits(self):
        self.install()
        with (
            patch.object(manage, "loaded", return_value=False),
            patch.object(manage, "busy", return_value=False),
            patch.object(manage.ramdisk, "ensure"),
            patch.object(manage, "launchctl", return_value=SimpleNamespace(returncode=0)) as ctl,
            patch.object(manage.admin, "request", side_effect=Invalid("503")),
            patch.object(manage.time, "sleep"),
        ):
            with self.assertRaisesRegex(Invalid, "ready"):
                manage.start(self.root)
            self.assertEqual(
                ctl.call_args.args, ("bootout", f"gui/{os.getuid()}/{manage.label(self.root)}")
            )
        with (
            patch.object(manage, "loaded", return_value=True),
            patch.object(manage, "busy", side_effect=[True, False]),
            patch.object(manage, "launchctl", return_value=SimpleNamespace(returncode=0)),
            patch.object(manage.time, "sleep") as sleep,
        ):
            self.assertFalse(manage.stop(self.root)["running"])
            sleep.assert_called_once()

    def test_ram_proof_refuses_non_ram_other_owner_or_wrong_capacity(self):
        size = DEFAULT_BYTES
        image = {
            "image-path": f"ram://{size // 512}",
            "blockcount": size // 512,
            "blocksize": 512,
            "owner-uid": os.getuid(),
            "system-entities": [{"dev-entry": "/dev/disk999", "mount-point": "/Volumes/test"}],
        }
        with patch.object(ramdisk, "images", return_value=[image]):
            ramdisk.prove_image("/dev/disk999", size, mounted="/Volumes/test")
            for field, value in (
                ("image-path", "/some/disk.dmg"),
                ("owner-uid", -1),
                ("blockcount", 1),
            ):
                original = image[field]
                image[field] = value
                with self.assertRaises(Invalid):
                    ramdisk.prove_image("/dev/disk999", size)
                image[field] = original
            with self.assertRaises(Invalid):
                ramdisk.prove_image("/dev/disk999", size, mounted="/Volumes/wrong")

    def test_stale_or_reused_device_never_authorizes_formatting(self):
        self.install()
        private_write(
            self.root / "ramdisk.json",
            json.dumps({"device": "/dev/disk999", "capacity_bytes": DEFAULT_BYTES}).encode(),
        )
        foreign = {
            "image-path": "/private/unrelated.dmg",
            "system-entities": [{"dev-entry": "/dev/disk999"}],
        }
        with (
            patch.object(ramdisk.sys, "platform", "darwin"),
            patch.object(ramdisk, "images", return_value=[foreign]),
            patch.object(ramdisk.os.path, "ismount", return_value=False),
            patch.object(ramdisk.subprocess, "run") as run,
        ):
            with self.assertRaises(Invalid):
                ramdisk.ensure(self.root)
            with self.assertRaises(Invalid):
                ramdisk.release(self.root)
            run.assert_not_called()
        with (
            patch.object(ramdisk, "images", return_value=[]),
            patch.object(ramdisk.os.path, "ismount", return_value=False),
            patch.object(ramdisk.subprocess, "run") as run,
        ):
            ramdisk.release(self.root)
            run.assert_not_called()
            self.assertFalse((self.root / "ramdisk.json").exists())

    def test_live_quota_api_requires_loopback_admin_and_applies_without_restart(self):
        config = initialize(self.root, demo=True)
        # Test API authorization and writer policy using a temp SQL database;
        # RAM identity itself is independently validated, never bypassed in production.
        text = config.path.read_text().replace("demo = true", "demo = false")
        text += "\n" if not text.endswith("\n") else ""
        text = text.replace("[monitor]\n", "[monitor]\nramdisk = true\n")
        config.path.write_text(text)
        private_write(
            self.root / "storage-policy.json",
            json.dumps({"ram_bytes": DEFAULT_BYTES, "quota_bytes": DEFAULT_BYTES}).encode(),
        )
        config = load(config.path)
        with patch("bobowatch.storage.validate_database"):
            store = SQLiteStore(config)
            try:
                app = Application(config, store)
                token = config.admin_token_file.read_text().strip()

                def request(address, credential):
                    payload = json.dumps(
                        {"command": "storage.quota", "quota_bytes": 2 * 1024**3}
                    ).encode()
                    statuses = []
                    response = app(
                        {
                            "REQUEST_METHOD": "POST",
                            "PATH_INFO": "/api/control",
                            "HTTP_HOST": "127.0.0.1:8720",
                            "REMOTE_ADDR": address,
                            "HTTP_AUTHORIZATION": "Bearer " + credential,
                            "CONTENT_LENGTH": str(len(payload)),
                            "wsgi.input": io.BytesIO(payload),
                        },
                        lambda status, headers: statuses.append(status),
                    )
                    return int(statuses[0].split()[0]), json.loads(b"".join(response))

                self.assertEqual(request("192.168.1.5", token)[0], 403)
                self.assertEqual(request("127.0.0.1", "wrong")[0], 403)
                code, result = request("127.0.0.1", token)
                self.assertEqual(code, 202)
                self.assertEqual(result["quota_bytes"], 2 * 1024**3)
                self.assertEqual(store._quota, 2 * 1024**3)
                prior_policy = (self.root / "storage-policy.json").read_bytes()
                with patch.object(store, "storage_status", return_value={"volume_bytes": 1024**3}):
                    self.assertEqual(request("127.0.0.1", token)[0], 400)
                self.assertEqual((self.root / "storage-policy.json").read_bytes(), prior_policy)
                old_token = token
                token = "rotated-admin-" + "x" * 40
                config.admin_token_file.write_text(token)
                self.assertTrue(app.reload())
                self.assertEqual(request("127.0.0.1", old_token)[0], 403)
                self.assertEqual(request("127.0.0.1", token)[0], 202)
            finally:
                store.close()
