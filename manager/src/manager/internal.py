import asyncio
import logging
import os
import time

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
port: int = 0

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


async def _handle_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    worker_id = None
    run_id = None
    try:
        while True:
            type_, data = await read_frame(reader)
            if type_ != "state":
                # One deployment upgrades together: an unknown name on the
                # internal link is a bug, so the connection drops (docs/06 §1.2).
                log.error("unknown frame %r on internal link; dropping connection", type_)
                break
            if "run_id" in data:
                run_id = data["run_id"]
                run_conns.add(run_id)
            elif "worker_id" in data:
                worker_id = data["worker_id"]
                workers[worker_id] = writer
                if "running" in data:
                    reported_running[worker_id] = set(data["running"])
    except (asyncio.IncompleteReadError, ConnectionError):
        pass
    finally:
        if worker_id is not None and workers.get(worker_id) is writer:
            del workers[worker_id]
            reported_running.pop(worker_id, None)
        if run_id is not None:
            run_conns.discard(run_id)
        writer.close()
