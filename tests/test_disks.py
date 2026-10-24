from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backup_steward"))
from steward.config import DEFAULTS
from steward.disks import Disks, Host


class DiskTests(unittest.IsolatedAsyncioTestCase):
    async def removal_fixture(self, events):
        cfg = dict(DEFAULTS, destination_uuid="fixture-uuid")
        disks = Disks(cfg, AsyncMock())
        disks.mounted_on_host = AsyncMock(side_effect=[True, True, False])
        disks.validate_destination = AsyncMock(return_value="/dev/sdb1")
        disks.mount_unit = AsyncMock(return_value=("target.mount", "/host/target"))

        async def output(*args, **kwargs):
            args = tuple(map(str, args))
            if args[0] == "journalctl":
                self.assertIn("--directory=/var/log/journal", args)
            if args[:3] == ("lsblk", "-ndo", "PKNAME"):
                return "sdb"
            if args[:3] == ("lsblk", "-ndo", "TYPE"):
                return "disk"
            if args[:2] == ("lsblk", "--json"):
                # Match real lsblk: PATH without --tree has no children.
                if "--tree" not in args:
                    return json.dumps(
                        {
                            "blockdevices": [
                                {"path": "/dev/sdb", "type": "disk"},
                                {"path": "/dev/sdb1", "uuid": "fixture-uuid", "type": "part"},
                            ]
                        }
                    )
                return json.dumps(
                    {"blockdevices": [{"children": [{"uuid": "fixture-uuid", "type": "part"}]}]}
                )
            if args[0] == "blkid":
                return "fixture-uuid"
            if "--show-cursor" in args:
                return "kernel record\n-- cursor: fixture-cursor"
            if args[0] == "journalctl":
                return events
            raise AssertionError(args)

        with (
            patch("steward.disks.command", side_effect=output),
            patch("steward.disks.os.sync"),
            patch("steward.disks.os.open", return_value=99),
            patch("steward.disks.os.fsync") as flush,
            patch("steward.disks.os.close"),
            patch("steward.disks.asyncio.sleep", new_callable=AsyncMock),
            patch.object(Path, "exists", return_value=False),
            patch.object(Path, "write_text") as detach,
        ):
            await disks.safe_remove()
            flush.assert_called_once_with(99)
            disks.host.remove_disk.assert_awaited_once_with("sdb")
            detach.assert_not_called()

    async def test_final_kernel_flush_confirmation_allows_removal(self):
        await self.removal_fixture("sd 1:0:0:0: [sdb] Synchronizing SCSI cache")

    async def test_kernel_removal_uses_fixed_host_command_and_binary_stdin(self):
        from dbus_fast import Variant

        host = Host("fixture")
        host.lease_owned = True
        host.start = AsyncMock()
        host.stop = AsyncMock()
        host.unit = AsyncMock(
            side_effect=[{"ActiveState": "activating"}, {"ActiveState": "active", "SubState": "exited"}]
        )
        with patch("steward.disks.asyncio.sleep", new_callable=AsyncMock):
            await host.remove_disk("sdb")
        name, properties = host.start.call_args.args
        values = {key: Variant(signature, value).value for key, signature, value in properties}
        self.assertTrue(name.startswith("ha-backup-steward-remove-"))
        self.assertEqual(
            values["ExecStart"],
            [["/usr/bin/tee", ["/usr/bin/tee", "/sys/class/block/sdb/device/delete"], False]],
        )
        self.assertEqual(values["StandardInputData"], b"1\n")
        self.assertEqual(values["StandardInput"], "data")
        self.assertEqual(values["Type"], "oneshot")
        self.assertTrue(values["RemainAfterExit"])
        host.stop.assert_awaited_once_with(name)

    async def test_kernel_removal_requires_owned_lock_and_safe_device_name(self):
        host = Host("fixture")
        host.start = AsyncMock()
        with self.assertRaisesRegex(RuntimeError, "owned host backup lock"):
            await host.remove_disk("sdb")
        host.lease_owned = True
        for name in ("../sdb", "sdb\n", "sdb/device", "sdb;echo", ""):
            with self.subTest(name=name), self.assertRaises(ValueError):
                await host.remove_disk(name)
        host.start.assert_not_awaited()

    async def test_failed_or_unknown_host_removal_never_reports_success(self):
        for result in ({"ActiveState": "failed"}, None):
            with self.subTest(result=result):
                host = Host("fixture")
                host.lease_owned = True
                host.start = AsyncMock()
                host.stop = AsyncMock()
                host.unit = AsyncMock(return_value=result)
                with patch("steward.disks.asyncio.sleep", new_callable=AsyncMock):
                    with self.assertRaisesRegex(RuntimeError, "leave power on"):
                        await host.remove_disk("sdb")
                host.stop.assert_awaited_once()

    async def test_invalid_partition_layout_blocks_before_sync_unmount_or_detach(self):
        for children in (
            [],
            [{"uuid": "fixture-uuid", "type": "part"}, {"uuid": "other", "type": "part"}],
            [{"uuid": "wrong-uuid", "type": "part"}],
            [{"uuid": "fixture-uuid", "type": "crypt"}],
        ):
            with self.subTest(children=children):
                host = AsyncMock()
                disks = Disks(dict(DEFAULTS, destination_uuid="fixture-uuid"), host)
                disks.journal_root = "/var/log/journal"
                disks.mounted_on_host = AsyncMock(return_value=True)
                disks.validate_destination = AsyncMock(return_value="/dev/sdb1")
                with (
                    patch(
                        "steward.disks.command",
                        new_callable=AsyncMock,
                        side_effect=["sdb", "disk", json.dumps({"blockdevices": [{"children": children}]})],
                    ),
                    patch("steward.disks.os.sync") as sync,
                    patch.object(Path, "write_text") as detach,
                ):
                    with self.assertRaisesRegex(RuntimeError, "dedicated backup partition"):
                        await disks.safe_remove()
                host.stop.assert_not_awaited()
                host.remove_disk.assert_not_awaited()
                sync.assert_not_called()
                detach.assert_not_called()

    async def test_persistent_host_journal_is_selected_explicitly(self):
        disks = Disks(DEFAULTS, AsyncMock())
        with patch(
            "steward.disks.command",
            new_callable=AsyncMock,
            return_value="kernel event\n-- cursor: persistent-cursor",
        ) as read:
            await disks.validate_journal()
        self.assertEqual(disks.journal_root, "/var/log/journal")
        self.assertIn("--directory=/var/log/journal", read.call_args.args)

    async def test_volatile_journal_is_used_when_persistent_journal_is_empty(self):
        disks = Disks(DEFAULTS, AsyncMock())
        with patch(
            "steward.disks.command",
            new_callable=AsyncMock,
            side_effect=["-- No entries --", "kernel event\n-- cursor: volatile-cursor"],
        ):
            await disks.validate_journal()
        self.assertEqual(disks.journal_root, "/run/log/journal")

    async def test_unreadable_journal_falls_back_to_volatile_mapping(self):
        disks = Disks(DEFAULTS, AsyncMock())
        with patch(
            "steward.disks.command",
            new_callable=AsyncMock,
            side_effect=[RuntimeError("unreadable"), "-- cursor: volatile-cursor"],
        ):
            await disks.validate_journal()
        self.assertEqual(disks.journal_root, "/run/log/journal")

    async def test_no_host_journal_blocks_removal_before_unmount(self):
        host = AsyncMock()
        disks = Disks(DEFAULTS, host)
        with patch("steward.disks.command", new_callable=AsyncMock, return_value="-- No entries --"):
            with self.assertRaisesRegex(RuntimeError, "disk operations are blocked"):
                await disks.safe_remove()
        host.stop.assert_not_awaited()

    async def test_kernel_flush_error_blocks_removal_confirmation(self):
        with self.assertRaisesRegex(RuntimeError, "failed SCSI"):
            await self.removal_fixture("[sdb] Synchronizing SCSI cache\n[sdb] Synchronize Cache(10) failed")

    async def test_absent_kernel_confirmation_blocks_removal_confirmation(self):
        with self.assertRaisesRegex(RuntimeError, "No final kernel"):
            await self.removal_fixture("unrelated kernel record")

    async def test_legacy_worker_blocks_host_lease(self):
        host = Host("fixture")
        host.unit = AsyncMock(side_effect=[None, {"ActiveState": "active"}])
        host.start = AsyncMock()
        with self.assertRaisesRegex(RuntimeError, "legacy"):
            await host.acquire()
        host.start.assert_not_awaited()

    async def test_foreign_app_lease_cannot_be_adopted(self):
        host = Host("fixture")
        host.unit = AsyncMock(return_value={"ActiveState": "active", "Description": "another instance"})
        with self.assertRaises(RuntimeError):
            await host.acquire(recovering=True)

    async def test_own_recovery_lease_can_be_adopted(self):
        host = Host("fixture")
        host.unit = AsyncMock(
            return_value={"ActiveState": "active", "Description": "HA Backup Steward lease fixture"}
        )
        await host.acquire(recovering=True)
        self.assertTrue(host.lease_owned)

    async def test_wrong_uuid_prevents_use(self):
        disks = Disks(DEFAULTS, AsyncMock())
        with (
            patch(
                "steward.disks.command",
                new_callable=AsyncMock,
                side_effect=["/media/NASRUI", "ext4", "/dev/sda1", "unexpected"],
            ),
            patch("steward.disks.confined"),
            patch.object(Path, "exists", return_value=True),
        ):
            with self.assertRaisesRegex(RuntimeError, "UUID"):
                await disks.validate("/media/NASRUI", "expected")

    async def test_underlying_internal_filesystem_is_not_accepted_as_mount(self):
        disks = Disks(DEFAULTS, AsyncMock())
        with (
            patch(
                "steward.disks.command",
                new_callable=AsyncMock,
                side_effect=["/media", "ext4", "/dev/mmcblk0p8"],
            ),
            patch("steward.disks.confined"),
            patch.object(Path, "exists", return_value=True),
        ):
            with self.assertRaisesRegex(RuntimeError, "exact ext4"):
                await disks.validate("/media/NASRUI", "expected")

    async def test_host_lease_shares_legacy_lock_using_stock_executables(self):
        host = Host("fixture")
        host.unit = AsyncMock(side_effect=[None, None, {"ActiveState": "active"}])
        host.start = AsyncMock()
        with patch("steward.disks.asyncio.sleep", new_callable=AsyncMock):
            await host.acquire()
        command_properties = host.start.call_args.args[1]
        execution = next(v for k, _, v in command_properties if k == "ExecStart")
        self.assertEqual(execution[0][0], "/usr/bin/flock")
        self.assertIn("/run/lock/ha-level3-hdd.lock", execution[0][1])
        self.assertTrue(host.lease_owned)


if __name__ == "__main__":
    unittest.main()
