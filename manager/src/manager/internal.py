import asyncio
import os

from shared.wire import read_frame

# Soft state, rebuilt from worker reports after any restart (docs/06 §3).
# workers: persistent link per worker_id; run_conns: proof a run is running;
# reported_running: each worker's claimed runs at its last report.
workers: dict[str, asyncio.StreamWriter] = {}
run_conns: set[str] = set()
reported_running: dict[str, set[str]] = {}
dispatched_at: dict[str, float] = {}
port: int = 0


async def start() -> asyncio.Server:
    global port
    server = await asyncio.start_server(
        _handle_connection, "0.0.0.0", int(os.environ.get("MANAGER_INTERNAL_PORT", "9000"))
    )
    port = server.sockets[0].getsockname()[1]
    return server


async def _handle_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    worker_id = None
    run_id = None
    try:
        while True:
            type_, data = await read_frame(reader)
            if type_ != "state":
                continue
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
