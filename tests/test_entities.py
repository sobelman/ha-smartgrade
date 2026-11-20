"""Tests for the read-only diagnostic, schedule and multi-product entities.

All HTTP traffic is mocked; the switch tests only hit the mocked toggle
endpoint. No real device is ever contacted or switched.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.smartgrade.api import SmartGradeApi, SmartGradeRequestError
from custom_components.smartgrade.const import (
    BASE_URL,
    device_mac,
    device_product_id,
    product_name,
    relay_count,
)
from custom_components.smartgrade.coordinator import SmartGradeDeviceData
from custom_components.smartgrade.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.smartgrade.sensor import _timestamp

from .conftest import (
    APP_TOKEN_RESPONSE,
    DEVICE_ID,
    NEXT_TS,
    STATS_OFF,
    USER_ID,
    device_list,
    make_jwt,
    mock_cloud,
    mock_schedule,
    setup_entry,
)

OTHER_ID = "112233445566"


def _entries(hass: HomeAssistant, entry: MockConfigEntry) -> list[er.RegistryEntry]:
    return er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)


def _other_device(**fields: Any) -> dict[str, Any]:
    device = {**device_list()[0], "id": OTHER_ID, "name": "Other"}
    device.update(fields)
    return device


def _mock_other(
    aioclient_mock: AiohttpClientMocker, stats: dict[str, Any] | None = None
) -> None:
    aioclient_mock.get(
        BASE_URL + f"api/v1/devices/{OTHER_ID}/last_usage_stats",
        json=stats,
        status=200 if stats is not None else 404,
    )
    mock_schedule(aioclient_mock, OTHER_ID, next_payload={}, future_payload=[])


def _calls(aioclient_mock: AiohttpClientMocker, suffix: str) -> int:
    return sum(1 for c in aioclient_mock.mock_calls if str(c[1]).endswith(suffix))


# --- diagnostics / device info ------------------------------------------------------


async def test_diagnostic_entities(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The boiler gets firmware, MAC, type, limits, signal and connectivity."""
    mock_cloud(aioclient_mock)
    await setup_entry(hass, mock_config_entry)

    assert hass.states.get("sensor.boiler_firmware").state == "20260410_var2_v2.9.2"
    assert hass.states.get("sensor.boiler_mac_address").state == "aa:bb:cc:dd:ee:ff"
    assert float(hass.states.get("sensor.boiler_signal_strength").state) == -67
    assert float(hass.states.get("sensor.boiler_maximum_on_time").state) == 120
    assert float(hass.states.get("sensor.boiler_power_factor").state) == 0.0
    last = hass.states.get("sensor.boiler_last_reading").state
    assert datetime.fromisoformat(last) == datetime(2026, 11, 20, 19, 0, tzinfo=UTC)

    device_type = hass.states.get("sensor.boiler_device_type")
    assert device_type.state == "product_8"
    assert device_type.attributes["product_id"] == 8
    assert device_type.attributes["model"] == "Boiler switch (Gewiss 3-module)"
    assert device_type.attributes["model_hebrew"] == "מתג חכם לדוד לגוויס 3 מקומות"
    assert device_type.attributes["relays"] == 1

    assert hass.states.get("binary_sensor.boiler_connectivity").state == STATE_ON
    assert (
        hass.states.get("binary_sensor.boiler_firmware_update_pending").state
        == STATE_OFF
    )

    ent_reg = er.async_get(hass)
    for entity_id in ("sensor.boiler_firmware", "binary_sensor.boiler_connectivity"):
        assert ent_reg.async_get(entity_id).entity_category == "diagnostic"
    # Single-phase boiler: phase 2/3 exist (the cloud reports 0) but are off.
    for phase in ("sensor.boiler_power_phase_2", "sensor.boiler_power_phase_3"):
        assert ent_reg.async_get(phase).disabled_by is not None


async def test_offline_connectivity_stays_available(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """When offline the connectivity sensor reports off, others go unavailable."""
    mock_cloud(aioclient_mock, devices=device_list(online=False))
    await setup_entry(hass, mock_config_entry)
    assert hass.states.get("binary_sensor.boiler_connectivity").state == STATE_OFF
    assert hass.states.get("switch.boiler").state == STATE_UNAVAILABLE
    assert hass.states.get("sensor.boiler_firmware").state == STATE_UNAVAILABLE

    coordinator = mock_config_entry.runtime_data
    coordinator.async_set_updated_data({})
    assert (
        hass.states.get("binary_sensor.boiler_connectivity").state == STATE_UNAVAILABLE
    )


async def test_firmware_update_pending(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """upgradeNow=true shows as a pending firmware update; no field, no entity."""
    devices = device_list()
    devices[0]["upgradeNow"] = True
    without = _other_device()
    del without["upgradeNow"]
    mock_cloud(aioclient_mock, devices=[*devices, without])
    _mock_other(aioclient_mock)
    await setup_entry(hass, mock_config_entry)
    assert (
        hass.states.get("binary_sensor.boiler_firmware_update_pending").state
        == STATE_ON
    )
    assert hass.states.get("binary_sensor.other_firmware_update_pending") is None


async def test_three_phase_stats(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A 3-phase DIN switch gets enabled phase 2/3 power sensors."""
    devices = [{**device_list()[0], "product_id": 15, "name": "Din"}]
    stats = {
        **STATS_OFF,
        "watt": 1000.0,
        "watt2": 1100.0,
        "watt3": 1200.0,
        "powerFactor": 0.97,
    }
    mock_cloud(aioclient_mock, devices=devices, stats=stats)
    await setup_entry(hass, mock_config_entry)
    assert float(hass.states.get("sensor.din_power").state) == 1000.0
    assert float(hass.states.get("sensor.din_power_phase_2").state) == 1100.0
    assert float(hass.states.get("sensor.din_power_phase_3").state) == 1200.0
    assert float(hass.states.get("sensor.din_power_factor").state) == 0.97
    assert hass.states.get("sensor.din_device_type").state == "product_15"


async def test_null_stats_are_not_created(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Null phase 2/3, power factor and timestamp give no empty sensors."""
    stats = {
        **STATS_OFF,
        "watt2": None,
        "watt3": None,
        "powerFactor": None,
        "createdAt": None,
    }
    mock_cloud(aioclient_mock, stats=stats)
    await setup_entry(hass, mock_config_entry)
    unique_ids = {e.unique_id for e in _entries(hass, mock_config_entry)}
    for key in ("watt2", "watt3", "powerFactor", "createdAt"):
        assert f"{DEVICE_ID}_{key}" not in unique_ids
    assert f"{DEVICE_ID}_watt" in unique_ids


# --- timers / scheduled actions (read-only) -----------------------------------------


async def test_timers_sensor(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """The timers sensor counts enabled timers and lists all of them."""
    mock_cloud(aioclient_mock)
    await setup_entry(hass, mock_config_entry)
    state = hass.states.get("sensor.boiler_timers")
    assert state.state == "1"
    assert state.attributes["total"] == 2
    morning, cycle = state.attributes["timers"]
    assert morning == {
        "name": "Morning",
        "enabled": True,
        "kind": "recurring",
        "days": ["sun", "mon", "tue", "wed", "thu"],
        "on_time": "06:00",
        "off_time": "06:50",
        "switches": [1],
    }
    assert cycle["enabled"] is False
    assert cycle["kind"] == "pattern"
    assert cycle["on_minutes"] == 10
    assert cycle["off_minutes"] == 20
    assert cycle["start_period"] == "08:00"
    # Nothing but GETs were sent: timers are never written.
    assert all(c[0] == "GET" for c in aioclient_mock.mock_calls)


async def test_timers_fetched_when_not_embedded(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Without timers[] in the device object, GET /timers is used."""
    devices = device_list()
    del devices[0]["timers"]
    aioclient_mock.get(
        BASE_URL + f"api/v1/devices/{DEVICE_ID}/timers",
        json={
            "timers": [
                {"id": 9, "name": "X", "is_enabled": True, "timer_kind": "normal"},
                "garbage",
            ]
        },
    )
    mock_cloud(aioclient_mock, devices=devices)
    await setup_entry(hass, mock_config_entry)
    state = hass.states.get("sensor.boiler_timers")
    assert state.state == "1"
    assert state.attributes["timers"][0]["switches"] is None
    assert state.attributes["timers"][0]["days"] is None


async def test_timers_endpoint_failure(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A failing /timers keeps the entry working; the sensor is unknown."""
    devices = device_list()
    del devices[0]["timers"]
    aioclient_mock.get(BASE_URL + f"api/v1/devices/{DEVICE_ID}/timers", status=500)
    mock_cloud(aioclient_mock, devices=devices)
    await setup_entry(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.boiler_timers").state == STATE_UNKNOWN


async def test_next_action_sensor(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """next_action/future_actions give the next timestamp and what it does."""
    mock_cloud(aioclient_mock)
    await setup_entry(hass, mock_config_entry)
    state = hass.states.get("sensor.boiler_next_scheduled_action")
    assert datetime.fromisoformat(state.state) == datetime.fromtimestamp(NEXT_TS, UTC)
    assert state.attributes["action"] == "on"
    assert state.attributes["switches"] == {"switch_1": True}
    upcoming = state.attributes["upcoming"]
    assert [u["action"] for u in upcoming] == ["on", "off"]
    # created_by is never exposed (it may identify a person).
    assert "created_by" not in str(state.attributes)


async def test_next_action_none_scheduled(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """No schedule (404 / empty body / past entries) means unknown."""
    past = int(datetime.now(UTC).timestamp()) - 7200
    mock_cloud(
        aioclient_mock,
        next_status=404,
        future_payload=[{"ts": past, "switch_1": True}, {"switch_1": False}],
    )
    await setup_entry(hass, mock_config_entry)
    state = hass.states.get("sensor.boiler_next_scheduled_action")
    assert state.state == STATE_UNKNOWN
    assert state.attributes["upcoming"] == []
    assert state.attributes["action"] is None


async def test_next_action_errors_are_tolerated(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """5xx on the schedule endpoints does not break the entry."""
    mock_cloud(aioclient_mock, next_status=500, future_status=500)
    await setup_entry(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.boiler_next_scheduled_action").state == STATE_UNKNOWN
    assert hass.states.get("switch.boiler").state == STATE_OFF


async def test_schedule_auth_error_starts_reauth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A 401 from next_action triggers reauth like the other endpoints."""
    mock_cloud(aioclient_mock, next_status=401)
    await setup_entry(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]


async def test_schedule_is_cached_and_refreshed_after_toggle(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Schedule endpoints are polled slowly, and again after a (mocked) toggle."""
    mock_cloud(aioclient_mock)
    aioclient_mock.post(
        BASE_URL + f"api/v1/devices/{DEVICE_ID}/toggle_switches", json={}
    )
    await setup_entry(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data
    assert _calls(aioclient_mock, "/next_action") == 1
    await coordinator.async_refresh()
    assert _calls(aioclient_mock, "/next_action") == 1  # cached
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: "switch.boiler"}, blocking=True
    )
    await hass.async_block_till_done()
    assert _calls(aioclient_mock, "/next_action") == 2


async def test_schedule_refetched_when_next_action_passed(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Once the next action is in the past the schedule is read again."""
    past = int(datetime.now(UTC).timestamp()) - 120
    mock_cloud(aioclient_mock, next_payload={"next_action": {"ts": past}})
    await setup_entry(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data
    assert _calls(aioclient_mock, "/next_action") == 1
    coordinator._extras_at -= 120  # pretend the last read was 2 minutes ago
    await coordinator.async_refresh()
    assert _calls(aioclient_mock, "/next_action") == 2


# --- device types ---------------------------------------------------------------------


async def test_multi_relay_light_switch(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A 2-gang light switch gets one switch per relay named from the app."""
    devices = [
        {
            **device_list()[0],
            "product_id": 10,
            "name": "Lights",
            "switch_num": 2,
            "switch_1": True,
            "switch_1_name": "Kitchen",
            "switch_2": False,
            "switch_2_name": "Hall",
        }
    ]
    mock_cloud(aioclient_mock, devices=devices)
    aioclient_mock.post(
        BASE_URL + f"api/v1/devices/{DEVICE_ID}/toggle_switches", json={}
    )
    await setup_entry(hass, mock_config_entry)
    assert hass.states.get("switch.lights_kitchen").state == STATE_ON
    assert hass.states.get("switch.lights_hall").state == STATE_OFF
    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_ON,
        {ATTR_ENTITY_ID: "switch.lights_hall"},
        blocking=True,
    )
    posts = [c for c in aioclient_mock.mock_calls if c[0] == "POST"]
    assert posts[-1][2] == {"switch_2": True}
    device = dr.async_entries_for_config_entry(
        dr.async_get(hass), mock_config_entry.entry_id
    )[0]
    assert device.model == "Light switch, 2 gangs (Gewiss)"


async def test_shutter_relays_disabled_by_default(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Shutter relays are exposed as opt-in switches (no cover entity)."""
    devices = [{**device_list()[0], "product_id": 18, "switch_num": 2}]
    mock_cloud(aioclient_mock, devices=devices)
    await setup_entry(hass, mock_config_entry)
    switches = [e for e in _entries(hass, mock_config_entry) if e.domain == "switch"]
    assert len(switches) == 2
    assert all(e.disabled_by is not None for e in switches)
    assert not any(e.domain == "cover" for e in _entries(hass, mock_config_entry))


async def test_non_relay_and_unknown_products(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Sensors get diagnostics only; unknown products get relays if reported."""
    sensor = _other_device(product_id=26, name="Motion", switch_1=None)
    unknown = {
        **device_list()[0],
        "id": "A1B2C3D4E5F6",
        "name": "Mystery",
        "product_id": 99,
        "switch_1": True,
    }
    unknown_no_relay = {
        **device_list()[0],
        "id": "not-a-mac",
        "name": "Blob",
        "product_id": None,
        "switch_1": None,
    }
    mock_cloud(aioclient_mock, devices=[sensor, unknown, unknown_no_relay])
    _mock_other(aioclient_mock)
    for dev_id in ("A1B2C3D4E5F6", "not-a-mac"):
        aioclient_mock.get(
            BASE_URL + f"api/v1/devices/{dev_id}/last_usage_stats", status=404
        )
        mock_schedule(aioclient_mock, dev_id, next_payload={}, future_payload=[])
    await setup_entry(hass, mock_config_entry)

    unique_ids = {e.unique_id for e in _entries(hass, mock_config_entry)}
    assert f"{OTHER_ID}_switch_1" not in unique_ids
    assert f"{OTHER_ID}_next_action" not in unique_ids
    assert f"{OTHER_ID}_is_online" in unique_ids
    assert hass.states.get("sensor.motion_device_type").state == "product_26"

    assert hass.states.get("switch.mystery").state == STATE_ON
    assert hass.states.get("sensor.mystery_device_type").state == "other"
    assert "not-a-mac_switch_1" not in unique_ids
    assert hass.states.get("sensor.blob_mac_address").state == "not-a-mac"

    models = {
        d.name: d.model
        for d in dr.async_entries_for_config_entry(
            dr.async_get(hass), mock_config_entry.entry_id
        )
    }
    assert models["Mystery"] == "SmartGrade device (product 99)"
    assert models["Motion"] == "Smart motion sensor"


async def test_diagnostics_include_schedule_without_personal_data(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Diagnostics carry timers/next action but redact created_by."""
    mock_cloud(aioclient_mock)
    await setup_entry(hass, mock_config_entry)
    diag = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    item = diag["devices"][DEVICE_ID]
    assert item["next_action"]["ts"] == NEXT_TS
    assert item["next_action"]["created_by"] == "**REDACTED**"
    assert len(item["timers"]) == 2


# --- helpers / API ------------------------------------------------------------------


def test_helpers() -> None:
    """Pure helpers: MAC, product id, names, relays and timestamps."""
    assert device_mac("AABBCCDDEEFF") == "aa:bb:cc:dd:ee:ff"
    assert device_mac("AA:BB:CC:DD:EE:FF") == "aa:bb:cc:dd:ee:ff"
    assert device_mac("xyz") is None
    assert device_mac("GGBBCCDDEEFF") is None
    assert device_product_id({"product_id": "8"}) == 8
    assert device_product_id({"product_id": "x"}) is None
    assert device_product_id({}) is None
    assert product_name(None) == "SmartGrade device (product None)"
    assert relay_count({"product_id": 8, "switch_num": 9}) == 3
    assert relay_count({"product_id": 8, "switch_num": "x"}) == 1
    assert relay_count({"product_id": 23}) == 0

    expected = datetime(2026, 11, 20, 19, 0, tzinfo=UTC)
    epoch = int(expected.timestamp())
    assert _timestamp(epoch) == expected
    assert _timestamp(epoch * 1000) == expected
    assert _timestamp(str(epoch)) == expected
    assert _timestamp("2026-11-20T19:00:00") == expected
    assert _timestamp("2026-11-20T21:00:00+02:00") == expected
    assert _timestamp("not a date") is None
    assert _timestamp(True) is None
    assert _timestamp(None) is None
    assert _timestamp([1]) is None


async def test_api_schedule_shapes(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The schedule endpoints accept wrapped and bare shapes."""
    aioclient_mock.get(BASE_URL + "api/v1/apps/token", json=APP_TOKEN_RESPONSE)
    aioclient_mock.get(
        BASE_URL + "api/v1/devices/D1/future_actions",
        json={"future_actions": [{"ts": 1}, {"no": "ts"}, "x"]},
    )
    aioclient_mock.get(BASE_URL + "api/v1/devices/D2/future_actions", status=404)
    aioclient_mock.get(BASE_URL + "api/v1/devices/D3/future_actions", status=500)
    aioclient_mock.get(BASE_URL + "api/v1/devices/D1/next_action", json=[])
    aioclient_mock.get(BASE_URL + "api/v1/devices/D3/next_action", status=500)
    aioclient_mock.get(BASE_URL + "api/v1/devices/D1/timers", json=None)
    api = SmartGradeApi(async_get_clientsession(hass), jwt=make_jwt(), user_id=USER_ID)
    assert await api.get_future_actions("D1") == [{"ts": 1}]
    assert await api.get_future_actions("D2") == []
    assert await api.get_next_action("D1") is None
    assert await api.get_timers("D1") == []
    for call in (api.get_future_actions("D3"), api.get_next_action("D3")):
        try:
            await call
        except SmartGradeRequestError as err:
            assert err.status == 500
        else:  # pragma: no cover
            raise AssertionError("expected SmartGradeRequestError")


async def test_device_data_defaults() -> None:
    """New schedule fields default to None for backwards compatibility."""
    data = SmartGradeDeviceData({"id": DEVICE_ID})
    assert data.timers is None
    assert data.next_action is None
    assert data.future_actions is None
