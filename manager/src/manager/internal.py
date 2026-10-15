import asyncio
import logging
import os
import time

from shared.events import STREAM_EVENT_TYPES, EventType
from shared.models import ExecutionRun, ExecutionSession, ReleaseArtifact
from shared.wire import encode, read_frame
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from manager import db
from manager.runs import is_active

log = logging.getLogger(__name__)

# Soft state, rebuilt from worker reports after any restart (docs/06 §3).
# workers: persistent link per worker_id; run_conns: proof a run is running;
# reported_running: each worker's claimed runs at its last report.
workers: dict[str, asyncio.StreamWriter] = {}
run_conns: set[str] = set()
reported_running: dict[str, set[str]] = {}
dispatched_at: dict[str, float] = {}
run_session: dict[str, str] = {}
# Live side: subscriber queues per session, and the session state projected
# from forwarded events (a re-statement, never a second source — docs/06 §4).
subscribers: dict[str, set[asyncio.Queue]] = {}
session_runs: dict[str, dict | None] = {}
# Accumulated text of the in-flight message per run: the SSE catch-up
# snapshot for late subscribers (docs/05 §4.2 step 3).
message_buffers: dict[str, str] = {}

# A subscriber that falls this far behind is dropped; it reconnects with
# its cursor and the log replays it (at-least-once stands).
SUBSCRIBER_QUEUE_LIMIT = 256

_TERMINAL_EVENTS = {EventType.run_finished.value, EventType.run_failed.value}
port: int = 0

_EVENT_NAMES = {member.value for member in EventType}

_loop: asyncio.AbstractEventLoop | None = None
_wakeup: asyncio.Event | None = None


async def start() -> asyncio.Server:
    global port
    server = await asyncio.start_server(
        _handle_connection, "0.0.0.0", int(os.environ.get("MANAGER_INTERNAL_PORT", "9000"))
    )
    port = server.sockets[0].getsockname()[1]
    return server


def request_dispatch() -> None:
    # Called from sync HTTP threads; wakes the dispatcher inside the loop.
    if _loop is not None and _wakeup is not None:
        _loop.call_soon_threadsafe(_wakeup.set)


def broadcast(type_: str, data: dict) -> None:
    if _loop is not None:
        _loop.call_soon_threadsafe(_send_all, type_, data)


async def run_dispatcher(stop: asyncio.Event) -> None:
    global _loop, _wakeup
    _loop = asyncio.get_running_loop()
    _wakeup = asyncio.Event()
    while not stop.is_set():
        # Read per tick so tests can tighten the cadence with monkeypatch.
        interval = float(os.environ.get("SWEEP_INTERVAL", "2.0"))
        try:
            await asyncio.wait_for(_wakeup.wait(), timeout=interval)
        except TimeoutError:
            pass
        _wakeup.clear()
        try:
            await dispatch_pass()
        except Exception:
            log.exception("dispatch pass failed")


async def dispatch_pass() -> None:
    # The sweep is gated on at least one worker having reported (docs/06 §2.5).
    if not workers:
        return
    claimed = set().union(*reported_running.values()) if reported_running else set()
    grace = float(os.environ.get("DISPATCH_GRACE", "3.0"))
    eligible = await asyncio.to_thread(_eligible_runs, set(run_conns), claimed, grace, time.monotonic())
    for run_id in eligible:
        packet = await asyncio.to_thread(_load_dispatch, run_id)
        if packet is None:
            continue
        targets = list(workers.values())
        if not targets:
            continue
        targets[0].write(encode("dispatch", packet))
        try:
            await targets[0].drain()
        except ConnectionError:
            continue
        dispatched_at[run_id] = time.monotonic()
        run_session[run_id] = packet["session_id"]


def _eligible_runs(excluded: set[str], claimed: set[str], grace: float, now: float) -> list[str]:
    fresh = []
    with Session(db.engine) as session:
        for run in session.scalars(select(ExecutionRun)).all():
            run_id = str(run.id)
            if run_id in excluded or run_id in claimed:
                continue
            if not is_active(session, run.id):
                continue
            last = dispatched_at.get(run_id)
            if last is not None and now - last < grace:
                continue
            fresh.append(run_id)
    return fresh


def _load_dispatch(run_id: str) -> dict | None:
    with Session(db.engine) as session, session.begin():
        row = session.execute(
            update(ExecutionRun)
            .where(ExecutionRun.id == run_id)
            .values(epoch=ExecutionRun.epoch + 1)
            .returning(
                ExecutionRun.epoch,
                ExecutionRun.session_id,
                ExecutionRun.content,
                ExecutionRun.params,
                ExecutionRun.output_schema,
                ExecutionRun.artifact_id,
            )
        ).first()
        if row is None:
            return None
        caller = session.scalar(select(ExecutionSession.caller).where(ExecutionSession.id == row.session_id))
        snapshot = session.scalar(select(ReleaseArtifact.snapshot).where(ReleaseArtifact.id == row.artifact_id))
    return {
        "run_id": run_id,
        "epoch": row.epoch,
        "session_id": str(row.session_id),
        "snapshot": snapshot,
        "input": {
            "content": row.content,
            "params": row.params,
            "output_schema": row.output_schema,
            "caller": caller,
        },
    }


def _send_all(type_: str, data: dict) -> None:
    frame = encode(type_, data)
    for writer in list(workers.values()):
        writer.write(frame)


def subscribe(session_id: str, queue: asyncio.Queue) -> None:
    subscribers.setdefault(session_id, set()).add(queue)


def unsubscribe(session_id: str, queue: asyncio.Queue) -> None:
    queues = subscribers.get(session_id)
    if queues is not None:
        queues.discard(queue)
        if not queues:
            subscribers.pop(session_id, None)


def _forward(session_id: str, payload: dict) -> None:
    queues = subscribers.get(session_id)
    if not queues:
        return
    for queue in list(queues):
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            log.error("subscriber too slow; dropping it until it reconnects with a cursor")
            queues.discard(queue)
            if not queues:
                subscribers.pop(session_id, None)


async def _handle_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    worker_id = None
    run_id = None
    try:
        while True:
            type_, data = await read_frame(reader)
            if type_ == "state":
                if "run_id" in data:
                    run_id = data["run_id"]
                    run_conns.add(run_id)
                elif "worker_id" in data:
                    worker_id = data["worker_id"]
                    workers[worker_id] = writer
                    if "running" in data:
                        reported_running[worker_id] = set(data["running"])
            elif type_ in _EVENT_NAMES:
                session_id = run_session.get(data.get("run_id"))
                if session_id is None:
                    log.error("event for undispatched run %r; dropping", data.get("run_id"))
                else:
                    if type_ in _TERMINAL_EVENTS:
                        session_runs[session_id] = None
                        message_buffers.pop(data["run_id"], None)
                    else:
                        session_runs[session_id] = {"run_id": data["run_id"], "seq": data.get("seq")}
                    _forward(session_id, {"type": type_, "data": data})
            elif type_ in STREAM_EVENT_TYPES:
                session_id = run_session.get(data.get("run_id"))
                if session_id is None:
                    log.error("stream frame for undispatched run %r; dropping", data.get("run_id"))
                else:
                    run = data["run_id"]
                    if type_ == "message_started":
                        message_buffers[run] = ""
                    else:
                        message_buffers[run] = message_buffers.get(run, "") + data.get("text", "")
                    _forward(session_id, {"type": type_, "data": data})
            else:
                # One deployment upgrades together: an unknown name on the
                # internal link is a bug, so the connection drops (docs/06 §1.2).
                log.error("unknown frame %r on internal link; dropping connection", type_)
                break
    except (asyncio.IncompleteReadError, ConnectionError):
        pass
    finally:
        if worker_id is not None and workers.get(worker_id) is writer:
            del workers[worker_id]
            reported_running.pop(worker_id, None)
        if run_id is not None:
            run_conns.discard(run_id)
            message_buffers.pop(run_id, None)
        writer.close()
