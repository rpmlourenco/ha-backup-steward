import asyncio
import os
import signal
from pathlib import Path

import aiohttp
from aiohttp import web

from .engine import Engine
from .server import application, load_config, scheduler
from .state import State
from .supervisor import Supervisor
from . import VERSION, telemetry


async def main():
    telemetry.configure()
    directory = Path(os.environ.get("STEWARD_DATA", "/data/steward"))
    cfg = load_config(directory)
    telemetry.protect(cfg.get("encryption_password"))
    telemetry.configure(cfg["timezone"])
    telemetry.event(f"HA Backup Steward {VERSION} a iniciar")
    state = State(directory)
    if state.data.get("state") in ("running", "recovering", "destination_mounting", "destination_mounted"):
        state.update(state="interrupted", stage="recovery_required")
    telemetry.announce_policy(cfg)
    if state.data.get("needs_cleanup"):
        telemetry.event("Recuperação pendente de uma execução anterior; novos backups bloqueados", level=30)
        if state.data.get("error_detail"):
            telemetry.event(
                f"Última falha: etapa={state.data.get('failure_stage')}; {state.data['error_detail']}",
                state.data.get("run_id"),
                level=30,
            )
    async with aiohttp.ClientSession() as session:
        api = Supervisor(session, os.environ.get("SUPERVISOR_TOKEN", ""))
        engine = Engine(cfg, state, api)
        runners = []
        for ingress, port in ((True, 8099), (False, 8100)):
            runner = web.AppRunner(application(engine, ingress=ingress), access_log=None)
            await runner.setup()
            await web.TCPSite(runner, "0.0.0.0", port).start()
            runners.append(runner)
        telemetry.event("Interface e API prontas; a aguardar pedidos ou a hora da agenda")
        schedule = asyncio.create_task(scheduler(engine))
        stopped = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stopped.set)
        try:
            await stopped.wait()
        finally:
            schedule.cancel()
            await asyncio.gather(schedule, return_exceptions=True)
            if engine.task and not engine.task.done():
                engine.task.cancel()
                await asyncio.gather(engine.task, return_exceptions=True)
            for runner in runners:
                await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
