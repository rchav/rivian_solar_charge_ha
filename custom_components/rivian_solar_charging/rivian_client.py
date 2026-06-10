"""Rivian GraphQL API client.

Handles authentication (including MFA/OTP), session token management,
silent token refresh, vehicle state queries, charging schedule reads/writes.

API reference: https://rivian-api.kaedenb.org/
Inspired by: https://github.com/ostap-korkuna/rivian-charging-automation
"""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

RIVIAN_GW = "https://rivian.com/api/gql/gateway/graphql"
RIVIAN_CHRG = "https://rivian.com/api/gql/chrg/user/graphql"
CLIENT_NAME = "com.rivian.android.consumer"


class RivianAuthError(Exception):
    """Raised when authentication fails and cannot be recovered silently."""


class RivianMFARequired(Exception):
    """Raised when MFA/OTP is required. Contains the otp_token."""

    def __init__(self, otp_token: str) -> None:
        super().__init__("MFA required")
        self.otp_token = otp_token


class RivianClient:
    """Async Rivian API client with silent token refresh."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._csrf_token: str | None = None
        self._app_session: str | None = None
        self._user_session: str | None = None
        self._refresh_token: str | None = None

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def create_csrf_token(self) -> None:
        """Obtain a CSRF token and app-session token."""
        payload = {
            "operationName": "CreateCSRFToken",
            "variables": [],
            "query": "mutation CreateCSRFToken { createCsrfToken { __typename csrfToken appSessionToken } }",
        }
        data = await self._post(RIVIAN_GW, payload, auth=False)
        token_data = data["data"]["createCsrfToken"]
        self._csrf_token = token_data["csrfToken"]
        self._app_session = token_data["appSessionToken"]
        _LOGGER.debug("CSRF token obtained")

    async def login(self, email: str, password: str) -> None:
        """Log in with email/password.

        Raises RivianMFARequired if OTP is needed.
        Raises RivianAuthError on failure.
        """
        await self.create_csrf_token()
        payload = {
            "operationName": "Login",
            "variables": {"email": email, "password": password},
            "query": (
                "mutation Login($email: String!, $password: String!) { "
                "login(email: $email, password: $password) { "
                "__typename "
                "... on MobileLoginResponse { accessToken refreshToken userSessionToken } "
                "... on MobileMFALoginResponse { otpToken } "
                "} }"
            ),
        }
        data = await self._post(RIVIAN_GW, payload, auth=True)
        result = data["data"]["login"]
        typename = result.get("__typename")
        if typename == "MobileMFALoginResponse":
            raise RivianMFARequired(result["otpToken"])
        if typename == "MobileLoginResponse":
            self._user_session = result["userSessionToken"]
            self._refresh_token = result.get("refreshToken")
            _LOGGER.debug("Rivian login successful (no MFA)")
        else:
            raise RivianAuthError(f"Unexpected login response: {result}")

    async def login_with_otp(self, email: str, otp_code: str, otp_token: str) -> None:
        """Complete MFA login with OTP code."""
        payload = {
            "operationName": "LoginWithOTP",
            "variables": {
                "email": email,
                "otpCode": otp_code,
                "otpToken": otp_token,
            },
            "query": (
                "mutation LoginWithOTP($email: String!, $otpCode: String!, $otpToken: String!) { "
                "loginWithOTP(email: $email, otpCode: $otpCode, otpToken: $otpToken) { "
                "__typename accessToken refreshToken userSessionToken "
                "} }"
            ),
        }
        data = await self._post(RIVIAN_GW, payload, auth=True)
        result = data["data"]["loginWithOTP"]
        if "userSessionToken" not in result:
            raise RivianAuthError(f"OTP login failed: {result}")
        self._user_session = result["userSessionToken"]
        self._refresh_token = result.get("refreshToken")
        _LOGGER.debug("Rivian OTP login successful")

    async def refresh_tokens(self) -> bool:
        """Silently refresh session using the stored refresh token.

        Returns True on success, False if refresh token is also expired.
        This should be called when API calls return auth errors, before
        falling back to full re-login.
        """
        if not self._refresh_token:
            _LOGGER.debug("No refresh token available — cannot refresh silently")
            return False

        try:
            await self.create_csrf_token()
            payload = {
                "operationName": "LoginWithToken",
                "variables": {"token": self._refresh_token},
                "query": (
                    "mutation LoginWithToken($token: String!) { "
                    "loginWithToken(token: $token) { "
                    "__typename "
                    "... on MobileLoginResponse { accessToken refreshToken userSessionToken } "
                    "} }"
                ),
            }
            data = await self._post(RIVIAN_GW, payload, auth=True)
            result = data["data"].get("loginWithToken", {})
            if result.get("__typename") == "MobileLoginResponse":
                self._user_session = result["userSessionToken"]
                self._refresh_token = result.get("refreshToken", self._refresh_token)
                _LOGGER.info("Rivian tokens refreshed silently")
                return True
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Silent token refresh failed: %s", err)
        return False

    def is_authenticated(self) -> bool:
        return bool(self._csrf_token and self._app_session and self._user_session)

    def restore_session(
        self,
        csrf_token: str,
        app_session: str,
        user_session: str,
        refresh_token: str = "",
    ) -> None:
        """Restore a previously saved session (no network call needed)."""
        self._csrf_token = csrf_token
        self._app_session = app_session
        self._user_session = user_session
        self._refresh_token = refresh_token or None

    def get_session_tokens(self) -> dict[str, str]:
        """Return tokens that can be persisted to HA storage."""
        return {
            "csrf_token": self._csrf_token or "",
            "app_session": self._app_session or "",
            "user_session": self._user_session or "",
            "refresh_token": self._refresh_token or "",
        }

    # ------------------------------------------------------------------
    # Vehicle state
    # ------------------------------------------------------------------

    async def get_vehicle_state(self, vehicle_id: str) -> dict[str, Any]:
        """Return vehicle state (charger, battery, location)."""
        payload = {
            "operationName": "GetVehicleState",
            "variables": {"vehicleID": vehicle_id},
            "query": (
                "query GetVehicleState($vehicleID: String!) { "
                "vehicleState(id: $vehicleID) { "
                "batteryLevel { value timeStamp } "
                "chargerState { value timeStamp } "
                "chargerStatus { value timeStamp } "
                "batteryLimit { value timeStamp } "
                "remoteChargingAvailable { value timeStamp } "
                "powerState { value timeStamp } "
                "gnssLocation { latitude longitude timeStamp } "
                "} }"
            ),
        }
        return (await self._post_with_refresh(RIVIAN_GW, payload))["data"]["vehicleState"]

    # ------------------------------------------------------------------
    # Charging schedule
    # ------------------------------------------------------------------

    async def get_charging_schedule(self, vehicle_id: str) -> list[dict]:
        """Return current charging schedules for the vehicle."""
        payload = {
            "operationName": "GetChargingSchedule",
            "variables": {"vehicleId": vehicle_id},
            "query": (
                "query GetChargingSchedule($vehicleId: String!) { "
                "getVehicle(id: $vehicleId) { "
                "chargingSchedules { startTime duration location { latitude longitude } amperage enabled weekDays } "
                "} }"
            ),
        }
        return (await self._post_with_refresh(RIVIAN_GW, payload))["data"]["getVehicle"]["chargingSchedules"]

    async def set_charging_schedule(
        self,
        vehicle_id: str,
        amperage: int,
        latitude: float,
        longitude: float,
        enabled: bool = True,
    ) -> bool:
        """Set a charging schedule to the given amperage.

        Uses an all-day, all-week schedule so the car accepts power immediately.
        Amperage of 0 → enabled=False (schedule present but disabled).
        Returns True on success.
        """
        if amperage <= 0:
            enabled = False

        schedule = {
            "weekDays": [
                "Monday", "Tuesday", "Wednesday",
                "Thursday", "Friday", "Saturday", "Sunday",
            ],
            "startTime": 0,
            "duration": 1440,
            "location": {"latitude": latitude, "longitude": longitude},
            "amperage": max(8, amperage) if enabled else 8,
            "enabled": enabled,
        }
        payload = {
            "operationName": "SetChargingSchedule",
            "variables": {
                "vehicleId": vehicle_id,
                "chargingSchedules": [schedule],
            },
            "query": (
                "mutation SetChargingSchedule($vehicleId: String!, $chargingSchedules: [InputChargingSchedule!]!) { "
                "setChargingSchedules(vehicleId: $vehicleId, chargingSchedules: $chargingSchedules) { success } }"
            ),
        }
        return (await self._post_with_refresh(RIVIAN_GW, payload))["data"]["setChargingSchedules"]["success"]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _post_with_refresh(self, url: str, payload: dict) -> dict[str, Any]:
        """POST with automatic silent token refresh on auth failure."""
        try:
            return await self._post(url, payload)
        except RivianAuthError:
            _LOGGER.info("Auth error — attempting silent token refresh")
            if await self.refresh_tokens():
                return await self._post(url, payload)
            raise

    async def _post(
        self, url: str, payload: dict, *, auth: bool = True
    ) -> dict[str, Any]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "apollographql-client-name": CLIENT_NAME,
        }
        if auth and self._app_session:
            headers["a-sess"] = self._app_session
        if auth and self._csrf_token:
            headers["csrf-token"] = self._csrf_token
        if auth and self._user_session:
            headers["u-sess"] = self._user_session

        async with self._session.post(url, json=payload, headers=headers) as resp:
            resp.raise_for_status()
            data: dict = await resp.json()
            if "errors" in data:
                raise RivianAuthError(f"GraphQL error: {data['errors']}")
            return data
