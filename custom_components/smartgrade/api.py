"""Async client for the SmartGrade Secure (IoTech Verticals) cloud API.

The API is undocumented. Everything here was reverse engineered from the
official Android app and verified against the live service (see VERIFIED.md).
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import aiohttp

from .const import APP_PASSWORD, APP_USER, BASE_URL, REQUEST_TIMEOUT

# HTTP Basic header for the app-level credential (built by hand because
# aiohttp.BasicAuth is deprecated in recent aiohttp releases).
_APP_BASIC = "Basic " + base64.b64encode(f"{APP_USER}:{APP_PASSWORD}".encode()).decode()


class SmartGradeError(Exception):
    """Base error for the SmartGrade API."""


class SmartGradeConnectionError(SmartGradeError):
    """The cloud could not be reached or returned an unexpected response."""


class SmartGradeRequestError(SmartGradeError):
    """The cloud rejected a request with an HTTP error status (not 401/403)."""

    def __init__(self, status: int, message: str) -> None:
        """Initialize with the HTTP status."""
        super().__init__(message)
        self.status = status


class SmartGradeAuthError(SmartGradeError):
    """The user JWT is missing, expired or rejected.

    The service has no refresh endpoint, so the only fix is a new SMS login.
    """


def jwt_exp(token: str | None) -> int:
    """Return the ``exp`` claim of a JWT (seconds since epoch), or 0 if unknown.

    The signature is not verified; this is only used to detect expiry early.
    """
    if not token:
        return 0
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return int(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except (IndexError, KeyError, TypeError, ValueError):
        return 0


class SmartGradeApi:
    """Thin async wrapper around the SmartGrade REST API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        jwt: str | None = None,
        user_id: str | int | None = None,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self.jwt = jwt
        self.user_id = user_id
        self._app_token: str | None = None
        self._app_token_exp = 0

    @property
    def jwt_expires_at(self) -> int:
        """Return the JWT expiry as epoch seconds (0 if unknown)."""
        return jwt_exp(self.jwt)

    async def _get_app_token(self) -> str:
        """Return the app-level token, fetching a new one if close to expiry."""
        if self._app_token and self._app_token_exp > time.time() + 3600:
            return self._app_token
        try:
            async with self._session.get(
                BASE_URL + "api/v1/apps/token",
                headers={"Accept": "application/json", "Authorization": _APP_BASIC},
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SmartGradeConnectionError(f"Cannot get app token: {err}") from err
        try:
            self._app_token = str(data["token"])
            self._app_token_exp = int(data["exp"])
        except (KeyError, TypeError, ValueError) as err:
            raise SmartGradeConnectionError("Unexpected app token response") from err
        return self._app_token

    async def _request(
        self, method: str, path: str, *, user: bool = True, **kwargs: Any
    ) -> Any:
        """Perform a request and return the decoded JSON body (or None)."""
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-APP-TOKEN": f"Bearer {await self._get_app_token()}",
        }
        if user:
            if not self.jwt:
                raise SmartGradeAuthError("Not logged in")
            headers["Authorization"] = f"Bearer {self.jwt}"
        try:
            async with self._session.request(
                method,
                BASE_URL + path,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                **kwargs,
            ) as resp:
                if resp.status in (401, 403):
                    raise SmartGradeAuthError(f"HTTP {resp.status} on {path}")
                resp.raise_for_status()
                text = await resp.text()
        except SmartGradeAuthError:
            raise
        except aiohttp.ClientResponseError as err:
            raise SmartGradeRequestError(
                err.status, f"{method} {path} returned HTTP {err.status}"
            ) from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SmartGradeConnectionError(f"{method} {path} failed: {err}") from err
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError as err:
            raise SmartGradeConnectionError(f"Invalid JSON from {path}") from err

    # --- login (SMS one-time code) ------------------------------------------------

    async def request_sms(self, mobile: str) -> bool:
        """Ask the cloud to send an SMS code. Returns True if accepted."""
        try:
            data = await self._request(
                "POST",
                "api/v1/customers",
                user=False,
                json={"customer": {"mobile": mobile}},
            )
        except SmartGradeRequestError as err:
            if 400 <= err.status < 500:
                return False
            raise
        return bool(isinstance(data, dict) and data.get("success"))

    async def submit_code(self, code: str) -> dict[str, Any]:
        """Exchange the SMS code for a user JWT (valid for 30 days)."""
        try:
            data = await self._request(
                "POST",
                "api/v1/users/login/code",
                user=False,
                json={"user": {"code": code}},
            )
        except SmartGradeRequestError as err:
            # A wrong or expired code is answered with a 4xx status.
            if 400 <= err.status < 500:
                raise SmartGradeAuthError(str(err)) from err
            raise
        if not isinstance(data, dict) or not data.get("jwt"):
            raise SmartGradeAuthError("No token in login response")
        self.jwt = data["jwt"]
        self.user_id = (data.get("user") or {}).get("id")
        return data

    # --- data ---------------------------------------------------------------------

    async def get_user_id(self) -> str | int:
        """Return the user id, fetching the profile if needed."""
        if not self.user_id:
            profile = await self._request("GET", "api/v1/users/profile")
            if isinstance(profile, dict) and isinstance(profile.get("user"), dict):
                profile = profile["user"]
            if not isinstance(profile, dict) or "id" not in profile:
                raise SmartGradeConnectionError("Unexpected profile response")
            self.user_id = profile["id"]
        return self.user_id

    async def get_sites(self) -> list[dict[str, Any]]:
        """Return the sites (homes) of the user, including shared ones."""
        user_id = await self.get_user_id()
        data = await self._request(
            "GET", f"api/v1/users/{user_id}/sites", params={"shared": "true"}
        )
        if isinstance(data, dict):
            data = data.get("sites")
        return list(data or [])

    async def get_site_devices(self, site_id: str | int) -> list[dict[str, Any]]:
        """Return the devices of a site with their live switch state.

        Verified live: this returns a bare JSON array. Older app models expect
        ``{"devices": [...]}``, so both shapes are accepted.
        """
        data = await self._request("GET", f"api/v1/sites/{site_id}/devices")
        if isinstance(data, dict):
            data = data.get("devices")
        return list(data or [])

    async def get_all_devices(self) -> dict[str, dict[str, Any]]:
        """Return all devices of all sites keyed by device id."""
        devices: dict[str, dict[str, Any]] = {}
        for site in await self.get_sites():
            for device in await self.get_site_devices(site["id"]):
                if not isinstance(device, dict) or "id" not in device:
                    continue
                device.setdefault("site_id", site["id"])
                device.setdefault("site_name", site.get("name"))
                devices[str(device["id"])] = device
        return devices

    async def get_last_usage_stats(self, device_id: str) -> dict[str, Any] | None:
        """Return the latest power reading of a device.

        Verified shape: {volt, amper, watt, watt2, watt3, powerFactor,
        signalStrength, createdAt}; refreshed by the device about once a minute.
        """
        data = await self._request(
            "GET", f"api/v1/devices/{device_id}/last_usage_stats"
        )
        return data if isinstance(data, dict) else None

    async def get_timers(self, device_id: str) -> list[dict[str, Any]]:
        """Return the timers of a device (read-only use).

        Each timer is ``{id, name, is_enabled, timer_kind, timer_detail}``.
        """
        data = await self._request("GET", f"api/v1/devices/{device_id}/timers")
        if isinstance(data, dict):
            data = data.get("timers")
        return [t for t in (data or []) if isinstance(t, dict)]

    async def get_next_action(self, device_id: str) -> dict[str, Any] | None:
        """Return the next scheduled action ``{ts, switch_N, created_by}``.

        Returns None when nothing is scheduled.
        """
        try:
            data = await self._request("GET", f"api/v1/devices/{device_id}/next_action")
        except SmartGradeRequestError as err:
            if err.status == 404:
                return None
            raise
        if isinstance(data, dict) and isinstance(data.get("next_action"), dict):
            data = data["next_action"]
        if not isinstance(data, dict) or data.get("ts") is None:
            return None
        return data

    async def get_future_actions(self, device_id: str) -> list[dict[str, Any]]:
        """Return the upcoming scheduled actions of a device."""
        try:
            data = await self._request(
                "GET", f"api/v1/devices/{device_id}/future_actions"
            )
        except SmartGradeRequestError as err:
            if err.status == 404:
                return []
            raise
        if isinstance(data, dict):
            data = data.get("future_actions") or data.get("actions")
        return [
            a for a in (data or []) if isinstance(a, dict) and a.get("ts") is not None
        ]

    async def set_switch(self, device_id: str, on: bool, switch: int = 1) -> None:
        """Turn relay ``switch`` of a device on or off.

        The echoed body can contain a stale state, so it is ignored; callers
        must re-read the device list to get the real state.
        """
        await self._request(
            "POST",
            f"api/v1/devices/{device_id}/toggle_switches",
            json={f"switch_{switch}": on},
        )
