"""Tests for the API client parsing, against mocked responses only."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.smartgrade.api import (
    SmartGradeApi,
    SmartGradeAuthError,
    SmartGradeConnectionError,
    SmartGradeError,
    jwt_exp,
)
from custom_components.smartgrade.const import BASE_URL

from .conftest import (
    APP_TOKEN_RESPONSE,
    DEVICE_ID,
    MOBILE,
    SITE_ID,
    STATS_ON,
    USER_ID,
    device_list,
    make_jwt,
    mock_cloud,
)


def test_jwt_exp() -> None:
    """The exp claim is decoded; garbage gives 0."""
    token = make_jwt(100)
    assert jwt_exp(token) > 0
    assert jwt_exp("not-a-jwt") == 0
    assert jwt_exp(None) == 0


async def test_bare_array_device_list(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The live endpoint returns a bare array; devices are keyed by id."""
    mock_cloud(aioclient_mock, devices=device_list(switch_on=True), stats=STATS_ON)
    api = SmartGradeApi(async_get_clientsession(hass), make_jwt(), USER_ID)
    devices = await api.get_all_devices()
    assert list(devices) == [DEVICE_ID]
    assert devices[DEVICE_ID]["switch_1"] is True
    assert devices[DEVICE_ID]["site_name"] == "Home"
    stats = await api.get_last_usage_stats(DEVICE_ID)
    assert stats["watt"] == 2707.0
    assert "kwh" not in stats
    # Every authenticated call carries both tokens.
    _, _, _, headers = aioclient_mock.mock_calls[-1]
    assert headers["Authorization"].startswith("Bearer ")
    assert headers["X-APP-TOKEN"] == "Bearer fake-app-token"
    _, _, _, token_headers = aioclient_mock.mock_calls[0]
    assert token_headers["Authorization"].startswith("Basic ")


async def test_wrapped_device_list(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The {"devices": [...]} shape used by older app models is also accepted."""
    mock_cloud(aioclient_mock, devices={"devices": device_list()})
    api = SmartGradeApi(async_get_clientsession(hass), make_jwt(), USER_ID)
    assert list(await api.get_all_devices()) == [DEVICE_ID]


async def test_app_token_cached(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The long-lived app token is fetched once."""
    mock_cloud(aioclient_mock)
    api = SmartGradeApi(async_get_clientsession(hass), make_jwt(), USER_ID)
    await api.get_site_devices(SITE_ID)
    await api.get_site_devices(SITE_ID)
    token_calls = [
        c for c in aioclient_mock.mock_calls if str(c[1]).endswith("apps/token")
    ]
    assert len(token_calls) == 1


async def test_unauthorized(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """401 maps to an auth error (reauth)."""
    mock_cloud(aioclient_mock, devices_status=401)
    api = SmartGradeApi(async_get_clientsession(hass), make_jwt(), USER_ID)
    with pytest.raises(SmartGradeAuthError):
        await api.get_site_devices(SITE_ID)


async def test_no_jwt(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Authenticated calls without a JWT fail fast."""
    mock_cloud(aioclient_mock)
    api = SmartGradeApi(async_get_clientsession(hass))
    with pytest.raises(SmartGradeAuthError):
        await api.get_site_devices(SITE_ID)


async def test_server_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """5xx maps to a generic SmartGrade error."""
    mock_cloud(aioclient_mock, devices_status=500)
    api = SmartGradeApi(async_get_clientsession(hass), make_jwt(), USER_ID)
    with pytest.raises(SmartGradeError):
        await api.get_site_devices(SITE_ID)


async def test_sms_login(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """SMS request and code submission parse the verified response shapes."""
    token = make_jwt()
    aioclient_mock.get(BASE_URL + "api/v1/apps/token", json=APP_TOKEN_RESPONSE)
    aioclient_mock.post(
        BASE_URL + "api/v1/customers", json={"success": True, "mobile": MOBILE}
    )
    aioclient_mock.post(
        BASE_URL + "api/v1/users/login/code",
        json={"jwt": token, "user": {"id": USER_ID, "mobile": MOBILE}},
    )
    api = SmartGradeApi(async_get_clientsession(hass))
    assert await api.request_sms(MOBILE) is True
    await api.submit_code("1234")
    assert api.jwt == token
    assert api.user_id == USER_ID
    assert api.jwt_expires_at == jwt_exp(token)


async def test_sms_rejected(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 4xx on the SMS request means the number was rejected."""
    aioclient_mock.get(BASE_URL + "api/v1/apps/token", json=APP_TOKEN_RESPONSE)
    aioclient_mock.post(BASE_URL + "api/v1/customers", status=422)
    aioclient_mock.post(BASE_URL + "api/v1/users/login/code", status=404)
    api = SmartGradeApi(async_get_clientsession(hass))
    assert await api.request_sms("123") is False
    with pytest.raises(SmartGradeAuthError):
        await api.submit_code("0000")


async def test_connection_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Network failures become SmartGradeConnectionError."""
    aioclient_mock.get(BASE_URL + "api/v1/apps/token", exc=TimeoutError)
    api = SmartGradeApi(async_get_clientsession(hass), make_jwt(), USER_ID)
    with pytest.raises(SmartGradeConnectionError):
        await api.get_site_devices(SITE_ID)
