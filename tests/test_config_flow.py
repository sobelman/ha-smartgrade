"""Tests for the SmartGrade config flow (API fully mocked)."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smartgrade.api import (
    SmartGradeAuthError,
    SmartGradeConnectionError,
)
from custom_components.smartgrade.const import (
    CONF_CODE,
    CONF_JWT,
    CONF_MOBILE,
    CONF_USER_ID,
    DOMAIN,
)

from .conftest import MOBILE, USER_ID, make_jwt

NEW_JWT = make_jwt(31 * 86400)  # differs from the fixture token


@pytest.fixture
def mock_api() -> Generator[AsyncMock]:
    """Patch the API used by the config flow."""
    with patch(
        "custom_components.smartgrade.config_flow.SmartGradeApi", autospec=True
    ) as cls:
        api = cls.return_value
        api.request_sms.return_value = True

        async def _submit(code: str) -> dict:
            api.jwt = NEW_JWT
            api.user_id = USER_ID
            return {"jwt": NEW_JWT, "user": {"id": USER_ID}}

        api.submit_code.side_effect = _submit
        api.user_id = None
        api.jwt = None
        yield api


@pytest.fixture(autouse=True)
def no_setup() -> Generator[AsyncMock]:
    """Do not actually set up the entry after the flow."""
    with patch(
        "custom_components.smartgrade.async_setup_entry", return_value=True
    ) as setup:
        yield setup


async def _start(hass: HomeAssistant) -> dict:
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )


async def test_full_flow(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """Phone number -> SMS code -> entry."""
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MOBILE: "050-000 0000"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "code"
    mock_api.request_sms.assert_awaited_once_with(MOBILE)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CODE: " 1234 "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"SmartGrade {MOBILE}"
    assert result["data"] == {
        CONF_MOBILE: MOBILE,
        CONF_JWT: NEW_JWT,
        CONF_USER_ID: USER_ID,
    }
    assert result["result"].unique_id == str(USER_ID)
    mock_api.submit_code.assert_awaited_once_with("1234")


async def test_short_number(hass: HomeAssistant, mock_api: AsyncMock) -> None:
    """Numbers of 8 digits or fewer are rejected locally (like the app)."""
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MOBILE: "1234"}
    )
    assert result["errors"] == {CONF_MOBILE: "invalid_mobile"}
    mock_api.request_sms.assert_not_awaited()


@pytest.mark.parametrize(
    ("side_effect", "return_value", "error"),
    [
        (None, False, "invalid_mobile"),
        (SmartGradeConnectionError("down"), True, "cannot_connect"),
        (RuntimeError("boom"), True, "unknown"),
    ],
)
async def test_sms_errors(
    hass: HomeAssistant,
    mock_api: AsyncMock,
    side_effect: Exception | None,
    return_value: bool,
    error: str,
) -> None:
    """Errors on the SMS request are shown, and the flow can recover."""
    mock_api.request_sms.side_effect = side_effect
    mock_api.request_sms.return_value = return_value
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MOBILE: MOBILE}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    mock_api.request_sms.side_effect = None
    mock_api.request_sms.return_value = True
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MOBILE: MOBILE}
    )
    assert result["step_id"] == "code"


@pytest.mark.parametrize(
    ("side_effect", "error"),
    [
        (SmartGradeAuthError("bad"), "invalid_code"),
        (SmartGradeConnectionError("down"), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_code_errors(
    hass: HomeAssistant, mock_api: AsyncMock, side_effect: Exception, error: str
) -> None:
    """Errors on code submission are shown, and a retry succeeds."""
    good = mock_api.submit_code.side_effect
    mock_api.submit_code.side_effect = side_effect
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MOBILE: MOBILE}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CODE: "0000"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "code"
    assert result["errors"] == {"base": error}

    mock_api.submit_code.side_effect = good
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CODE: "1234"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_already_configured(
    hass: HomeAssistant, mock_api: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """The same account cannot be added twice."""
    mock_config_entry.add_to_hass(hass)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MOBILE: MOBILE}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CODE: "1234"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(
    hass: HomeAssistant, mock_api: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Reauth sends a new SMS to the stored number and replaces the JWT."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["mobile"] == MOBILE

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "code"
    mock_api.request_sms.assert_awaited_once_with(MOBILE)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CODE: "1234"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_JWT] == NEW_JWT


async def test_reauth_sms_error(
    hass: HomeAssistant, mock_api: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """An SMS failure during reauth is shown on the confirm step."""
    mock_config_entry.add_to_hass(hass)
    mock_api.request_sms.side_effect = SmartGradeConnectionError("down")
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "reauth_confirm"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_reauth_wrong_account(
    hass: HomeAssistant, mock_api: AsyncMock, mock_config_entry: MockConfigEntry
) -> None:
    """Logging in to a different account during reauth is refused."""
    mock_config_entry.add_to_hass(hass)

    async def _other(code: str) -> dict:
        mock_api.jwt = NEW_JWT
        mock_api.user_id = 999
        return {}

    mock_api.submit_code.side_effect = _other
    result = await mock_config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_CODE: "1234"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert mock_config_entry.data[CONF_JWT] != NEW_JWT
