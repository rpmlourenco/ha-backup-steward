"""One serialized, durable workflow; delivered L2 and completed L3 are separate outcomes."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import secrets
import shutil
import os

from .config import public_config
from .disks import Disks, Host
from .schedule import checksum_due
from .storage import Copier, confined, prune_l2
from .supervisor import APIError, JobFailed


class Engine:
    def __init__(self, cfg, state, supervisor, host_factory=Host, disks_factory=Disks, copier_factory=Copier):
        self.cfg, self.state, self.api = cfg, state, supervisor
        self.host_factory, self.disks_factory, self.copier_factory = (
            host_factory,
            disks_factory,
            copier_factory,
        )
        self.lock = asyncio.Lock()
        self.task = None

    @property
    def busy(self):
        return self.lock.locked() or bool(self.task and not self.task.done())

    def start(self, mode="backup", scheduled_date=None):
        if self.busy:
            raise ValueError("A job is already active")
        if self.cfg["execution_mode"] == "observe":
            raise ValueError("Observation mode never starts backups or controls power")
        if self.state.data.get("needs_cleanup"):
            raise ValueError("An interrupted execution needs recovery first")
        if mode not in ("backup", "backup_checksum", "checksum"):
            raise ValueError("Invalid operation")
        self.task = asyncio.create_task(self.run(mode, scheduled_date))
        return self.task

    async def notify(self, message):
        try:
            await self.api.notify(message)
        except Exception:
            self.state.event("notification_pending", "Notification unavailable; inspect app status")

    async def run(self, mode, scheduled_date=None):
        async with self.lock:
            cfg = dict(self.cfg)
            state = self.state
            host = self.host_factory(state.identity["instance_id"])
            disks = self.disks_factory(cfg, host)
            copier = self.copier_factory(cfg, disks)
            run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            checksum = mode != "backup" or checksum_due(
                datetime.now(timezone.utc), cfg, state.data.get("last_checksum")
            )
            test = cfg["execution_mode"] == "test"
            cleanup_needed = False
            backup_pending = False
            try:
                await self.api.preflight(cfg)
                await host.acquire()
                await disks.validate_source()
                await disks.validate_journal()
                if await self.api.core_state(cfg["power_entity"]) != "off" or await disks.mounted_on_host():
                    raise RuntimeError("Destination is already powered or mounted; refusing to interfere")
                state.update(
                    state="running",
                    stage="power",
                    run_id=run_id,
                    mode=mode,
                    execution_mode=cfg["execution_mode"],
                    events=[],
                    l2="pending" if mode != "checksum" else "skipped",
                    l3="pending",
                    retention="pending",
                    config_snapshot=public_config(cfg),
                    error=None,
                    backup_job=None,
                    backup_name=None,
                    backup_slug=None,
                    safe_to_power_off=False,
                    backup_pending=False,
                    failure_stage=None,
                )
                if scheduled_date:
                    state.update(last_schedule_date=scheduled_date)
                # Record ownership before the first side effect; recovery fails safe on uncertain outcomes.
                cleanup_needed = True
                state.update(needs_cleanup=True)
                await self.api.power(cfg["power_entity"], True)
                await disks.mount()
                if shutil.disk_usage(cfg["destination_mount"]).free < cfg["min_free_gib"] * 1024**3:
                    raise RuntimeError("Destination free space is below policy")
                if mode != "checksum":
                    state.event("l2_create", "Creating encrypted native L2")
                    name = f"HA Backup Steward L2 {run_id} {secrets.token_hex(4)}"
                    state.update(backup_name=name)
                    # Persist pending before POST: a lost HTTP response must not trigger unmount while HA is backing up.
                    backup_pending = True
                    state.update(backup_pending=True)
                    job = await self.api.create_l2(cfg, name)
                    state.update(backup_job=job)
                    await self.api.wait_job(job, cfg["backup_timeout_minutes"])
                    backup_pending = False
                    state.update(backup_pending=False)
                    slug, info = await self.api.find_backup(name)
                    state.update(backup_slug=slug)
                    directory = Path(cfg["source_mount"]) / (
                        cfg["test_directory"] + "/level-2" if test else cfg["l2_directory"]
                    )
                    confined(directory, Path(cfg["source_mount"]))
                    await disks.validate_source()
                    directory.mkdir(parents=True, exist_ok=True)
                    state.event(
                        "l2_download", "Transferring L2; retention is blocked until delivery completes"
                    )
                    async with asyncio.timeout(cfg["backup_timeout_minutes"] * 60):
                        path, digest = await self.api.download(
                            slug, directory, f"steward_l2_{run_id}_{slug}.tar", info.get("size_bytes")
                        )
                    state.update(l2="delivered", l2_file=path.name, l2_sha256=digest)
                    # Native deletion happens strictly after durable delivery. Any warning is visible.
                    try:
                        await self.api.delete_temporary(slug)
                    except Exception:
                        state.event(
                            "temporary_retained", "L2 delivered; native temporary copy could not be removed"
                        )
                    if not test:
                        try:
                            await disks.validate_source()
                            removed = await asyncio.to_thread(prune_l2, directory, cfg["l2_keep"], slug)
                            state.update(retention="success", l2_removed=len(removed))
                        except Exception:
                            state.update(retention="failed")
                            await self.notify(
                                "L2 was delivered, but archive retention failed. L3 will continue."
                            )
                    else:
                        state.update(retention="disabled_in_test")
                    state.event("l3_copy", "Copying and checking L3")
                    await copier.copy(run_id, test)
                if checksum:
                    state.event("checksum", "Comparing complete source and mirror SHA-256 checksums")
                    await copier.checksum(test)
                    # A test checksum cannot satisfy the production checksum calendar.
                    if not test:
                        state.update(last_checksum=datetime.now(timezone.utc).isoformat())
                if mode != "checksum" and not test:
                    await disks.validate_destination()
                    await asyncio.to_thread(copier.prune_history)
                state.update(l3="success")
                state.event("safe_remove", "Synchronizing, unmounting and confirming final SCSI cache flush")
                await disks.safe_remove()
                state.update(safe_to_power_off=True)
                await disks.verify_removed()
                await self.api.power(cfg["power_entity"], False)
                cleanup_needed = False
                state.update(
                    needs_cleanup=False,
                    state="success",
                    stage="complete",
                    last_success=datetime.now(timezone.utc).isoformat(),
                )
                await self.notify("Backup Steward completed. Destination safely removed and power off.")
            except asyncio.CancelledError:
                state.update(
                    state="interrupted",
                    stage="recovery_required",
                    needs_cleanup=cleanup_needed,
                    error="App stopped during an execution; recovery must confirm native backup completion",
                )
                raise
            except Exception as error:
                if isinstance(error, JobFailed):
                    backup_pending = False
                    state.update(backup_pending=False)
                # Only our already-sanitized application errors are exposed; never raw HTTP/process output.
                detail = (
                    str(error)
                    if type(error) in (RuntimeError, ValueError, APIError, JobFailed)
                    else type(error).__name__
                )
                for secret in (
                    cfg.get("encryption_password"),
                    state.identity["api_token"],
                    os.environ.get("SUPERVISOR_TOKEN"),
                ):
                    if secret:
                        detail = detail.replace(secret, "[redacted]")
                failed_stage = state.data.get("stage")
                state.event("failure_detail", detail[:240])
                state.update(
                    state="failed",
                    error=type(error).__name__,
                    error_detail=detail[:240],
                    failure_stage=failed_stage,
                    stage="failed",
                )
                await self.notify("Backup Steward failed. Inspect the stage and recovery status in the app.")
            finally:
                if cleanup_needed and not backup_pending:
                    try:
                        if not state.data.get("safe_to_power_off"):
                            await disks.safe_remove()
                            state.update(safe_to_power_off=True)
                        await disks.verify_removed()
                        await self.api.power(cfg["power_entity"], False)
                        state.update(needs_cleanup=False)
                    except Exception:
                        state.update(needs_cleanup=True)
                        state.event("recovery_required", "Safe shutdown not confirmed; power remains on")
                # Keep the lease during an uncertain native backup or incomplete physical cleanup.
                if not state.data.get("needs_cleanup"):
                    await host.release()
                await host.close()

    async def recover(self):
        other_task = self.task and self.task is not asyncio.current_task() and not self.task.done()
        if self.lock.locked() or other_task or self.cfg["execution_mode"] == "observe":
            raise ValueError("Recovery is unavailable while busy or in observation mode")
        if not self.state.data.get("needs_cleanup"):
            return
        async with self.lock:
            # Use the run's original physical identities even if settings have since changed.
            cfg = dict(self.cfg)
            cfg.update(self.state.data.get("config_snapshot", {}))
            host = self.host_factory(self.state.identity["instance_id"])
            disks = self.disks_factory(cfg, host)
            try:
                await host.acquire(recovering=True)
                job = self.state.data.get("backup_job")
                if self.state.data.get("backup_pending"):
                    if not job:
                        raise RuntimeError("Native backup outcome is unknown; manual diagnosis required")
                    try:
                        await self.api.wait_job(job, cfg["backup_timeout_minutes"])
                    except JobFailed:
                        pass  # Terminal failure permits cleanup; the native backup is preserved.
                    self.state.update(backup_pending=False)
                await self.api.preflight(cfg)
                if not self.state.data.get("safe_to_power_off"):
                    await disks.safe_remove()
                    self.state.update(safe_to_power_off=True)
                await disks.verify_removed()
                await self.api.power(cfg["power_entity"], False)
                self.state.update(needs_cleanup=False, state="interrupted", stage="recovered")
                await host.release()
            finally:
                await host.close()
