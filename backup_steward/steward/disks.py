"""Host systemd over D-Bus, legacy-compatible lease, and conservative disk removal.

No SSH, Docker socket, host PID namespace or installed host helper is used.
NASRUI automount remains exclusively owned by the existing udev rule.
"""

import asyncio
import json
import os
from pathlib import Path

from .storage import command, confined


class Host:
    def __init__(self, instance_id):
        self.instance_id = instance_id
        self.bus = None
        self.lease_unit = "ha-backup-steward-lease.service"
        self.lease_owned = False

    async def call(self, path, interface, member, signature="", body=None):
        from dbus_fast import Message, MessageType
        from dbus_fast.aio import MessageBus

        if self.bus is None:
            self.bus = await MessageBus(bus_address="unix:path=/run/dbus/system_bus_socket").connect()
        async with asyncio.timeout(30):
            reply = await self.bus.call(
                Message(
                    destination="org.freedesktop.systemd1",
                    path=path,
                    interface=interface,
                    member=member,
                    signature=signature,
                    body=body or [],
                )
            )
        if reply.message_type == MessageType.ERROR:
            if reply.error_name.endswith(("NoSuchUnit", "LoadFailed")):
                return None
            raise RuntimeError("Host systemd request failed: " + reply.error_name)
        return reply.body

    async def unit(self, name):
        result = await self.call(
            "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager", "GetUnit", "s", [name]
        )
        if result is None:
            return None
        values = await self.call(
            result[0], "org.freedesktop.DBus.Properties", "GetAll", "s", ["org.freedesktop.systemd1.Unit"]
        )
        return {k: v.value for k, v in values[0].items()}

    async def start(self, name, properties):
        from dbus_fast import Variant

        await self.call(
            "/org/freedesktop/systemd1",
            "org.freedesktop.systemd1.Manager",
            "StartTransientUnit",
            "ssa(sv)a(sa(sv))",
            [name, "fail", [(k, Variant(t, v)) for k, t, v in properties], []],
        )

    async def stop(self, name):
        await self.call(
            "/org/freedesktop/systemd1",
            "org.freedesktop.systemd1.Manager",
            "StopUnit",
            "ss",
            [name, "replace"],
        )
        for _ in range(30):
            state = await self.unit(name)
            if not state or state["ActiveState"] in ("inactive", "failed"):
                return
            await asyncio.sleep(1)
        raise RuntimeError("Host unit did not stop")

    async def acquire(self, recovering=False):
        description = "HA Backup Steward lease " + self.instance_id
        existing = await self.unit(self.lease_unit)
        if existing and existing["ActiveState"] in ("active", "activating"):
            if recovering and existing.get("Description") == description:
                self.lease_owned = True
                return
            raise RuntimeError("Another Backup Steward instance owns the disks")
        legacy = await self.unit("ha-level3-backup.service")
        if legacy and legacy["ActiveState"] in ("active", "activating", "deactivating"):
            raise RuntimeError("A legacy L3 worker is running")
        # A transient stock flock process shares the exact lock used by the legacy scripts.
        # No executable or permanent unit file is installed on HAOS.
        await self.start(
            self.lease_unit,
            [
                ("Description", "s", description),
                ("Type", "s", "exec"),
                (
                    "ExecStart",
                    "a(sasb)",
                    [
                        [
                            "/usr/bin/flock",
                            [
                                "/usr/bin/flock",
                                "-n",
                                "-x",
                                "/run/lock/ha-level3-hdd.lock",
                                "/usr/bin/sleep",
                                "infinity",
                            ],
                            False,
                        ]
                    ],
                ),
                ("CollectMode", "s", "inactive-or-failed"),
            ],
        )
        await asyncio.sleep(0.3)
        state = await self.unit(self.lease_unit)
        if not state or state["ActiveState"] != "active":
            raise RuntimeError("Could not acquire the host backup lock")
        self.lease_owned = True

    async def release(self):
        if self.lease_owned:
            await self.stop(self.lease_unit)
            self.lease_owned = False

    async def close(self):
        if self.bus:
            self.bus.disconnect()


class Disks:
    def __init__(self, cfg, host):
        self.cfg, self.host = cfg, host

    async def validate(self, mount, uuid):
        path = Path(mount)
        if not path.exists():
            raise RuntimeError("Required disk mount is missing")
        confined(path, path)
        target = await command("findmnt", "-n", "-T", mount, "-o", "TARGET")
        filesystem = await command("findmnt", "-n", "-T", mount, "-o", "FSTYPE")
        device = await command("findmnt", "-n", "-T", mount, "-o", "SOURCE")
        if target != mount or filesystem != "ext4":
            raise RuntimeError("Expected exact ext4 disk mount, refusing underlying internal storage")
        actual = await command("blkid", "-s", "UUID", "-o", "value", device)
        if actual != uuid:
            raise RuntimeError("Unexpected filesystem UUID")
        return device

    async def validate_source(self):
        return await self.validate(self.cfg["source_mount"], self.cfg["source_uuid"])

    async def validate_destination(self):
        return await self.validate(self.cfg["destination_mount"], self.cfg["destination_uuid"])

    async def mount_unit(self):
        # Technical HAOS layout is fixed; user data paths remain validated /media children.
        host_path = "/mnt/data/supervisor" + self.cfg["destination_mount"]
        name = await command("systemd-escape", "--path", "--suffix=mount", host_path)
        return name, host_path

    async def mounted_on_host(self):
        name, _ = await self.mount_unit()
        state = await self.host.unit(name)
        return bool(state and state["ActiveState"] in ("active", "activating", "deactivating"))

    async def mount(self):
        uuid = self.cfg["destination_uuid"]
        device = Path("/dev/disk/by-uuid") / uuid
        for _ in range(self.cfg["device_wait_seconds"]):
            if device.exists():
                break
            await asyncio.sleep(1)
        else:
            raise RuntimeError("Destination disk did not appear")
        if await command("blkid", "-s", "TYPE", "-o", "value", device) != "ext4":
            raise RuntimeError("Destination is not ext4")
        # Refuse to hide files in the internal media directory.
        target = Path(self.cfg["destination_mount"])
        if target.exists() and (target.is_symlink() or not target.is_dir() or any(target.iterdir())):
            raise RuntimeError("Destination mountpoint is not an empty directory")
        name, host_path = await self.mount_unit()
        await self.host.start(
            name,
            [
                ("Description", "s", "HA Backup Steward destination"),
                ("What", "s", str(device)),
                ("Where", "s", host_path),
                ("Type", "s", "ext4"),
                ("Options", "s", "rw,noatime,nosuid,nodev,noexec"),
                ("CollectMode", "s", "inactive-or-failed"),
            ],
        )
        for _ in range(30):
            try:
                await self.validate_destination()
                return
            except RuntimeError:
                await asyncio.sleep(1)
        raise RuntimeError("Host mount is not visible correctly inside the app")

    async def safe_remove(self):
        if await self.mounted_on_host():
            device = await self.validate_destination()
        else:
            # A previous attempt may have unmounted successfully before an interruption.
            # Continue only with the configured physical UUID, never a guessed /dev/sdX.
            device = str((Path("/dev/disk/by-uuid") / self.cfg["destination_uuid"]).resolve(strict=True))
            if await command("blkid", "-s", "UUID", "-o", "value", device) != self.cfg["destination_uuid"]:
                raise RuntimeError("Recovery disk UUID differs")
        parent = await command("lsblk", "-ndo", "PKNAME", device)
        if not parent or "/" in parent:
            raise RuntimeError("Unable to identify whole disk")
        disk = Path("/dev") / parent
        if await command("lsblk", "-ndo", "TYPE", disk) != "disk":
            raise RuntimeError("Destination parent is not a disk")
        layout = json.loads(await command("lsblk", "--json", "-o", "PATH,TYPE,UUID", disk))
        children = layout["blockdevices"][0].get("children", [])
        # Dedicated backup disk only: never remove a disk with other partitions or layered storage.
        if (
            len(children) != 1
            or children[0].get("uuid") != self.cfg["destination_uuid"]
            or children[0]["type"] != "part"
        ):
            raise RuntimeError("Safe removal requires one dedicated backup partition")
        await asyncio.to_thread(os.sync)
        name, _ = await self.mount_unit()
        if await self.mounted_on_host():
            await self.host.stop(name)
        if await self.mounted_on_host():
            raise RuntimeError("Destination is still mounted on host")
        if (
            Path(self.cfg["destination_mount"]).exists()
            and await command("findmnt", "-n", "-T", self.cfg["destination_mount"], "-o", "FSTYPE") == "ext4"
        ):
            # /media itself may be ext4; compare exact target before deciding.
            if (
                await command("findmnt", "-n", "-T", self.cfg["destination_mount"], "-o", "TARGET")
                == self.cfg["destination_mount"]
            ):
                raise RuntimeError("Destination is still mounted inside app")
        if await command("blkid", "-s", "UUID", "-o", "value", device) != self.cfg["destination_uuid"]:
            raise RuntimeError("Disk identity changed before cache flush")
        fd = os.open(disk, os.O_RDONLY | os.O_CLOEXEC)
        try:
            await asyncio.to_thread(os.fsync, fd)
        finally:
            os.close(fd)
        # Read-only host journal mapping provides the same kernel confirmation as the old helper.
        mark = await command("journalctl", "-k", "-n", "1", "--show-cursor", "--no-pager", "-o", "cat")
        cursor = next(
            (
                line.removeprefix("-- cursor: ")
                for line in mark.splitlines()
                if line.startswith("-- cursor: ")
            ),
            None,
        )
        if not cursor:
            raise RuntimeError("Cannot mark kernel journal; leave power on")
        removal = Path("/sys/class/block") / parent / "device/delete"
        removal.write_text("1\n", encoding="ascii")
        await asyncio.sleep(5)
        if disk.exists() or Path(device).exists():
            raise RuntimeError("Disk did not leave the kernel; leave power on")
        # Journald can lag after the device disappears; allow a bounded wait.
        for _ in range(15):
            events = await command("journalctl", "-k", "--after-cursor=" + cursor, "--no-pager", "-o", "cat")
            relevant = [line for line in events.splitlines() if f"[{parent}]" in line]
            if any("Synchronize Cache(" in line and "failed" in line for line in relevant):
                raise RuntimeError("Kernel reported failed SCSI cache flush; leave power on")
            if any("Synchronizing SCSI cache" in line for line in relevant):
                target = Path(self.cfg["destination_mount"])
                if target.exists():
                    target.rmdir()
                return
            await asyncio.sleep(1)
        raise RuntimeError("No final kernel flush confirmation; leave power on")

    async def verify_removed(self):
        if (
            await self.mounted_on_host()
            or (Path("/dev/disk/by-uuid") / self.cfg["destination_uuid"]).exists()
        ):
            raise RuntimeError("Destination appeared again after removal; power-off is blocked")
