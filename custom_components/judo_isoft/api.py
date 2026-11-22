"""Client for the legacy local JUDO API."""

from __future__ import annotations

from datetime import UTC, datetime
from time import perf_counter
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

import asyncio
import ssl

import aiohttp

from .const import LOGGER


class JudoISoftApiClientError(Exception):
    """Exception to indicate a general API error."""


class JudoISoftApiClientCommunicationError(
    JudoISoftApiClientError,
):
    """Exception to indicate a communication error."""


class _JudoSessionSetupError(JudoISoftApiClientCommunicationError):
    """A failed login or connection shared by all measurements in the poll."""


class JudoISoftApiClientAuthenticationError(
    JudoISoftApiClientError,
):
    """Exception to indicate an authentication error."""


_COUNTER_WIDTH = 7


def _verify_response_or_raise(response: aiohttp.ClientResponse) -> None:
    """Verify that the response is valid."""
    if response.status in (401, 403):
        msg = "Invalid credentials"
        raise JudoISoftApiClientAuthenticationError(
            msg,
        )
    response.raise_for_status()


class JudoISoftApiClient:
    """Client for the legacy local JUDO API."""

    def __init__(
        self,
        ip: str,
        username: str,
        password: str,
        session: aiohttp.ClientSession,
    ) -> None:
        """Client for the legacy local JUDO API."""
        self._ip = ip
        self._username = username
        self._password = password
        self._session = session
        self._token: str | None = None
        self._type: str | None = None
        self._serial: str | int | None = None
        self._connected = False
        self._msgnum = 0
        self._values: dict[str, int | bool] = {}
        self._last_success: dict[str, str] = {}

    async def async_get_data(self) -> dict[str, Any]:
        """Poll independently, retaining the values that could not be refreshed."""
        fresh: set[str] = set()
        for group, command, fields in (
            ("consumption", "water total", ("water_total_raw", "water_total_soft")),
            ("waterstop", "valve", ("valve",)),
            ("waterstop", "vacation", ("vacation",)),
        ):
            try:
                result = await self._get(group, command)
                values = self._parse_values(command, result)
            except JudoISoftApiClientAuthenticationError:
                raise
            except _JudoSessionSetupError:
                LOGGER.debug("JUDO session setup failed; stopping this poll")
                break
            except (JudoISoftApiClientError, TypeError, ValueError):
                LOGGER.debug("Could not refresh JUDO %s/%s", group, command)
                continue
            timestamp = datetime.now(UTC).isoformat()
            for field, value in zip(fields, values, strict=True):
                self._values[field] = value
                self._last_success[field] = timestamp
                fresh.add(field)

        if not self._values:
            msg = "JUDO did not return any valid measurements"
            raise JudoISoftApiClientCommunicationError(msg)

        return {
            "type": self._type,
            "serial": self._serial,
            **self._values,
            "last_success": self._last_success.copy(),
            "stale_fields": self._values.keys() - fresh,
        }

    @staticmethod
    def _parse_values(command: str, value: Any) -> tuple[int | bool, ...]:
        if (
            command == "water total"
            and isinstance(value, str)
            and len(value) >= 2 * _COUNTER_WIDTH
        ):
            return int(value[:_COUNTER_WIDTH]), int(value[-_COUNTER_WIDTH:])
        if command == "valve" and value in ("opened", "closed"):
            return (value == "opened",)
        if command == "vacation" and value in ("0", "1", 0, 1):
            return (value in ("1", 1),)
        msg = "Unrecognized JUDO measurement"
        raise ValueError(msg)

    def _reset_session(self) -> None:
        self._token = None
        self._connected = False

    async def _connect(self) -> None:
        if not self._token:
            reply = await self._request(
                "register",
                "login",
                {
                    "name": "login",
                    "user": self._username,
                    "password": self._password,
                    "role": "customer",
                },
            )
            token = reply.get("token")
            if reply.get("status") != "ok" or not isinstance(token, str) or not token:
                if reply.get("data") == "login failed":
                    msg = "JUDO rejected the credentials"
                    raise JudoISoftApiClientAuthenticationError(msg)
                msg = "JUDO did not return a session token"
                raise JudoISoftApiClientCommunicationError(msg)
            self._token = token

        if not self._type or not self._serial:
            reply = await self._request("register", "show")
            devices = reply.get("data")
            if (
                not isinstance(devices, list)
                or not devices
                or not isinstance(devices[0], dict)
            ):
                msg = "JUDO did not return an attached device"
                raise JudoISoftApiClientCommunicationError(msg)
            self._type = devices[0].get("wtuType")
            self._serial = devices[0].get("serial number")
            if not self._type or not self._serial:
                msg = "JUDO did not identify the attached device"
                raise JudoISoftApiClientCommunicationError(msg)

        reply = await self._request(
            "register",
            "connect",
            {"parameter": self._type, "serial number": self._serial},
        )
        if reply.get("status") != "ok" and reply.get("data") != "already connected":
            self._reset_session()
            msg = "JUDO could not connect to the attached device"
            raise JudoISoftApiClientCommunicationError(msg)
        self._connected = True

    async def _get(
        self, group: str, command: str, params: Mapping[str, str | int] | None = None
    ) -> Any:
        """Read data, recovering once on an explicit session rejection."""
        for _ in range(2):
            if not self._connected:
                try:
                    await self._connect()
                except JudoISoftApiClientCommunicationError:
                    self._reset_session()
                    msg = "Could not establish a JUDO session"
                    raise _JudoSessionSetupError(msg) from None
            reply = await self._request(group, command, params)
            if reply.get("status") == "ok":
                return reply.get("data")
            # Only retry if the server said it did not accept the read.
            if reply.get("data") not in ("not logged in", "no token", "not connected"):
                break
            self._reset_session()
        msg = "JUDO rejected the measurement request"
        raise JudoISoftApiClientCommunicationError(msg)

    async def _request(
        self, group: str, command: str, params: Mapping[str, str | int] | None = None
    ) -> dict[str, Any]:
        self._msgnum += 1
        query: dict[str, str | int] = {
            "group": group,
            "command": command,
            "msgnumber": self._msgnum,
        }
        if command != "login" and self._token:
            query["token"] = self._token
        query.update(params or {})
        url = f"https://{self._ip}:8124/?" + "&".join(
            f"{key}={value}" for key, value in query.items()
        )
        started = perf_counter()
        try:
            response = await self._submit(url)
        except JudoISoftApiClientError:
            # The firmware may still be holding the previous response for this token.
            self._reset_session()
            raise
        finally:
            LOGGER.debug(
                "JUDO %s/%s took %.3f s", group, command, perf_counter() - started
            )
        if not isinstance(response, dict):
            self._reset_session()
            msg = "JUDO did not return a response object"
            raise JudoISoftApiClientCommunicationError(msg)
        return response

    async def _submit(self, url: str) -> Any:
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        # aiohttp can retry GET after a disconnect, even once the device accepted it.
        attempted = False

        async def send_once(
            request: aiohttp.ClientRequest, handler: aiohttp.ClientHandlerType
        ) -> aiohttp.ClientResponse:
            nonlocal attempted
            if attempted:
                msg = "JUDO connection dropped; request not replayed"
                raise JudoISoftApiClientCommunicationError(msg)
            attempted = True
            return await handler(request)

        try:
            async with asyncio.timeout(30):
                async with self._session.get(
                    url, ssl=ssl_ctx, allow_redirects=False, middlewares=(send_once,)
                ) as response:
                    _verify_response_or_raise(response)
                    return await response.json()
        except TimeoutError:
            msg = "JUDO request timed out"
            raise JudoISoftApiClientCommunicationError(msg) from None
        except (aiohttp.ClientError, ValueError):
            msg = "Invalid or unsuccessful response from JUDO"
            raise JudoISoftApiClientCommunicationError(msg) from None

    async def async_set(self, key: str, value: str) -> Any:
        """Existing control path, outside the polling changes."""
        if key == "valve_mode":
            self._get(
                "waterstop", "valve", {"valve": "open" if value == "1" else "close"}
            )
        if key == "vacation_mode":
            self._get("waterstop", "vacation", {"valve": value})
