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
from .const import DOMAIN, EXTRAS_INTERVAL, SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)

type SmartGradeConfigEntry = ConfigEntry[SmartGradeCoordinator]


@dataclass
class SmartGradeDeviceData:
    """Latest known data for one device."""

    device: dict[str, Any]
    stats: dict[str, Any] | None = None
    timers: list[dict[str, Any]] | None = None
    next_action: dict[str, Any] | None = None
    future_actions: list[dict[str, Any]] | None = None


@dataclass
class _Extras:
    """Slow-changing, read-only schedule data for one device."""

    timers: list[dict[str, Any]] | None = None
    next_action: dict[str, Any] | None = None
    future_actions: list[dict[str, Any]] | None = None


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
        self._extras: dict[str, _Extras] = {}
        self._extras_at = 0.0

    def request_extras_refresh(self) -> None:
        """Re-read the schedule data on the next update (e.g. after a toggle)."""
        self._extras_at = 0.0

    def _extras_due(self, now: float) -> bool:
        """Return True when the schedule data should be re-read."""
        if now - self._extras_at >= EXTRAS_INTERVAL.total_seconds():
            return True
        # A scheduled action has run: fetch the following one right away.
        for extras in self._extras.values():
            ts = _as_float((extras.next_action or {}).get("ts"))
            if ts is not None and ts <= now and now - self._extras_at >= 60:
                return True
        return False

    async def _async_fetch_extras(self, devices: dict[str, dict[str, Any]]) -> None:
        """Fetch timers and scheduled actions (read-only endpoints only)."""
        ids = list(devices)
        need_timers = [i for i in ids if not isinstance(devices[i].get("timers"), list)]
        results = await asyncio.gather(
            *(self.api.get_next_action(i) for i in ids),
            *(self.api.get_future_actions(i) for i in ids),
            *(self.api.get_timers(i) for i in need_timers),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, SmartGradeAuthError):
                raise result
        count = len(ids)
        next_res = results[:count]
        future_res = results[count : 2 * count]
        timer_res = dict(zip(need_timers, results[2 * count :], strict=True))
        extras: dict[str, _Extras] = {}
        for idx, dev_id in enumerate(ids):
            old = self._extras.get(dev_id, _Extras())
            nxt, fut = next_res[idx], future_res[idx]
            timers = timer_res.get(dev_id, old.timers)
            if isinstance(timers, BaseException):
                _LOGGER.debug("No timers for %s: %s", dev_id, timers)
                timers = old.timers
            if isinstance(nxt, BaseException):
                _LOGGER.debug("No next action for %s: %s", dev_id, nxt)
                nxt = old.next_action
            if isinstance(fut, BaseException):
                _LOGGER.debug("No future actions for %s: %s", dev_id, fut)
                fut = old.future_actions
            extras[dev_id] = _Extras(timers=timers, next_action=nxt, future_actions=fut)
        self._extras = extras
        self._extras_at = time.time()

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
            if self._extras_due(time.time()):
                await self._async_fetch_extras(devices)
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
            extras = self._extras.get(dev_id, _Extras())
            timers = device.get("timers")
            data[dev_id] = SmartGradeDeviceData(
                device=device,
                stats=stats,
                timers=timers if isinstance(timers, list) else extras.timers,
                next_action=extras.next_action,
                future_actions=extras.future_actions,
            )
        return data


def _as_float(value: Any) -> float | None:
    """Return value as a float, or None."""
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None
