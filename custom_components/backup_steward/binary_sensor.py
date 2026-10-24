from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity

from .entity import StewardEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        [
            StewardBinarySensor(entry, "busy", "Running", "mdi:backup-restore"),
            StewardBinarySensor(entry, "needs_cleanup", "Recovery required", "mdi:alert-circle-outline"),
            StewardBinarySensor(entry, "schedule_enabled", "App schedule enabled", "mdi:calendar-check"),
        ]
    )


class StewardBinarySensor(StewardEntity, BinarySensorEntity):
    def __init__(self, entry, key, name, icon):
        super().__init__(entry, key, name, icon)
        self.key = key
        if key == "needs_cleanup":
            self._attr_device_class = BinarySensorDeviceClass.PROBLEM

    @property
    def is_on(self):
        if self.key == "needs_cleanup":
            return bool(self.coordinator.data["state"].get(self.key))
        return bool(self.coordinator.data.get(self.key))
