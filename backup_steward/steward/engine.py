"""One serialized, durable workflow; delivered L2 and completed L3 are separate outcomes."""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
import secrets
import shutil
import os

from .config import public_config, verification_source
from .disks import Disks, Host
from .schedule import checksum_due
from .storage import Copier, confined, prune_l2
from .supervisor import APIError, JobFailed
from . import telemetry
from .integrity import IntegrityFailure
from .cancellation import RunCancelled, RunControl


class Engine:
    def __init__(self, cfg, state, supervisor, host_factory=Host, disks_factory=Disks, copier_factory=Copier):
        self.cfg, self.state, self.api = cfg, state, supervisor
        telemetry.protect(cfg.get("encryption_password"))
        self.host_factory, self.disks_factory, self.copier_factory = (
            host_factory,
            disks_factory,
            copier_factory,
        )
        self.lock = asyncio.Lock()
        self.task = None
        self.control = None

    @property
    def busy(self):
        return self.lock.locked() or bool(self.task and not self.task.done())

    def start(self, mode="backup", scheduled_date=None, *, source=None):
        if self.busy:
            raise ValueError("A job is already active")
        if self.cfg["execution_mode"] == "observe":
            raise ValueError("Observation mode never starts backups or controls power")
        if self.state.data.get("needs_cleanup"):
            raise ValueError("An interrupted execution needs recovery first")
        if mode not in ("backup", "backup_only", "backup_checksum", "checksum"):
            raise ValueError("Invalid operation")
        source = verification_source(self.cfg, mode, source)
        self.control = RunControl()
        self.task = asyncio.create_task(self.run(mode, scheduled_date, source=source, control=self.control))
        return self.task

    @property
    def can_cancel(self):
        return bool(self.busy and self.control and self.control.accepting and not self.control.requested)

    def cancel(self):
        if self.busy and self.control and self.control.accepting and self.control.requested:
            return
        if not self.can_cancel:
            raise ValueError("No cancellable backup or verification is active")
        self.control.request()
        self.state.update(cancel_requested=True)
        self.state.event(
            "cancel_requested",
            "Cancelamento pedido; a aguardar um ponto seguro. A criação nativa, se ativa, tem de terminar.",
            update_stage=False,
        )

    async def notify(self, message):
        try:
            await self.api.notify(message)
        except Exception:
            self.state.event("notification_pending", "Notification unavailable; inspect app status")

    def start_destination(self, action):
        from .destination import start

        return start(self, action)

    async def preview_restore(self, sources=None):
        from .restore import preview

        return await preview(self, sources)

    def start_restore(self, token, erase_sources, checksum):
        from .restore import start

        return start(self, token, erase_sources, checksum)

    async def run(self, mode, scheduled_date=None, *, source=None, control=None, restore_options=None):
        async with self.lock:
            control = control or RunControl()
            self.control = control
            cfg = dict(self.cfg)
            source = verification_source(cfg, mode, source)
            state = self.state
            host = self.host_factory(state.identity["instance_id"])
            disks = self.disks_factory(cfg, host)
            is_backup = mode in ("backup", "backup_only", "backup_checksum")
            copier = self.copier_factory(
                cfg, disks, state.event, lambda report: state.update(integrity=report)
            )
            if mode == "restore":
                from .restore import Restorer

                copier = Restorer(
                    cfg, disks, state.event, lambda report: state.update(integrity=report), restore_options
                )
            run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            checksum = mode in ("backup_checksum", "checksum") or (
                mode == "backup"
                and checksum_due(datetime.now(timezone.utc), cfg, state.data.get("last_checksum"))
            )
            if mode == "restore":
                checksum = restore_options["checksum"]
            test = cfg["execution_mode"] == "test"
            cleanup_needed = False
            backup_pending = False
            try:
                state.update(
                    state="running",
                    destination_manual=False,
                    stage="preflight",
                    run_id=run_id,
                    mode=mode,
                    verification_source=source,
                    restore_areas=[a["source"] for a in restore_options["areas"]] if restore_options else [],
                    restore_checksum=checksum if mode == "restore" else None,
                    execution_mode=cfg["execution_mode"],
                    events=[],
                    l1="pending" if is_backup and cfg["l1_enabled"] else "skipped",
                    l1_retention="pending" if is_backup and cfg["l1_enabled"] else "not_applicable",
                    l1_removed=None,
                    l1_slug=None,
                    l1_file=None,
                    l1_sha256=None,
                    backup_kind="l2",
                    destination_owned=False,
                    l2="pending" if is_backup else "skipped",
                    l3="pending",
                    retention="pending" if is_backup else "not_applicable",
                    l2_removed=None,
                    config_snapshot=public_config(cfg),
                    error=None,
                    error_detail=None,
                    backup_job=None,
                    backup_name=None,
                    backup_slug=None,
                    safe_to_power_off=False,
                    backup_pending=False,
                    failure_stage=None,
                    integrity=None,
                    cancel_requested=control.requested,
                )
                if mode != "restore":
                    copier.plan_checks(
                        include_copy=is_backup, include_full=checksum, test=test, source=source
                    )
                control.checkpoint()
                state.event("preflight", "A validar permissões, ausência de concorrência, discos e journal")
                if is_backup:
                    state.event("native_wait", "A aguardar disponibilidade do gestor nativo de backups")
                    await control.interruptible(self.api.wait_native_idle(cfg["backup_timeout_minutes"]))
                await self.api.preflight(cfg)
                await host.acquire()
                await disks.validate_source()
                await disks.validate_journal()
                if await self.api.core_state(cfg["power_entity"]) != "off" or await disks.mounted_on_host():
                    raise RuntimeError("Destination is already powered or mounted; refusing to interfere")
                if scheduled_date:
                    state.update(last_schedule_date=scheduled_date)
                control.checkpoint()
                if is_backup and cfg["l1_enabled"]:
                    from .native_l1 import run as run_l1

                    await run_l1(cfg, state, self.api, disks, control, run_id, test)
                    control.checkpoint()
                    await self.api.preflight(cfg)
                    if (
                        await self.api.core_state(cfg["power_entity"]) != "off"
                        or await disks.mounted_on_host()
                    ):
                        raise RuntimeError("Destination changed during L1; refusing to interfere")
                # Record ownership before the first side effect; recovery fails safe on uncertain outcomes.
                cleanup_needed = True
                state.update(needs_cleanup=True, destination_owned=True)
                state.event("power", "A ligar a tomada do destino")
                await self.api.power(cfg["power_entity"], True)
                state.event(
                    "mount",
                    "A montar e validar o L3 em modo de leitura"
                    if mode == "restore"
                    else "A montar e validar o disco de destino",
                )
                if mode == "restore":
                    await disks.mount(read_only=True)
                else:
                    await disks.mount()
                state.event("mount_ready", "Destino montado e acessível; a verificar espaço livre")
                if (
                    mode != "restore"
                    and shutil.disk_usage(cfg["destination_mount"]).free < cfg["min_free_gib"] * 1024**3
                ):
                    raise RuntimeError("Destination free space is below policy")
                control.checkpoint()
                if mode == "restore":
                    state.event(
                        "restore_preflight", "A validar todos os espelhos, áreas e espaço antes de apagar"
                    )
                    await control.interruptible(copier.prepare())
                    await control.interruptible(copier.restore())
                if is_backup:
                    state.event("l2_create", "A criar o L2 nativo, comprimido e encriptado")
                    name = f"HA Backup Steward L2 {run_id} {secrets.token_hex(4)}"
                    state.update(backup_name=name, backup_kind="l2", backup_job=None)
                    # Persist pending before POST: a lost HTTP response must not trigger unmount while HA is backing up.
                    backup_pending = True
                    state.update(backup_pending=True)
                    job = await self.api.create_l2(cfg, name)
                    state.update(backup_job=job)
                    state.event("l2_wait", f"A aguardar a criação nativa; trabalho={job}")
                    await self.api.wait_job(job, cfg["backup_timeout_minutes"])
                    backup_pending = False
                    state.update(backup_pending=False)
                    control.checkpoint()
                    state.event("l2_created", "Criação nativa concluída; a confirmar o arquivo encriptado")
                    slug, info = await self.api.find_backup(name)
                    state.update(backup_slug=slug)
                    directory = Path(cfg["source_mount"]) / (
                        cfg["test_directory"] + "/level-2" if test else cfg["l2_directory"]
                    )
                    confined(directory, Path(cfg["source_mount"]))
                    await disks.validate_source()
                    directory.mkdir(parents=True, exist_ok=True)
                    state.event(
                        "l2_download",
                        "A transferir o L2; a retenção só é permitida depois da entrega confirmada",
                    )
                    async with asyncio.timeout(cfg["backup_timeout_minutes"] * 60):
                        path, digest = await control.interruptible(
                            self.api.download(
                                slug, directory, f"steward_l2_{run_id}_{slug}.tar", info.get("size_bytes")
                            )
                        )
                    state.update(l2="delivered", l2_file=path.name, l2_sha256=digest)
                    control.checkpoint()
                    # Native deletion happens strictly after durable delivery. Any warning is visible.
                    try:
                        await self.api.delete_temporary(slug)
                    except Exception:
                        state.event(
                            "temporary_retained", "L2 delivered; native temporary copy could not be removed"
                        )
                    control.checkpoint()
                    if not test:
                        try:
                            await disks.validate_source()
                            state.event("l2_retention", "A aplicar a retenção dos arquivos L2 entregues")
                            removed = await asyncio.to_thread(prune_l2, directory, cfg["l2_keep"], slug)
                            state.update(retention="success", l2_removed=len(removed))
                        except Exception:
                            state.update(retention="failed")
                            await self.notify(
                                "L2 was delivered, but archive retention failed. L3 will continue."
                            )
                    else:
                        state.update(retention="disabled_in_test")
                    control.checkpoint()
                    state.event("l3_copy", "A copiar e verificar o L3")
                    await control.interruptible(copier.copy(run_id, test))
                control.checkpoint()
                if checksum:
                    scope = (
                        ", ".join(state.data["restore_areas"])
                        if mode == "restore"
                        else source or "todas as origens"
                    )
                    state.event("checksum", f"A comparar SHA-256: {scope}")
                    await control.interruptible(copier.checksum(test, source=source))
                control.checkpoint()
                copier.finish_checks()
                control.accepting = False
                # A partial or failed report never advances the production calendar.
                if checksum and not test and source is None and mode != "restore":
                    state.update(last_checksum=datetime.now(timezone.utc).isoformat())
                if is_backup and not test:
                    await disks.validate_destination()
                    state.event("history_retention", "A aplicar a retenção do histórico L3")
                    await asyncio.to_thread(copier.prune_history)
                state.update(l3="success")
                state.event("safe_remove", "A sincronizar, desmontar e confirmar o flush SCSI final")
                await disks.safe_remove()
                state.update(safe_to_power_off=True)
                await disks.verify_removed()
                state.event("power_off", "Fecho seguro confirmado; a desligar a tomada")
                await self.api.power(cfg["power_entity"], False)
                cleanup_needed = False
                state.update(
                    needs_cleanup=False,
                    state="success",
                    stage="complete",
                    last_success=(
                        datetime.now(timezone.utc).isoformat()
                        if source is None and mode != "restore"
                        else state.data.get("last_success")
                    ),
                )
            except RunCancelled:
                copier.abort_checks()
                state.update(state="cancelled", error=None, error_detail=None, failure_stage=None)
                state.event(
                    "cancelled", "Execução cancelada a pedido; a concluir o fecho seguro se necessário"
                )
            except asyncio.CancelledError:
                copier.abort_checks()
                state.update(
                    state="interrupted",
                    stage="recovery_required",
                    needs_cleanup=cleanup_needed or bool(state.data.get("backup_pending")),
                    error="App stopped during an execution; recovery must confirm native backup completion",
                )
                raise
            except Exception as error:
                copier.abort_checks()
                if isinstance(error, JobFailed):
                    backup_pending = False
                    state.update(backup_pending=False)
                    if not cleanup_needed:
                        state.update(needs_cleanup=False)
                if state.data.get("l1") in ("pending", "created") and str(
                    state.data.get("stage", "")
                ).startswith("l1_"):
                    state.update(l1="failed")
                if state.data.get("stage") == "l1_retention":
                    state.update(l1_retention="failed")
                # Only our already-sanitized application errors are exposed; never raw HTTP/process output.
                detail = (
                    str(error)
                    if type(error) in (RuntimeError, ValueError, APIError, JobFailed, IntegrityFailure)
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
                state.event("failure_detail", f"Falha na etapa {failed_stage}: {detail[:240]}")
                state.update(
                    state="failed",
                    error=type(error).__name__,
                    error_detail=detail[:240],
                    failure_stage=failed_stage,
                    stage="failed",
                )
                await self.notify("Backup Steward failed. Inspect the stage and recovery status in the app.")
            finally:
                control.accepting = False
                backup_pending = bool(state.data.get("backup_pending"))
                if backup_pending and not cleanup_needed:
                    state.event(
                        "recovery_required",
                        "Estado do L1 por confirmar; novos backups bloqueados até recuperação",
                    )
                if cleanup_needed and backup_pending:
                    state.event(
                        "recovery_required",
                        "Estado do trabalho nativo por confirmar; disco e alimentação mantidos, recuperação necessária",
                    )
                if cleanup_needed and not backup_pending:
                    state.event(
                        "cancel_cleanup"
                        if state.data.get("state") == "cancelled"
                        else "cleanup_after_failure",
                        "A concluir o fecho seguro após o cancelamento"
                        if state.data.get("state") == "cancelled"
                        else "A tentar concluir o fecho seguro após a falha",
                    )
                    try:
                        if not state.data.get("safe_to_power_off"):
                            await disks.safe_remove()
                            state.update(safe_to_power_off=True)
                        await disks.verify_removed()
                        await self.api.power(cfg["power_entity"], False)
                        state.update(needs_cleanup=False)
                        state.event(
                            "cleanup_done",
                            "Fecho seguro concluído após o cancelamento; tomada desligada"
                            if state.data.get("state") == "cancelled"
                            else "Fecho seguro concluído após a falha; tomada desligada",
                        )
                    except Exception as cleanup_error:
                        state.update(needs_cleanup=True)
                        if state.data.get("state") == "cancelled":
                            state.update(state="failed", error="CleanupFailed")
                            await self.notify(
                                "Cancellation could not confirm safe shutdown; recovery is required."
                            )
                        state.event(
                            "recovery_required",
                            "Fecho seguro não confirmado; alimentação mantida: "
                            + telemetry.redact(str(cleanup_error))[:240],
                        )
                if is_backup:
                    state.remember_backup()
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
            manual = self.state.data.get("destination_manual", False)
            try:
                self.state.update(state="recovering", stage="recovery_start")
                self.state.event(
                    "recovery_start",
                    "Controlo manual: a iniciar o fecho seguro do destino"
                    if manual
                    else "A recuperar a execução anterior; não será criado outro backup",
                )
                await host.acquire(recovering=True)
                job = self.state.data.get("backup_job")
                if self.state.data.get("backup_pending"):
                    if not job:
                        raise RuntimeError("Native backup outcome is unknown; manual diagnosis required")
                    self.state.event("recovery_wait", f"A confirmar o fim do trabalho nativo {job}")
                    try:
                        if self.state.data.get("backup_kind") == "l1":
                            await self.api.finish_l1(job, cfg["backup_timeout_minutes"])
                        else:
                            await self.api.wait_job(job, cfg["backup_timeout_minutes"])
                    except JobFailed:
                        pass  # Terminal failure permits cleanup; the native backup is preserved.
                    self.state.update(backup_pending=False)
                await self.api.preflight(cfg)
                if (
                    not manual
                    and self.state.data.get("backup_kind") == "l1"
                    and self.state.data.get("destination_owned") is False
                ):
                    self.state.update(needs_cleanup=False, state="interrupted", stage="recovered")
                    self.state.remember_backup()
                    await host.release()
                    self.state.event("recovered", "L1 nativo terminado; o destino L3 não foi ligado")
                    return
                if not self.state.data.get("safe_to_power_off"):
                    self.state.event(
                        "recovery_shutdown",
                        "A concluir a desmontagem e flush seguros do destino"
                        if manual
                        else "Trabalho nativo terminado; a concluir a desmontagem e flush seguros",
                    )
                    await disks.safe_remove()
                    self.state.update(safe_to_power_off=True)
                await disks.verify_removed()
                await self.api.power(cfg["power_entity"], False)
                self.state.update(
                    needs_cleanup=False,
                    destination_manual=False,
                    state="destination_off" if manual else "interrupted",
                    stage="recovered",
                )
                if not manual:
                    self.state.remember_backup()
                await host.release()
                self.state.event(
                    "recovered",
                    "Controlo manual concluído; destino desligado em segurança"
                    if manual
                    else "Recuperação concluída; destino desligado em segurança",
                )
            except Exception as error:
                detail = telemetry.redact(str(error))[:240]
                self.state.event("failure_detail", f"Recuperação falhou: {detail}")
                self.state.update(
                    state="failed", needs_cleanup=True, error=type(error).__name__, error_detail=detail
                )
                raise
            finally:
                await host.close()
