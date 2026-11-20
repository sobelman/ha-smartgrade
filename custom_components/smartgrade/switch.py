"""Switch platform for SmartGrade Secure relays."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import SmartGradeError
from .const import DOMAIN, SWITCH_PRODUCTS
from .coordinator import SmartGradeConfigEntry, SmartGradeCoordinator
from .entity import SmartGradeEntity

PARALLEL_UPDATES = 1


def _switch_count(device: dict[str, Any]) -> int:
    try:
        return min(3, max(1, int(device.get("switch_num") or 1)))
    except (TypeError, ValueError):
        return 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmartGradeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one switch per relay of every supported device."""
    coordinator = entry.runtime_data
    async_add_entities(
        SmartGradeSwitch(coordinator, dev_id, number)
        for dev_id, item in coordinator.data.items()
        if item.device.get("product_id") in SWITCH_PRODUCTS
        for number in range(1, _switch_count(item.device) + 1)
    )


class SmartGradeSwitch(SmartGradeEntity, SwitchEntity):
    """A relay (switch_1..switch_3) of a SmartGrade device."""

    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(
        self, coordinator: SmartGradeCoordinator, device_id: str, number: int
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, device_id)
        self._number = number
        self._key = f"switch_{number}"
        self._attr_unique_id = f"{device_id}_{self._key}"
        custom_name = self.device.get(f"{self._key}_name")
        if custom_name:
            self._attr_name = custom_name
        elif number == 1:
            self._attr_name = None  # main feature: entity takes the device name
        else:
            self._attr_translation_key = "relay"
            self._attr_translation_placeholders = {"number": str(number)}

    @property
    def is_on(self) -> bool | None:
        """Return the relay state from the (live) device list."""
        value = self.device.get(self._key)
        return None if value is None else bool(value)

    async def _async_set(self, on: bool) -> None:
        try:
            await self.coordinator.api.set_switch(self._device_id, on, self._number)
        except SmartGradeError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="switch_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        # The toggle response may echo a stale state, so re-read the device list.
        await self.coordinator.async_request_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the relay on."""
        await self._async_set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the relay off."""
        await self._async_set(False)
