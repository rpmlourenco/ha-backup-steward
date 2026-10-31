"""Native L1 acceptance with fake HA, temporary archives and isolated disk stubs."""

import asyncio
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backup_steward"))
from steward.config import DEFAULTS
from steward.engine import Engine
from steward.native_l1 import prune
from steward.state import State
from steward.storage import Copier
from steward.supervisor import APIError, JobFailed, Supervisor


def archive(path, slug="abcdef12", day=1, protected=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(dict(slug=slug, date=f"2026-10-{day:02}T04:00:00+00:00", protected=protected)).encode()
    with tarfile.open(path, "w") as tar:
        info = tarfile.TarInfo("backup.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return path


class NativeAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.api = Supervisor(None, "fixture-key")
        self.api.core_ws = AsyncMock(
            return_value={
                "config": {
                    "schedule": {"recurrence": "never"},
                    "create_backup": {
                        "agent_ids": ["hassio.local", "cloud.cloud"],
                        "password": "fixture-native-secret",
                    },
                    "agents": {"hassio.local": {"protected": True}},
                }
            }
        )

    async def test_policy_requires_single_scheduler_local_copy_and_encryption(self):
        self.assertEqual(await self.api.l1_policy(), {"local": True, "encrypted": True})
        config = self.api.core_ws.return_value["config"]
        config["schedule"]["recurrence"] = "daily"
        with self.assertRaisesRegex(APIError, "Disable only"):
            await self.api.l1_policy()
        config["schedule"]["recurrence"] = "never"
        config["create_backup"]["agent_ids"] = ["cloud.cloud"]
        with self.assertRaisesRegex(APIError, "local storage"):
            await self.api.l1_policy()
        config["create_backup"]["agent_ids"] = ["hassio.local"]
        config["agents"]["hassio.local"]["protected"] = False
        with self.assertRaisesRegex(APIError, "encryption"):
            await self.api.l1_policy()

    async def test_creation_reuses_native_settings_without_copying_password(self):
        self.api.core_ws.return_value = {"backup_job_id": "a" * 32}
        self.assertEqual(await self.api.create_l1(), "a" * 32)
        self.api.core_ws.assert_awaited_once_with("backup/generate_with_automatic_settings")
        self.api.core_ws.return_value = {"backup_job_id": "unexpected"}
        with self.assertRaises(APIError):
            await self.api.create_l1()

    async def test_completion_uses_exact_job_reference_and_checks_cloud_delivery(self):
        self.api.wait_job = AsyncMock(return_value={"reference": "abcdef12"})
        self.api.wait_native_idle = AsyncMock()
        self.api.request = AsyncMock(return_value={"protected": True, "compressed": True})
        backup = {"backup_id": "abcdef12", "agents": {"hassio.local": {"protected": True}}}
        self.api.core_ws.return_value = {"backups": [dict(backup, backup_id="12345678"), backup]}
        slug, _ = await self.api.finish_l1("a" * 32, 1)
        self.assertEqual(slug, "abcdef12")
        self.api.request.assert_awaited_once_with("GET", "/backups/abcdef12/info")
        backup["failed_agent_ids"] = ["cloud.cloud"]
        with self.assertRaises(JobFailed):
            await self.api.finish_l1("a" * 32, 1)
        self.api.wait_job.return_value = {"reference": "87654321"}
        with self.assertRaises(APIError):
            await self.api.finish_l1("a" * 32, 1)

    async def test_failed_native_job_still_waits_until_manager_is_idle(self):
        self.api.wait_job = AsyncMock(side_effect=JobFailed("terminal"))
        self.api.wait_native_idle = AsyncMock()
        with self.assertRaises(JobFailed):
            await self.api.finish_l1("a" * 32, 1)
        self.api.wait_native_idle.assert_awaited_once_with(1)

    async def test_busy_native_manager_is_waited_and_unavailable_is_rejected(self):
        self.api.core_state = AsyncMock(side_effect=["create_backup", "idle"])
        with patch("steward.supervisor.asyncio.sleep", new=AsyncMock()):
            await self.api.wait_native_idle(1)
        self.assertEqual(self.api.core_state.await_count, 2)
        self.api.core_state = AsyncMock(return_value="unavailable")
        with self.assertRaises(APIError):
            await self.api.wait_native_idle(1)

    async def test_real_websocket_handshake_uses_allowlisted_command(self):
        seen = []

        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.send_json({"type": "auth_required"})
            auth = await ws.receive_json()
            self.assertEqual(auth, {"type": "auth", "access_token": "fixture-key"})
            await ws.send_json({"type": "auth_ok"})
            command = await ws.receive_json()
            seen.append(command)
            await ws.send_json({"id": command["id"], "success": True, "result": {"backup_job_id": "a" * 32}})
            await ws.close()
            return ws

        app = web.Application()
        app.router.add_get("/core/websocket", handler)
        async with TestServer(app) as server, ClientSession() as session:
            api = Supervisor(session, "fixture-key", str(server.make_url("/")).rstrip("/"))
            self.assertEqual(await api.create_l1(), "a" * 32)
            with self.assertRaises(ValueError):
                await api.core_ws("backup/config/update")
        self.assertEqual(seen, [{"id": 1, "type": "backup/generate_with_automatic_settings"}])


class L1WorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source, dest = self.root / "source", self.root / "dest"
        source.mkdir()
        dest.mkdir()
        self.cfg = dict(
            DEFAULTS,
            execution_mode="test",
            source_mount=str(source),
            destination_mount=str(dest),
            l1_enabled=True,
            encryption_password="fixture-key",
            checksum_frequency="disabled",
            power_entity="switch.fixture",
            min_free_gib=1,
        )
        self.state = State(self.root / "state")
        self.api = AsyncMock()
        self.api.core_state.return_value = "off"
        self.api.create_l1.return_value = "a" * 32
        self.api.finish_l1.return_value = ("abcdef12", {"size_bytes": 4096})
        self.api.create_l2.return_value = "b" * 32
        self.api.find_backup.return_value = ("12345678", {"size_bytes": 8192})
        self.api.download.return_value = (source / "fixture.tar", "digest")
        self.host, self.disks = AsyncMock(), AsyncMock()
        self.disks.mounted_on_host.return_value = False
        self.copier = Mock(copy=AsyncMock(), checksum=AsyncMock())
        self.engine = Engine(
            self.cfg, self.state, self.api, lambda _: self.host, lambda *_: self.disks, lambda *_: self.copier
        )

    async def run_engine(self, mode="backup_only"):
        with patch("steward.engine.shutil.disk_usage", return_value=Mock(free=1000 * 1024**3)):
            await self.engine.run(mode)

    async def test_l1_delivery_precedes_l2_power_and_copy_and_native_is_preserved(self):
        order = []

        async def finish(*args):
            order.append("l1-finished")
            self.api.power.assert_not_awaited()
            self.api.create_l2.assert_not_awaited()
            return "abcdef12", {"size_bytes": 4096}

        async def download(slug, directory, filename, expected):
            order.append(slug)
            return directory / filename, "digest"

        self.api.finish_l1.side_effect = finish
        self.api.download.side_effect = download
        await self.run_engine()
        self.assertEqual(order, ["l1-finished", "abcdef12", "12345678"])
        self.assertEqual(self.state.data["state"], "success")
        self.assertEqual(self.state.data["last_backup"]["l1"], "delivered")
        self.api.delete_temporary.assert_awaited_once_with("12345678")
        self.assertIn("level-1", str(self.api.download.call_args_list[0].args[1]))

    async def test_cancel_during_native_l1_waits_then_touches_no_destination(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def finish(*args):
            entered.set()
            await release.wait()
            return "abcdef12", {"size_bytes": 4096}

        self.api.finish_l1.side_effect = finish
        task = self.engine.start("backup_only")
        await asyncio.wait_for(entered.wait(), 2)
        self.engine.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        release.set()
        await asyncio.wait_for(task, 2)
        self.assertEqual(self.state.data["state"], "cancelled")
        self.assertFalse(self.state.data["needs_cleanup"])
        self.api.power.assert_not_awaited()
        self.api.download.assert_not_awaited()
        self.disks.safe_remove.assert_not_awaited()

    async def test_lost_creation_response_blocks_new_run_without_power_operations(self):
        self.api.create_l1.side_effect = TimeoutError()
        await self.run_engine()
        self.assertTrue(self.state.data["needs_cleanup"])
        self.assertTrue(self.state.data["backup_pending"])
        self.assertEqual(self.state.data["backup_kind"], "l1")
        self.api.power.assert_not_awaited()
        self.host.release.assert_not_awaited()
        with self.assertRaises(ValueError):
            self.engine.start()
        with self.assertRaisesRegex(RuntimeError, "unknown"):
            await self.engine.recover()
        self.disks.safe_remove.assert_not_awaited()

    async def test_known_job_recovery_waits_for_l1_without_mounting_or_powering_l3(self):
        self.api.finish_l1.side_effect = TimeoutError()
        await self.run_engine()
        self.api.finish_l1.side_effect = None
        await self.engine.recover()
        self.assertFalse(self.state.data["needs_cleanup"])
        self.assertFalse(self.state.data["backup_pending"])
        self.api.create_l1.assert_awaited_once()
        self.api.create_l2.assert_not_awaited()
        self.api.power.assert_not_awaited()
        self.disks.safe_remove.assert_not_awaited()

    async def test_app_stop_during_l1_preserves_pending_obligation_across_restart(self):
        entered = asyncio.Event()

        async def pending(*args):
            entered.set()
            await asyncio.Event().wait()

        self.api.finish_l1.side_effect = pending
        task = self.engine.start("backup_only")
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        restored = State(self.root / "state")
        self.assertTrue(restored.data["needs_cleanup"])
        self.assertTrue(restored.data["backup_pending"])
        self.assertEqual(restored.data["backup_job"], "a" * 32)
        self.host.release.assert_not_awaited()
        self.api.power.assert_not_awaited()
        self.disks.safe_remove.assert_not_awaited()

    async def test_later_manual_disk_session_does_not_inherit_l1_only_recovery(self):
        self.api.finish_l1.side_effect = JobFailed("terminal")
        await self.run_engine()
        await self.engine.start_destination("mount")
        self.assertTrue(self.state.data["destination_owned"])
        await self.engine.start_destination("shutdown")
        self.disks.safe_remove.assert_awaited_once()
        self.api.power.assert_any_await("switch.fixture", False)
        self.assertFalse(self.state.data["needs_cleanup"])

    async def test_terminal_failure_or_download_failure_does_not_leave_false_recovery(self):
        self.api.finish_l1.side_effect = JobFailed("terminal")
        await self.run_engine()
        self.assertFalse(self.state.data["needs_cleanup"])
        self.api.power.assert_not_awaited()
        self.api.finish_l1.side_effect = None
        self.api.download.side_effect = RuntimeError("download failed")
        await self.run_engine()
        self.assertFalse(self.state.data["needs_cleanup"])
        self.api.create_l2.assert_not_awaited()
        self.api.delete_temporary.assert_not_awaited()

    async def test_disabled_l1_and_checksum_only_never_create_native_l1(self):
        self.cfg["l1_enabled"] = False
        await self.run_engine()
        self.api.create_l1.assert_not_awaited()
        self.cfg["l1_enabled"] = True
        await self.run_engine("checksum")
        self.api.create_l1.assert_not_awaited()

    async def test_production_retention_runs_only_after_l1_delivery(self):
        self.cfg["execution_mode"] = "production"
        with (
            patch("steward.native_l1.prune", return_value=["old.tar"]) as cleanup,
            patch("steward.engine.prune_l2", return_value=[]),
        ):
            await self.run_engine()
        cleanup.assert_called_once_with(
            Path(self.cfg["source_mount"]) / self.cfg["l1_directory"], 3, "abcdef12"
        )
        self.assertEqual(self.state.data["l1_retention"], "success")
        self.assertEqual(self.state.data["l1_removed"], 1)


class L1StorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_retention_only_removes_managed_l1_archives_after_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = archive(root / "steward_l1_20261001T040000Z_12345678.tar", "12345678", 1)
            new = archive(root / "steward_l1_20261002T040000Z_abcdef12.tar", day=2)
            other = archive(root / "native-or-l2.tar")
            self.assertEqual(prune(root, 1, "abcdef12"), [old.name])
            self.assertTrue(new.exists() and other.exists())
            broken = root / "steward_l1_20261003T040000Z_87654321.tar"
            broken.write_bytes(b"truncated")
            with self.assertRaises(tarfile.ReadError):
                prune(root, 1, "abcdef12")
            self.assertTrue(new.exists())

    async def test_real_test_copy_includes_and_hashes_l1_alongside_l2(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, dest = root / "source", root / "dest"
            source.mkdir()
            dest.mkdir()
            cfg = dict(DEFAULTS, source_mount=str(source), destination_mount=str(dest), l1_enabled=True)
            for level in ("level-1", "level-2"):
                archive(source / cfg["test_directory"] / level / "fixture.tar")
            copier = Copier(cfg, AsyncMock())
            copier.plan_checks(include_copy=True, include_full=False, test=True)
            await copier.copy("fixture", test=True)
            report = copier.finish_checks()
            self.assertTrue((dest / cfg["test_directory"] / "mirror" / "L1" / "fixture.tar").exists())
            self.assertTrue(
                any(a["kind"] == "l1_sha256" and a["status"] == "success" for a in report["areas"])
            )
