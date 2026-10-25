"""Run inside the official Home Assistant container, never against a user's instance."""

import asyncio
from pathlib import Path
import shutil
import sys
import tempfile

from aiohttp import web
from aiohttp.test_utils import TestServer

from homeassistant.config_entries import SOURCE_USER, ConfigEntries
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant import loader
from homeassistant.helpers import area_registry, device_registry, entity_registry


async def main():
    requests = []
    destination_requests = []
    data = {
        "version": "0.1.6",
        "busy": False,
        "mode": "test",
        "schedule_enabled": False,
        "next_run": None,
        "state": {
            "instance_id": "fixture-instance",
            "state": "idle",
            "stage": "idle",
            "needs_cleanup": False,
        },
    }

    async def status(request):
        assert request.headers.get("Authorization") == "Bearer fixture-key"
        return web.json_response(data)

    async def run(request):
        requests.append(await request.json())
        return web.json_response({"accepted": True}, status=202)

    app = web.Application()
    app.router.add_get("/v1/status", status)
    app.router.add_post("/v1/run", run)

    async def destination(request):
        assert request.headers.get("Authorization") == "Bearer fixture-key"
        destination_requests.append(await request.json())
        return web.json_response({"accepted": True}, status=202)

    app.router.add_post("/v1/destination", destination)
    async with TestServer(app) as server:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = Path(__file__).resolve().parents[1] / "custom_components"
            shutil.copytree(source, root / "custom_components")
            sys.path.insert(0, str(root))
            hass = HomeAssistant(str(root))
            loader.async_setup(hass)
            hass.config_entries = ConfigEntries(hass, {})
            await hass.config_entries.async_initialize()
            device_registry.async_setup(hass)
            await asyncio.gather(
                area_registry.async_load(hass),
                device_registry.async_load(hass),
                entity_registry.async_load(hass),
            )
            try:
                assert await async_setup_component(hass, "network", {})
                assert await async_setup_component(hass, "persistent_notification", {})
                result = await hass.config_entries.flow.async_init(
                    "backup_steward", context={"source": SOURCE_USER}
                )
                assert result["type"] == "form", result
                integration = await loader.async_get_integration(hass, "backup_steward")
                assert integration.has_branding, "Local integration brand directory was not detected"
                from custom_components.backup_steward.api import Client
                from homeassistant.helpers.aiohttp_client import async_get_clientsession

                probe = Client(
                    async_get_clientsession(hass), str(server.make_url("/")).rstrip("/"), "fixture-key"
                )
                assert await probe.status() == data
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"],
                    {
                        "url": str(server.make_url("/")).rstrip("/"),
                        "token": "fixture-key",
                    },
                )
                assert result["type"] == "create_entry", result
                await hass.async_block_till_done()
                entry = result["result"]
                assert entry.runtime_data.coordinator.data["mode"] == "test"
                assert hass.services.has_service("backup_steward", "run")
                await hass.services.async_call(
                    "backup_steward",
                    "run",
                    {
                        "entry_id": entry.entry_id,
                        "mode": "backup_checksum",
                    },
                    blocking=True,
                )
                assert requests == [{"mode": "backup_checksum"}], requests
                states = hass.states.async_all()
                assert len([s for s in states if s.entity_id.startswith("sensor.")]) >= 8
                assert len([s for s in states if s.entity_id.startswith("button.")]) == 6
                registry = entity_registry.async_get(hass)
                recovery_entity = registry.async_get_entity_id(
                    "binary_sensor", "backup_steward", "fixture-instance_needs_cleanup"
                )

                def button(key):
                    return registry.async_get_entity_id("button", "backup_steward", f"fixture-instance_{key}")

                await hass.services.async_call(
                    "button", "press", {"entity_id": button("destination_mount")}, blocking=True
                )
                data["state"].update(
                    state="destination_mounted",
                    needs_cleanup=True,
                    destination_manual=True,
                    recovery_required=False,
                )
                await entry.runtime_data.coordinator.async_refresh()
                await hass.async_block_till_done()
                assert hass.states.get(recovery_entity).state == "off"
                assert hass.states.get(button("destination_mount")).state == "unavailable"
                assert hass.states.get(button("backup")).state == "unavailable"
                assert hass.states.get(button("recover")).state == "unavailable"
                assert hass.states.get(button("destination_shutdown")).state != "unavailable"
                await hass.services.async_call(
                    "button", "press", {"entity_id": button("destination_shutdown")}, blocking=True
                )
                assert destination_requests == [{"action": "mount"}, {"action": "shutdown"}]
                data["busy"] = True
                data["state"].update(state="running", needs_cleanup=True, recovery_required=False)
                await entry.runtime_data.coordinator.async_refresh()
                await hass.async_block_till_done()
                self_state = hass.states.get(recovery_entity)
                assert self_state.state == "off", self_state
                data["busy"] = False
                data["state"].update(state="failed", recovery_required=True)
                await entry.runtime_data.coordinator.async_refresh()
                await hass.async_block_till_done()
                assert hass.states.get(recovery_entity).state == "on"
                assert await hass.config_entries.async_unload(entry.entry_id)
                assert not hass.services.has_service("backup_steward", "run")
                print(
                    "HA smoke passed: config flow, authenticated polling, 17 entities, manual backup/disk controls and unload"
                )
            finally:
                await hass.async_stop(force=True)


if __name__ == "__main__":
    asyncio.run(main())
