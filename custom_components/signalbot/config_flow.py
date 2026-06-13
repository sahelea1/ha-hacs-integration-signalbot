"""Config and options flow for the Signalbot integration."""
from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from typing import Any
from uuid import uuid4

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SignalApiClient, SignalApiError, SignalApiResponseError
from .const import (
    CONF_API_URL,
    CONF_DEVICE_NAME,
    CONF_ID,
    CONF_NUMBER,
    CONF_PHONE,
    CONF_POLL_INTERVAL,
    CONF_PREFER,
    CONF_RECEIVE_ENABLED,
    CONF_RECIPIENT_NAME,
    CONF_RECIPIENTS,
    CONF_USERNAME,
    DEFAULT_API_URL,
    DEFAULT_DEVICE_NAME,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MIN_POLL_INTERVAL,
    PREFER_PHONE,
    PREFER_USERNAME,
)

_LOGGER = logging.getLogger(__name__)

# How often to poll /v1/accounts while waiting for the linked number to appear.
_LINK_POLL_INTERVAL = 2.0
# Maximum time to wait for the linked number to appear after the QR scan.
_LINK_POLL_TIMEOUT = 120.0

_PREFER_OPTIONS = [PREFER_PHONE, PREFER_USERNAME]


def _normalize_url(url: str) -> str:
    """Return *url* trimmed and without a trailing slash."""
    return url.strip().rstrip("/")


class SignalbotConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Signalbot config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise transient flow state."""
        self._api_url: str = ""
        self._device_name: str = DEFAULT_DEVICE_NAME
        self._number: str | None = None
        self._accounts: list[str] = []
        # Linking state.
        self._link_baseline: set[str] = set()
        self._link_task: asyncio.Task[str | None] | None = None
        # Registration state.
        self._reg_number: str = ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _client(self) -> SignalApiClient:
        """Return a Signal API client bound to the current api_url."""
        return SignalApiClient(async_get_clientsession(self.hass), self._api_url)

    async def _create_entry(self) -> ConfigFlowResult:
        """Create the config entry once the sender number is known."""
        assert self._number is not None
        await self.async_set_unique_id(self._number)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"Signalbot ({self._number})",
            data={
                CONF_API_URL: self._api_url,
                CONF_NUMBER: self._number,
                CONF_DEVICE_NAME: self._device_name,
            },
        )

    # ------------------------------------------------------------------
    # Step: user
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect the API URL and device name, then validate connectivity."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._api_url = _normalize_url(user_input[CONF_API_URL])
            self._device_name = user_input.get(
                CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME
            ).strip()

            client = self._client()
            try:
                await client.async_about()
                self._accounts = list(await client.async_list_accounts())
            except SignalApiError as err:
                _LOGGER.debug("Cannot connect to Signal API: %s", err)
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_menu()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_API_URL,
                    default=(user_input or {}).get(CONF_API_URL, DEFAULT_API_URL),
                ): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.URL)
                ),
                vol.Required(
                    CONF_DEVICE_NAME,
                    default=(user_input or {}).get(
                        CONF_DEVICE_NAME, DEFAULT_DEVICE_NAME
                    ),
                ): selector.TextSelector(),
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    # ------------------------------------------------------------------
    # Step: menu
    # ------------------------------------------------------------------

    async def async_step_menu(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user choose how to provide a sender account."""
        options = ["link", "register"]
        if self._accounts:
            options.append("existing")
        return self.async_show_menu(step_id="menu", menu_options=options)

    # ------------------------------------------------------------------
    # Step: link (QR-code device linking)
    # ------------------------------------------------------------------

    async def async_step_link(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show a QR code and wait for the linked number to appear.

        The step is entered three times:

        1. First call (``user_input is None`` and no task): fetch a fresh QR URI
           and show the form so the user can scan it.
        2. After Submit (``user_input is not None``): kick off the polling task
           and show a progress spinner.
        3. Progress re-entry (task done): finish the flow or retry on timeout.
        """
        client = self._client()

        # Phase 3: a polling task is in flight or has just finished.
        if self._link_task is not None:
            if not self._link_task.done():
                return self.async_show_progress(
                    step_id="link",
                    progress_action="linking",
                    progress_task=self._link_task,
                )

            task = self._link_task
            self._link_task = None
            try:
                number = task.result()
            except asyncio.CancelledError:  # noqa: BLE001 - dialog dismissed
                number = None
            except Exception:  # noqa: BLE001 - defensive
                _LOGGER.exception("Unexpected error while polling for linked account")
                number = None

            if number:
                self._number = number
                return self.async_show_progress_done(next_step_id="link_finish")

            # Timed out (or cancelled) without a new account: offer a retry.
            return self.async_show_progress_done(next_step_id="link_retry")

        # Phase 2: user clicked Submit on the QR form -> start polling.
        if user_input is not None:
            self._link_task = self.hass.async_create_task(
                self._async_wait_for_link(),
                "signalbot_link_poll",
            )
            return self.async_show_progress(
                step_id="link",
                progress_action="linking",
                progress_task=self._link_task,
            )

        # Phase 1: fetch a fresh link URI and snapshot the current accounts.
        try:
            self._link_baseline = set(await client.async_list_accounts())
            link_uri = await client.async_qrcodelink_uri(self._device_name)
        except SignalApiError as err:
            _LOGGER.debug("Failed to obtain QR link URI: %s", err)
            return self.async_show_form(
                step_id="link",
                data_schema=vol.Schema({}),
                errors={"base": "cannot_connect"},
            )

        schema = vol.Schema(
            {
                vol.Optional("qr"): selector.QrCodeSelector(
                    config=selector.QrCodeSelectorConfig(
                        data=link_uri,
                        scale=6,
                        error_correction_level=(
                            selector.QrErrorCorrectionLevel.QUARTILE
                        ),
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="link",
            data_schema=schema,
            last_step=False,
        )

    async def _async_wait_for_link(self) -> str | None:
        """Poll ``/v1/accounts`` until a new number appears; return it or None."""
        client = self._client()
        deadline = asyncio.get_running_loop().time() + _LINK_POLL_TIMEOUT
        while asyncio.get_running_loop().time() < deadline:
            try:
                accounts = set(await client.async_list_accounts())
            except SignalApiError as err:
                _LOGGER.debug("Error polling accounts while linking: %s", err)
            else:
                new = accounts - self._link_baseline
                if new:
                    # Pick a deterministic value if several appeared at once.
                    return sorted(new)[0]
            await asyncio.sleep(_LINK_POLL_INTERVAL)
        return None

    async def async_step_link_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the entry after a successful link."""
        return await self._create_entry()

    async def async_step_link_retry(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Inform the user the link timed out and let them retry."""
        if user_input is not None:
            # Reset so a fresh QR is fetched on the next link attempt.
            self._link_task = None
            return await self.async_step_link()
        return self.async_show_form(
            step_id="link_retry",
            data_schema=vol.Schema({}),
        )

    # ------------------------------------------------------------------
    # Step: register (new number) + verify
    # ------------------------------------------------------------------

    async def async_step_register(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Request an SMS/voice verification code for a new number."""
        errors: dict[str, str] = {}

        if user_input is not None:
            number = user_input[CONF_NUMBER].strip()
            captcha = (user_input.get("captcha") or "").strip() or None
            use_voice = bool(user_input.get("use_voice", False))

            client = self._client()
            try:
                await client.async_register(
                    number, captcha=captcha, use_voice=use_voice
                )
            except SignalApiResponseError as err:
                _LOGGER.debug("Registration failed (%s): %s", err.status, err.body)
                errors["base"] = "invalid_captcha"
            except SignalApiError as err:
                _LOGGER.debug("Registration connection error: %s", err)
                errors["base"] = "cannot_connect"
            else:
                self._reg_number = number
                return await self.async_step_verify()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_NUMBER,
                    default=(user_input or {}).get(CONF_NUMBER, ""),
                ): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEL)
                ),
                vol.Optional("captcha", default=""): selector.TextSelector(),
                vol.Optional("use_voice", default=False): selector.BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="register", data_schema=schema, errors=errors
        )

    async def async_step_verify(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Submit the verification token (and optional PIN)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            token = user_input["token"].strip()
            pin = (user_input.get("pin") or "").strip() or None

            client = self._client()
            try:
                await client.async_verify(self._reg_number, token, pin=pin)
            except SignalApiResponseError as err:
                _LOGGER.debug("Verification failed (%s): %s", err.status, err.body)
                errors["base"] = "verification_failed"
            except SignalApiError as err:
                _LOGGER.debug("Verification connection error: %s", err)
                errors["base"] = "cannot_connect"
            else:
                self._number = self._reg_number
                return await self._create_entry()

        schema = vol.Schema(
            {
                vol.Required("token"): selector.TextSelector(),
                vol.Optional("pin", default=""): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.PASSWORD
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="verify",
            data_schema=schema,
            errors=errors,
            description_placeholders={CONF_NUMBER: self._reg_number},
        )

    # ------------------------------------------------------------------
    # Step: existing (already-registered number)
    # ------------------------------------------------------------------

    async def async_step_existing(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a number already registered on the server."""
        if not self._accounts:
            return await self.async_step_menu()

        if user_input is not None:
            self._number = user_input[CONF_NUMBER]
            return await self._create_entry()

        schema = vol.Schema(
            {
                vol.Required(CONF_NUMBER): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=list(self._accounts),
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="existing", data_schema=schema)

    # ------------------------------------------------------------------
    # Options flow
    # ------------------------------------------------------------------

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> SignalbotOptionsFlow:
        """Return the options flow handler."""
        return SignalbotOptionsFlow(config_entry)


def _recipient_form_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """Return the add/edit recipient form schema, pre-filled with *defaults*."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_RECIPIENT_NAME,
                default=defaults.get(CONF_RECIPIENT_NAME, ""),
            ): selector.TextSelector(),
            vol.Optional(
                CONF_PHONE, default=defaults.get(CONF_PHONE, "")
            ): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.TEL)
            ),
            vol.Optional(
                CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")
            ): selector.TextSelector(),
            vol.Required(
                CONF_PREFER, default=defaults.get(CONF_PREFER, PREFER_PHONE)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=_PREFER_OPTIONS,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    translation_key="prefer",
                )
            ),
        }
    )


class SignalbotOptionsFlow(OptionsFlow):
    """Manage Signalbot recipients and receive settings."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Load a working copy of the current options."""
        self._entry = config_entry
        options = config_entry.options
        self._recipients: list[dict[str, Any]] = deepcopy(
            list(options.get(CONF_RECIPIENTS, []))
        )
        self._receive_enabled: bool = options.get(CONF_RECEIVE_ENABLED, True)
        self._poll_interval: int = options.get(
            CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
        )
        self._edit_id: str | None = None

    # ------------------------------------------------------------------

    def _save(self) -> ConfigFlowResult:
        """Persist the working copy as the entry options."""
        return self.async_create_entry(
            title="",
            data={
                CONF_RECIPIENTS: self._recipients,
                CONF_RECEIVE_ENABLED: self._receive_enabled,
                CONF_POLL_INTERVAL: self._poll_interval,
            },
        )

    def _name_for(self, recipient_id: str) -> str:
        """Return the display name for a recipient id."""
        for rec in self._recipients:
            if rec.get(CONF_ID) == recipient_id:
                return rec.get(CONF_RECIPIENT_NAME, recipient_id)
        return recipient_id

    # ------------------------------------------------------------------
    # Init menu
    # ------------------------------------------------------------------

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the options menu."""
        options = ["add_recipient"]
        if self._recipients:
            options.extend(["edit_recipient", "remove_recipient"])
        options.append("settings")
        return self.async_show_menu(step_id="init", menu_options=options)

    # ------------------------------------------------------------------
    # Add recipient
    # ------------------------------------------------------------------

    async def async_step_add_recipient(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add a new recipient."""
        errors: dict[str, str] = {}

        if user_input is not None:
            phone = (user_input.get(CONF_PHONE) or "").strip()
            username = (user_input.get(CONF_USERNAME) or "").strip()
            if not phone and not username:
                errors["base"] = "recipient_needs_address"
            else:
                self._recipients.append(
                    {
                        CONF_ID: str(uuid4()),
                        CONF_RECIPIENT_NAME: user_input[CONF_RECIPIENT_NAME].strip(),
                        CONF_PHONE: phone,
                        CONF_USERNAME: username,
                        CONF_PREFER: user_input[CONF_PREFER],
                    }
                )
                return self._save()

        return self.async_show_form(
            step_id="add_recipient",
            data_schema=_recipient_form_schema(user_input),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Edit recipient
    # ------------------------------------------------------------------

    async def async_step_edit_recipient(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick which recipient to edit."""
        if not self._recipients:
            return await self.async_step_init()

        if user_input is not None:
            self._edit_id = user_input[CONF_ID]
            return await self.async_step_edit_recipient_form()

        options = [
            selector.SelectOptionDict(
                value=rec[CONF_ID], label=rec.get(CONF_RECIPIENT_NAME, rec[CONF_ID])
            )
            for rec in self._recipients
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_ID): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="edit_recipient", data_schema=schema
        )

    async def async_step_edit_recipient_form(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the chosen recipient in place."""
        errors: dict[str, str] = {}
        current = next(
            (r for r in self._recipients if r.get(CONF_ID) == self._edit_id), None
        )
        if current is None:
            return await self.async_step_init()

        if user_input is not None:
            phone = (user_input.get(CONF_PHONE) or "").strip()
            username = (user_input.get(CONF_USERNAME) or "").strip()
            if not phone and not username:
                errors["base"] = "recipient_needs_address"
            else:
                current[CONF_RECIPIENT_NAME] = user_input[CONF_RECIPIENT_NAME].strip()
                current[CONF_PHONE] = phone
                current[CONF_USERNAME] = username
                current[CONF_PREFER] = user_input[CONF_PREFER]
                return self._save()

        defaults = user_input if user_input is not None else current
        return self.async_show_form(
            step_id="edit_recipient_form",
            data_schema=_recipient_form_schema(defaults),
            errors=errors,
            description_placeholders={
                CONF_RECIPIENT_NAME: current.get(CONF_RECIPIENT_NAME, "")
            },
        )

    # ------------------------------------------------------------------
    # Remove recipient
    # ------------------------------------------------------------------

    async def async_step_remove_recipient(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Remove one or more recipients."""
        if not self._recipients:
            return await self.async_step_init()

        if user_input is not None:
            to_remove = set(user_input.get(CONF_ID, []))
            self._recipients = [
                r for r in self._recipients if r.get(CONF_ID) not in to_remove
            ]
            return self._save()

        options = [
            selector.SelectOptionDict(
                value=rec[CONF_ID], label=rec.get(CONF_RECIPIENT_NAME, rec[CONF_ID])
            )
            for rec in self._recipients
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_ID): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options,
                        mode=selector.SelectSelectorMode.LIST,
                        multiple=True,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="remove_recipient", data_schema=schema
        )

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit receive_enabled and poll_interval."""
        if user_input is not None:
            self._receive_enabled = bool(user_input[CONF_RECEIVE_ENABLED])
            self._poll_interval = int(user_input[CONF_POLL_INTERVAL])
            return self._save()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_RECEIVE_ENABLED, default=self._receive_enabled
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_POLL_INTERVAL, default=self._poll_interval
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=MIN_POLL_INTERVAL,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="s",
                    )
                ),
            }
        )
        return self.async_show_form(step_id="settings", data_schema=schema)
