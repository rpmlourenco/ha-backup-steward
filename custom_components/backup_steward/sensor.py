from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity

from .entity import StewardEntity

SENSORS = (
    ("state", "Status", "mdi:shield-check-outline", False),
    ("stage", "Current stage", "mdi:progress-check", False),
    ("l1", "L1 result", "mdi:backup-restore", False),
    ("l2", "L2 result", "mdi:archive-check-outline", False),
    ("l3", "L3 result", "mdi:harddisk", False),
    ("retention", "L2 retention", "mdi:archive-clock-outline", False),
    ("last_success", "Last successful backup", "mdi:check-circle-outline", True),
    ("last_checksum", "Last full checksum", "mdi:shield-search", True),
    ("next_run", "Next scheduled backup", "mdi:calendar-clock", True),
)


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(StewardSensor(entry, *description) for description in SENSORS)


class StewardSensor(StewardEntity, SensorEntity):
    def __init__(self, entry, key, name, icon, timestamp):
        super().__init__(entry, key, name, icon)
        self.key, self.timestamp = key, timestamp
        if timestamp:
            self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        value = (
            self.coordinator.data.get("next_run")
            if self.key == "next_run"
            else self.coordinator.data["state"].get(self.key)
        )
        return datetime.fromisoformat(value) if self.timestamp and value else value

    @property
    def extra_state_attributes(self):
        if self.key != "state":
            return None
        state = self.coordinator.data["state"]
        attributes = {
            k: state.get(k)
            for k in ("run_id", "mode", "execution_mode", "error", "failure_stage", "verification_source")
        }
        report = state.get("integrity") or {}
        attributes.update(
            integrity_status=report.get("status"),
            integrity_errors=report.get("error_count"),
            integrity_limit_reached=report.get("limit_reached"),
            integrity_areas=report.get("areas", []),
        )
        return attributes
