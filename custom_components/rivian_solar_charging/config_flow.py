"""Config flow for Rivian Solar Charging."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_REFRESH_TOKEN,
    CONF_APP_SESSION,
    CONF_BATTERY_LIMIT,
    CONF_CHARGE_LIMIT,
    CONF_CSRF_TOKEN,
    CONF_EMAIL,
    CONF_GRID_POWER_ENTITY,
    CONF_HOME_LAT,
    CONF_HOME_LNG,
    CONF_PASSWORD,
    CONF_POWERWALL_ENTITY,
    CONF_POWERWALL_STOP_PCT,
    CONF_RIVIAN_START_LIMIT,
    CONF_SCAN_INTERVAL,
    CONF_USER_SESSION,
    CONF_VEHICLE_ID,
    DEFAULT_CHARGE_LIMIT,
    DEFAULT_POWERWALL_MIN_PCT,
    DEFAULT_POWERWALL_STOP_PCT,
    DEFAULT_RIVIAN_START_LIMIT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .rivian_client import RivianAuthError, RivianMFARequired, RivianClient

_LOGGER = logging.getLogger(__name__)


class RivianSolarChargingConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle setup flow: credentials → (optional MFA) → entities & location."""

    VERSION = 1

    def __init__(self) -> None:
        self._email: str = ""
        self._password: str = ""
        self._otp_token: str = ""
        self._csrf_token: str = ""
        self._app_session: str = ""
        self._user_session: str = ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            self._email = user_input[CONF_EMAIL]
            self._password = user_input[CONF_PASSWORD]
            try:
                async with aiohttp.ClientSession() as http:
                    client = RivianClient(http)
                    await client.login(self._email, self._password)
                    tokens = client.get_session_tokens()
                    self._csrf_token = tokens["csrf_token"]
                    self._app_session = tokens["app_session"]
                    self._user_session = tokens["user_session"]
                return await self.async_step_vehicle()
            except RivianMFARequired as exc:
                self._otp_token = exc.otp_token
                async with aiohttp.ClientSession() as http:
                    client = RivianClient(http)
                    await client.create_csrf_token()
                    tokens = client.get_session_tokens()
                    self._csrf_token = tokens["csrf_token"]
                    self._app_session = tokens["app_session"]
                return await self.async_step_mfa()
            except RivianAuthError:
                errors["base"] = "invalid_auth"
            except aiohttp.ClientError:
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_EMAIL): str,
                vol.Required(CONF_PASSWORD): str,
            }),
            errors=errors,
        )

    async def async_step_mfa(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                async with aiohttp.ClientSession() as http:
                    client = RivianClient(http)
                    client.restore_session(self._csrf_token, self._app_session, "")
                    await client.login_with_otp(
                        self._email, user_input["otp_code"], self._otp_token
                    )
                    tokens = client.get_session_tokens()
                    self._csrf_token = tokens["csrf_token"]
                    self._app_session = tokens["app_session"]
                    self._user_session = tokens["user_session"]
                return await self.async_step_vehicle()
            except RivianAuthError:
                errors["base"] = "invalid_otp"
            except aiohttp.ClientError:
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="mfa",
            data_schema=vol.Schema({vol.Required("otp_code"): str}),
            description_placeholders={"email": self._email},
            errors=errors,
        )

    async def async_step_vehicle(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            for key in (CONF_POWERWALL_ENTITY, CONF_GRID_POWER_ENTITY):
                if self.hass.states.get(user_input[key]) is None:
                    errors[key] = "entity_not_found"
            if not errors:
                return self.async_create_entry(
                    title=f"Rivian Solar Charging ({user_input[CONF_VEHICLE_ID][:8]}…)",
                    data={
                        CONF_EMAIL: self._email,
                        CONF_PASSWORD: self._password,
                        CONF_CSRF_TOKEN: self._csrf_token,
                        CONF_APP_SESSION: self._app_session,
                        CONF_USER_SESSION: self._user_session,
                        CONF_REFRESH_TOKEN: tokens.get("refresh_token", ""),
                        **user_input,
                    },
                )

        home_state = self.hass.states.get("zone.home")
        default_lat = home_state.attributes.get("latitude", 0.0) if home_state else 0.0
        default_lng = home_state.attributes.get("longitude", 0.0) if home_state else 0.0

        return self.async_show_form(
            step_id="vehicle",
            data_schema=vol.Schema({
                vol.Required(CONF_VEHICLE_ID): str,
                vol.Required(
                    CONF_POWERWALL_ENTITY,
                    ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Required(
                    CONF_GRID_POWER_ENTITY,
                    ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Required(CONF_HOME_LAT, default=default_lat): vol.Coerce(float),
                vol.Required(CONF_HOME_LNG, default=default_lng): vol.Coerce(float),
                vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(
                    vol.Coerce(int), vol.Range(min=60, max=3600)
                ),
                vol.Optional(CONF_CHARGE_LIMIT, default=DEFAULT_CHARGE_LIMIT): vol.All(
                    vol.Coerce(int), vol.Range(min=50, max=100)
                ),
                vol.Optional(CONF_BATTERY_LIMIT, default=DEFAULT_POWERWALL_MIN_PCT): vol.All(
                    vol.Coerce(int), vol.Range(min=80, max=100)
                ),
                vol.Optional(CONF_POWERWALL_STOP_PCT, default=DEFAULT_POWERWALL_STOP_PCT): vol.All(
                    vol.Coerce(int), vol.Range(min=20, max=90)
                ),
                vol.Optional(CONF_RIVIAN_START_LIMIT, default=DEFAULT_RIVIAN_START_LIMIT): vol.All(
                    vol.Coerce(int), vol.Range(min=50, max=100)
                ),
            }),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> RivianSolarOptionsFlow:
        return RivianSolarOptionsFlow(config_entry)


class RivianSolarOptionsFlow(config_entries.OptionsFlow):
    """Allow tweaking all tunable parameters without re-adding the integration."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        d = {**self._entry.data, **self._entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(CONF_SCAN_INTERVAL,
                    default=d.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                ): vol.All(vol.Coerce(int), vol.Range(min=60, max=3600)),
                vol.Optional(CONF_CHARGE_LIMIT,
                    default=d.get(CONF_CHARGE_LIMIT, DEFAULT_CHARGE_LIMIT)
                ): vol.All(vol.Coerce(int), vol.Range(min=50, max=100)),
                vol.Optional(CONF_BATTERY_LIMIT,
                    default=d.get(CONF_BATTERY_LIMIT, DEFAULT_POWERWALL_MIN_PCT)
                ): vol.All(vol.Coerce(int), vol.Range(min=80, max=100)),
                vol.Optional(CONF_POWERWALL_STOP_PCT,
                    default=d.get(CONF_POWERWALL_STOP_PCT, DEFAULT_POWERWALL_STOP_PCT)
                ): vol.All(vol.Coerce(int), vol.Range(min=20, max=90)),
                vol.Optional(CONF_RIVIAN_START_LIMIT,
                    default=d.get(CONF_RIVIAN_START_LIMIT, DEFAULT_RIVIAN_START_LIMIT)
                ): vol.All(vol.Coerce(int), vol.Range(min=50, max=100)),
            }),
        )
