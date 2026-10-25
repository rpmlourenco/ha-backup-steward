"""Manual destination sessions share the backup lock and durable cleanup path."""

import asyncio

from .config import public_config
from .supervisor import APIError
from . import telemetry


def start(engine, action):
    if action not in ("mount", "shutdown"):
        raise ValueError("Invalid destination action")
    if engine.busy or engine.cfg["execution_mode"] == "observe":
        raise ValueError("Destination control is unavailable while busy or in observation mode")
    owned = bool(engine.state.data.get("needs_cleanup"))
    if (action == "mount" and owned) or (action == "shutdown" and not owned):
        raise ValueError("Mount requires a closed destination; shutdown requires an app-owned destination")
    engine.task = asyncio.create_task(control(engine, action))
    return engine.task


async def control(engine, action):
    if action == "shutdown":
        try:
            await engine.recover()
        except Exception:
            await engine.notify("Backup Steward destination shutdown failed. Inspect app status.")
        return
    async with engine.lock:
        cfg = dict(engine.cfg)
        state = engine.state
        host = engine.host_factory(state.identity["instance_id"])
        disks = engine.disks_factory(cfg, host)
        try:
            state.update(
                state="destination_mounting", destination_manual=False, error=None, error_detail=None
            )
            state.event("destination_preflight", "A validar o controlo manual do destino")
            await engine.api.preflight(cfg)
            await host.acquire()
            await disks.validate_journal()
            if await engine.api.core_state(cfg["power_entity"]) != "off" or await disks.mounted_on_host():
                raise RuntimeError("Destination is already powered or mounted; refusing to interfere")
            # Record ownership and physical identities before power-on. A restart
            # or uncertain response must leave a recoverable shutdown obligation.
            state.update(
                needs_cleanup=True,
                destination_manual=True,
                config_snapshot=public_config(cfg),
                backup_pending=False,
                backup_job=None,
                safe_to_power_off=False,
            )
            state.event("destination_power", "Controlo manual: a ligar a tomada do destino")
            await engine.api.power(cfg["power_entity"], True)
            state.event("destination_mount", "Controlo manual: a montar e validar o destino")
            await disks.mount()
            state.update(state="destination_mounted")
            state.event(
                "destination_ready",
                "Destino ligado e montado manualmente; usa Desmontar e desligar quando terminares",
            )
        except asyncio.CancelledError:
            state.update(state="interrupted")
            state.event(
                "recovery_required", "Controlo manual interrompido; confirmar o fecho seguro do destino"
            )
            raise
        except Exception as error:
            detail = telemetry.redact(
                str(error) if type(error) in (RuntimeError, ValueError, APIError) else type(error).__name__
            )[:240]
            failed_stage = state.data.get("stage")
            state.update(
                state="failed", error=type(error).__name__, error_detail=detail, failure_stage=failed_stage
            )
            state.event("failure_detail", f"Controlo manual falhou na etapa {failed_stage}: {detail}")
            await engine.notify("Backup Steward destination mount failed. Inspect app status.")
        finally:
            if not state.data.get("needs_cleanup"):
                await host.release()
            await host.close()
