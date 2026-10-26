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

    async def test_backup_only_skips_overdue_full_checksum_but_keeps_copy_checks(self):
        self.engine.cfg["execution_mode"] = "production"
        with (
            patch("steward.engine.checksum_due", return_value=True),
            patch("steward.engine.prune_l2", return_value=[]),
        ):
            await self.run_engine("backup_only")
        self.assertEqual(self.state.data["state"], "success")
        self.copier.copy.assert_awaited_once()
        self.copier.finish_checks.assert_called_once()
        self.copier.plan_checks.assert_called_once_with(
            include_copy=True, include_full=False, test=False, source=None
        )
        self.copier.checksum.assert_not_awaited()
        self.assertIsNone(self.state.data["last_checksum"])
        self.disks.safe_remove.assert_awaited_once()

    async def test_cancel_before_task_starts_touches_no_power_or_backup(self):
        task = self.engine.start("backup_only")
        self.engine.cancel()
        await task
        self.assertEqual(self.state.data["state"], "cancelled")
        self.api.power.assert_not_awaited()
        self.api.create_l2.assert_not_awaited()
        self.api.notify.assert_not_awaited()
        self.assertFalse(self.engine.can_cancel)

    async def test_cancel_waits_for_native_completion_and_preserves_native_archive(self):
        entered, finish = asyncio.Event(), asyncio.Event()

        async def native(*args):
            entered.set()
            await finish.wait()

        self.api.wait_job.side_effect = native
        with patch("steward.engine.shutil.disk_usage", return_value=Mock(free=1000 * 1024**3)):
            task = self.engine.start("backup_only")
            await asyncio.wait_for(entered.wait(), 2)
            self.engine.cancel()
            self.engine.cancel()  # repeated requests are harmless
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            self.disks.safe_remove.assert_not_awaited()
            self.assertFalse(self.engine.can_cancel)
            finish.set()
            await asyncio.wait_for(task, 2)
        self.assertEqual(self.state.data["state"], "cancelled")
        self.api.download.assert_not_awaited()
        self.api.delete_temporary.assert_not_awaited()
        self.disks.safe_remove.assert_awaited_once()
        self.assertEqual(self.api.power.call_args.args, ("switch.disk", False))
        self.api.notify.assert_not_awaited()

    async def test_cancel_with_unknown_native_outcome_keeps_power_and_recovery(self):
        entered, finish = asyncio.Event(), asyncio.Event()

        async def native(*args):
            entered.set()
            await finish.wait()
            raise TimeoutError()

        self.api.wait_job.side_effect = native
        with patch("steward.engine.shutil.disk_usage", return_value=Mock(free=1000 * 1024**3)):
            task = self.engine.start("backup_only")
            await asyncio.wait_for(entered.wait(), 2)
            self.engine.cancel()
            finish.set()
            await asyncio.wait_for(task, 2)
        self.assertEqual(self.state.data["state"], "failed")
        self.assertEqual(self.state.data["failure_stage"], "l2_wait")
        self.assertTrue(self.state.data["needs_cleanup"])
        self.disks.safe_remove.assert_not_awaited()
        self.assertEqual(self.api.power.await_count, 1)
        self.api.notify.assert_awaited_once()

    async def test_cancel_during_mount_waits_until_mount_finishes(self):
        entered, finish = asyncio.Event(), asyncio.Event()

        async def mount(*args):
            entered.set()
            await finish.wait()

        self.disks.mount.side_effect = mount
        with patch("steward.engine.shutil.disk_usage", return_value=Mock(free=1000 * 1024**3)):
            task = self.engine.start("backup_only")
            await asyncio.wait_for(entered.wait(), 2)
            self.engine.cancel()
            await asyncio.sleep(0)
            self.disks.safe_remove.assert_not_awaited()
            finish.set()
            await asyncio.wait_for(task, 2)
        self.api.create_l2.assert_not_awaited()
        self.assertEqual(self.state.data["state"], "cancelled")
        self.disks.safe_remove.assert_awaited_once()

    async def test_cancel_copy_awaits_local_worker_exit_before_safe_removal(self):
        entered, stopped = asyncio.Event(), asyncio.Event()

        async def copy(*args):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                await asyncio.sleep(0)
                stopped.set()

        async def remove():
            self.assertTrue(stopped.is_set())

        self.copier.copy.side_effect = copy
        self.disks.safe_remove.side_effect = remove
        with patch("steward.engine.shutil.disk_usage", return_value=Mock(free=1000 * 1024**3)):
            task = self.engine.start("backup_only")
            await asyncio.wait_for(entered.wait(), 2)
            self.engine.cancel()
            await asyncio.wait_for(task, 2)
        self.assertEqual(self.state.data["state"], "cancelled")
        self.copier.prune_history.assert_not_called()
        self.api.notify.assert_not_awaited()
        self.assertIsNone(self.state.data["last_success"])

    async def test_cancel_checksum_preserves_dates_and_cleanup_failure_notifies(self):
        self.engine.cfg["execution_mode"] = "production"
        entered = asyncio.Event()

        async def checksum(*args, **kwargs):
            entered.set()
            await asyncio.Future()

        self.copier.checksum.side_effect = checksum
        self.disks.safe_remove.side_effect = RuntimeError("flush unknown")
        previous = "2026-10-01T04:00:00+00:00"
        self.state.update(last_checksum=previous, last_success=previous)
        with patch("steward.engine.shutil.disk_usage", return_value=Mock(free=1000 * 1024**3)):
            task = self.engine.start("checksum")
            await asyncio.wait_for(entered.wait(), 2)
            self.engine.cancel()
            await asyncio.wait_for(task, 2)
        self.assertEqual(self.state.data["state"], "failed")
        self.assertTrue(self.state.public()["recovery_required"])
        self.assertEqual(self.api.power.await_count, 1)
        self.assertEqual(self.state.data["last_checksum"], previous)
        self.assertEqual(self.state.data["last_success"], previous)
        self.api.notify.assert_awaited_once()

    async def test_cancel_during_native_post_keeps_unknown_job_for_recovery(self):
        from steward.supervisor import APIError

        entered, finish = asyncio.Event(), asyncio.Event()

        async def create(*args):
            entered.set()
            await finish.wait()
            raise APIError("Native POST response unknown")

        self.api.create_l2.side_effect = create
        with patch("steward.engine.shutil.disk_usage", return_value=Mock(free=1000 * 1024**3)):
            task = self.engine.start("backup_only")
            await asyncio.wait_for(entered.wait(), 2)
            self.engine.cancel()
            self.disks.safe_remove.assert_not_awaited()
            finish.set()
            await asyncio.wait_for(task, 2)
        self.assertTrue(self.state.data["backup_pending"])
        self.assertIsNone(self.state.data["backup_job"])
        self.assertTrue(self.state.data["needs_cleanup"])
        self.assertEqual(self.api.power.await_count, 1)
        self.disks.safe_remove.assert_not_awaited()

    async def test_cancel_download_preserves_native_backup_and_does_not_apply_retention(self):
        entered = asyncio.Event()

        async def download(*args):
            entered.set()
            await asyncio.Future()

        self.api.download.side_effect = download
        with (
            patch("steward.engine.shutil.disk_usage", return_value=Mock(free=1000 * 1024**3)),
            patch("steward.engine.prune_l2") as prune,
        ):
            task = self.engine.start("backup_only")
            await asyncio.wait_for(entered.wait(), 2)
            self.engine.cancel()
            await asyncio.wait_for(task, 2)
        self.assertEqual(self.state.data["state"], "cancelled")
        self.api.delete_temporary.assert_not_awaited()
        prune.assert_not_called()
        self.copier.copy.assert_not_awaited()
        self.disks.safe_remove.assert_awaited_once()

    async def test_cancel_waits_for_l2_retention_thread_before_unmount(self):
        import threading

        self.engine.cfg["execution_mode"] = "production"
        entered = asyncio.Event()
        finish = threading.Event()
        loop = asyncio.get_running_loop()

        def prune(*args):
            loop.call_soon_threadsafe(entered.set)
            if not finish.wait(3):
                raise AssertionError("test retention worker was not released")
            return []

        with (
            patch("steward.engine.shutil.disk_usage", return_value=Mock(free=1000 * 1024**3)),
            patch("steward.engine.prune_l2", side_effect=prune),
        ):
            task = self.engine.start("backup_only")
            try:
                await asyncio.wait_for(entered.wait(), 2)
                self.engine.cancel()
                await asyncio.sleep(0)
                self.disks.safe_remove.assert_not_awaited()
            finally:
                finish.set()
            await asyncio.wait_for(task, 2)
        self.assertEqual(self.state.data["state"], "cancelled")
        self.copier.copy.assert_not_awaited()
        self.disks.safe_remove.assert_awaited_once()

    async def test_final_safe_shutdown_cannot_be_cancelled(self):
        entered, finish = asyncio.Event(), asyncio.Event()

        async def remove():
            entered.set()
            await finish.wait()

        self.disks.safe_remove.side_effect = remove
        with patch("steward.engine.shutil.disk_usage", return_value=Mock(free=1000 * 1024**3)):
            task = self.engine.start("checksum")
            await asyncio.wait_for(entered.wait(), 2)
            self.assertFalse(self.engine.can_cancel)
            with self.assertRaises(ValueError):
                self.engine.cancel()
            finish.set()
            await asyncio.wait_for(task, 2)
        self.assertEqual(self.state.data["state"], "success")

    async def test_cancellation_rejects_idle_and_manual_disk_operations(self):
        with self.assertRaises(ValueError):
            self.engine.cancel()
        self.engine.cfg["execution_mode"] = "observe"
        with self.assertRaises(ValueError):
            self.engine.start("backup_only")
        self.engine.cfg["execution_mode"] = "test"
        task = self.engine.start("backup_only")
        self.engine.cancel()
        await task
        await self.engine.start_destination("mount")
        with self.assertRaises(ValueError):
            self.engine.cancel()

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
        with self.assertLogs("steward", level="INFO") as logs:
            await self.run_engine()
        output = "\n".join(logs.output)
        for step in (
            "Execução iniciada",
            "preflight:",
            "mount:",
            "l2_create:",
            "l2_download:",
            "l3_copy:",
            "safe_remove:",
            "Execução concluída",
        ):
            self.assertIn(step, output)
        self.assertNotIn("fixture-secret", output)
        self.assertEqual(self.state.data["state"], "success")
        self.assertEqual(self.state.data["l2"], "delivered")
        self.assertEqual(self.state.data["retention"], "disabled_in_test")
        self.copier.prune_history.assert_not_called()
        self.disks.safe_remove.assert_awaited_once()
        self.assertEqual(self.api.power.call_args_list[-1].args, ("switch.disk", False))
        self.api.notify.assert_not_awaited()

    async def test_manual_session_mounts_and_closes_without_creating_a_backup(self):
        self.state.update(last_success="2026-10-25T00:00:00+00:00")
        task = self.engine.start_destination("mount")
        self.assertTrue(self.engine.busy)
        with self.assertRaises(ValueError):
            self.engine.start_destination("mount")
        await task
        self.assertEqual(self.state.data["state"], "destination_mounted")
        self.assertTrue(self.state.data["needs_cleanup"])
        self.assertFalse(self.state.public()["recovery_required"])
        self.host.release.assert_not_awaited()
        self.api.create_l2.assert_not_awaited()
        self.copier.copy.assert_not_awaited()
        with self.assertRaises(ValueError):
            self.engine.start("backup")
        # Even an out-of-band configuration change cannot redirect shutdown.
        self.engine.cfg["power_entity"] = "switch.changed"
        await self.engine.start_destination("shutdown")
        self.assertEqual(self.state.data["state"], "destination_off")
        self.assertFalse(self.state.data["needs_cleanup"])
        self.disks.safe_remove.assert_awaited_once()
        self.disks.verify_removed.assert_awaited_once()
        self.assertEqual(self.api.power.call_args_list[-1].args, ("switch.disk", False))
        self.host.release.assert_awaited_once()
        self.api.notify.assert_not_awaited()
        self.assertEqual(self.state.data["last_success"], "2026-10-25T00:00:00+00:00")

    async def test_manual_mount_failure_preserves_power_and_recovery_ownership(self):
        self.disks.mount.side_effect = RuntimeError("mount refused")
        await self.engine.start_destination("mount")
        self.assertEqual(self.state.data["state"], "failed")
        self.assertTrue(self.state.public()["recovery_required"])
        self.host.release.assert_not_awaited()
        self.disks.safe_remove.assert_not_awaited()
        self.api.power.assert_awaited_once_with("switch.disk", True)
        self.api.notify.assert_awaited_once()

    async def test_manual_preflight_failure_never_touches_power(self):
        self.api.preflight.side_effect = RuntimeError("busy")
        await self.engine.start_destination("mount")
        self.api.power.assert_not_awaited()
        self.disks.mount.assert_not_awaited()
        self.assertFalse(self.state.data["needs_cleanup"])

    async def test_manual_control_never_adopts_an_external_powered_destination(self):
        self.api.core_state.return_value = "on"
        await self.engine.start_destination("mount")
        self.api.power.assert_not_awaited()
        self.disks.mount.assert_not_awaited()
        self.assertFalse(self.state.data["needs_cleanup"])

    async def test_manual_shutdown_failure_does_not_cut_power(self):
        await self.engine.start_destination("mount")
        self.disks.safe_remove.side_effect = RuntimeError("flush unknown")
        await self.engine.start_destination("shutdown")
        self.assertTrue(self.state.data["needs_cleanup"])
        self.assertEqual(self.api.power.await_count, 1)
        self.host.release.assert_not_awaited()
        self.api.notify.assert_awaited_once()

    async def test_scheduler_waits_while_destination_is_manually_open(self):
        from steward.server import scheduler

        self.state.update(state="destination_mounted", needs_cleanup=True, destination_manual=True)
        with (
            patch.object(self.engine, "start") as start,
            patch("steward.server.due", side_effect=AssertionError("Manual session must block calendar")),
            patch("steward.server.asyncio.sleep", side_effect=asyncio.CancelledError),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await scheduler(self.engine)
        start.assert_not_called()

    async def test_manual_controls_reject_observation_invalid_actions_and_unowned_shutdown(self):
        for action in ("shutdown", "format", "mount; reboot"):
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.engine.start_destination(action)
        self.engine.cfg["execution_mode"] = "observe"
        with self.assertRaises(ValueError):
            self.engine.start_destination("mount")
        self.api.power.assert_not_awaited()

    async def test_cancelled_manual_power_on_remains_recoverable(self):
        entered = asyncio.Event()

        async def interrupted_power(*args):
            entered.set()
            await asyncio.Future()

        self.api.power.side_effect = interrupted_power
        task = self.engine.start_destination("mount")
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.state.data["state"], "interrupted")
        self.assertTrue(self.state.public()["recovery_required"])
        self.host.release.assert_not_awaited()

    async def test_transfer_failure_never_deletes_native_or_old_archives(self):
        self.api.download.side_effect = RuntimeError("download failed")
        with patch("steward.engine.prune_l2") as prune:
            await self.run_engine()
        self.assertEqual(self.state.data["state"], "failed")
        self.api.notify.assert_awaited_once()
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
        self.api.notify.assert_awaited_once_with(
            "L2 was delivered, but archive retention failed. L3 will continue."
        )

    async def test_full_checksum_failure_does_not_prune_history(self):
        self.engine.cfg["execution_mode"] = "production"
        self.copier.checksum.side_effect = RuntimeError("difference")
        with patch("steward.engine.prune_l2", return_value=[]):
            await self.run_engine("backup_checksum")
        self.copier.prune_history.assert_not_called()
        self.assertIsNone(self.state.data["last_checksum"])

    async def test_aggregate_integrity_failure_closes_safely_without_advancing_success(self):
        from steward.integrity import IntegrityFailure

        self.engine.cfg["execution_mode"] = "production"
        previous = "2026-10-01T04:00:00+00:00"
        self.state.update(last_checksum=previous, last_success=previous)
        self.copier.finish_checks.side_effect = IntegrityFailure(
            dict(complete=True, limit_reached=False, error_count=2)
        )
        with patch("steward.engine.prune_l2", return_value=[]):
            await self.run_engine("backup_checksum")
        self.copier.checksum.assert_awaited_once()
        self.copier.finish_checks.assert_called_once()
        self.copier.prune_history.assert_not_called()
        self.disks.safe_remove.assert_awaited_once()
        self.assertEqual(self.api.power.call_args.args, ("switch.disk", False))
        self.assertEqual(self.state.data["state"], "failed")
        self.assertEqual(self.state.data["last_checksum"], previous)
        self.assertEqual(self.state.data["last_success"], previous)
        self.assertFalse(self.state.data["needs_cleanup"])
        self.api.notify.assert_awaited_once()

    async def test_test_checksum_does_not_satisfy_production_calendar(self):
        await self.run_engine("backup_checksum")
        self.copier.checksum.assert_awaited_once()
        self.assertIsNone(self.state.data["last_checksum"])

    async def test_checksum_only_does_not_create_native_backup(self):
        await self.run_engine("checksum")
        self.api.create_l2.assert_not_awaited()
        self.copier.copy.assert_not_awaited()
        self.copier.checksum.assert_awaited_once()

    async def test_partial_checksum_preserves_global_dates_and_uses_selected_scope(self):
        self.engine.cfg["execution_mode"] = "production"
        previous = "2026-10-01T04:00:00+00:00"
        self.state.update(last_checksum=previous, last_success=previous)
        source = self.cfg["source_mount"]
        with patch("steward.engine.shutil.disk_usage", return_value=Mock(free=1000 * 1024**3)):
            await self.engine.start("checksum", source=source)
        self.assertEqual(self.state.data["state"], "success")
        self.assertEqual(self.state.data["verification_source"], source)
        self.assertEqual(self.state.data["last_checksum"], previous)
        self.assertEqual(self.state.data["last_success"], previous)
        self.copier.checksum.assert_awaited_once_with(False, source=source)
        self.copier.plan_checks.assert_called_once_with(
            include_copy=False, include_full=True, test=False, source=source
        )
        self.copier.copy.assert_not_awaited()
        self.copier.prune_history.assert_not_called()
        self.api.create_l2.assert_not_awaited()
        self.disks.safe_remove.assert_awaited_once()
        self.api.notify.assert_not_awaited()

    async def test_invalid_scopes_are_rejected_before_starting_any_work(self):
        self.engine.cfg["execution_mode"] = "production"
        for mode, source in [
            ("backup", self.cfg["source_mount"]),
            ("backup_checksum", self.cfg["source_mount"]),
            ("checksum", "/etc"),
            ("checksum", "/media/unknown"),
            ("checksum", self.cfg["source_mount"] + "/.."),
            ("checksum", self.cfg["source_mount"] + "/"),
            ("checksum", [self.cfg["source_mount"]]),
            ("checksum", False),
        ]:
            with self.subTest(mode=mode, source=source), self.assertRaises(ValueError):
                self.engine.start(mode, source=source)
        self.assertFalse(self.engine.busy)
        self.api.power.assert_not_awaited()
        self.copier.checksum.assert_not_awaited()

    async def test_full_manual_checksum_still_advances_global_date(self):
        self.engine.cfg["execution_mode"] = "production"
        await self.run_engine("checksum")
        self.assertEqual(self.state.data["state"], "success")
        self.assertIsNotNone(self.state.data["last_checksum"])
        self.assertIsNone(self.state.data["verification_source"])
        self.copier.checksum.assert_awaited_once_with(False, source=None)

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
