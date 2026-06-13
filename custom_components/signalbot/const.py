"""Constants for the Signalbot integration."""
from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "signalbot"
DEFAULT_NAME = "Signalbot"
MANUFACTURER = "Signal"  # used as device manufacturer

PLATFORMS: list[Platform] = [Platform.NOTIFY, Platform.SENSOR]

# Config entry data keys
# The integration now stores only the companion add-on manager URL in
# ``entry.data``. Everything else (number, api_url, recipients, …) is fetched
# at runtime from the add-on's manager API (GET {manager_url}/api/config).
CONF_MANAGER_URL = "manager_url"

# Add-on / runtime config keys (these come from the manager API, not entry.options).
CONF_POLL_INTERVAL = "poll_interval"
CONF_KNOWN_SENDERS_ONLY = "known_senders_only"

# Recipient dict keys
CONF_ID = "id"
CONF_RECIPIENT_NAME = "name"
CONF_PHONE = "phone_number"
CONF_USERNAME = "username"
CONF_PREFER = "prefer"        # "phone" | "username"

PREFER_PHONE = "phone"
PREFER_USERNAME = "username"

# Defaults
DEFAULT_POLL_INTERVAL = 5
MIN_POLL_INTERVAL = 2
DEFAULT_KNOWN_SENDERS_ONLY = True
DEFAULT_SIGNAL_API_PORT = 8080

EVENT_MESSAGE_RECEIVED = "signalbot_message_received"

# Service
SERVICE_SEND_MESSAGE = "send_message"
ATTR_MESSAGE = "message"
ATTR_RECIPIENTS = "recipients"
ATTR_ATTACHMENTS = "attachments"

# NOTE: Confirmed against .dev/research.md (signal-cli man page / signal-cli-rest-api):
# Signal usernames require a "u:" prefix when used as a recipient address, e.g.
# "u:alice.1234". The prefix is passed through directly to signal-cli, which
# resolves the username internally (there is no separate resolve endpoint).
USERNAME_ADDRESS_PREFIX: str = "u:"  # Confirmed required prefix for usernames.


def _format_username(username: str) -> str:
    """Return the username in the address format expected by signal-cli-rest-api.

    Signal usernames require the ``u:`` prefix (per .dev/research.md). The prefix
    is added only if not already present, to avoid double-prefixing.
    """
    username = username.strip()
    if not username:
        return username
    if USERNAME_ADDRESS_PREFIX and not username.startswith(USERNAME_ADDRESS_PREFIX):
        return f"{USERNAME_ADDRESS_PREFIX}{username}"
    return username


def format_recipient(recipient: dict) -> str | None:
    """Return the signal-cli-rest-api address string for a recipient dict.

    A recipient may have a phone number and/or a username; ``prefer`` selects which
    to use, falling back to whichever is present.  Returns ``None`` if neither is
    set.  Phone numbers are returned as-is (E.164).  Usernames are returned through
    ``_format_username()`` so the exact wire format can be adjusted in one place.
    """
    prefer: str = recipient.get(CONF_PREFER, PREFER_PHONE)
    phone: str = (recipient.get(CONF_PHONE) or "").strip()
    username: str = (recipient.get(CONF_USERNAME) or "").strip()

    if prefer == PREFER_USERNAME:
        if username:
            return _format_username(username)
        if phone:
            return phone
    else:
        # Default: prefer phone
        if phone:
            return phone
        if username:
            return _format_username(username)

    return None


def match_recipient(
    recipients: list[dict], source: str | None, source_uuid: str | None = None
) -> dict | None:
    """Return the configured recipient that matches an incoming message sender, else None.

    Matching is by phone number (E.164) against each recipient's CONF_PHONE, comparing
    whitespace-stripped values. (Signal receive envelopes identify the sender by phone
    number / UUID, not by @username, so username-only recipients cannot be matched on
    incoming messages — they can still be messaged TO. This is a Signal limitation.)
    """
    if not source:
        return None
    normalized_source = source.strip()
    for recipient in recipients:
        phone: str = (recipient.get(CONF_PHONE) or "").strip()
        if phone and phone == normalized_source:
            return recipient
    return None
