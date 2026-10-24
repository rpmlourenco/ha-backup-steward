import asyncio
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backup_steward"))
from steward.config import DEFAULTS
from steward.engine import Engine
from steward.state import State
from steward.supervisor import JobFailed


class EngineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        source, dest = self.root / "source", self.root / "destination"
        source.mkdir()
        dest.mkdir()
        self.cfg = dict(
            DEFAULTS,
            execution_mode="test",
            source_mount=str(source),
            destination_mount=str(dest),
            checksum_frequency="disabled",
            encryption_password="fixture-secret",
            power_entity="switch.disk",
            min_free_gib=1,
        )
        self.state = State(self.root / "data")
        self.api = AsyncMock()
        self.api.core_state.return_value = "off"
        self.api.create_l2.return_value = "job123"
        self.api.find_backup.return_value = ("abcdef12", {"size_bytes": 4096})
        self.api.download.return_value = (source / "fixture.tar", "sha256")
        self.host = AsyncMock()
        self.disks = AsyncMock()
        self.disks.mounted_on_host.return_value = False
        self.copier = Mock()
        self.copier.copy = AsyncMock()
        self.copier.checksum = AsyncMock()
        self.engine = Engine(
            self.cfg, self.state, self.api, lambda _: self.host, lambda *_: self.disks, lambda *_: self.copier
        )

    async def run_engine(self, mode="backup"):
        with patch("steward.engine.shutil.disk_usage", return_value=Mock(free=1000 * 1024**3)):
            await self.engine.run(mode)

    async def test_observe_mode_never_starts_a_job(self):
        self.engine.cfg["execution_mode"] = "observe"
        with self.assertRaises(ValueError):
            self.engine.start()
        self.api.power.assert_not_called()

    async def test_unreadable_host_journal_blocks_power_and_backup_creation(self):
        self.disks.validate_journal.side_effect = RuntimeError("No host journal")
        await self.run_engine()
        self.api.power.assert_not_awaited()
        self.api.create_l2.assert_not_awaited()
        self.disks.mount.assert_not_awaited()
        self.disks.safe_remove.assert_not_awaited()

    async def test_successful_test_copy_preserves_legacy_and_skips_pruning(self):
        await self.run_engine()
        self.assertEqual(self.state.data["state"], "success")
        self.assertEqual(self.state.data["l2"], "delivered")
        self.assertEqual(self.state.data["retention"], "disabled_in_test")
        self.copier.prune_history.assert_not_called()
        self.disks.safe_remove.assert_awaited_once()
        self.assertEqual(self.api.power.call_args_list[-1].args, ("switch.disk", False))

    async def test_transfer_failure_never_deletes_native_or_old_archives(self):
        self.api.download.side_effect = RuntimeError("download failed")
        with patch("steward.engine.prune_l2") as prune:
            await self.run_engine()
        self.assertEqual(self.state.data["state"], "failed")
        self.api.delete_temporary.assert_not_awaited()
        self.copier.copy.assert_not_awaited()
        prune.assert_not_called()

    async def test_error_diagnostics_redact_encryption_secrets(self):
        self.api.download.side_effect = RuntimeError("fixture-secret")
        await self.run_engine()
        self.assertNotIn("fixture-secret", str(self.state.public()))
        self.assertEqual(self.state.data["failure_stage"], "l2_download")

    async def test_native_backup_timeout_keeps_power_and_host_lease(self):
        self.api.wait_job.side_effect = TimeoutError
        await self.run_engine()
        self.assertTrue(self.state.data["needs_cleanup"])
        self.assertTrue(self.state.data["backup_pending"])
        self.disks.safe_remove.assert_not_awaited()
        self.host.release.assert_not_awaited()
        self.assertEqual(self.api.power.call_count, 1)

    async def test_terminal_native_failure_allows_safe_cleanup_but_no_retention(self):
        self.api.wait_job.side_effect = JobFailed("fixture terminal failure")
        await self.run_engine()
        self.disks.safe_remove.assert_awaited_once()
        self.api.delete_temporary.assert_not_awaited()
        self.assertFalse(self.state.data["needs_cleanup"])
        self.assertEqual(self.state.data["state"], "failed")

    async def test_safe_remove_failure_never_turns_power_off(self):
        self.disks.safe_remove.side_effect = RuntimeError("flush failed")
        await self.run_engine()
        self.assertTrue(self.state.data["needs_cleanup"])
        self.assertEqual(self.api.power.call_count, 1)
        self.host.release.assert_not_awaited()

    async def test_retention_failure_is_visible_but_l3_continues(self):
        self.engine.cfg["execution_mode"] = "production"
        with patch("steward.engine.prune_l2", side_effect=ValueError("fixture failure")):
            await self.run_engine()
        self.assertEqual(self.state.data["retention"], "failed")
        self.assertEqual(self.state.data["l3"], "success")
        self.copier.copy.assert_awaited_once()

    async def test_full_checksum_failure_does_not_prune_history(self):
        self.engine.cfg["execution_mode"] = "production"
        self.copier.checksum.side_effect = RuntimeError("difference")
        with patch("steward.engine.prune_l2", return_value=[]):
            await self.run_engine("backup_checksum")
        self.copier.prune_history.assert_not_called()
        self.assertIsNone(self.state.data["last_checksum"])

    async def test_test_checksum_does_not_satisfy_production_calendar(self):
        await self.run_engine("backup_checksum")
        self.copier.checksum.assert_awaited_once()
        self.assertIsNone(self.state.data["last_checksum"])

    async def test_checksum_only_does_not_create_native_backup(self):
        await self.run_engine("checksum")
        self.api.create_l2.assert_not_awaited()
        self.copier.copy.assert_not_awaited()
        self.copier.checksum.assert_awaited_once()

    async def test_preexisting_mount_is_not_touched(self):
        self.disks.mounted_on_host.return_value = True
        await self.run_engine()
        self.api.power.assert_not_awaited()
        self.disks.safe_remove.assert_not_awaited()

    async def test_duplicate_and_interrupted_requests_are_rejected(self):
        await self.engine.lock.acquire()
        with self.assertRaises(ValueError):
            self.engine.start()
        self.engine.lock.release()
        self.state.update(needs_cleanup=True)
        with self.assertRaises(ValueError):
            self.engine.start()

    async def test_recovery_uses_saved_physical_identities(self):
        self.state.update(needs_cleanup=True, config_snapshot={"power_entity": "switch.original"})
        await self.engine.recover()
        self.assertEqual(self.api.power.call_args.args, ("switch.original", False))
        self.assertFalse(self.state.data["needs_cleanup"])

    async def test_recovery_is_valid_when_started_as_engine_task(self):
        self.state.update(needs_cleanup=True)
        self.engine.task = asyncio.create_task(self.engine.recover())
        await self.engine.task
        self.assertFalse(self.state.data["needs_cleanup"])

    async def test_unknown_native_job_blocks_unsafe_recovery(self):
        self.state.update(needs_cleanup=True, backup_pending=True, backup_job=None)
        with self.assertRaises(RuntimeError):
            await self.engine.recover()
        self.disks.safe_remove.assert_not_awaited()
        self.api.power.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
