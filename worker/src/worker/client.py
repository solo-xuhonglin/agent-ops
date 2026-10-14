import asyncio
import logging
import os
import socket

from shared.wire import encode, read_frame

from worker import db
from worker.executor import execute

log = logging.getLogger(__name__)


async def _connect() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    host = os.environ.get("MANAGER_HOST", "manager")
    port = int(os.environ.get("MANAGER_PORT", "9000"))
    return await asyncio.open_connection(host, port)


async def _run_one(dispatch: dict) -> None:
    """Open the Run connection for the dispatched run, execute, close it.

    The Run connection's presence is the manager's evidence the run started
    (docs/06 §2.2); it must outlive the log writes but nothing else.
    """
    worker_id = os.environ.get("WORKER_ID", socket.gethostname())
    _reader, writer = await _connect()
    try:
        writer.write(
            encode("state", {"worker_id": worker_id, "run_id": dispatch["run_id"], "epoch": dispatch["epoch"]})
        )
        await writer.drain()
        await asyncio.to_thread(execute, db.engine, dispatch)
    finally:
        writer.close()
        await writer.wait_closed()


async def run() -> None:
    worker_id = os.environ.get("WORKER_ID", socket.gethostname())
    heartbeat = float(os.environ.get("HEARTBEAT_INTERVAL", "5"))
    while True:
        try:
            reader, writer = await _connect()
            # Registration is the first state frame, claiming nothing.
            writer.write(encode("state", {"worker_id": worker_id, "running": []}))
            await writer.drain()
            log.info("worker %s connected", worker_id)

            async def beat(link: asyncio.StreamWriter):
                while True:
                    await asyncio.sleep(heartbeat)
                    link.write(encode("state", {"worker_id": worker_id}))
                    await link.drain()

            beater = asyncio.create_task(beat(writer))
            try:
                while True:
                    type_, data = await read_frame(reader)
                    if type_ == "dispatch":
                        asyncio.create_task(_run_one(data))
                    elif type_ == "cancel":
                        log.info("cancel noted for %s", data.get("run_id"))
            finally:
                beater.cancel()
        except (ConnectionError, OSError):
            log.warning("link lost; reconnecting in 2s")
            await asyncio.sleep(2)
