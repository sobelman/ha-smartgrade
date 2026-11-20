"""Sensors for SmartGrade Secure: power metering, diagnostics and schedules.

Everything here is read-only. Timers and scheduled actions are only shown;
they are never created, changed or deleted.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import time
from typing import Any

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
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import (
    PRODUCTS,
    THREE_PHASE_PRODUCTS,
    device_mac,
    device_product_id,
    product_name,
    relay_count,
)
from .coordinator import (
    SmartGradeConfigEntry,
    SmartGradeCoordinator,
    SmartGradeDeviceData,
)
from .entity import SmartGradeEntity

PARALLEL_UPDATES = 0

DEVICE_TYPE_OTHER = "other"
DEVICE_TYPE_OPTIONS = [f"product_{pid}" for pid in PRODUCTS] + [DEVICE_TYPE_OTHER]
MAX_UPCOMING = 10


def _float(value: Any) -> float | None:
    """Return a float, or None if the value is missing or not numeric."""
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _timestamp(value: Any) -> datetime | None:
    """Parse an epoch (s or ms) or ISO 8601 value into an aware datetime."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        seconds = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(seconds, UTC)
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return _timestamp(int(text))
        parsed = dt_util.parse_datetime(text)
        if parsed is None:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _stat(key: str) -> Callable[[SmartGradeDeviceData], Any]:
    """Return a getter for a numeric value of last_usage_stats."""

    def getter(data: SmartGradeDeviceData) -> Any:
        return _float((data.stats or {}).get(key))

    return getter


def _has_stat(key: str) -> Callable[[SmartGradeDeviceData], bool]:
    """Return a check: the stat is reported with a real (non-null) value."""

    def check(data: SmartGradeDeviceData) -> bool:
        return bool(data.stats) and data.stats.get(key) is not None

    return check


def _has_key(key: str) -> Callable[[SmartGradeDeviceData], bool]:
    """Return a check: the stats response contains the key (old behaviour)."""

    def check(data: SmartGradeDeviceData) -> bool:
        return bool(data.stats) and key in data.stats

    return check


def _three_phase(data: SmartGradeDeviceData) -> bool:
    return device_product_id(data.device) in THREE_PHASE_PRODUCTS


def _device_type(data: SmartGradeDeviceData) -> str:
    product_id = device_product_id(data.device)
    return f"product_{product_id}" if product_id in PRODUCTS else DEVICE_TYPE_OTHER


def _device_type_attrs(data: SmartGradeDeviceData) -> dict[str, Any]:
    product_id = device_product_id(data.device)
    hebrew = PRODUCTS.get(product_id, (None, None))[1] if product_id else None
    return {
        "product_id": product_id,
        "model": product_name(product_id),
        "model_hebrew": hebrew,
        "relays": relay_count(data.device),
        "switch_type": data.device.get("switch_type"),
    }


def _mac(data: SmartGradeDeviceData) -> str | None:
    dev_id = str(data.device.get("id") or "")
    return device_mac(dev_id) or dev_id or None


def _switches(item: dict[str, Any]) -> dict[str, bool]:
    """Return the relays an action or timer targets: {"switch_1": True, ...}."""
    return {
        f"switch_{n}": bool(item[f"switch_{n}"])
        for n in (1, 2, 3)
        if item.get(f"switch_{n}") is not None
    }


def _timer_summary(timer: dict[str, Any]) -> dict[str, Any]:
    """Return the readable fields of one timer (never its raw internals)."""
    detail = timer.get("timer_detail")
    detail = detail if isinstance(detail, dict) else {}
    summary: dict[str, Any] = {
        "name": timer.get("name"),
        "enabled": bool(timer.get("is_enabled")),
        "kind": timer.get("timer_kind"),
        "days": detail.get("days"),
    }
    for key in (
        "on_time",
        "off_time",
        "on_minutes",
        "off_minutes",
        "start_period",
        "stop_period",
        "from_date",
        "to_date",
    ):
        if detail.get(key) is not None:
            summary[key] = detail[key]
    summary["switches"] = [
        n for n in (1, 2, 3) if detail.get(f"switch_{n}") is not None
    ] or None
    return summary


def _timers_count(data: SmartGradeDeviceData) -> int | None:
    if data.timers is None:
        return None
    return sum(1 for t in data.timers if t.get("is_enabled"))


def _timers_attrs(data: SmartGradeDeviceData) -> dict[str, Any]:
    timers = data.timers or []
    return {"total": len(timers), "timers": [_timer_summary(t) for t in timers]}


def _upcoming(data: SmartGradeDeviceData) -> list[dict[str, Any]]:
    """Return the scheduled actions still in the future, soonest first."""
    now = time.time()
    actions = list(data.future_actions or [])
    if data.next_action:
        actions.append(data.next_action)
    seen: set[tuple[Any, ...]] = set()
    result = []
    for action in sorted(actions, key=lambda a: _float(a.get("ts")) or 0):
        ts = _float(action.get("ts"))
        when = _timestamp(ts)
        if ts is None or when is None or ts < now - 60:
            continue
        switches = _switches(action)
        key = (ts, tuple(sorted(switches.items())))
        if key in seen:
            continue
        seen.add(key)
        result.append({"time": when, "switches": switches})
    return result


def _action_word(switches: dict[str, bool]) -> str | None:
    values = set(switches.values())
    if values == {True}:
        return "on"
    if values == {False}:
        return "off"
    return "mixed" if values else None


def _next_action(data: SmartGradeDeviceData) -> datetime | None:
    upcoming = _upcoming(data)
    return upcoming[0]["time"] if upcoming else None


def _next_action_attrs(data: SmartGradeDeviceData) -> dict[str, Any]:
    upcoming = _upcoming(data)
    if not upcoming:
        return {"action": None, "switches": None, "upcoming": []}
    return {
        "action": _action_word(upcoming[0]["switches"]),
        "switches": upcoming[0]["switches"],
        "upcoming": [
            {
                "time": item["time"].isoformat(),
                "action": _action_word(item["switches"]),
                "switches": item["switches"],
            }
            for item in upcoming[:MAX_UPCOMING]
        ],
    }


@dataclass(frozen=True, kw_only=True)
class SmartGradeSensorDescription(SensorEntityDescription):
    """Describes a SmartGrade sensor."""

    value_fn: Callable[[SmartGradeDeviceData], Any]
    exists_fn: Callable[[SmartGradeDeviceData], bool] = lambda _: True
    enabled_fn: Callable[[SmartGradeDeviceData], bool] | None = None
    attrs_fn: Callable[[SmartGradeDeviceData], dict[str, Any]] | None = None


# Stats keys verified live against GET api/v1/devices/{id}/last_usage_stats:
# {volt, amper, watt, watt2, watt3, powerFactor, signalStrength, createdAt}.
# There is no energy (kWh) field in this response.
SENSORS: tuple[SmartGradeSensorDescription, ...] = (
    SmartGradeSensorDescription(
        key="watt",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_stat("watt"),
        exists_fn=_has_key("watt"),
    ),
    SmartGradeSensorDescription(
        key="watt2",
        translation_key="power_phase_2",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_stat("watt2"),
        exists_fn=_has_stat("watt2"),
        enabled_fn=_three_phase,
    ),
    SmartGradeSensorDescription(
        key="watt3",
        translation_key="power_phase_3",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_stat("watt3"),
        exists_fn=_has_stat("watt3"),
        enabled_fn=_three_phase,
    ),
    SmartGradeSensorDescription(
        key="amper",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=_stat("amper"),
        exists_fn=_has_key("amper"),
    ),
    SmartGradeSensorDescription(
        key="volt",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_stat("volt"),
        exists_fn=_has_key("volt"),
    ),
    SmartGradeSensorDescription(
        # Reported as a 0..1 ratio (unit not verified live).
        key="powerFactor",
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=_stat("powerFactor"),
        exists_fn=_has_stat("powerFactor"),
    ),
    SmartGradeSensorDescription(
        key="signalStrength",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_stat("signalStrength"),
        exists_fn=_has_key("signalStrength"),
    ),
    SmartGradeSensorDescription(
        key="createdAt",
        translation_key="last_reading",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _timestamp((data.stats or {}).get("createdAt")),
        exists_fn=_has_stat("createdAt"),
    ),
    SmartGradeSensorDescription(
        key="firmware",
        translation_key="firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.device.get("firmware"),
        exists_fn=lambda data: data.device.get("firmware") is not None,
    ),
    SmartGradeSensorDescription(
        key="max_on_min",
        translation_key="max_on_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _float(data.device.get("max_on_min")),
        exists_fn=lambda data: data.device.get("max_on_min") is not None,
    ),
    SmartGradeSensorDescription(
        key="mac",
        translation_key="mac_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_mac,
    ),
    SmartGradeSensorDescription(
        key="device_type",
        translation_key="device_type",
        device_class=SensorDeviceClass.ENUM,
        options=DEVICE_TYPE_OPTIONS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_device_type,
        attrs_fn=_device_type_attrs,
    ),
    SmartGradeSensorDescription(
        key="timers",
        translation_key="timers",
        value_fn=_timers_count,
        exists_fn=lambda data: data.timers is not None or relay_count(data.device) > 0,
        attrs_fn=_timers_attrs,
    ),
    SmartGradeSensorDescription(
        key="next_action",
        translation_key="next_action",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_next_action,
        exists_fn=lambda data: relay_count(data.device) > 0,
        attrs_fn=_next_action_attrs,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmartGradeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the sensors that apply to every device."""
    coordinator = entry.runtime_data
    async_add_entities(
        SmartGradeSensor(coordinator, dev_id, description)
        for dev_id, item in coordinator.data.items()
        for description in SENSORS
        if description.exists_fn(item)
    )


class SmartGradeSensor(SmartGradeEntity, SensorEntity):
    """A read-only value of a SmartGrade device."""

    entity_description: SmartGradeSensorDescription

    def __init__(
        self,
        coordinator: SmartGradeCoordinator,
        device_id: str,
        description: SmartGradeSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id)
        self.entity_description = description
        self._attr_unique_id = f"{device_id}_{description.key}"
        data = self.device_data
        if description.enabled_fn is not None and data is not None:
            self._attr_entity_registry_enabled_default = description.enabled_fn(data)

    @property
    def native_value(self) -> Any:
        """Return the current value."""
        data = self.device_data
        return None if data is None else self.entity_description.value_fn(data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes (timer list, upcoming actions, model)."""
        data = self.device_data
        if data is None or self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(data)
