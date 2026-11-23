"""JudoISoftEntity class."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import MEASUREMENT_FIELDS
from .const import ATTRIBUTION
from .coordinator import JudoISoftDataUpdateCoordinator


class JudoISoftEntity(CoordinatorEntity[JudoISoftDataUpdateCoordinator]):
    """JudoISoftEntity class."""

    _attr_attribution = ATTRIBUTION

    def __init__(self, coordinator: JudoISoftDataUpdateCoordinator) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._attr_unique_id = coordinator.config_entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={
                (
                    coordinator.config_entry.domain,
                    coordinator.config_entry.entry_id,
                ),
            },
        )

    @property
    def extra_state_attributes(self) -> dict[str, str | bool | None] | None:
        """Expose freshness for measurements without changing their state type."""
        description = self.entity_description
        if description is None or description.key not in MEASUREMENT_FIELDS:
            return None
        data = self.coordinator.data or {}
        last_success = data.get("last_success", {}).get(description.key)
        return {
            "last_success": last_success,
            "stale": (
                last_success is None
                or not self.coordinator.last_update_success
                or description.key in data.get("stale_fields", ())
            ),
        }
