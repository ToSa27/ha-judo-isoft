"""Sample API Client."""

from __future__ import annotations

import socket
from typing import Any

import aiohttp
import async_timeout
import asyncio
import ssl

from .const import LOGGER

class JudoISoftApiClientError(Exception):
    """Exception to indicate a general API error."""


class JudoISoftApiClientCommunicationError(
    JudoISoftApiClientError,
):
    """Exception to indicate a communication error."""


class JudoISoftApiClientAuthenticationError(
    JudoISoftApiClientError,
):
    """Exception to indicate an authentication error."""


def _verify_response_or_raise(response: aiohttp.ClientResponse) -> None:
    """Verify that the response is valid."""
    if response.status in (401, 403):
        msg = "Invalid credentials"
        raise JudoISoftApiClientAuthenticationError(
            msg,
        )
    response.raise_for_status()


class JudoISoftApiClient:
    """Sample API Client."""

    def __init__(
        self,
        ip: str,
        username: str,
        password: str,
        session: aiohttp.ClientSession,
    ) -> None:
        """Sample API Client."""
        self._ip = ip
        self._username = username
        self._password = password
        self._session = session
        self._token = None
        self._type = None
        self._serial = None
        self._connected = False
        self._msgnum = 0

    async def async_get_data(self) -> Any:
        """Get data from the API and consolidate into json string response."""
        water_total = await self._get('consumption', 'water total')
        return {
            "type": self._type,
            "serial": self._serial,
            "water_total_raw": int(water_total[:7].strip()),
            "water_total_soft": int(water_total[-7:].strip()),
            "valve": await self._get('waterstop', 'valve') == 'opened',
            "vacation": await self._get('waterstop', 'vacation') == "1",
        }

    async def _login(self) -> None:
        """Login to the API."""
        await self._get('register', 'login', {
            'name': 'login',
            'user': self._username,
            'password': self._password,
            'role': 'customer'
        })

    async def _show(self) -> None:
        """Get type and serial."""
        if (not self._token):
            await self._login()
        r = await self._get('register', 'show')
        if r and len(r) > 0:
            self._type = r[0]['wtuType']
            self._serial = r[0]['serial number']

    async def _connect(self) -> None:
        """Connect to the device."""
        if (not self._token) or (not self._type) or (not self._serial):
            await self._show()
        r = await self._get('register', 'connect', {
            'parameter': self._type,
            'serial number': self._serial
        })

    async def _get(self, group, command, params = {}) -> Any:
        """Get device data."""
        if command != 'login' and command != 'show' and command != 'connect' and not self._connected:
            await self._connect()
        req = 'https://' + self._ip + ':8124/?'
        req += 'group=' + group
        req += '&command=' + command
        self._msgnum += 1
        req += '&msgnumber=' + str(self._msgnum)
        if command != 'login':
            req += '&token=' + self._token
        for key, value in params.items():
            req += '&' + key + '=' + value

        LOGGER.debug("Requesting URL: %s", req)
#        res = requests.get(req)
#        res = await self._session.request(
#            method="GET",
#            url=req,
#        )
#        res = await self._session.get(req)
        res = await self._submit(req)
        LOGGER.debug("Response: %s", res)
#        res.raise_for_status()

        if res:
            if 'token' in res and self._token != res['token']:
                self._token = res['token']
            if 'wtuType' in res and self._type != res['wtuType']:
                self._type = res['wtuType']
            if 'serial number' in res and self._serial != res['serial number']:
                self._serial = res['serial number']
            if res['status'] == 'ok':
                if command == 'connect':
                    self._connected = True
                if 'data' in res:
                    return res['data']
            if 'error' in res and res['error'] == 'error':
                if 'data' in res:
                    if res['data'] == 'not logged in':
                        await self._login()
                        return await self._get(group, command, params)
                    if res['data'] == 'not connected':
                        self._connected = False
                        return await self._get(group, command, params)
                    if res['data'] == 'already connected':
                        return res['data']
        return None

    async def _submit(self, url) -> Any:
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        attempts = 0
        while True:
            attempts += 1
            LOGGER.debug("Attempt: %s", attempts)
            try:
                async with async_timeout.timeout(30):
                    response = await self._session.request(
                        method="get",
                        url=url,
                        ssl=ssl_ctx,
                    )
                    _verify_response_or_raise(response)
                    return await response.json()
            except TimeoutError as exception:
                msg = f"Timeout error fetching information - {exception}"
                LOGGER.warn("Failed: %s", msg)
                if attempts > 3:
                    raise JudoISoftApiClientCommunicationError(
                        msg,
                    ) from exception
            except (aiohttp.ClientError, socket.gaierror) as exception:
                msg = f"Error fetching information - {exception}"
                LOGGER.warn("Failed: %s", msg)
                if attempts > 3:
                    raise JudoISoftApiClientCommunicationError(
                        msg,
                    ) from exception
            except Exception as exception:  # pylint: disable=broad-except
                msg = f"Something really wrong happened! - {exception}"
                LOGGER.warn("Failed: %s", msg)
                if attempts > 3:
                    raise JudoISoftApiClientError(
                        msg,
                    ) from exception

    async def async_set(self, key: str, value: str) -> Any:
        if key == 'valve_mode':
            self._get('waterstop', 'valve', {
                'valve': 'open' if value == '1' else 'close'
            })
        if key == 'vacation_mode':
            self._get('waterstop', 'vacation', {
                'valve': value
            })

    # async def async_get_data(self) -> Any:
    #     """Get data from the API."""
    #     return await self._api_wrapper(
    #         method="get",
    #         url="https://jsonplaceholder.typicode.com/posts/1",
    #     )

    # async def async_set_title(self, value: str) -> Any:
    #     """Get data from the API."""
    #     return await self._api_wrapper(
    #         method="patch",
    #         url="https://jsonplaceholder.typicode.com/posts/1",
    #         data={"title": value},
    #         headers={"Content-type": "application/json; charset=UTF-8"},
    #     )

    # async def _api_wrapper(
    #     self,
    #     method: str,
    #     url: str,
    #     data: dict | None = None,
    #     headers: dict | None = None,
    # ) -> Any:
    #     """Get information from the API."""
    #     try:
    #         async with async_timeout.timeout(10):
    #             response = await self._session.request(
    #                 method=method,
    #                 url=url,
    #                 headers=headers,
    #                 json=data,
    #             )
    #             _verify_response_or_raise(response)
    #             return await response.json()

    #     except TimeoutError as exception:
    #         msg = f"Timeout error fetching information - {exception}"
    #         raise JudoISoftApiClientCommunicationError(
    #             msg,
    #         ) from exception
    #     except (aiohttp.ClientError, socket.gaierror) as exception:
    #         msg = f"Error fetching information - {exception}"
    #         raise JudoISoftApiClientCommunicationError(
    #             msg,
    #         ) from exception
    #     except Exception as exception:  # pylint: disable=broad-except
    #         msg = f"Something really wrong happened! - {exception}"
    #         raise JudoISoftApiClientError(
    #             msg,
    #         ) from exception
