import socket
import threading
import time
from contextlib import contextmanager

import uvicorn
from manager.app import app
from shared.models import RunLog
from shared.wire import encode, read_frame_sync
from sqlalchemy.orm import Session

from manager import internal

TEST_KEY = "manager-test-key"

# Soft state a uvicorn-thread lifespan rebinds; snapshotted around live_server.
_GLOBAL_STATE = (
    "workers",
    "run_conns",
    "reported_running",
    "dispatched_at",
    "run_session",
    "subscribers",
    "session_runs",
    "port",
    "_loop",
    "_wakeup",
)


@contextmanager
def live_server():
    """Run the real app on a uvicorn thread; yield its base URL.

    The thread's lifespan rebinds internal's module state to its own loop;
    the snapshot restores the earlier state (the session TestClient's) so
    later suites see it.
    """
    snapshot = {name: getattr(internal, name) for name in _GLOBAL_STATE}
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        for name, value in snapshot.items():
            setattr(internal, name, value)


class FakeWorker:
    """A sync stand-in worker: reports on the persistent link and can read frames."""

    def __init__(self, worker_id: str = "w-test"):
        self.worker_id = worker_id
        self.sock = socket.create_connection(("127.0.0.1", internal.port), timeout=3)
        self.sock.sendall(encode("state", {"worker_id": worker_id, "running": []}))
        time.sleep(0.2)

    def read(self):
        return read_frame_sync(self.sock)

    def close(self):
        self.sock.close()
        time.sleep(0.2)


def write_log(engine, run_id, seq: int, type_: str, payload: dict | None = None):
    with Session(engine) as session, session.begin():
        session.add(RunLog(run_id=run_id, seq=seq, action_id=None, type=type_, payload=payload or {}))


def submit(client, headers, session_id: str, content: str = "hi"):
    response = client.post(
        f"/v1/sessions/{session_id}/runs", headers=headers, json={"content": content, "params": {"project_id": "P"}}
    )
    assert response.status_code == 201
    return response.json()
