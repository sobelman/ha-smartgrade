"""Base entity for SmartGrade Secure."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, device_mac, device_product_id, product_name
from .coordinator import SmartGradeCoordinator, SmartGradeDeviceData


class SmartGradeEntity(CoordinatorEntity[SmartGradeCoordinator]):
    """Common base for SmartGrade entities bound to one device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SmartGradeCoordinator, device_id: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._device_id = device_id
        device = self.device
        product_id = device_product_id(device)
        mac = device_mac(device_id)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            connections={(CONNECTION_NETWORK_MAC, mac)} if mac else set(),
            name=device.get("name") or device_id,
            manufacturer=MANUFACTURER,
            model=product_name(product_id),
            model_id=str(product_id) if product_id is not None else None,
            sw_version=device.get("firmware"),
            serial_number=device_id,
            suggested_area=device.get("location") or None,
        )

    @property
    def device_data(self) -> SmartGradeDeviceData | None:
        """Return the coordinator data for this device."""
        return self.coordinator.data.get(self._device_id)

    @property
    def device(self) -> dict[str, Any]:
        """Return the raw device object from the cloud."""
        data = self.device_data
        return data.device if data else {}

    @property
    def available(self) -> bool:
        """Return True if the cloud reports the device online."""
        return (
            super().available
            and self.device_data is not None
            and self.device.get("is_online") is not False
        )
