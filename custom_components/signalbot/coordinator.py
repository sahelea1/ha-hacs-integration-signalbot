"""DataUpdateCoordinator for the Signalbot integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SignalApiClient, SignalApiError
from .const import (
    CONF_ID,
    CONF_KNOWN_SENDERS_ONLY,
    CONF_NUMBER,
    CONF_POLL_INTERVAL,
    CONF_RECEIVE_ENABLED,
    CONF_RECIPIENT_NAME,
    CONF_RECIPIENTS,
    DEFAULT_KNOWN_SENDERS_ONLY,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    EVENT_MESSAGE_RECEIVED,
    match_recipient,
)

_LOGGER = logging.getLogger(__name__)

# Only "normal" (and "native") modes support HTTP GET polling on /v1/receive.
# In json-rpc modes the receive endpoint is WebSocket-only, so polling is skipped.
_HTTP_RECEIVE_MODES = frozenset({"normal", "native"})


class SignalbotCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that polls signal-cli-rest-api for health and incoming messages."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: SignalApiClient,
        entry: ConfigEntry,
    ) -> None:
        """Initialise the coordinator."""
        poll_interval = entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=poll_interval),
        )
        self.client = client
        self.entry = entry
        self.number: str = entry.data[CONF_NUMBER]
        self.last_message: dict[str, Any] | None = None
        self.mode: str | None = None
        self.version: str | None = None
        self.healthy: bool = False
        # One-time-warning guards so log lines are not spammed on every poll.
        self._jsonrpc_warned: bool = False

    @staticmethod
    def _extract_version(about: dict[str, Any]) -> str | None:
        """Extract a human-readable version string from /v1/about output."""
        version = about.get("version")
        if isinstance(version, str) and version:
            return version
        build = about.get("build")
        if build is not None:
            return str(build)
        versions = about.get("versions")
        if isinstance(versions, list) and versions:
            return ", ".join(str(v) for v in versions)
        return None

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch health info and (optionally) poll for incoming messages."""
        try:
            about = await self.client.async_about()
        except SignalApiError as err:
            self.healthy = False
            raise UpdateFailed(f"Error communicating with signal-cli-rest-api: {err}") from err

        mode = about.get("mode") if isinstance(about, dict) else None
        self.mode = mode
        self.version = self._extract_version(about) if isinstance(about, dict) else None
        self.healthy = True

        accounts: list[str] | None = None

        if self.entry.options.get(CONF_RECEIVE_ENABLED, True):
            if mode in _HTTP_RECEIVE_MODES:
                # Reset the warning guard so a later mode change re-warns if needed.
                self._jsonrpc_warned = False
                await self._async_receive()
            elif not self._jsonrpc_warned:
                self._jsonrpc_warned = True
                _LOGGER.warning(
                    "Signalbot message receiving is enabled but signal-cli-rest-api "
                    "is running in '%s' mode, which serves /v1/receive over WebSocket "
                    "only. HTTP polling cannot receive messages. Run signal-cli-rest-api "
                    "with MODE=normal to enable message receiving.",
                    mode,
                )

        return {
            "healthy": True,
            "mode": mode,
            "version": self.version,
            "last_message": self.last_message,
            "accounts": accounts,
        }

    async def _async_receive(self) -> None:
        """Poll for incoming messages and fire events for text messages.

        Receiving errors are logged but never fail the overall update.
        """
        try:
            envelopes = await self.client.async_receive(self.number)
        except SignalApiError as err:
            _LOGGER.debug("Error while receiving Signal messages: %s", err)
            return

        recipients: list[dict] = list(self.entry.options.get(CONF_RECIPIENTS, []))
        known_senders_only: bool = self.entry.options.get(
            CONF_KNOWN_SENDERS_ONLY, DEFAULT_KNOWN_SENDERS_ONLY
        )

        for item in envelopes:
            if not isinstance(item, dict):
                continue
            envelope = item.get("envelope")
            if not isinstance(envelope, dict):
                continue
            data_message = envelope.get("dataMessage")
            # Ignore receipts/typing/sync messages: only handle text data messages.
            if not isinstance(data_message, dict):
                continue
            text = data_message.get("message")
            if not text:
                continue

            source: str | None = envelope.get("source") or envelope.get("sourceNumber")
            source_uuid: str | None = envelope.get("sourceUuid")

            matched: dict | None = match_recipient(recipients, source, source_uuid)

            if known_senders_only and matched is None:
                # Sender is not in the configured recipients list — silently ignore.
                continue

            group_info = data_message.get("groupInfo")
            group_id: str | None = None
            if isinstance(group_info, dict):
                group_id = group_info.get("groupId")

            recipient_id: str | None = matched.get(CONF_ID) if matched else None
            recipient_name: str | None = matched.get(CONF_RECIPIENT_NAME) if matched else None

            message: dict[str, Any] = {
                "source": source,
                "source_name": envelope.get("sourceName"),
                "message": text,
                "timestamp": envelope.get("timestamp"),
                "recipient_id": recipient_id,
                "recipient_name": recipient_name,
            }
            if group_id:
                message["group_id"] = group_id

            self.last_message = message

            self.hass.bus.async_fire(
                EVENT_MESSAGE_RECEIVED,
                {**message, "config_entry_id": self.entry.entry_id},
            )
