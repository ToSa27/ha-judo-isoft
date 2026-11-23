"""Client for the legacy local JUDO API."""

from __future__ import annotations

from calendar import monthrange
from datetime import UTC, datetime
from http import HTTPStatus
from time import perf_counter
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import date

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


class _JudoForbiddenError(JudoISoftApiClientAuthenticationError):
    """An HTTP 403 that may be specific to a firmware command."""


_COUNTER_WIDTH = 7
_PERCENT_MAX = 100
_DAILY_INTERVAL = 24 * 60 * 60
_REGENERATION_STATES = {
    ("193", "regenerate", "lso", "lsu"): True,
    ("192", "lso", "lsu"): False,
}
_BASE_READS = (
    ("consumption", "water total", ("water_total_raw", "water_total_soft"), 0),
    ("waterstop", "valve", ("valve",), 0),
    ("waterstop", "vacation", ("vacation",), 0),
)
_EXTENDED_READS = (
    ("consumption", "salt range", ("salt_range",), _DAILY_INTERVAL),
    ("consumption", "salt quantity", ("salt_quantity",), _DAILY_INTERVAL),
    ("consumption", "residual capacity", ("residual_capacity",), 0),
    ("consumption", "actual flow rate", ("actual_flow_rate",), 0),
    ("info", "natural hardness", ("natural_hardness",), _DAILY_INTERVAL),
    ("settings", "residual hardness", ("residual_hardness",), _DAILY_INTERVAL),
    ("settings", "regeneration", ("regeneration",), 0),
)
_PERIOD_READS = (
    ("consumption", "water daily", ("water_today",), 0),
    ("consumption", "water monthly", ("water_this_month",), 0),
)
_DAILY_BUCKETS = 8
_EXTENDED_COMMANDS = frozenset(
    (group, command) for group, command, _, _ in (*_EXTENDED_READS, *_PERIOD_READS)
)
MEASUREMENT_FIELDS = frozenset(
    field
    for _, _, fields, _ in (*_BASE_READS, *_EXTENDED_READS, *_PERIOD_READS)
    for field in fields
)


def _verify_response_or_raise(response: aiohttp.ClientResponse) -> None:
    """Verify that the response is valid."""
    if response.status == HTTPStatus.FORBIDDEN:
        msg = "JUDO denied the request"
        raise _JudoForbiddenError(msg)
    if response.status == HTTPStatus.UNAUTHORIZED:
        msg = "Invalid credentials"
        raise JudoISoftApiClientAuthenticationError(msg)
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
        self._next_read: dict[str, float] = {}
        self._stale_fields: set[str] = set()
        self._period_start: dict[str, date] = {}

    async def async_get_data(
        self,
        *,
        include_extended: bool = False,
        local_date: Callable[[], date] | None = None,
    ) -> dict[str, Any]:
        """Poll independently, retaining the values that could not be refreshed."""
        reads = (*_BASE_READS, *_EXTENDED_READS) if include_extended else _BASE_READS
        history: dict[str, tuple[dict[str, int], int]] = {}
        if include_extended and local_date is not None:
            consumption_date = local_date()
            reads += _PERIOD_READS
            year, month, day = (
                consumption_date.year,
                consumption_date.month,
                consumption_date.day,
            )
            history = {
                "water daily": (
                    {"year": year, "month": month, "day": day},
                    _DAILY_BUCKETS,
                ),
                "water monthly": (
                    {"year": year, "month": month},
                    monthrange(year, month)[1],
                ),
            }
            self._reset_old_periods(consumption_date)
        now = perf_counter()
        due = [read for read in reads if now >= self._next_read.get(read[1], now)]
        for _, _, fields, _ in due:
            self._stale_fields.update(fields)

        for group, command, fields, interval in due:
            if interval:
                self._next_read[command] = perf_counter() + interval
            try:
                if command in history:
                    params, buckets = history[command]
                    result = await self._get(group, command, params)
                    values = (self._sum_buckets(result, buckets),)
                else:
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
                self._stale_fields.discard(field)

        # Do not publish a previous period if midnight passed during the HTTP reads.
        if history and local_date is not None:
            self._reset_old_periods(local_date())

        if not self._values:
            msg = "JUDO did not return any valid measurements"
            raise JudoISoftApiClientCommunicationError(msg)

        return {
            "type": self._type,
            "serial": self._serial,
            **self._values,
            "last_success": self._last_success.copy(),
            "stale_fields": self._values.keys() & self._stale_fields,
        }

    def _reset_old_periods(self, today: date) -> None:
        for field, start in (
            ("water_today", today),
            ("water_this_month", today.replace(day=1)),
        ):
            if self._period_start.get(field) != start:
                self._values.pop(field, None)
                self._last_success.pop(field, None)
                self._period_start[field] = start

    @staticmethod
    def _sum_buckets(value: Any, expected: int) -> int:
        parts = value.split() if isinstance(value, str) else []
        msg = "Unrecognized JUDO consumption history"
        if len(parts) != expected:
            raise ValueError(msg)
        samples = []
        for part in parts:
            if part == "-1":  # No sample in the appliance's history file.
                continue
            if not (part.isascii() and part.isdecimal()):
                raise ValueError(msg)
            samples.append(int(part))
        if not samples:
            raise ValueError(msg)
        return sum(samples)

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
        if command in (
            "salt range",
            "salt quantity",
            "actual flow rate",
            "natural hardness",
            "residual hardness",
        ):
            if (
                isinstance(value, str)
                and (digits := value.strip()).isascii()
                and digits.isdecimal()
            ):
                value = int(digits)
            if type(value) is int and value >= 0:
                return (value,)
        if command == "residual capacity" and isinstance(value, str):
            _, separator, percent = value.partition(",")
            if separator and (percent := percent.strip()).endswith("%"):
                percentage = int(percent[:-1])
                if 0 <= percentage <= _PERCENT_MAX:
                    return (percentage,)
        if command == "regeneration" and isinstance(value, str):
            parts = tuple(part.strip() for part in value.split(","))
            if parts in _REGENERATION_STATES:
                return (_REGENERATION_STATES[parts],)
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
        except _JudoForbiddenError:
            if (group, command) in _EXTENDED_COMMANDS:
                msg = "JUDO denied this optional measurement"
                raise JudoISoftApiClientCommunicationError(msg) from None
            self._reset_session()
            raise
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
