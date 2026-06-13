"""DataUpdateCoordinator for the Signalbot integration.

The coordinator reads everything from the companion add-on's manager API at
runtime (number, the bundled signal-cli-rest-api base URL, recipients, polling
preferences) and then polls signal-cli-rest-api directly for incoming messages.
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

# Only "normal" (and "native") modes support HTTP GET polling on /v1/receive.
# In json-rpc modes the receive endpoint is WebSocket-only, so polling is skipped.
_HTTP_RECEIVE_MODES = frozenset({"normal", "native"})


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
        # One-time-warning guard so log lines are not spammed on every poll.
        self._jsonrpc_warned: bool = False

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

        # Receive incoming messages (best effort; never fails the update).
        if linked and self._api_client is not None:
            if mode in _HTTP_RECEIVE_MODES:
                self._jsonrpc_warned = False
                await self._async_receive(
                    number, recipients, bool(known_senders_only)
                )
            elif not self._jsonrpc_warned:
                self._jsonrpc_warned = True
                _LOGGER.warning(
                    "Signalbot message receiving requires signal-cli-rest-api "
                    "MODE=normal, but it is running in '%s' mode (WebSocket-only "
                    "receive). The add-on defaults to normal mode; receiving is "
                    "skipped.",
                    mode,
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

    async def _async_receive(
        self,
        number: str | None,
        recipients: list[dict[str, Any]],
        known_senders_only: bool,
    ) -> None:
        """Poll for incoming messages and fire events for text messages.

        Receiving errors are logged but never fail the overall update.
        """
        if not number or self._api_client is None:
            return

        try:
            envelopes = await self._api_client.async_receive(number)
        except SignalApiError as err:
            _LOGGER.debug("Error while receiving Signal messages: %s", err)
            return

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

            matched = match_recipient(recipients, source, source_uuid)

            if known_senders_only and matched is None:
                # Sender is not in the configured recipients list — silently ignore.
                continue

            recipient_id = matched.get(CONF_ID) if matched else None
            recipient_name = matched.get(CONF_RECIPIENT_NAME) if matched else None

            message: dict[str, Any] = {
                "source": source,
                "source_uuid": source_uuid,
                "source_name": envelope.get("sourceName"),
                "message": text,
                "timestamp": envelope.get("timestamp"),
                "recipient_id": recipient_id,
                "recipient_name": recipient_name,
            }

            self.last_message = message

            self.hass.bus.async_fire(
                EVENT_MESSAGE_RECEIVED,
                {**message, "config_entry_id": self.entry.entry_id},
            )
