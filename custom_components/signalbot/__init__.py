"""The Signalbot integration.

The config entry stores only the companion add-on manager URL
(``entry.data[CONF_MANAGER_URL]``). Everything else (linked number, the bundled
signal-cli-rest-api base URL, recipients, polling preferences) is fetched at
runtime from the add-on's manager API by the coordinator.
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import SignalApiClient, SignalApiError, SignalManagerClient
from .const import (
    ATTR_ATTACHMENTS,
    ATTR_MESSAGE,
    ATTR_RECIPIENTS,
    CONF_MANAGER_URL,
    CONF_RECIPIENT_NAME,
    DEFAULT_NAME,
    DOMAIN,
    MANUFACTURER,
    PLATFORMS,
    SERVICE_SEND_MESSAGE,
    format_recipient,
)
from .coordinator import SignalbotCoordinator

_LOGGER = logging.getLogger(__name__)

SEND_MESSAGE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_MESSAGE): cv.string,
        vol.Required(ATTR_RECIPIENTS): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_ATTACHMENTS): vol.All(cv.ensure_list, [cv.string]),
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Signalbot integration (no YAML configuration)."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Signalbot from a config entry."""
    session = async_get_clientsession(hass)
    manager = SignalManagerClient(session, entry.data[CONF_MANAGER_URL])

    coordinator = SignalbotCoordinator(hass, manager, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "manager": manager,
        "coordinator": coordinator,
    }

    # Register the account device so it exists even before entities are added.
    device_name = (coordinator.data or {}).get("device_name") or DEFAULT_NAME
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=device_name,
        manufacturer=MANUFACTURER,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register the send_message service once for the whole integration.
    if not hass.services.has_service(DOMAIN, SERVICE_SEND_MESSAGE):
        _async_register_services(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN, None)
            hass.services.async_remove(DOMAIN, SERVICE_SEND_MESSAGE)
    return unload_ok


def _async_register_services(hass: HomeAssistant) -> None:
    """Register integration-level services."""

    async def async_handle_send_message(call: ServiceCall) -> None:
        """Handle the send_message service call.

        Uses the first loaded config entry's coordinator (number + api_url).
        With multiple accounts, the first loaded one is used.
        """
        entries: dict[str, Any] = hass.data.get(DOMAIN, {})
        if not entries:
            raise ServiceValidationError("No Signalbot account is configured")

        # Use the first loaded entry.
        entry_id = next(iter(entries))
        coordinator: SignalbotCoordinator = entries[entry_id]["coordinator"]
        data = coordinator.data or {}

        if not data.get("linked"):
            raise ServiceValidationError("Signal account is not linked")

        number = coordinator.number
        api_url = coordinator.api_url
        if not number or not api_url:
            raise ServiceValidationError("No linked Signal number available")

        message: str = call.data[ATTR_MESSAGE]
        raw_recipients: list[str] = call.data[ATTR_RECIPIENTS]
        attachments: list[str] | None = call.data.get(ATTR_ATTACHMENTS)

        # Build a name -> address lookup from the configured recipients (from the
        # add-on manager, surfaced on the coordinator data).
        configured: list[dict[str, Any]] = data.get("recipients", [])
        name_to_address: dict[str, str] = {}
        for recipient in configured:
            name = recipient.get(CONF_RECIPIENT_NAME)
            address = format_recipient(recipient)
            if name and address:
                name_to_address[name.strip().casefold()] = address

        resolved: list[str] = []
        for raw in raw_recipients:
            key = raw.strip().casefold()
            resolved.append(name_to_address.get(key, raw))

        session = async_get_clientsession(hass)
        client = SignalApiClient(session, api_url)
        try:
            await client.async_send_message(
                number, message, resolved, attachments=attachments
            )
        except SignalApiError as err:
            raise HomeAssistantError(f"Failed to send Signal message: {err}") from err

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_MESSAGE,
        async_handle_send_message,
        schema=SEND_MESSAGE_SCHEMA,
    )
