"""Tests for setup, the coordinator and the entities (HTTP mocked)."""

from __future__ import annotations

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.smartgrade.const import BASE_URL, CONF_JWT, DOMAIN
from custom_components.smartgrade.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import (
    DEVICE_ID,
    STATS_ON,
    device_list,
    make_jwt,
    mock_cloud,
    setup_entry,
)

SWITCH = "switch.boiler"
POWER = "sensor.boiler_power"
CURRENT = "sensor.boiler_current"
VOLTAGE = "sensor.boiler_voltage"


async def test_setup_and_unload(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Entities are created from the bare-array device list and stats."""
    mock_cloud(aioclient_mock)
    await setup_entry(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED

    assert hass.states.get(SWITCH).state == STATE_OFF
    assert float(hass.states.get(POWER).state) == 0.0
    assert float(hass.states.get(CURRENT).state) == 0.0
    assert float(hass.states.get(VOLTAGE).state) == 241.0
    assert hass.states.get(POWER).attributes["unit_of_measurement"] == "W"

    ent_reg = er.async_get(hass)
    assert ent_reg.async_get(SWITCH).unique_id == f"{DEVICE_ID}_switch_1"
    signal = ent_reg.async_get("sensor.boiler_signal_strength")
    assert signal is not None and signal.disabled_by is None
    # No energy sensor: the live stats endpoint has no kWh field.
    assert not any(
        e.unique_id.endswith("kwh")
        for e in er.async_entries_for_config_entry(ent_reg, mock_config_entry.entry_id)
    )

    devices = dr.async_entries_for_config_entry(
        dr.async_get(hass), mock_config_entry.entry_id
    )
    assert len(devices) == 1
    device = devices[0]
    assert (DOMAIN, DEVICE_ID) in device.identifiers
    assert device.name == "Boiler"
    assert device.model == "Boiler switch (Gewiss 3-module)"
    assert device.sw_version == "20260410_var2_v2.9.2"
    assert ("mac", "aa:bb:cc:dd:ee:ff") in device.connections

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED


async def test_on_state_and_metering(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Live state comes from the list, power from last_usage_stats."""
    mock_cloud(aioclient_mock, devices=device_list(switch_on=True), stats=STATS_ON)
    await setup_entry(hass, mock_config_entry)
    assert hass.states.get(SWITCH).state == STATE_ON
    assert float(hass.states.get(POWER).state) == 2707.0
    assert float(hass.states.get(CURRENT).state) == 12.29


async def test_offline_device(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """is_online=false makes the entities unavailable."""
    mock_cloud(aioclient_mock, devices=device_list(online=False))
    await setup_entry(hass, mock_config_entry)
    assert hass.states.get(SWITCH).state == STATE_UNAVAILABLE
    assert hass.states.get(POWER).state == STATE_UNAVAILABLE


async def test_no_stats(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A device without metering still gets its switch, but no sensors."""
    mock_cloud(aioclient_mock, stats_status=404)
    await setup_entry(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED
    assert hass.states.get(SWITCH) is not None
    assert hass.states.get(POWER) is None


async def test_turn_on_calls_toggle_and_refreshes(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Turning on POSTs toggle_switches (to the mock) and re-reads the list."""
    mock_cloud(aioclient_mock)
    aioclient_mock.post(
        BASE_URL + f"api/v1/devices/{DEVICE_ID}/toggle_switches",
        json={"switch_1": False},  # the live echo can be stale; it is ignored
    )
    await setup_entry(hass, mock_config_entry)
    before = len(aioclient_mock.mock_calls)
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: SWITCH}, blocking=True
    )
    await hass.async_block_till_done()
    calls = aioclient_mock.mock_calls[before:]
    toggles = [c for c in calls if c[0] == "POST"]
    assert len(toggles) == 1
    assert toggles[0][2] == {"switch_1": True}


async def test_auth_failure_starts_reauth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A 401 puts the entry in setup error and starts a reauth flow."""
    mock_cloud(aioclient_mock, devices_status=401)
    await setup_entry(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]


async def test_expired_jwt_starts_reauth_without_calls(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An expired 30-day JWT triggers reauth before any request is made."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="1234",
        data={"mobile": "0500000000", CONF_JWT: make_jwt(-10), "user_id": 1234},
    )
    await setup_entry(hass, entry)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert aioclient_mock.call_count == 0
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]


async def test_server_error_retries(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A 5xx at startup means setup retry, not reauth."""
    mock_cloud(aioclient_mock, devices_status=500)
    await setup_entry(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    assert not hass.config_entries.flow.async_progress()


async def test_diagnostics_redacted(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Diagnostics never contain the JWT or phone number."""
    mock_cloud(aioclient_mock)
    await setup_entry(hass, mock_config_entry)
    diag = await async_get_config_entry_diagnostics(hass, mock_config_entry)
    text = str(diag)
    assert mock_config_entry.data[CONF_JWT] not in text
    assert "0500000000" not in text
    assert diag["devices"][DEVICE_ID]["stats"]["watt"] == 0.0
