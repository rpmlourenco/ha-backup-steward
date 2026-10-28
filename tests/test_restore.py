import asyncio
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backup_steward"))
from steward.config import DEFAULTS
from steward.engine import Engine
from steward.restore import Restorer, selected_pairs, walk_tree
from steward.state import State
from steward.storage import command


class RestoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.live = self.root / "NAS"
        self.other = self.root / "photos"
        self.disk = self.root / "offline"
        for path in (self.live, self.other, self.disk):
            path.mkdir()
        self.cfg = dict(
            DEFAULTS,
            execution_mode="production",
            source_mount=str(self.live),
            destination_mount=str(self.disk),
            additional_sources=[str(self.other)],
            encryption_password="restore-encryption-secret-for-tests",
            power_entity="switch.fixture",
        )
        for live in (self.live, self.other):
            mirror = self.disk / "mirror" / live.name
            mirror.mkdir(parents=True)
            (mirror / "restored.bin").write_bytes(b"saved content")
        self.state = State(self.root / "state")
        self.api, self.host, self.disks = AsyncMock(), AsyncMock(), AsyncMock()
        self.api.core_state.return_value = "off"
        self.disks.mounted_on_host.return_value = False
        self.engine = Engine(self.cfg, self.state, self.api, lambda _: self.host, lambda *_: self.disks)

    async def plan(self, sources=None, checksum=False):
        plan = await self.engine.preview_restore(sources)
        return dict(
            areas=self.engine.restore_plan["areas"],
            erase_sources=[a["source"] for a in plan["areas"] if a["nonempty"]],
            checksum=checksum,
        )

    async def test_missing_area_preview_and_confirmation_do_not_create_directories(self):
        self.other.rmdir()
        plan = await self.engine.preview_restore([str(self.other)])
        self.assertTrue(plan["areas"][0]["create"])
        self.assertFalse(plan["areas"][0]["nonempty"])
        self.assertNotIn("parent_identity", plan["areas"][0])
        self.assertFalse(self.other.exists())
        with patch.object(self.engine, "run", new=AsyncMock()):
            await self.engine.start_restore(plan["token"], [], False)
        self.assertFalse(self.other.exists())
        self.api.power.assert_not_awaited()

    async def test_missing_area_is_created_and_restored_with_optional_checksum(self):
        self.other.rmdir()
        plan = await self.engine.preview_restore([str(self.other)])
        await self.engine.start_restore(plan["token"], [], True)
        self.assertEqual(self.state.data["state"], "success")
        self.assertEqual((self.other / "restored.bin").read_bytes(), b"saved content")
        self.assertEqual(self.state.data["integrity"]["status"], "passed")
        self.assertEqual(len(self.state.data["integrity"]["areas"]), 2)
        self.assertTrue(any(e["stage"] == "restore_create" for e in self.state.data["events"]))
        self.assertFalse(list(self.other.rglob("*.partial")))
        self.api.create_l2.assert_not_awaited()

    async def test_missing_area_is_not_created_when_another_mirror_is_unavailable(self):
        self.other.rmdir()
        options = await self.plan()
        shutil.rmtree(self.disk / "mirror" / self.live.name)
        restorer = Restorer(self.cfg, self.disks, lambda *_: None, lambda *_: None, options)
        with self.assertRaises(ValueError):
            await restorer.prepare()
        self.assertFalse(self.other.exists())

    async def test_missing_area_is_not_created_when_space_is_insufficient(self):
        self.other.rmdir()
        options = await self.plan([str(self.other)])
        restorer = Restorer(self.cfg, self.disks, lambda *_: None, lambda *_: None, options)
        with patch("steward.restore.shutil.disk_usage", return_value=type("Usage", (), {"free": 0})()):
            with self.assertRaises(ValueError):
                await restorer.prepare()
        self.assertFalse(self.other.exists())

    async def test_missing_main_disk_is_never_replaced_with_an_ordinary_directory(self):
        self.live.rmdir()
        with self.assertRaises(ValueError):
            await self.engine.preview_restore([str(self.live)])
        self.assertFalse(self.live.exists())
        self.api.power.assert_not_awaited()

    async def test_missing_area_appearing_after_preview_requires_new_confirmation(self):
        self.other.rmdir()
        plan = await self.engine.preview_restore([str(self.other)])
        self.other.mkdir()
        (self.other / "new.bin").write_bytes(b"keep")
        with self.assertRaises(ValueError):
            self.engine.start_restore(plan["token"], [], False)
        self.assertEqual((self.other / "new.bin").read_bytes(), b"keep")
        self.api.power.assert_not_awaited()

    async def test_changed_parent_blocks_missing_area_creation(self):
        parent = self.root / "areas"
        parent.mkdir()
        live = parent / "photos"
        self.cfg["additional_sources"] = [str(live)]
        plan = await self.engine.preview_restore([str(live)])
        parent.rename(self.root / "previous-areas")
        parent.mkdir()
        with self.assertRaises(ValueError):
            self.engine.start_restore(plan["token"], [], False)
        self.assertFalse(live.exists())

    async def test_missing_area_with_symlink_ancestor_is_rejected(self):
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        self.cfg["additional_sources"] = [str(alias / "absent")]
        with self.assertRaises(ValueError):
            await self.engine.preview_restore([str(alias / "absent")])
        self.assertFalse((self.root / "absent").exists())

    async def test_existing_area_disappearing_returns_validation_error(self):
        plan = await self.engine.preview_restore([str(self.other)])
        self.other.rmdir()
        with self.assertRaises(ValueError):
            self.engine.start_restore(plan["token"], [], False)

    async def test_nested_test_area_is_created_only_inside_the_source(self):
        self.cfg["execution_mode"] = "test"
        mirror = self.disk / self.cfg["test_directory"] / "mirror" / self.live.name
        mirror.mkdir(parents=True)
        (mirror / "saved.bin").write_bytes(b"test backup")
        plan = await self.engine.preview_restore([str(self.live)])
        live = self.live / self.cfg["test_directory"] / "level-2"
        self.assertFalse(live.exists())
        await self.engine.start_restore(plan["token"], [], False)
        self.assertEqual(self.state.data["state"], "success")
        self.assertEqual((live / "saved.bin").read_bytes(), b"test backup")
        self.assertFalse((self.live / "saved.bin").exists())

    async def test_scope_whitelist_and_test_isolation(self):
        self.assertEqual(len(selected_pairs(self.cfg, None)), 2)
        self.assertEqual(selected_pairs(self.cfg, [str(self.other)])[0][1], self.other)
        for invalid in ([], ["/etc"], [str(self.live), str(self.live)], [str(self.live) + "/.."], [False]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                selected_pairs(self.cfg, invalid)
        test = dict(self.cfg, execution_mode="test")
        pair = selected_pairs(test, None)[0]
        self.assertEqual(pair[1], self.live / self.cfg["test_directory"] / "level-2")
        with self.assertRaises(ValueError):
            selected_pairs(test, [str(self.other)])

    async def test_nonempty_requires_exact_confirmation_and_single_use_token(self):
        (self.live / "old").write_text("old")
        plan = await self.engine.preview_restore([str(self.live)])
        self.assertTrue(plan["areas"][0]["nonempty"])
        for token, erase, checksum in [
            ("invalid", [str(self.live)], False),
            (plan["token"], [], False),
            (plan["token"], [str(self.other)], False),
            (plan["token"], [str(self.live)], "false"),
        ]:
            with self.assertRaises(ValueError):
                self.engine.start_restore(token, erase, checksum)
        self.api.power.assert_not_awaited()
        with patch.object(self.engine, "run", new=AsyncMock()) as run:
            await self.engine.start_restore(plan["token"], [str(self.live)], True)
            run.assert_awaited_once()
            with self.assertRaises(ValueError):
                self.engine.start_restore(plan["token"], [str(self.live)], True)
        self.assertEqual((self.live / "old").read_text(), "old")

    async def test_preview_expiry_configuration_change_and_new_data_block_start(self):
        for change in ("expiry", "config", "nonempty", "identity"):
            with self.subTest(change=change):
                self.cfg["history_days"] = 30
                if (self.live / "new").exists():
                    (self.live / "new").unlink()
                plan = await self.engine.preview_restore([str(self.live)])
                if change == "expiry":
                    self.engine.restore_plan["expires"] = 0
                if change == "config":
                    self.cfg["history_days"] = 31
                if change == "nonempty":
                    (self.live / "new").write_text("new")
                if change == "identity":
                    self.engine.restore_plan["areas"][0]["identity"] = [-1, -1]
                with self.assertRaises(ValueError):
                    self.engine.start_restore(plan["token"], [], False)
        self.api.power.assert_not_awaited()

    async def test_every_mirror_and_space_checked_before_any_erasure(self):
        for live in (self.live, self.other):
            (live / "old").write_text("original")
        options = await self.plan()
        restorer = Restorer(self.cfg, self.disks, lambda *_: None, lambda *_: None, options)
        shutil.rmtree(self.disk / "mirror" / self.other.name)
        with self.assertRaises((ValueError, FileNotFoundError)):
            await restorer.prepare()
        self.assertTrue((self.live / "old").exists())
        (self.disk / "mirror" / self.other.name).mkdir()
        with self.assertRaises(ValueError):
            await restorer.prepare()
        self.assertTrue((self.live / "old").exists())
        (self.disk / "mirror" / self.other.name / "large").write_bytes(b"x" * 10000)
        with patch("steward.restore.shutil.disk_usage", return_value=type("Usage", (), {"free": 0})()):
            with self.assertRaisesRegex(ValueError, "Espaço insuficiente"):
                await restorer.prepare()
        self.assertTrue((self.live / "old").exists())

    async def test_changed_area_or_unconfirmed_data_block_before_delete(self):
        options = await self.plan([str(self.live)])
        restorer = Restorer(self.cfg, self.disks, lambda *_: None, lambda *_: None, options)
        (self.live / "new").write_text("keep")
        with self.assertRaises(ValueError):
            await restorer.prepare()
        with self.assertRaises(ValueError):
            await restorer.restore()
        self.assertTrue((self.live / "new").exists())
        options["areas"][0]["identity"] = [-1, -1]
        with self.assertRaises(ValueError):
            await restorer.prepare()

    async def test_symlink_hardlink_special_files_and_mount_boundaries_block_restore(self):
        external = self.root / "external"
        external.write_text("keep")
        link = self.live / "link"
        for kind in ("symlink", "hardlink", "fifo"):
            if kind == "symlink":
                link.symlink_to(external)
            if kind == "hardlink":
                os.link(external, link)
            if kind == "fifo":
                os.mkfifo(link)
            with self.assertRaises(ValueError):
                await walk_tree(self.live)
            link.unlink()
        with patch("steward.restore.nested_mounts", side_effect=ValueError("nested mount")):
            with self.assertRaises(ValueError):
                await walk_tree(self.live, erase=True)
        self.assertEqual(external.read_text(), "keep")

    async def test_actual_rclone_restore_one_area_inplace_without_changing_mirror(self):
        (self.live / "old.bin").write_bytes(b"old")
        (self.other / "untouched").write_text("keep")
        options = await self.plan([str(self.live)], checksum=True)
        reports = []
        restorer = Restorer(self.cfg, self.disks, lambda *_: None, reports.append, options)
        before = {str(p.relative_to(self.disk)): p.read_bytes() for p in self.disk.rglob("*") if p.is_file()}
        await restorer.prepare()
        with patch("steward.restore.command", wraps=command) as calls:
            await restorer.restore()
        copies = [call.args for call in calls.await_args_list if call.args[0] == "rclone"]
        self.assertEqual(len(copies), 1)
        self.assertIn("--inplace", copies[0])
        self.assertNotIn("sync", copies[0])
        self.assertNotIn("--backup-dir", copies[0])
        await restorer.checksum()
        restorer.finish_checks()
        self.assertEqual(reports[-1]["status"], "passed")
        self.assertFalse((self.live / "old.bin").exists())
        self.assertEqual((self.live / "restored.bin").read_bytes(), b"saved content")
        self.assertEqual((self.other / "untouched").read_text(), "keep")
        self.assertFalse(list(self.live.rglob("*.partial")))
        self.assertEqual(
            before,
            {str(p.relative_to(self.disk)): p.read_bytes() for p in self.disk.rglob("*") if p.is_file()},
        )

    async def test_engine_restore_preserves_backup_and_dates_no_l2_no_retention(self):
        previous = "2026-10-26T19:36:31+00:00"
        self.state.update(
            last_success=previous,
            last_checksum=previous,
            last_backup_success=previous,
            last_backup={"run_id": "previous-backup"},
        )
        for checksum in (False, True):
            for live in (self.live, self.other):
                for path in live.iterdir():
                    path.unlink()
            plan = await self.engine.preview_restore()
            with patch("steward.engine.prune_l2") as prune:
                await self.engine.start_restore(plan["token"], [], checksum)
                prune.assert_not_called()
            self.assertEqual(self.state.data["state"], "success")
            self.assertEqual(self.state.data["last_success"], previous)
            self.assertEqual(self.state.data["last_checksum"], previous)
            self.assertEqual(self.state.data["last_backup"], {"run_id": "previous-backup"})
            self.assertEqual(self.state.data["retention"], "not_applicable")
            self.assertEqual(self.state.data["integrity"]["status"], "passed")
            self.assertEqual(len(self.state.data["integrity"]["areas"]), 4 if checksum else 2)
        self.disks.mount.assert_awaited_with(read_only=True)
        self.api.create_l2.assert_not_awaited()
        self.api.notify.assert_not_awaited()
        self.disks.safe_remove.assert_awaited()
        self.api.power.assert_awaited_with(self.cfg["power_entity"], False)
        self.assertFalse((self.disk / "history").exists())

    async def test_cancel_stops_restore_worker_before_safe_removal(self):
        plan = await self.engine.preview_restore([str(self.live)])
        entered = asyncio.Event()
        stopped = asyncio.Event()

        async def copying(*args):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        async def closing():
            self.assertTrue(stopped.is_set())

        self.disks.safe_remove.side_effect = closing
        with patch.object(Restorer, "restore", copying):
            task = self.engine.start_restore(plan["token"], [], False)
            await entered.wait()
            self.engine.cancel()
            await task
        self.assertEqual(self.state.data["state"], "cancelled")
        self.assertFalse(self.state.data["needs_cleanup"])
        self.api.notify.assert_not_awaited()
        self.assertIsNone(self.state.data["last_success"])

    async def test_restore_failure_closes_disk_and_keeps_previous_backup(self):
        self.state.update(last_backup={"run_id": "previous"})
        plan = await self.engine.preview_restore([str(self.live)])
        with patch.object(Restorer, "restore", new=AsyncMock(side_effect=RuntimeError("write failed"))):
            await self.engine.start_restore(plan["token"], [], True)
        self.assertEqual(self.state.data["state"], "failed")
        self.assertEqual(self.state.data["failure_stage"], "restore_preflight")
        self.assertEqual(self.state.data["last_backup"], {"run_id": "previous"})
        self.disks.safe_remove.assert_awaited_once()
        self.assertFalse(self.state.data["needs_cleanup"])
        self.api.notify.assert_awaited_once()

    async def test_mirror_replacement_after_preflight_cannot_erase_live_data(self):
        (self.live / "old").write_text("keep")
        options = await self.plan([str(self.live)])
        restorer = Restorer(self.cfg, self.disks, lambda *_: None, lambda *_: None, options)
        await restorer.prepare()
        mirror = self.disk / "mirror" / self.live.name
        mirror.rename(mirror.with_name("moved"))
        mirror.mkdir()
        (mirror / "other").write_text("other")
        with self.assertRaises(ValueError):
            await restorer.restore()
        self.assertEqual((self.live / "old").read_text(), "keep")

    async def test_unavailable_or_observation_mode_never_prepares_restore(self):
        self.engine.cfg["execution_mode"] = "observe"
        with self.assertRaises(ValueError):
            await self.engine.preview_restore()
        self.engine.cfg["execution_mode"] = "production"
        self.state.update(needs_cleanup=True)
        with self.assertRaises(ValueError):
            await self.engine.preview_restore()
        self.api.power.assert_not_awaited()
