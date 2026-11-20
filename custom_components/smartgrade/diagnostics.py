"""Diagnostics support for SmartGrade Secure."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_JWT, CONF_MOBILE, CONF_USER_ID
from .coordinator import SmartGradeConfigEntry

TO_REDACT = {
    CONF_JWT,
    CONF_MOBILE,
    CONF_USER_ID,
    "mobile",
    "email",
    "user_id",
    "name",
    "site_name",
    "created_by",
    "street_name",
    "street_number",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SmartGradeConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "devices": {
            dev_id: async_redact_data(asdict(item), TO_REDACT)
            for dev_id, item in coordinator.data.items()
        },
    }
