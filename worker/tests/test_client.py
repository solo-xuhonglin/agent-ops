import asyncio
import contextlib

import pytest
from shared.wire import encode

from worker import client

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_reconnects_after_server_closes(monkeypatch):
    accepted = 0

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        nonlocal accepted
        accepted += 1
        try:
            await reader.readexactly(4)
            await asyncio.sleep(0.1)
            writer.close()
            await writer.wait_closed()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setenv("MANAGER_HOST", "127.0.0.1")
    monkeypatch.setenv("MANAGER_PORT", str(port))
    monkeypatch.setenv("HEARTBEAT_INTERVAL", "30")
    monkeypatch.setenv("RECONNECT_DELAY", "0.1")

    async with server:
        task = asyncio.create_task(client.run())
        try:
            for _ in range(50):
                if accepted >= 2:
                    break
                await asyncio.sleep(0.1)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    assert accepted >= 2


async def test_unknown_frame_closes_link_and_reconnects(monkeypatch):
    accepted = 0

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        nonlocal accepted
        accepted += 1
        try:
            await reader.readexactly(4)
            writer.write(encode("bogus", {}))
            await writer.drain()
            await asyncio.sleep(1)
            writer.close()
            await writer.wait_closed()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setenv("MANAGER_HOST", "127.0.0.1")
    monkeypatch.setenv("MANAGER_PORT", str(port))
    monkeypatch.setenv("HEARTBEAT_INTERVAL", "30")
    monkeypatch.setenv("RECONNECT_DELAY", "0.1")

    async with server:
        task = asyncio.create_task(client.run())
        try:
            for _ in range(50):
                if accepted >= 2:
                    break
                await asyncio.sleep(0.1)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    assert accepted >= 2
