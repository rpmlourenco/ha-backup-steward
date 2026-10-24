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
            if args[:3] == ("lsblk", "-ndo", "PKNAME"):
                return "sdb"
            if args[:3] == ("lsblk", "-ndo", "TYPE"):
                return "disk"
            if args[:2] == ("lsblk", "--json"):
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
            detach.assert_called_once_with("1\n", encoding="ascii")

    async def test_final_kernel_flush_confirmation_allows_removal(self):
        await self.removal_fixture("sd 1:0:0:0: [sdb] Synchronizing SCSI cache")

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
