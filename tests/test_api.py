"""Offline tests for the legacy JUDO transport and polling client."""

# ruff: noqa: PT009, PT027

from __future__ import annotations

import asyncio
import importlib
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import aiohttp

# Import the real client and constants without running the HA integration setup.
package = ModuleType("_test_judo_isoft")
package.__path__ = [
    str(Path(__file__).resolve().parents[1] / "custom_components" / "judo_isoft")
]
sys.modules[package.__name__] = package
api = importlib.import_module(f"{package.__name__}.api")


class ApiTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the client with no connection to an appliance."""

    async def test_timeout_is_not_retried(self) -> None:
        """Do not give the legacy server a second pending read on timeout."""
        request = AsyncMock(side_effect=TimeoutError)
        session = SimpleNamespace(get=MagicMock())
        session.get.return_value.__aenter__ = request
        client = api.JudoISoftApiClient("judo.invalid", "test", "test", session)
        with self.assertRaises(api.JudoISoftApiClientCommunicationError):
            await client._submit("https://judo.invalid:8124/")  # noqa: SLF001
        request.assert_awaited_once()

    async def test_failed_measurement_retains_its_timestamp(self) -> None:
        """A failed valve read must not discard a fresh consumption reading."""
        client = api.JudoISoftApiClient("judo.invalid", "test", "test", None)
        timeout = api.JudoISoftApiClientCommunicationError("simulated timeout")
        get = AsyncMock(
            side_effect=[
                "      1      2",
                "opened",
                "1",
                "      3      4",
                timeout,
                "0",
            ]
        )
        client._get = get  # noqa: SLF001
        first = await client.async_get_data()
        second = await client.async_get_data()
        self.assertEqual(second["water_total_raw"], 3)
        self.assertTrue(second["valve"])
        self.assertFalse(second["vacation"])
        self.assertEqual(
            second["last_success"]["valve"], first["last_success"]["valve"]
        )
        self.assertEqual(second["stale_fields"], {"valve"})
        self.assertEqual(first["water_total_raw"], 1)

    async def test_total_failure_preserves_but_marks_cached_values(self) -> None:
        """Keep the last sample visible after a later outage."""
        client = api.JudoISoftApiClient("judo.invalid", "test", "test", None)
        timeout = api.JudoISoftApiClientCommunicationError("simulated timeout")
        client._get = AsyncMock(  # noqa: SLF001
            side_effect=["      1      2", "opened", "1", timeout, timeout, timeout],
        )
        first = await client.async_get_data()
        second = await client.async_get_data()
        self.assertEqual(second["last_success"], first["last_success"])
        self.assertEqual(second["stale_fields"], set(first["last_success"]))
        self.assertEqual(second["water_total_raw"], 1)

    async def test_initial_failure_does_not_create_fake_values(self) -> None:
        """Do not report a successful first refresh with no readings."""
        client = api.JudoISoftApiClient("judo.invalid", "test", "test", None)
        client._get = AsyncMock(  # noqa: SLF001
            side_effect=api.JudoISoftApiClientCommunicationError("simulated outage"),
        )
        with self.assertRaises(api.JudoISoftApiClientCommunicationError):
            await client.async_get_data()

    async def test_read_after_timeout_uses_a_new_session(self) -> None:
        """A late reply must not share the next read's pending response slot."""
        client = api.JudoISoftApiClient("judo.invalid", "test", "test", None)
        timeout = api.JudoISoftApiClientCommunicationError("simulated timeout")
        submit = AsyncMock(
            side_effect=[
                {"status": "ok", "token": "first"},
                {"status": "ok", "data": [{"wtuType": "test", "serial number": "1"}]},
                {"status": "ok"},
                timeout,
                {"status": "ok", "token": "second"},
                {"status": "ok"},
                {"status": "ok", "data": "opened"},
            ]
        )
        client._submit = submit  # noqa: SLF001
        with self.assertRaises(api.JudoISoftApiClientCommunicationError):
            await client._get("consumption", "water total")  # noqa: SLF001
        self.assertEqual(await client._get("waterstop", "valve"), "opened")  # noqa: SLF001
        self.assertIn("token=second", submit.await_args.args[0])

    async def test_login_rejection_surfaces_as_authentication_error(self) -> None:
        """Translate the appliance's HTTP 200 login failure into an auth error."""
        client = api.JudoISoftApiClient("judo.invalid", "test", "test", None)
        client._submit = AsyncMock(  # noqa: SLF001
            return_value={"status": "error", "data": "login failed"},
        )
        with self.assertRaises(api.JudoISoftApiClientAuthenticationError):
            await client.async_get_data()

    async def test_expired_session_recovery_is_bounded(self) -> None:
        """Stop if the device rejects the read even on a fresh session."""
        client = api.JudoISoftApiClient("judo.invalid", "test", "test", None)
        submit = AsyncMock(
            side_effect=[
                {"status": "ok", "token": "first"},
                {"status": "ok", "data": [{"wtuType": "test", "serial number": 1}]},
                {"status": "ok"},
                {"status": "error", "data": "not logged in"},
                {"status": "ok", "token": "second"},
                {"status": "ok"},
                {"status": "error", "data": "not logged in"},
            ]
        )
        client._submit = submit  # noqa: SLF001
        with self.assertRaises(api.JudoISoftApiClientCommunicationError):
            await client._get("consumption", "water total")  # noqa: SLF001
        reads = [
            c.args[0]
            for c in submit.await_args_list
            if "group=consumption" in c.args[0]
        ]
        self.assertEqual(len(reads), 2)
        self.assertIn("token=first", reads[0])
        self.assertIn("token=second", reads[1])

    async def test_malformed_measurement_is_not_reported_as_false(self) -> None:
        """Leave an unread valve unknown while exposing successful measurements."""
        client = api.JudoISoftApiClient("judo.invalid", "test", "test", None)
        client._get = AsyncMock(side_effect=["      1      2", "unexpected", "0"])  # noqa: SLF001
        snapshot = await client.async_get_data()
        self.assertNotIn("valve", snapshot)
        self.assertNotIn("valve", snapshot["last_success"])
        self.assertFalse(snapshot["vacation"])

    async def test_login_timeout_stops_remaining_reads(self) -> None:
        """Do not resend a timed-out setup request for every measurement."""
        client = api.JudoISoftApiClient("judo.invalid", "test", "test", None)
        submit = AsyncMock(
            side_effect=api.JudoISoftApiClientCommunicationError("timeout")
        )
        client._submit = submit  # noqa: SLF001
        with self.assertRaises(api.JudoISoftApiClientCommunicationError):
            await client.async_get_data()
        submit.assert_awaited_once()

    async def test_rejected_discovery_token_does_not_survive_next_poll(self) -> None:
        """Use a fresh token after the server rejected device discovery."""
        client = api.JudoISoftApiClient("judo.invalid", "test", "test", None)
        submit = AsyncMock(
            side_effect=[
                {"status": "ok", "token": "first"},
                {"status": "error", "data": "not logged in"},
                {"status": "ok", "token": "second"},
                {"status": "ok", "data": [{"wtuType": "test", "serial number": 1}]},
                {"status": "ok"},
                {"status": "ok", "data": "      1      2"},
                {"status": "ok", "data": "opened"},
                {"status": "ok", "data": "0"},
            ]
        )
        client._submit = submit  # noqa: SLF001
        with self.assertRaises(api.JudoISoftApiClientCommunicationError):
            await client.async_get_data()
        snapshot = await client.async_get_data()
        self.assertEqual(snapshot["water_total_soft"], 2)
        self.assertIn("token=second", submit.await_args_list[3].args[0])

    async def test_aiohttp_does_not_replay_after_disconnect(self) -> None:
        """A closed connection after sending a read must not cause a replay."""
        received = 0

        async def disconnect(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            nonlocal received
            try:
                await reader.readuntil(b"\r\n\r\n")
                received += 1
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(disconnect, "127.0.0.1", 0)
        async with server, aiohttp.ClientSession() as session:
            port = server.sockets[0].getsockname()[1]
            client = api.JudoISoftApiClient("127.0.0.1", "test", "test", session)
            with self.assertRaises(api.JudoISoftApiClientCommunicationError):
                await client._submit(f"http://127.0.0.1:{port}/")  # noqa: SLF001
        self.assertEqual(received, 1)
