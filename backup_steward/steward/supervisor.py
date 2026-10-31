"""Supervisor and Core API adapters; errors never include request bodies or secrets."""

import asyncio
import hashlib
import os
from pathlib import Path
import re

import aiohttp
from . import telemetry


class APIError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class JobFailed(APIError):
    """A native job is definitively terminal, unlike a timeout or lost response."""


class Supervisor:
    def __init__(self, session, token, base="http://supervisor"):
        telemetry.protect(token)
        self.session = session
        self.headers = {"Authorization": f"Bearer {token}"}
        self.base = base.rstrip("/")

    async def request(self, method, path, body=None):
        async with self.session.request(
            method,
            self.base + path,
            headers=self.headers,
            json=body,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as response:
            if response.status >= 400:
                raise APIError(
                    f"Supervisor {method} {path.split('?')[0]}: HTTP {response.status}", response.status
                )
            result = await response.json()
            if isinstance(result, dict) and result.get("result") == "error":
                raise APIError("Supervisor rejected the request")
            return result.get("data", result) if isinstance(result, dict) else result

    async def core_state(self, entity):
        result = await self.request("GET", f"/core/api/states/{entity}")
        return result["state"]

    async def power(self, entity, on):
        await self.request(
            "POST", "/core/api/services/switch/turn_" + ("on" if on else "off"), {"entity_id": entity}
        )
        for _ in range(30):
            if await self.core_state(entity) == ("on" if on else "off"):
                return
            await asyncio.sleep(1)
        raise APIError("Power switch did not confirm the requested state")

    async def preflight(self, cfg):
        # /jobs/info is readable with the backup role, but /jobs/<uuid> is not.
        # Probe the exact permission used by polling before touching the HDD.
        try:
            await self.request("GET", "/jobs/00000000000000000000000000000000")
        except APIError as error:
            if error.status != 404:
                raise
        for entity in cfg["legacy_scripts"]:
            try:
                status = await self.core_state(entity)
            except APIError as error:
                if error.status == 404:
                    continue  # The old scripts have been removed after migration.
                raise
            if status != "off":
                raise APIError("A legacy backup script is active or unavailable")
        if await self.core_state("sensor.backup_backup_manager_state") != "idle":
            raise APIError("The native backup manager is busy or unavailable")
        # Auto Backup may be removed after migration; its entity then returns HTTP 404.
        try:
            status = await self.core_state("binary_sensor.auto_backup_backup_status")
        except APIError as error:
            if error.status != 404:
                raise
            status = "missing"
        if status not in ("off", "missing"):
            raise APIError("Auto Backup is busy or unavailable")

    async def core_ws(self, command):
        if command not in ("backup/config/info", "backup/info", "backup/generate_with_automatic_settings"):
            raise ValueError("Unsupported native backup command")
        async with asyncio.timeout(60):
            async with self.session.ws_connect(self.base + "/core/websocket") as ws:
                if (await ws.receive_json()).get("type") != "auth_required":
                    raise APIError("Unexpected Core authentication handshake")
                await ws.send_json({"type": "auth", "access_token": self.headers["Authorization"][7:]})
                if (await ws.receive_json()).get("type") != "auth_ok":
                    raise APIError("Native backup API authentication failed")
                await ws.send_json({"id": 1, "type": command})
                while True:
                    response = await ws.receive_json()
                    if response.get("id") != 1:
                        continue
                    if response.get("success") is not True:
                        raise APIError("Native backup API rejected " + command)
                    return response["result"]

    async def l1_policy(self):
        config = (await self.core_ws("backup/config/info"))["config"]
        selection = config["create_backup"]
        if config["schedule"]["recurrence"] != "never":
            raise APIError("Disable only the native HA backup schedule before enabling L1 in Steward")
        if "hassio.local" not in selection["agent_ids"]:
            raise APIError("Select local storage in native L1 backup settings")
        if (
            not selection.get("password")
            or config.get("agents", {}).get("hassio.local", {}).get("protected") is False
        ):
            raise APIError("Native local L1 must use encryption")
        # Do not return, log or persist native passwords or full configuration.
        return {"local": True, "encrypted": True}

    async def wait_native_idle(self, minutes):
        async with asyncio.timeout(minutes * 60):
            while True:
                status = await self.core_state("sensor.backup_backup_manager_state")
                if status == "idle":
                    return
                if status not in ("create_backup", "blocked", "receive_backup", "restore_backup"):
                    raise APIError("Native backup manager is unavailable")
                await asyncio.sleep(2)

    async def create_l1(self):
        result = await self.core_ws("backup/generate_with_automatic_settings")
        job = result.get("backup_job_id", "")
        if not re.fullmatch(r"[a-f0-9]{32}", job):
            raise APIError("Native L1 did not return a valid job ID")
        return job

    async def finish_l1(self, job, minutes):
        try:
            result = await self.wait_job(job, minutes)
        except JobFailed:
            await self.wait_native_idle(minutes)
            raise
        await self.wait_native_idle(minutes)
        slug = result.get("reference", "")
        if not isinstance(slug, str) or not re.fullmatch(r"[a-f0-9]{8}", slug):
            raise APIError("Cannot correlate the native L1 job with its archive")
        status = await self.core_ws("backup/info")
        matches = [b for b in status["backups"] if b.get("backup_id") == slug]
        if len(matches) != 1:
            raise APIError("Native L1 is not available after completion")
        backup = matches[0]
        if any(backup.get(k) for k in ("failed_agent_ids", "failed_addons", "failed_folders")):
            raise JobFailed("Native L1 finished with failed destinations or contents")
        local = backup.get("agents", {}).get("hassio.local", {})
        if local.get("protected") is not True:
            raise APIError("Native L1 has no encrypted local copy")
        info = await self.request("GET", f"/backups/{slug}/info")
        if info.get("protected") is not True or info.get("compressed") is not True:
            raise APIError("Native L1 archive is not encrypted and compressed")
        return slug, info

    async def notify(self, message):
        await self.request(
            "POST",
            "/core/api/services/persistent_notification/create",
            {
                "notification_id": "backup_steward",
                "title": "HA Backup Steward",
                "message": message,
            },
        )

    async def create_l2(self, cfg, name):
        result = await self.request(
            "POST",
            "/backups/new/partial",
            {
                "name": name,
                "homeassistant": True,
                "addons": "ALL",
                "folders": ["ssl", "share", "addons/local"],
                "password": cfg["encryption_password"],
                "compressed": True,
                "background": True,
                "location": ".local",
            },
        )
        job = result.get("job_id")
        if not job:
            raise APIError("Supervisor did not return a backup job ID")
        telemetry.event(f"Supervisor aceitou a criação L2; trabalho={job}")
        return job

    async def wait_job(self, job, minutes):
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", job):
            raise APIError("Invalid job ID")
        async with asyncio.timeout(minutes * 60):
            while True:
                result = await self.request("GET", "/jobs/" + job)
                if result.get("done"):
                    if result.get("errors"):
                        raise JobFailed("Supervisor backup job failed; inspect Supervisor logs")
                    telemetry.event(f"Trabalho nativo concluído; trabalho={job}")
                    return result
                await asyncio.sleep(2)

    async def find_backup(self, name):
        result = await self.request("GET", "/backups")
        matches = [b for b in result["backups"] if b.get("name") == name]
        if len(matches) != 1:
            raise APIError("Unable to correlate exactly one native backup")
        slug = matches[0]["slug"]
        if not re.fullmatch(r"[a-f0-9]{8}", slug):
            raise APIError("Unexpected backup slug")
        info = await self.request("GET", f"/backups/{slug}/info")
        if info.get("protected") is not True or info.get("compressed") is not True:
            raise APIError("Native backup is not encrypted and compressed")
        return slug, info

    async def download(self, slug, directory, filename, expected_size=None):
        if not re.fullmatch(r"[a-f0-9]{8}", slug) or not re.fullmatch(r"[A-Za-z0-9_.-]+\.tar", filename):
            raise ValueError("Invalid managed backup identity")
        target = Path(directory) / filename
        partial = target.with_suffix(".tar.partial")
        digest = hashlib.sha256()
        length = 0
        # Exclusive creation and no symlink following. An interrupted partial is retained for diagnosis.
        with partial.open("xb") as f:
            async with self.session.get(
                self.base + f"/backups/{slug}/download",
                headers=self.headers,
                timeout=aiohttp.ClientTimeout(total=None, sock_read=120),
            ) as response:
                if response.status != 200:
                    raise APIError(f"Backup download failed: HTTP {response.status}")
                async for chunk in response.content.iter_chunked(1024 * 1024):
                    f.write(chunk)
                    digest.update(chunk)
                    length += len(chunk)
                header_size = response.content_length
            f.flush()
            os.fsync(f.fileno())
        if length == 0 or (expected_size is not None and length != expected_size):
            raise APIError("Downloaded backup size differs from the native backup")
        if header_size is not None and length != header_size:
            raise APIError("Incomplete HTTP download")
        if target.exists() or target.is_symlink():
            raise APIError("Refusing to overwrite an existing archive")
        from .storage import filesystem_call, inspect_archive

        archive = await filesystem_call(inspect_archive, partial)
        if archive["slug"] != slug:
            raise APIError("Downloaded archive has the wrong slug")
        os.replace(partial, target)
        return target, digest.hexdigest()

    async def delete_temporary(self, slug):
        if not re.fullmatch(r"[a-f0-9]{8}", slug):
            raise ValueError("Invalid backup slug")
        # Never remove a cloud or network copy of the same native backup.
        await self.request("DELETE", f"/backups/{slug}", {"location": ".local"})
