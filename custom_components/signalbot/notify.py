"""Notify platform for the Signalbot integration."""
from __future__ import annotations

import logging

from homeassistant.components.notify import NotifyEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_info import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import SignalApiClient, SignalApiError
from .const import (
    CONF_DEVICE_NAME,
    CONF_ID,
    CONF_NUMBER,
    CONF_RECIPIENT_NAME,
    CONF_RECIPIENTS,
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
    """Set up one notify entity per recipient with a usable address."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: SignalbotCoordinator = data["coordinator"]
    client: SignalApiClient = data["client"]

    entities: list[SignalbotNotifyEntity] = []
    recipients = entry.options.get(CONF_RECIPIENTS, [])
    for recipient in recipients:
        address = format_recipient(recipient)
        if not address:
            continue
        entities.append(
            SignalbotNotifyEntity(coordinator, client, entry, recipient, address)
        )

    async_add_entities(entities)


class SignalbotNotifyEntity(NotifyEntity):
    """Notify entity that sends a Signal message to a single configured recipient."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SignalbotCoordinator,
        client: SignalApiClient,
        entry: ConfigEntry,
        recipient: dict,
        address: str,
    ) -> None:
        """Initialise the notify entity."""
        self._client = client
        self._number = entry.data[CONF_NUMBER]
        self._address = address

        recipient_id = recipient.get(CONF_ID)
        self._attr_name = recipient.get(CONF_RECIPIENT_NAME) or address
        self._attr_unique_id = f"{entry.entry_id}_notify_{recipient_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data.get(CONF_DEVICE_NAME) or DEFAULT_NAME,
            manufacturer=MANUFACTURER,
        )

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        """Send a message to this recipient."""
        try:
            await self._client.async_send_message(
                self._number, message, [self._address]
            )
        except SignalApiError as err:
            raise HomeAssistantError(
                f"Failed to send Signal message to {self._address}: {err}"
            ) from err
