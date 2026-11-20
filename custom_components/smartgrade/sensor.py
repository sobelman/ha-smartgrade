"""Power metering sensors for SmartGrade Secure."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import SmartGradeConfigEntry, SmartGradeCoordinator
from .entity import SmartGradeEntity

PARALLEL_UPDATES = 0

# Keys verified live against GET api/v1/devices/{id}/last_usage_stats, which
# returns {volt, amper, watt, watt2, watt3, powerFactor, signalStrength,
# createdAt}. There is no energy (kWh) field in this response.
SENSORS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="watt",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="amper",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SensorEntityDescription(
        key="volt",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="signalStrength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmartGradeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create sensors for every device that returns usage stats."""
    coordinator = entry.runtime_data
    async_add_entities(
        SmartGradeSensor(coordinator, dev_id, description)
        for dev_id, item in coordinator.data.items()
        if item.stats
        for description in SENSORS
        if description.key in item.stats
    )


class SmartGradeSensor(SmartGradeEntity, SensorEntity):
    """A value from the device's latest usage stats."""

    def __init__(
        self,
        coordinator: SmartGradeCoordinator,
        device_id: str,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"

    @property
    def native_value(self) -> float | None:
        """Return the latest reading."""
        data = self.device_data
        if data is None or not data.stats:
            return None
        value = data.stats.get(self.entity_description.key)
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None
