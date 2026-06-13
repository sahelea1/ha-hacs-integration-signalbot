"""Config flow for the Signalbot integration (Supervisor discovery)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from .const import CONF_MANAGER_URL, DOMAIN

_LOGGER = logging.getLogger(__name__)

_DEFAULT_MANAGER_URL = "http://local-signalbot:8099"
_CONNECT_TIMEOUT = 10


class SignalbotConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Signalbot config flow.

    Primary path: Supervisor discovery via async_step_hassio.
    Fallback path: manual entry via async_step_user.
    """

    VERSION = 1

    def __init__(self) -> None:
        """Initialise transient flow state."""
        self._manager_url: str = ""

    # ------------------------------------------------------------------
    # Supervisor discovery path
    # ------------------------------------------------------------------

    async def async_step_hassio(
        self, discovery_info: HassioServiceInfo
    ) -> ConfigFlowResult:
        """Handle discovery triggered by the Signalbot add-on via Supervisor."""
        host: str = discovery_info.config["host"]
        port: int = discovery_info.config["port"]
        self._manager_url = f"http://{host}:{port}"

        await self.async_set_unique_id("signalbot")
        self._abort_if_unique_id_configured(
            updates={CONF_MANAGER_URL: self._manager_url}
        )

        self.context["title_placeholders"] = {"name": "Signalbot"}
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user to confirm the discovered add-on before creating the entry."""
        if user_input is not None:
            # Best-effort connectivity check — don't block entry creation on failure,
            # because the add-on may still be starting up.  The coordinator will retry.
            await self._async_try_connect(self._manager_url)
            return self.async_create_entry(
                title="Signalbot",
                data={CONF_MANAGER_URL: self._manager_url},
            )

        return self.async_show_form(
            step_id="hassio_confirm",
            data_schema=vol.Schema({}),
            description_placeholders={"manager_url": self._manager_url},
        )

    # ------------------------------------------------------------------
    # Manual / fallback path
    # ------------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow manual configuration for non-supervised installs."""
        errors: dict[str, str] = {}

        if user_input is not None:
            manager_url = _normalise_url(user_input[CONF_MANAGER_URL])
            reachable = await self._async_try_connect(manager_url)
            if not reachable:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id("signalbot")
                self._abort_if_unique_id_configured(
                    updates={CONF_MANAGER_URL: manager_url}
                )
                return self.async_create_entry(
                    title="Signalbot",
                    data={CONF_MANAGER_URL: manager_url},
                )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_MANAGER_URL,
                    default=(user_input or {}).get(
                        CONF_MANAGER_URL, _DEFAULT_MANAGER_URL
                    ),
                ): str,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _async_try_connect(self, manager_url: str) -> bool:
        """Return True if the manager API responds; False otherwise."""
        session = async_get_clientsession(self.hass)
        url = f"{manager_url}/api/status"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=_CONNECT_TIMEOUT)) as resp:
                _LOGGER.debug(
                    "Signalbot manager connectivity check %s -> HTTP %s", url, resp.status
                )
                return True
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            _LOGGER.debug("Signalbot manager not reachable at %s: %s", url, err)
            return False


def _normalise_url(url: str) -> str:
    """Return *url* stripped of surrounding whitespace and trailing slashes."""
    return url.strip().rstrip("/")
