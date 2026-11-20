"""Binary sensors for SmartGrade Secure: cloud connectivity and firmware."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import (
    SmartGradeConfigEntry,
    SmartGradeCoordinator,
    SmartGradeDeviceData,
)
from .entity import SmartGradeEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class SmartGradeBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a SmartGrade binary sensor."""

    value_fn: Callable[[SmartGradeDeviceData], bool | None]
    exists_fn: Callable[[SmartGradeDeviceData], bool] = lambda _: True
    always_available: bool = False


def _bool(value: object) -> bool | None:
    return None if value is None else bool(value)


BINARY_SENSORS: tuple[SmartGradeBinarySensorDescription, ...] = (
    SmartGradeBinarySensorDescription(
        key="is_online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _bool(data.device.get("is_online")),
        # Must stay available while the device is offline, to show it.
        always_available=True,
    ),
    SmartGradeBinarySensorDescription(
        # upgradeNow from the device object. Its exact meaning is not
        # verified live; it is shown as "firmware update pending".
        key="upgradeNow",
        translation_key="firmware_update",
        device_class=BinarySensorDeviceClass.UPDATE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _bool(data.device.get("upgradeNow")),
        exists_fn=lambda data: "upgradeNow" in data.device,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmartGradeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the binary sensors of every device."""
    coordinator = entry.runtime_data
    async_add_entities(
        SmartGradeBinarySensor(coordinator, dev_id, description)
        for dev_id, item in coordinator.data.items()
        for description in BINARY_SENSORS
        if description.exists_fn(item)
    )


class SmartGradeBinarySensor(SmartGradeEntity, BinarySensorEntity):
    """A read-only on/off value of a SmartGrade device."""

    entity_description: SmartGradeBinarySensorDescription

    def __init__(
        self,
        coordinator: SmartGradeCoordinator,
        device_id: str,
        description: SmartGradeBinarySensorDescription,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"

    @property
    def available(self) -> bool:
        """Connectivity stays available while the device is offline."""
        if self.entity_description.always_available:
            return self.coordinator.last_update_success and self.device_data is not None
        return super().available

    @property
    def is_on(self) -> bool | None:
        """Return the current value."""
        data = self.device_data
        return None if data is None else self.entity_description.value_fn(data)
