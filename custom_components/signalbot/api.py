"""Async HTTP client for signal-cli-rest-api (bbernhard/signal-cli-rest-api).

This module is intentionally free of Home Assistant imports so it can be
unit-tested independently.

Typical usage:

    async with aiohttp.ClientSession() as session:
        client = SignalApiClient(session, "http://localhost:8080")
        await client.async_send_message("+15550001111", "Hello!", ["+15559998888"])
"""
from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

import aiohttp

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SignalApiError(Exception):
    """Base exception for all signal-cli-rest-api errors."""


class SignalApiConnectionError(SignalApiError):
    """Raised when a network-level error prevents reaching the API."""


class SignalApiResponseError(SignalApiError):
    """Raised when the API returns a non-2xx HTTP response.

    Attributes:
        status: The HTTP status code returned by the server.
        body: The raw response body text (may be empty).
    """

    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body
        super().__init__(f"signal-cli-rest-api responded with {status}: {body!r}")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=30)


class SignalApiClient:
    """Async client for signal-cli-rest-api."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str) -> None:
        """Initialise the client.

        Args:
            session: An existing :class:`aiohttp.ClientSession` to use for all
                requests.  The caller is responsible for its lifecycle.
            base_url: Root URL of the signal-cli-rest-api service, e.g.
                ``"http://localhost:8080"``.  Trailing slashes are stripped.
        """
        self._session = session
        self._base_url = base_url.rstrip("/")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any | None = None,
        params: dict[str, Any] | None = None,
        expect_bytes: bool = False,
        timeout: aiohttp.ClientTimeout | None = None,
    ) -> Any:
        """Perform an HTTP request and return the parsed response.

        Args:
            method: HTTP method (``"GET"``, ``"POST"``, …).
            path: Path relative to *base_url*, starting with ``"/"``.
            json: Optional request body that will be JSON-serialised.
            params: Optional query-string parameters.
            expect_bytes: When ``True`` the raw response bytes are returned
                instead of attempting JSON parsing.
            timeout: Override the default :class:`aiohttp.ClientTimeout`.

        Returns:
            Parsed JSON (``dict`` / ``list``) or ``bytes`` when *expect_bytes*
            is ``True``.  If the response body is empty, returns ``{}`` for
            JSON requests.

        Raises:
            SignalApiConnectionError: On network-level failures.
            SignalApiResponseError: On HTTP 4xx / 5xx responses.
        """
        url = f"{self._base_url}{path}"
        effective_timeout = timeout or _DEFAULT_TIMEOUT

        try:
            async with self._session.request(
                method,
                url,
                json=json,
                params=params,
                timeout=effective_timeout,
            ) as response:
                if response.status < 200 or response.status >= 300:
                    try:
                        body = await response.text()
                    except Exception:  # noqa: BLE001
                        body = ""
                    raise SignalApiResponseError(response.status, body)

                if expect_bytes:
                    return await response.read()

                # Gracefully handle empty bodies (e.g. 204 No Content).
                text = await response.text()
                if not text.strip():
                    return {}
                return await response.json(content_type=None)

        except SignalApiError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise SignalApiConnectionError(
                f"Cannot connect to signal-cli-rest-api at {url}: {err}"
            ) from err

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    async def async_about(self) -> dict:
        """Return API version/capabilities information.

        Endpoint: ``GET /v1/about``
        """
        result: dict = await self._request("GET", "/v1/about")
        return result

    async def async_list_accounts(self) -> list[str]:
        """Return the list of registered Signal account numbers.

        Endpoint: ``GET /v1/accounts``
        """
        result: list[str] = await self._request("GET", "/v1/accounts")
        return result

    async def async_qrcodelink(self, device_name: str) -> bytes:
        """Return a QR-code PNG for linking a new device.

        Endpoint: ``GET /v1/qrcodelink?device_name=<device_name>``

        Args:
            device_name: Human-readable name shown to the primary device during
                the linking flow (e.g. ``"Home Assistant"``).

        Returns:
            Raw PNG bytes of the QR code.
        """
        result: bytes = await self._request(
            "GET",
            "/v1/qrcodelink",
            params={"device_name": device_name},
            expect_bytes=True,
        )
        return result

    async def async_qrcodelink_uri(self, device_name: str) -> str:
        """Return the raw sgnl:// device-link URI (from GET /v1/qrcodelink/raw).

        Endpoint: ``GET /v1/qrcodelink/raw?device_name=<device_name>``

        The response is JSON of the form
        ``{"DeviceLinkUri": "sgnl://linkdevice?uuid=...&pub_key=..."}``.  This
        raw URI can be rendered client-side (e.g. by Home Assistant's
        ``QrCodeSelector``) instead of serving a server-rendered PNG.

        Args:
            device_name: Human-readable name shown to the primary device during
                the linking flow (e.g. ``"Home Assistant"``).

        Returns:
            The raw ``sgnl://linkdevice?...`` URI string.

        Raises:
            SignalApiResponseError: If the URI field is missing from the
                response.
        """
        result = await self._request(
            "GET",
            "/v1/qrcodelink/raw",
            params={"device_name": device_name},
        )

        uri: Any = None
        if isinstance(result, dict):
            # Be tolerant of alternate casings the API may use.
            for key in ("DeviceLinkUri", "deviceLinkUri", "device_link_uri", "uri"):
                value = result.get(key)
                if isinstance(value, str) and value:
                    uri = value
                    break

        if not isinstance(uri, str) or not uri:
            raise SignalApiResponseError(
                200,
                f"No device-link URI found in /v1/qrcodelink/raw response: {result!r}",
            )

        return uri

    async def async_send_message(
        self,
        number: str,
        message: str,
        recipients: list[str],
        *,
        attachments: list[str] | None = None,
    ) -> dict:
        """Send a Signal message.

        Endpoint: ``POST /v2/send``

        Args:
            number: The sender's E.164 phone number (must be registered with the
                API).
            message: Plain-text message body.
            recipients: List of E.164 phone numbers or Signal usernames to send
                to.
            attachments: Optional list of base64-encoded file data strings.  When
                provided they are sent as ``"base64_attachments"``.

        Returns:
            Response dict from the API (may include timestamps, etc.).
        """
        body: dict[str, Any] = {
            "message": message,
            "number": number,
            "recipients": recipients,
        }
        if attachments is not None:
            body["base64_attachments"] = attachments

        result: dict = await self._request("POST", "/v2/send", json=body)
        return result

    async def async_receive(
        self,
        number: str,
        *,
        timeout_seconds: int = 1,
    ) -> list[dict]:
        """Poll for incoming messages for *number*.

        Endpoint: ``GET /v1/receive/{number}?timeout=<timeout_seconds>``

        The HTTP timeout is set to ``timeout_seconds + 5`` so the server has
        time to long-poll before the client gives up.

        Args:
            number: E.164 phone number of the account to receive for.
            timeout_seconds: How long (in seconds) the server should wait for a
                new message before returning an empty list.

        Returns:
            List of message dicts; empty list if no messages arrived.
        """
        encoded_number = quote(number, safe="")
        # Give the server time to complete its long-poll, plus a margin.
        http_timeout = aiohttp.ClientTimeout(total=timeout_seconds + 5)

        result = await self._request(
            "GET",
            f"/v1/receive/{encoded_number}",
            params={"timeout": timeout_seconds},
            timeout=http_timeout,
        )
        if isinstance(result, list):
            return result
        return []

    async def async_register(
        self,
        number: str,
        *,
        captcha: str | None = None,
        use_voice: bool = False,
    ) -> None:
        """Register a phone number with the Signal network.

        Endpoint: ``POST /v1/register/{number}``

        Args:
            number: E.164 phone number to register.
            captcha: Optional captcha token required by Signal when too many
                registration attempts have been made.
            use_voice: When ``True``, request the verification code via a phone
                call instead of SMS.
        """
        encoded_number = quote(number, safe="")
        body: dict[str, Any] = {"use_voice": use_voice}
        if captcha is not None:
            body["captcha"] = captcha

        await self._request("POST", f"/v1/register/{encoded_number}", json=body)

    async def async_verify(
        self,
        number: str,
        token: str,
        *,
        pin: str | None = None,
    ) -> None:
        """Verify a Signal registration with the SMS/voice code.

        Endpoint: ``POST /v1/register/{number}/verify/{token}``

        Args:
            number: E.164 phone number being verified.
            token: The verification code received via SMS or voice call.
            pin: Optional Signal registration lock PIN (if one has been set on
                the account).
        """
        encoded_number = quote(number, safe="")
        encoded_token = quote(token, safe="")
        body: dict[str, Any] = {"pin": pin} if pin is not None else {}

        await self._request(
            "POST",
            f"/v1/register/{encoded_number}/verify/{encoded_token}",
            json=body,
        )

    async def async_health(self) -> bool:
        """Return ``True`` if the API is reachable, ``False`` otherwise.

        Calls :meth:`async_about` and swallows any :class:`SignalApiError`.
        """
        try:
            await self.async_about()
        except SignalApiError:
            return False
        return True
