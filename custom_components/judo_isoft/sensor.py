"""Sensor platform for judo_isoft."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfVolume

from .entity import JudoISoftEntity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import JudoISoftDataUpdateCoordinator
    from .data import JudoISoftConfigEntry


ENTITY_DESCRIPTIONS = (
    SensorEntityDescription(
        key="water_total_raw",
        name="Water Total Raw",
        icon="mdi:water",
        native_unit_of_measurement=UnitOfVolume.LITERS,
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SensorEntityDescription(
        key="water_total_soft",
        name="Water Total Soft",
        icon="mdi:water",
        native_unit_of_measurement=UnitOfVolume.LITERS,
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SensorEntityDescription(
        key="salt_range",
        name="Salt Range",
        icon="mdi:calendar-clock",
    ),
    SensorEntityDescription(
        key="salt_quantity",
        name="Salt Quantity",
        icon="mdi:shaker-outline",
    ),
    SensorEntityDescription(
        key="residual_capacity",
        name="Residual Capacity",
        icon="mdi:gauge",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="natural_hardness",
        name="Raw Water Hardness",
        icon="mdi:water",
    ),
    SensorEntityDescription(
        key="residual_hardness",
        name="Residual Hardness",
        icon="mdi:water-check",
    ),
    SensorEntityDescription(
        key="actual_flow_rate",
        name="Actual Flow Rate",
        icon="mdi:water-pump",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,  # noqa: ARG001 Unused function argument: `hass`
    entry: JudoISoftConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    async_add_entities(
        JudoISoftSensor(
            coordinator=entry.runtime_data.coordinator,
            entity_description=entity_description,
        )
        for entity_description in ENTITY_DESCRIPTIONS
    )


class JudoISoftSensor(JudoISoftEntity, SensorEntity):
    """judo_isoft Sensor class."""

    def __init__(
        self,
        coordinator: JudoISoftDataUpdateCoordinator,
        entity_description: SensorEntityDescription,
    ) -> None:
        """Initialize the sensor class."""
        super().__init__(coordinator)
        self.entity_description = entity_description
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{entity_description.key}"
        )

    @property
    def native_value(self) -> int | None:
        """Return the native value of the sensor."""
        return self.coordinator.data.get(self.entity_description.key)
