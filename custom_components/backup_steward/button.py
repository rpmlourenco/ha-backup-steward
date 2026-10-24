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
        return bool(data["state"].get("needs_cleanup")) == (self.key == "recover")

    async def async_press(self):
        try:
            if self.key == "recover":
                await self.api.recover()
            else:
                await self.api.run(self.key)
        except (CannotConnect, InvalidAuth) as error:
            raise HomeAssistantError("Backup Steward could not accept the operation") from error
        await self.coordinator.async_request_refresh()
