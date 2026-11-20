"""Edge cases for entities, coordinator, API and config flow (HTTP mocked).

The switch tests only hit the mocked toggle endpoint; no real device is
ever switched.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_ON,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.smartgrade.api import (
    SmartGradeApi,
    SmartGradeAuthError,
    SmartGradeConnectionError,
    SmartGradeRequestError,
)
from custom_components.smartgrade.const import BASE_URL, CONF_JWT, DOMAIN
from custom_components.smartgrade.coordinator import SmartGradeDeviceData

from .conftest import (
    APP_TOKEN_RESPONSE,
    DEVICE_ID,
    MOBILE,
    SITE_ID,
    STATS_OFF,
    USER_ID,
    device_list,
    make_jwt,
    mock_cloud,
    setup_entry,
)

SWITCH = "switch.boiler"
POWER = "sensor.boiler_power"
TOGGLE_URL = BASE_URL + f"api/v1/devices/{DEVICE_ID}/toggle_switches"
DEVICES_URL = BASE_URL + f"api/v1/sites/{SITE_ID}/devices"


# --- switch ------------------------------------------------------------------------


async def test_turn_off_posts_and_refreshes(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Turning off POSTs switch_1=false and then re-reads the device list."""
    mock_cloud(aioclient_mock, devices=device_list(switch_on=True))
    aioclient_mock.post(TOGGLE_URL, json={"switch_1": True})  # stale echo
    await setup_entry(hass, mock_config_entry)
    assert hass.states.get(SWITCH).state == STATE_ON
    before = len(aioclient_mock.mock_calls)
    await hass.services.async_call(
        SWITCH_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: SWITCH}, blocking=True
    )
    await hass.async_block_till_done()
    calls = aioclient_mock.mock_calls[before:]
    posts = [i for i, c in enumerate(calls) if c[0] == "POST"]
    assert len(posts) == 1
    assert calls[posts[0]][2] == {"switch_1": False}
    # The real state is re-read from the list after the toggle.
    assert any(
        c[0] == "GET" and str(c[1]) == DEVICES_URL for c in calls[posts[0] + 1 :]
    )


async def test_switch_failure_raises_translated_error(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A cloud error on toggle becomes a translated HomeAssistantError."""
    mock_cloud(aioclient_mock)
    aioclient_mock.post(TOGGLE_URL, status=500)
    await setup_entry(hass, mock_config_entry)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            SWITCH_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: SWITCH}, blocking=True
        )
    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "switch_failed"


async def test_multi_relay_device(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """switch_num=3 creates three relays: device name, custom name, Relay N."""
    devices = device_list()
    devices[0].update(
        switch_num=3,
        switch_2=True,
        switch_2_name="Pump",
        switch_3=None,
    )
    mock_cloud(aioclient_mock, devices=devices)
    aioclient_mock.post(TOGGLE_URL, json={})
    await setup_entry(hass, mock_config_entry)

    ent_reg = er.async_get(hass)
    entries = {
        e.unique_id: e
        for e in er.async_entries_for_config_entry(ent_reg, mock_config_entry.entry_id)
        if e.domain == "switch"
    }
    assert set(entries) == {f"{DEVICE_ID}_switch_{n}" for n in (1, 2, 3)}
    relay3 = entries[f"{DEVICE_ID}_switch_3"]
    assert relay3.translation_key == "relay"
    assert hass.states.get(SWITCH).name == "Boiler"
    assert hass.states.get("switch.boiler_pump").state == STATE_ON
    state3 = hass.states.get(relay3.entity_id)
    assert state3.name == "Boiler Relay 3"
    assert state3.state == STATE_UNKNOWN

    await hass.services.async_call(
        SWITCH_DOMAIN,
        SERVICE_TURN_OFF,
        {ATTR_ENTITY_ID: "switch.boiler_pump"},
        blocking=True,
    )
    posts = [c for c in aioclient_mock.mock_calls if c[0] == "POST"]
    assert posts[-1][2] == {"switch_2": False}


async def test_non_switch_product_and_bad_switch_num(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Sensor-only products get no switch; a garbage switch_num means one relay."""
    devices = device_list()
    devices[0]["switch_num"] = "garbage"
    sensor_only = {**device_list()[0], "id": "112233445566", "product_id": 26}
    mock_cloud(aioclient_mock, devices=[*devices, sensor_only])
    aioclient_mock.get(
        BASE_URL + "api/v1/devices/112233445566/last_usage_stats", status=404
    )
    await setup_entry(hass, mock_config_entry)
    switches = [
        e
        for e in er.async_entries_for_config_entry(
            er.async_get(hass), mock_config_entry.entry_id
        )
        if e.domain == "switch"
    ]
    assert [e.unique_id for e in switches] == [f"{DEVICE_ID}_switch_1"]


# --- coordinator / sensors ---------------------------------------------------------


async def test_stats_auth_error_starts_reauth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A 401 from last_usage_stats (inside the gather) also triggers reauth."""
    mock_cloud(aioclient_mock, stats_status=401)
    await setup_entry(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]


async def test_sensor_value_when_stats_change(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Sensors go unknown when stats vanish or are not numeric."""
    mock_cloud(aioclient_mock)
    await setup_entry(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data
    assert float(hass.states.get(POWER).state) == 0.0

    device = coordinator.data[DEVICE_ID].device
    coordinator.async_set_updated_data(
        {DEVICE_ID: SmartGradeDeviceData(device, {**STATS_OFF, "watt": "n/a"})}
    )
    assert hass.states.get(POWER).state == STATE_UNKNOWN

    coordinator.async_set_updated_data(
        {DEVICE_ID: SmartGradeDeviceData(device, {**STATS_OFF, "watt": None})}
    )
    assert hass.states.get(POWER).state == STATE_UNKNOWN

    coordinator.async_set_updated_data({DEVICE_ID: SmartGradeDeviceData(device)})
    assert hass.states.get(POWER).state == STATE_UNKNOWN

    coordinator.async_set_updated_data({})  # device disappeared
    assert hass.states.get(POWER).state == "unavailable"
    assert hass.states.get(SWITCH).state == "unavailable"


# --- API edges ---------------------------------------------------------------------


async def test_get_user_id_from_profile(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Without a stored user id the profile is fetched (wrapped or bare)."""
    mock_cloud(aioclient_mock)
    aioclient_mock.get(
        BASE_URL + "api/v1/users/profile", json={"user": {"id": USER_ID}}
    )
    api = SmartGradeApi(async_get_clientsession(hass), make_jwt())
    assert await api.get_user_id() == USER_ID
    assert list(await api.get_all_devices()) == [DEVICE_ID]
    profile_calls = [c for c in aioclient_mock.mock_calls if "profile" in str(c[1])]
    assert len(profile_calls) == 1  # cached afterwards

    aioclient_mock.clear_requests()
    aioclient_mock.get(BASE_URL + "api/v1/apps/token", json=APP_TOKEN_RESPONSE)
    aioclient_mock.get(BASE_URL + "api/v1/users/profile", json={"id": 77})
    api = SmartGradeApi(async_get_clientsession(hass), make_jwt())
    assert await api.get_user_id() == 77


async def test_get_user_id_bad_profile(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A profile without an id is an unexpected response."""
    aioclient_mock.get(BASE_URL + "api/v1/apps/token", json=APP_TOKEN_RESPONSE)
    aioclient_mock.get(BASE_URL + "api/v1/users/profile", json={"user": {}})
    api = SmartGradeApi(async_get_clientsession(hass), make_jwt())
    with pytest.raises(SmartGradeConnectionError):
        await api.get_user_id()


@pytest.mark.parametrize("body", [{}, {"token": "x"}, {"token": "x", "exp": "soon"}])
async def test_unexpected_app_token_body(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, body: dict
) -> None:
    """A malformed app-token response is a connection error."""
    aioclient_mock.get(BASE_URL + "api/v1/apps/token", json=body)
    api = SmartGradeApi(async_get_clientsession(hass), make_jwt(), USER_ID)
    with pytest.raises(SmartGradeConnectionError):
        await api.get_site_devices(SITE_ID)


async def test_invalid_and_empty_json(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Invalid JSON raises; an empty body is treated as no data."""
    aioclient_mock.get(BASE_URL + "api/v1/apps/token", json=APP_TOKEN_RESPONSE)
    aioclient_mock.get(DEVICES_URL, text="<html>oops</html>")
    aioclient_mock.get(
        BASE_URL + f"api/v1/devices/{DEVICE_ID}/last_usage_stats", text=""
    )
    api = SmartGradeApi(async_get_clientsession(hass), make_jwt(), USER_ID)
    with pytest.raises(SmartGradeConnectionError):
        await api.get_site_devices(SITE_ID)
    assert await api.get_last_usage_stats(DEVICE_ID) is None


async def test_sms_and_login_server_errors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """5xx on the SMS request or login is an error, not 'wrong number/code'."""
    aioclient_mock.get(BASE_URL + "api/v1/apps/token", json=APP_TOKEN_RESPONSE)
    aioclient_mock.post(BASE_URL + "api/v1/customers", status=503)
    aioclient_mock.post(BASE_URL + "api/v1/users/login/code", status=502)
    api = SmartGradeApi(async_get_clientsession(hass))
    with pytest.raises(SmartGradeRequestError) as err:
        await api.request_sms(MOBILE)
    assert err.value.status == 503
    with pytest.raises(SmartGradeRequestError) as err:
        await api.submit_code("1234")
    assert err.value.status == 502
    assert api.jwt is None


async def test_login_without_jwt(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 200 login response without a token is an auth error."""
    aioclient_mock.get(BASE_URL + "api/v1/apps/token", json=APP_TOKEN_RESPONSE)
    aioclient_mock.post(BASE_URL + "api/v1/customers", json={"success": False})
    aioclient_mock.post(BASE_URL + "api/v1/users/login/code", json={"user": {}})
    api = SmartGradeApi(async_get_clientsession(hass))
    assert await api.request_sms(MOBILE) is False
    with pytest.raises(SmartGradeAuthError):
        await api.submit_code("1234")


# --- config flow edges -------------------------------------------------------------


async def test_reauth_without_mobile_falls_back_to_user_step(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An entry without a stored number asks for it during reauth."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=str(USER_ID), data={CONF_JWT: make_jwt(-10)}
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert not aioclient_mock.mock_calls


async def test_config_flow_unexpected_errors(hass: HomeAssistant) -> None:
    """Unexpected exceptions map to the 'unknown' error."""
    from unittest.mock import patch  # noqa: PLC0415

    with patch(
        "custom_components.smartgrade.config_flow.SmartGradeApi", autospec=True
    ) as cls:
        api: AsyncMock = cls.return_value
        api.request_sms.side_effect = RuntimeError
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"mobile": MOBILE}
        )
        assert result["errors"] == {"base": "unknown"}

        api.request_sms.side_effect = None
        api.request_sms.return_value = True
        api.submit_code.side_effect = RuntimeError
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"mobile": MOBILE}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"code": "1234"}
        )
        assert result["errors"] == {"base": "unknown"}
