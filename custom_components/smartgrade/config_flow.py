"""Config flow for SmartGrade Secure: phone number -> SMS code -> JWT.

The service has no refresh token and the JWT lives exactly 30 days, so
re-authentication repeats the SMS login for the stored phone number.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
import re
from typing import Any

from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .api import SmartGradeApi, SmartGradeAuthError, SmartGradeError
from .const import CONF_CODE, CONF_JWT, CONF_MOBILE, CONF_USER_ID, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema({vol.Required(CONF_MOBILE): str})
STEP_CODE_SCHEMA = vol.Schema({vol.Required(CONF_CODE): str})


def _normalize_mobile(value: str) -> str:
    """Strip spaces, dashes and brackets. The live service accepts 05XXXXXXXX."""
    return re.sub(r"[\s\-()]", "", value)


class SmartGradeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for SmartGrade Secure."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        self._api: SmartGradeApi | None = None
        self._mobile: str | None = None

    async def _async_send_sms(self) -> str | None:
        """Request an SMS code for ``self._mobile``; return an error key or None."""
        assert self._mobile is not None
        self._api = SmartGradeApi(async_get_clientsession(self.hass))
        try:
            accepted = await self._api.request_sms(self._mobile)
        except SmartGradeError:
            return "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error requesting SMS code")
            return "unknown"
        return None if accepted else "invalid_mobile"

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the phone number registered in the SmartGrade app."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._mobile = _normalize_mobile(user_input[CONF_MOBILE])
            if len(self._mobile) <= 8:
                errors[CONF_MOBILE] = "invalid_mobile"
            elif (error := await self._async_send_sms()) is None:
                return await self.async_step_code()
            else:
                errors["base"] = error
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def async_step_code(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the SMS code and finish the login."""
        errors: dict[str, str] = {}
        if user_input is not None:
            assert self._api is not None
            try:
                await self._api.submit_code(user_input[CONF_CODE].strip())
            except SmartGradeAuthError:
                errors["base"] = "invalid_code"
            except SmartGradeError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error submitting SMS code")
                errors["base"] = "unknown"
            else:
                user_id = self._api.user_id
                data = {
                    CONF_MOBILE: self._mobile,
                    CONF_JWT: self._api.jwt,
                    CONF_USER_ID: user_id,
                }
                await self.async_set_unique_id(str(user_id or self._mobile))
                if self.source == SOURCE_REAUTH:
                    self._abort_if_unique_id_mismatch(reason="wrong_account")
                    return self.async_update_reload_and_abort(
                        self._get_reauth_entry(), data_updates=data
                    )
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"SmartGrade {self._mobile}", data=data
                )
        return self.async_show_form(
            step_id="code",
            data_schema=STEP_CODE_SCHEMA,
            errors=errors,
            description_placeholders={"mobile": self._mobile or ""},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start re-authentication after the 30-day token expired."""
        self._mobile = entry_data.get(CONF_MOBILE)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm, then send a new SMS code to the stored phone number."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not self._mobile:
                return await self.async_step_user()
            if (error := await self._async_send_sms()) is None:
                return await self.async_step_code()
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={"mobile": self._mobile or ""},
        )
