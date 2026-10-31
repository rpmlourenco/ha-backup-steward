from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN


class StewardEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, entry, key, name, icon):
        super().__init__(entry.runtime_data.coordinator)
        self.api = entry.runtime_data.api
        instance = self.coordinator.data["state"]["instance_id"]
        self._attr_unique_id = f"{instance}_{key}"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_device_info = {
            "identifiers": {(DOMAIN, instance)},
            "name": "Backup Steward",
            "manufacturer": "HA Backup Steward",
            "model": "L1/L2/L3 backup app",
            "sw_version": self.coordinator.data["version"],
        }
