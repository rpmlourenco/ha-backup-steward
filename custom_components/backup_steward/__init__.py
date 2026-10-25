"""HA entities and manual actions for the independently scheduled Backup Steward app."""

from dataclasses import dataclass
from datetime import timedelta
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ServiceValidationError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CannotConnect, Client, InvalidAuth

DOMAIN = "backup_steward"
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.BUTTON]
LOGGER = logging.getLogger(__name__)


@dataclass
class Runtime:
    api: Client
    coordinator: DataUpdateCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    api = Client(async_get_clientsession(hass), entry.data["url"], entry.data["token"])

    async def update():
        try:
            return await api.status()
        except InvalidAuth as error:
            raise ConfigEntryAuthFailed("Invalid Backup Steward pairing key") from error
        except CannotConnect as error:
            raise UpdateFailed("Backup Steward app is unavailable") from error

    coordinator = DataUpdateCoordinator(
        hass,
        LOGGER,
        name="Backup Steward",
        update_method=update,
        update_interval=timedelta(seconds=15),
        config_entry=entry,
    )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = Runtime(api, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if not hass.services.has_service(DOMAIN, "run"):

        async def run(call: ServiceCall):
            target = hass.config_entries.async_get_entry(call.data["entry_id"])
            if not target or target.domain != DOMAIN or not getattr(target, "runtime_data", None):
                raise ServiceValidationError("Choose a loaded Backup Steward integration")
            try:
                if "source" in call.data:
                    if call.data["mode"] != "checksum":
                        raise ServiceValidationError("Source selection requires checksum mode")
                    await target.runtime_data.api.run(call.data["mode"], source=call.data["source"])
                else:
                    await target.runtime_data.api.run(call.data["mode"])
            except (CannotConnect, InvalidAuth) as error:
                raise ServiceValidationError(
                    "The app could not accept the operation; inspect its status"
                ) from error
            await target.runtime_data.coordinator.async_request_refresh()

        hass.services.async_register(
            DOMAIN,
            "run",
            run,
            schema=vol.Schema(
                {
                    vol.Required("entry_id"): str,
                    vol.Optional("source"): str,
                    vol.Optional("mode", default="backup"): vol.In(["backup", "backup_checksum", "checksum"]),
                }
            ),
        )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and not any(
        other.entry_id != entry.entry_id and getattr(other, "runtime_data", None)
        for other in hass.config_entries.async_entries(DOMAIN)
    ):
        hass.services.async_remove(DOMAIN, "run")
    return unloaded
