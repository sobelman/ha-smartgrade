"""Data update coordinator for SmartGrade Secure."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SmartGradeApi, SmartGradeAuthError, SmartGradeError
from .const import DOMAIN, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)

type SmartGradeConfigEntry = ConfigEntry[SmartGradeCoordinator]


@dataclass
class SmartGradeDeviceData:
    """Latest known data for one device."""

    device: dict[str, Any]
    stats: dict[str, Any] | None = None


class SmartGradeCoordinator(DataUpdateCoordinator[dict[str, SmartGradeDeviceData]]):
    """Poll the SmartGrade cloud for device state and power readings.

    The device list carries the live relay state; power readings come from
    ``last_usage_stats`` (updated by the device about once a minute).
    MQTT push (vernemq.iotechv.com:1883) is intentionally not used yet because
    sharing the app's MQTT client id disconnects the phone (see findings.md).
    """

    config_entry: SmartGradeConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: SmartGradeConfigEntry, api: SmartGradeApi
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.api = api

    async def _async_update_data(self) -> dict[str, SmartGradeDeviceData]:
        """Fetch devices and their latest usage stats."""
        expires = self.api.jwt_expires_at
        if expires and expires <= time.time():
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="token_expired"
            )
        try:
            devices = await self.api.get_all_devices()
            # has_current is unreliable (often null on metering devices), so
            # always ask for stats and keep whatever comes back.
            results = await asyncio.gather(
                *(self.api.get_last_usage_stats(dev_id) for dev_id in devices),
                return_exceptions=True,
            )
        except SmartGradeAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="token_expired"
            ) from err
        except SmartGradeError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        data: dict[str, SmartGradeDeviceData] = {}
        for (dev_id, device), result in zip(devices.items(), results, strict=True):
            if isinstance(result, SmartGradeAuthError):
                raise ConfigEntryAuthFailed(
                    translation_domain=DOMAIN, translation_key="token_expired"
                ) from result
            stats: dict[str, Any] | None = None
            if isinstance(result, BaseException):
                _LOGGER.debug("No usage stats for %s: %s", dev_id, result)
            else:
                stats = result
            data[dev_id] = SmartGradeDeviceData(device=device, stats=stats)
        return data
