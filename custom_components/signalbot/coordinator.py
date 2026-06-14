"""DataUpdateCoordinator for the Signalbot integration.

The coordinator reads everything from the companion add-on's manager API at
runtime (number, the bundled signal-cli-rest-api base URL, recipients, polling
preferences) and consumes incoming messages from the manager's message buffer.
The signal-cli-rest-api client is still derived so notify entities and the
send_message service can send messages.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import SignalApiClient, SignalApiError, SignalManagerClient
from .const import (
    CONF_ID,
    CONF_PHONE,
    CONF_RECIPIENT_NAME,
    DEFAULT_KNOWN_SENDERS_ONLY,
    DEFAULT_NAME,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_SIGNAL_API_PORT,
    DOMAIN,
    EVENT_MESSAGE_RECEIVED,
    MIN_POLL_INTERVAL,
    match_recipient,
)

_LOGGER = logging.getLogger(__name__)


class SignalbotCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that reads add-on config and polls for incoming messages."""

    def __init__(
        self,
        hass: HomeAssistant,
        manager_client: SignalManagerClient,
        entry: ConfigEntry,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_POLL_INTERVAL),
        )
        self.manager_client = manager_client
        self.manager_url = manager_client.manager_url
        self.entry = entry

        self.last_message: dict[str, Any] | None = None
        self._recipients_hash: int | None = None
        self._api_client: SignalApiClient | None = None
        self.number: str | None = None
        self.api_url: str | None = None
        # Cursor of the highest message id consumed from the manager buffer.
        self._msg_cursor: int | None = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _derive_api_url(self, cfg_api_url: Any) -> str:
        """Return the signal-cli-rest-api base URL.

        Uses ``api_url`` from the manager config when present, otherwise derives
        ``http://{manager_host}:8080`` from the manager URL.
        """
        if isinstance(cfg_api_url, str) and cfg_api_url.strip():
            return cfg_api_url.strip().rstrip("/")
        hostname = urlparse(self.manager_url).hostname or "localhost"
        return f"http://{hostname}:{DEFAULT_SIGNAL_API_PORT}"

    @staticmethod
    def _hash_recipients(recipients: list[dict[str, Any]]) -> int:
        """Return a stable hash of the recipient set (ids + addresses)."""
        items = tuple(
            (
                str(recipient.get(CONF_ID)),
                str(recipient.get(CONF_PHONE) or ""),
                str(recipient.get("username") or ""),
            )
            for recipient in recipients
        )
        return hash(items)

    def get_api_client(self) -> SignalApiClient | None:
        """Return the current signal-cli-rest-api client, if any."""
        return self._api_client

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch add-on config and (optionally) poll for incoming messages."""
        try:
            cfg = await self.manager_client.async_get_config()
        except SignalApiError as err:
            raise UpdateFailed(
                f"Error communicating with Signalbot add-on manager: {err}"
            ) from err

        # Derive/refresh the signal-cli-rest-api client.
        api_url = self._derive_api_url(cfg.get("api_url"))
        if api_url != self.api_url or self._api_client is None:
            self.api_url = api_url
            session = async_get_clientsession(self.hass)
            self._api_client = SignalApiClient(session, api_url)

        number = cfg.get("number")
        linked = bool(cfg.get("linked"))
        mode = cfg.get("mode")
        version = cfg.get("version")
        recipients: list[dict[str, Any]] = cfg.get("recipients", []) or []
        known_senders_only = cfg.get("known_senders_only", DEFAULT_KNOWN_SENDERS_ONLY)
        poll_interval = cfg.get("poll_interval", DEFAULT_POLL_INTERVAL)
        device_name = cfg.get("device_name") or DEFAULT_NAME

        self.number = number

        # Apply a changed poll interval.
        if isinstance(poll_interval, (int, float)):
            new_interval = timedelta(
                seconds=max(MIN_POLL_INTERVAL, int(poll_interval))
            )
            if new_interval != self.update_interval:
                self.update_interval = new_interval

        # Detect recipient changes → reload the config entry so notify entities
        # are rebuilt. Skip the reload on the very first refresh (hash is None).
        new_hash = self._hash_recipients(recipients)
        if new_hash != self._recipients_hash:
            first_refresh = self._recipients_hash is None
            self._recipients_hash = new_hash
            if not first_refresh:
                self.hass.async_create_task(
                    self.hass.config_entries.async_reload(self.entry.entry_id)
                )

        # Consume incoming messages from the manager (best effort; never fails
        # the update). The manager is now the single drainer of signal-cli's
        # destructive receive queue.
        if linked:
            await self._async_consume_messages(
                recipients, bool(known_senders_only)
            )

        return {
            "healthy": True,
            "linked": linked,
            "number": number,
            "mode": mode,
            "version": version,
            "api_url": api_url,
            "recipients": recipients,
            "known_senders_only": bool(known_senders_only),
            "device_name": device_name,
            "last_message": self.last_message,
        }

    async def _async_consume_messages(
        self,
        recipients: list[dict[str, Any]],
        known_senders_only: bool,
    ) -> None:
        """Consume buffered incoming messages from the manager and fire events.

        Receiving errors are logged but never fail the overall update.
        """
        try:
            try:
                result = await self.manager_client.async_get_messages(
                    self._msg_cursor
                )
            except SignalApiError as err:
                _LOGGER.debug("Error while consuming Signal messages: %s", err)
                return

            messages = result.get("messages") or []
            last_id = result.get("last_id")

            # First run: adopt the current high-water mark without firing the
            # backlog so old messages don't refire on startup.
            if self._msg_cursor is None:
                self._msg_cursor = last_id if isinstance(last_id, int) else 0
                return

            # Manager restarted (counter reset lower) → resync the cursor and
            # avoid duplicate fires.
            if isinstance(last_id, int) and last_id < self._msg_cursor:
                self._msg_cursor = last_id
                return

            for m in messages:
                if not isinstance(m, dict):
                    continue

                if isinstance(m.get("id"), int):
                    self._msg_cursor = max(self._msg_cursor, m["id"])

                source = m.get("source")
                source_uuid = m.get("source_uuid")

                matched = match_recipient(recipients, source, source_uuid)

                if known_senders_only and matched is None:
                    # Sender is not in the configured recipients — silently ignore.
                    continue

                recipient_id = matched.get(CONF_ID) if matched else None
                recipient_name = (
                    matched.get(CONF_RECIPIENT_NAME) if matched else None
                )

                message: dict[str, Any] = {
                    "source": source,
                    "source_uuid": source_uuid,
                    "source_name": m.get("source_name"),
                    "message": m.get("message"),
                    "timestamp": m.get("timestamp"),
                    "recipient_id": recipient_id,
                    "recipient_name": recipient_name,
                    "command": m.get("command"),
                    "command_args": m.get("command_args"),
                }

                self.last_message = message

                self.hass.bus.async_fire(
                    EVENT_MESSAGE_RECEIVED,
                    {**message, "config_entry_id": self.entry.entry_id},
                )
        except Exception as err:  # noqa: BLE001
            # Receiving must never fail the whole update.
            _LOGGER.debug("Unexpected error while consuming Signal messages: %s", err)
