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


# A recurring 06:00-06:50 Sun-Thu timer (like the one seen live) and a
# disabled pattern timer.
TIMERS: list[dict[str, Any]] = [
    {
        "id": 1,
        "name": "Morning",
        "is_enabled": True,
        "timer_kind": "recurring",
        "timer_detail": {
            "days": ["sun", "mon", "tue", "wed", "thu"],
            "on_time": "06:00",
            "off_time": "06:50",
            "switch_1": True,
            "from_date": None,
            "to_date": None,
        },
    },
    {
        "id": 2,
        "name": "Cycle",
        "is_enabled": False,
        "timer_kind": "pattern",
        "timer_detail": {
            "days": ["fri"],
            "on_minutes": 10,
            "off_minutes": 20,
            "start_period": "08:00",
            "stop_period": "12:00",
            "switch_1": True,
        },
    },
]

NEXT_TS = int(time.time()) + 3600


def next_action(ts: int = NEXT_TS) -> dict[str, Any]:
    """Return a next_action payload ({ts, switch_N, created_by})."""
    return {"ts": ts, "switch_1": True, "created_by": "timer"}


def future_actions(ts: int = NEXT_TS) -> list[dict[str, Any]]:
    """Return a future_actions payload."""
    return [
        next_action(ts),
        {"ts": ts + 3000, "switch_1": False, "created_by": "timer"},
    ]


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
            "switch_type": "ud",
            "max_on_min": 120,
            "upgradeNow": False,
            "location": None,
            "createdDate": "2026-01-01T00:00:00.000Z",
            "timers": [dict(t) for t in TIMERS],
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
    next_payload: Any = None,
    future_payload: Any = None,
    next_status: int = 200,
    future_status: int = 200,
    device_id: str = DEVICE_ID,
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
        BASE_URL + f"api/v1/devices/{device_id}/last_usage_stats",
        json=STATS_OFF if stats is None else stats,
        status=stats_status,
    )
    mock_schedule(
        aioclient_mock,
        device_id,
        next_payload=next_action() if next_payload is None else next_payload,
        future_payload=future_actions() if future_payload is None else future_payload,
        next_status=next_status,
        future_status=future_status,
    )


def mock_schedule(
    aioclient_mock: AiohttpClientMocker,
    device_id: str,
    *,
    next_payload: Any = None,
    future_payload: Any = None,
    next_status: int = 200,
    future_status: int = 200,
) -> None:
    """Register the read-only schedule endpoints of one device."""
    aioclient_mock.get(
        BASE_URL + f"api/v1/devices/{device_id}/next_action",
        json=next_payload,
        status=next_status,
    )
    aioclient_mock.get(
        BASE_URL + f"api/v1/devices/{device_id}/future_actions",
        json=[] if future_payload is None else future_payload,
        status=future_status,
    )


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add and set up a config entry."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
