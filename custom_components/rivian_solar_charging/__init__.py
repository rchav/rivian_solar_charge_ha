"""Rivian Solar Charging — Home Assistant integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_APP_SESSION,
    CONF_CSRF_TOKEN,
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    CONF_USER_SESSION,
    DOMAIN,
)
from .coordinator import SolarChargingCoordinator
from .rivian_client import RivianAuthError, RivianMFARequired, RivianClient

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "switch"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the integration from a config entry."""
    session = async_get_clientsession(hass)
    client = RivianClient(session)

    csrf = entry.data.get(CONF_CSRF_TOKEN, "")
    app_sess = entry.data.get(CONF_APP_SESSION, "")
    user_sess = entry.data.get(CONF_USER_SESSION, "")
    refresh_tok = entry.data.get(CONF_REFRESH_TOKEN, "")

    if csrf and app_sess and user_sess:
        # Restore saved session — includes refresh token for silent renewal
        client.restore_session(csrf, app_sess, user_sess, refresh_tok)
        _LOGGER.debug("Restored Rivian session from config entry")
    else:
        # No saved tokens — fresh login (will trigger MFA prompt)
        try:
            await client.login(entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD])
            _persist_tokens(hass, entry, client)
        except RivianMFARequired:
            raise ConfigEntryNotReady(
                "Rivian MFA required. Please re-add the integration to enter your OTP."
            )
        except RivianAuthError as err:
            raise ConfigEntryNotReady(f"Rivian auth failed: {err}") from err

    coordinator = SolarChargingCoordinator(hass, client, dict(entry.data))

    # After first successful API call, persist any refreshed tokens
    await coordinator.async_config_entry_first_refresh()
    _persist_tokens(hass, entry, client)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


def _persist_tokens(
    hass: HomeAssistant, entry: ConfigEntry, client: RivianClient
) -> None:
    """Save current tokens back to config entry so they survive restarts."""
    tokens = client.get_session_tokens()
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            CONF_CSRF_TOKEN: tokens["csrf_token"],
            CONF_APP_SESSION: tokens["app_session"],
            CONF_USER_SESSION: tokens["user_session"],
            CONF_REFRESH_TOKEN: tokens["refresh_token"],
        },
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the integration."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
