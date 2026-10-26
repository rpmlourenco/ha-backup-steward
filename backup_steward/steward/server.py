"""Ingress UI and a separate authenticated local API for the HA integration."""

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets

from aiohttp import web

from . import VERSION
from .config import public_config, validate
from .schedule import due, next_run
from .state import atomic_json
from . import telemetry


STATIC = Path(__file__).parent / "static"


def safe_error(error):
    # Only application validation messages are returned. Unexpected exception details stay private.
    return str(error) if isinstance(error, ValueError) else type(error).__name__


def application(engine, ingress=False):
    @web.middleware
    async def authorize(request, handler):
        if ingress:
            trusted = os.environ.get("STEWARD_INGRESS_IP", "172.30.32.2")
            if request.remote != trusted:
                raise web.HTTPForbidden(text="Access this interface through Home Assistant ingress")
            if request.method not in ("GET", "HEAD") and request.headers.get("X-Steward-UI") != "1":
                raise web.HTTPForbidden(text="Missing UI request protection")
        else:
            supplied = request.headers.get("Authorization", "").removeprefix("Bearer ")
            if not secrets.compare_digest(supplied, engine.state.identity["api_token"]):
                raise web.HTTPUnauthorized()
        try:
            return await handler(request)
        except (ValueError, KeyError, TypeError) as error:
            return web.json_response({"error": safe_error(error)}, status=400)

    app = web.Application(middlewares=[authorize], client_max_size=64 * 1024)

    async def status(request):
        now = datetime.now(timezone.utc)
        return web.json_response(
            {
                "version": VERSION,
                "busy": engine.busy,
                "can_cancel": engine.can_cancel,
                "mode": engine.cfg["execution_mode"],
                "state": engine.state.public(busy=engine.busy),
                "schedule_enabled": engine.cfg["schedule_enabled"],
                "schedule_time": engine.cfg["schedule_time"],
                "timezone": engine.cfg["timezone"],
                "checksum_frequency": engine.cfg["checksum_frequency"],
                "next_run": next_run(now, engine.cfg),
                "daily_retention_days": engine.cfg["l2_keep"],
                "destination_mount": engine.cfg["destination_mount"],
                "verification_sources": [engine.cfg["source_mount"]]
                + (engine.cfg["additional_sources"] if engine.cfg["execution_mode"] != "test" else []),
            }
        )

    async def run(request):
        body = await request.json()
        if not isinstance(body, dict) or "mode" not in body or set(body) - {"mode", "source"}:
            raise ValueError("Expected operation mode and optional configured source")
        if "source" in body:
            if not isinstance(body["source"], str):
                raise ValueError("Source must be a configured path")
            engine.start(body["mode"], source=body["source"])
        else:
            engine.start(body["mode"])
        return web.json_response({"accepted": True}, status=202)

    async def recover(request):
        if engine.busy:
            raise ValueError("Another operation is active")
        if engine.cfg["execution_mode"] == "observe":
            raise ValueError("Observation mode never performs recovery operations")
        # Serialize recovery before returning, so a backup cannot start while cleanup is pending.
        engine.task = asyncio.create_task(engine.recover())

        def consume_result(task):
            try:
                task.result()
            except Exception:
                engine.state.event("recovery_required", "Recovery could not confirm safe shutdown")

        engine.task.add_done_callback(consume_result)
        return web.json_response({"accepted": True}, status=202)

    async def cancel(request):
        body = await request.json()
        if body != {}:
            raise ValueError("Cancel accepts an empty object only")
        engine.cancel()
        return web.json_response({"accepted": True}, status=202)

    app.router.add_post("/v1/cancel", cancel)
    app.router.add_get("/v1/status", status)
    app.router.add_post("/v1/run", run)
    app.router.add_post("/v1/recover", recover)

    async def destination(request):
        body = await request.json()
        if not isinstance(body, dict) or set(body) != {"action"}:
            raise ValueError("Expected only the destination action")
        engine.start_destination(body["action"])
        return web.json_response({"accepted": True}, status=202)

    app.router.add_post("/v1/destination", destination)
    if ingress:

        async def index(request):
            return web.FileResponse(STATIC / "index.html")

        async def config(request):
            if request.method == "GET":
                return web.json_response(public_config(engine.cfg))
            if engine.busy or engine.state.data.get("needs_cleanup"):
                raise ValueError("Finish or recover the current operation before editing settings")
            body = await request.json()
            if not isinstance(body, dict) or "api_token" in body:
                raise ValueError("Invalid configuration")
            if body.get("encryption_password") == "":
                body.pop("encryption_password")
            updated = validate(dict(engine.cfg, **body))
            telemetry.protect(updated.get("encryption_password"))
            atomic_json(engine.state.directory / "config.json", updated)
            engine.cfg = updated
            telemetry.announce_policy(updated)
            return web.json_response(public_config(updated))

        async def pairing(request):
            # Available only via ingress, only on an explicit authenticated UI request.
            return web.json_response({"api_token": engine.state.identity["api_token"]})

        app.router.add_get("/", index)
        app.router.add_get("/v1/config", config)
        app.router.add_put("/v1/config", config)
        app.router.add_post("/v1/pair", pairing)
        app.router.add_static("/static/", STATIC)
    return app


async def scheduler(engine):
    while True:
        now = datetime.now(timezone.utc)
        if (
            not engine.busy
            and not engine.state.data.get("needs_cleanup")
            and due(now, engine.cfg, engine.state.data)
        ):
            from zoneinfo import ZoneInfo

            day = now.astimezone(ZoneInfo(engine.cfg["timezone"])).date().isoformat()
            # Claim the calendar date before execution: failed preflight must not loop every minute.
            engine.state.update(last_schedule_date=day)
            try:
                engine.start("backup", day)
            except ValueError:
                engine.state.event("schedule_blocked", "Scheduled operation could not start")
        await asyncio.sleep(30)


def load_config(directory, options=Path("/data/options.json")):
    raw = json.loads(options.read_text(encoding="utf-8")) if options.exists() else {}
    # Secrets and UI settings are private /data state, included in this app's hot backup.
    override = Path(directory) / "config.json"
    if override.exists():
        raw.update(json.loads(override.read_text(encoding="utf-8")))
    return validate(raw)
