"""Shared fixtures for SmartGrade tests.

All HTTP traffic is mocked. The payload shapes mirror what the live service
returned during manual verification (see VERIFIED.md), with anonymised ids.
No test talks to the real cloud.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.smartgrade.const import (
    BASE_URL,
    CONF_JWT,
    CONF_MOBILE,
    CONF_USER_ID,
    DOMAIN,
)

USER_ID = 1234
SITE_ID = 5678
DEVICE_ID = "AABBCCDDEEFF"
MOBILE = "0500000000"


def make_jwt(exp_offset: int = 30 * 86400) -> str:
    """Build an unsigned, fake JWT with an exp claim (never a real token)."""

    def b64(obj: dict[str, Any]) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    now = int(time.time())
    return f"{b64({'alg': 'none'})}.{b64({'sub': USER_ID, 'iat': now, 'exp': now + exp_offset})}.sig"


APP_TOKEN_RESPONSE = {
    "token": "fake-app-token",
    "exp": int(time.time()) + 20 * 365 * 86400,
}

SITES_RESPONSE = {
    "sites": [{"id": SITE_ID, "name": "Home", "user_id": USER_ID, "devices": []}]
}


def device_list(switch_on: bool = False, online: bool = True) -> list[dict[str, Any]]:
    """Return the bare-array device list, as the live endpoint does."""
    return [
        {
            "id": DEVICE_ID,
            "name": "Boiler",
            "site_id": SITE_ID,
            "product_id": 8,
            "firmware": "20260410_var2_v2.9.2",
            "is_online": online,
            "has_current": None,
            "switch_num": 1,
            "switch_1": switch_on,
            "switch_1_name": None,
            "timers": [],
        }
    ]


STATS_OFF = {
    "volt": 241.0,
    "amper": 0.0,
    "watt": 0.0,
    "watt2": 0.0,
    "watt3": 0.0,
    "powerFactor": 0.0,
    "signalStrength": -67,
    "createdAt": "2026-11-20T19:00:00.000Z",
}
STATS_ON = {**STATS_OFF, "volt": 236.8, "amper": 12.29, "watt": 2707.0}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading custom_components in every test."""
    return


@pytest.fixture
def jwt() -> str:
    """Return a fake, valid JWT."""
    return make_jwt()


@pytest.fixture
def mock_config_entry(jwt: str) -> MockConfigEntry:
    """Return a config entry as created by the config flow."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"SmartGrade {MOBILE}",
        unique_id=str(USER_ID),
        data={CONF_MOBILE: MOBILE, CONF_JWT: jwt, CONF_USER_ID: USER_ID},
    )


def mock_cloud(
    aioclient_mock: AiohttpClientMocker,
    *,
    devices: Any = None,
    stats: dict[str, Any] | None = None,
    devices_status: int = 200,
    stats_status: int = 200,
) -> None:
    """Register mocked responses for the read-only endpoints."""
    aioclient_mock.get(BASE_URL + "api/v1/apps/token", json=APP_TOKEN_RESPONSE)
    aioclient_mock.get(BASE_URL + f"api/v1/users/{USER_ID}/sites", json=SITES_RESPONSE)
    aioclient_mock.get(
        BASE_URL + f"api/v1/sites/{SITE_ID}/devices",
        json=device_list() if devices is None else devices,
        status=devices_status,
    )
    aioclient_mock.get(
        BASE_URL + f"api/v1/devices/{DEVICE_ID}/last_usage_stats",
        json=STATS_OFF if stats is None else stats,
        status=stats_status,
    )


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add and set up a config entry."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
