import asyncio
import logging
import os
import socket
import threading
import uuid

from shared.events import public_event
from shared.models import RunLog
from shared.wire import encode, read_frame
from sqlalchemy.orm import Session

from worker import db
from worker import engine as engine_module
from worker.llm import LlmClient, llm_from_env

log = logging.getLogger(__name__)

# Cancellation relays: the persistent link sets the run's Event, the engine
# thread reads it between chunks (docs/05 §6).
cancel_events: dict[str, threading.Event] = {}


def _pump(dispatch: dict, llm: LlmClient) -> None:
    """The worker's run path, on a dedicated Run connection (docs/06 §2.2).

    Each durable row lands before its wire frame rides out (docs/06 §2.3);
    stream frames ride out immediately, never persisted.
    """
    run_id = dispatch["run_id"]
    cancelled = cancel_events.setdefault(run_id, threading.Event())
    host = os.environ.get("MANAGER_HOST", "manager")
    port = int(os.environ.get("MANAGER_PORT", "9000"))
    sock = socket.create_connection((host, port))
    try:
        sock.sendall(
            encode(
                "state",
                {
                    "worker_id": os.environ.get("WORKER_ID", socket.gethostname()),
                    "run_id": run_id,
                    "epoch": dispatch["epoch"],
                },
            )
        )
        for item in engine_module.execute(db.engine, dispatch, llm, cancelled):
            if item["kind"] == "stream":
                sock.sendall(encode(item["type"], item["data"]))
                continue
            with Session(db.engine) as session, session.begin():
                session.add(
                    RunLog(
                        run_id=uuid.UUID(run_id),
                        seq=item["seq"],
                        action_id=item["action_id"],
                        type=item["type"],
                        payload=item["payload"],
                    )
                )
            entry = {
                "type": item["type"],
                "data": {"run_id": run_id, "seq": item["seq"], "action_id": item["action_id"], **item["payload"]},
            }
            # The wire carries log types; private fields are stripped here.
            sock.sendall(encode(item["type"], public_event(entry)["data"]))
    except Exception:
        # The run stays open; the sweep re-dispatches it (at-least-once).
        log.exception("run path failed for %s", run_id)
    finally:
        sock.close()
        cancel_events.pop(run_id, None)


async def run() -> None:
    worker_id = os.environ.get("WORKER_ID", socket.gethostname())
    heartbeat = float(os.environ.get("HEARTBEAT_INTERVAL", "5"))
    llm = llm_from_env()
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
                        asyncio.create_task(asyncio.to_thread(_pump, data, llm))
                    elif type_ == "cancel":
                        event = cancel_events.get(data.get("run_id"))
                        if event is not None:
                            event.set()
                    else:
                        log.error("unknown frame %r on internal link; dropping it", type_)
                        raise ConnectionError("unknown frame type")
            finally:
                beater.cancel()
        except (ConnectionError, OSError, asyncio.IncompleteReadError):
            log.warning("link lost; reconnecting")
            await asyncio.sleep(float(os.environ.get("RECONNECT_DELAY", "2")))


async def _connect() -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    host = os.environ.get("MANAGER_HOST", "manager")
    port = int(os.environ.get("MANAGER_PORT", "9000"))
    return await asyncio.open_connection(host, port)
