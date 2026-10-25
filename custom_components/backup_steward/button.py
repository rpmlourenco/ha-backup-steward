from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError

from .api import CannotConnect, InvalidAuth
from .entity import StewardEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        [
            StewardButton(entry, "backup", "Run backup", "mdi:backup-restore"),
            StewardButton(
                entry, "backup_checksum", "Run backup with full checksum", "mdi:shield-sync-outline"
            ),
            StewardButton(entry, "checksum", "Verify full checksums", "mdi:shield-search"),
            StewardButton(entry, "recover", "Recover safe shutdown", "mdi:harddisk-remove"),
            StewardButton(entry, "destination_mount", "Power on and mount destination", "mdi:harddisk-plus"),
            StewardButton(
                entry, "destination_shutdown", "Unmount and power off destination", "mdi:harddisk-remove"
            ),
        ]
    )


class StewardButton(StewardEntity, ButtonEntity):
    def __init__(self, entry, key, name, icon):
        super().__init__(entry, key, name, icon)
        self.key = key

    @property
    def available(self):
        data = self.coordinator.data
        if not super().available or data["mode"] == "observe" or data["busy"]:
            return False
        owned = bool(data["state"].get("needs_cleanup"))
        if self.key == "destination_shutdown":
            return owned
        if self.key == "recover":
            return bool(data["state"].get("recovery_required", owned))
        return not owned

    async def async_press(self):
        try:
            if self.key == "recover":
                await self.api.recover()
            elif self.key.startswith("destination_"):
                await self.api.destination(self.key.removeprefix("destination_"))
            else:
                await self.api.run(self.key)
        except (CannotConnect, InvalidAuth) as error:
            raise HomeAssistantError("Backup Steward could not accept the operation") from error
        await self.coordinator.async_request_refresh()
