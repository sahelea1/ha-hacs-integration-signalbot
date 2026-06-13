"""Notify platform for the Signalbot integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.notify import NotifyEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_info import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import SignalApiClient, SignalApiError
from .const import (
    CONF_ID,
    CONF_RECIPIENT_NAME,
    DEFAULT_NAME,
    DOMAIN,
    MANUFACTURER,
    format_recipient,
)
from .coordinator import SignalbotCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one notify entity per recipient with a usable address.

    Recipients come from the coordinator data (sourced from the add-on manager).
    When the recipient set changes the coordinator reloads the entry, so this
    runs again and the entities are rebuilt.
    """
    coordinator: SignalbotCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    recipients: list[dict[str, Any]] = coordinator.data.get("recipients", [])
    device_name = coordinator.data.get("device_name") or DEFAULT_NAME

    entities: list[SignalbotNotifyEntity] = []
    for recipient in recipients:
        address = format_recipient(recipient)
        if not address:
            continue
        entities.append(
            SignalbotNotifyEntity(coordinator, entry, recipient, address, device_name)
        )

    async_add_entities(entities)


class SignalbotNotifyEntity(NotifyEntity):
    """Notify entity that sends a Signal message to a single configured recipient."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SignalbotCoordinator,
        entry: ConfigEntry,
        recipient: dict[str, Any],
        address: str,
        device_name: str,
    ) -> None:
        """Initialise the notify entity."""
        self._coordinator = coordinator
        self._address = address

        recipient_id = recipient.get(CONF_ID)
        self._attr_name = recipient.get(CONF_RECIPIENT_NAME) or address
        self._attr_unique_id = f"{entry.entry_id}_notify_{recipient_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=device_name,
            manufacturer=MANUFACTURER,
        )

    def _get_client(self) -> SignalApiClient:
        """Return a usable signal-cli-rest-api client, reusing the coordinator's."""
        client = self._coordinator.get_api_client()
        if client is not None:
            return client
        api_url = self._coordinator.api_url
        if not api_url:
            raise HomeAssistantError("Signal API URL is not available yet")
        session = async_get_clientsession(self._coordinator.hass)
        return SignalApiClient(session, api_url)

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        """Send a message to this recipient."""
        if not self._coordinator.data.get("linked"):
            raise HomeAssistantError("Signal account is not linked")
        number = self._coordinator.number
        if not number:
            raise HomeAssistantError("No linked Signal number available")

        client = self._get_client()
        try:
            await client.async_send_message(number, message, [self._address])
        except SignalApiError as err:
            raise HomeAssistantError(
                f"Failed to send Signal message to {self._address}: {err}"
            ) from err
