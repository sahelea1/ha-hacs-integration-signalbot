"""Sensor platform for the Signalbot integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_info import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_DEVICE_NAME,
    CONF_RECIPIENTS,
    DEFAULT_NAME,
    DOMAIN,
    MANUFACTURER,
)
from .coordinator import SignalbotCoordinator

_LOGGER = logging.getLogger(__name__)

_STATE_MAX_LEN = 255


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Signalbot sensors."""
    coordinator: SignalbotCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    async_add_entities(
        [
            SignalbotStatusSensor(coordinator, entry),
            SignalbotLastMessageSensor(coordinator, entry),
        ]
    )


class _SignalbotBaseSensor(CoordinatorEntity[SignalbotCoordinator], SensorEntity):
    """Base class providing shared device info."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SignalbotCoordinator, entry: ConfigEntry) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.data.get(CONF_DEVICE_NAME) or DEFAULT_NAME,
            manufacturer=MANUFACTURER,
        )


class SignalbotStatusSensor(_SignalbotBaseSensor):
    """Sensor reporting the connection status of the Signal API."""

    _attr_icon = "mdi:message-text"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SignalbotCoordinator, entry: ConfigEntry) -> None:
        """Initialise the status sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_status"
        self._attr_name = "Status"

    @property
    def native_value(self) -> str:
        """Return the connection state."""
        return "connected" if self.coordinator.healthy else "disconnected"

    @property
    def available(self) -> bool:
        """Return True if the coordinator's last update succeeded."""
        return self.coordinator.last_update_success

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic attributes."""
        recipients = self._entry.options.get(CONF_RECIPIENTS, [])
        return {
            "number": self.coordinator.number,
            "mode": self.coordinator.mode,
            "version": self.coordinator.version,
            "recipient_count": len(recipients),
        }


class SignalbotLastMessageSensor(_SignalbotBaseSensor):
    """Sensor reporting the last received Signal message."""

    _attr_icon = "mdi:message-arrow-left"

    def __init__(self, coordinator: SignalbotCoordinator, entry: ConfigEntry) -> None:
        """Initialise the last-message sensor."""
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_last_message"
        self._attr_name = "Last message"

    @property
    def native_value(self) -> str | None:
        """Return the last received message text, truncated to the state limit."""
        message = self.coordinator.last_message
        if not message:
            return None
        text = message.get("message")
        if not isinstance(text, str):
            return None
        if len(text) > _STATE_MAX_LEN:
            return text[:_STATE_MAX_LEN]
        return text

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return details of the last received message."""
        message = self.coordinator.last_message or {}
        return {
            "source": message.get("source"),
            "source_name": message.get("source_name"),
            "timestamp": message.get("timestamp"),
            "group_id": message.get("group_id"),
            "full_message": message.get("message"),
        }
