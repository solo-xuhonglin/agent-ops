import socket
import time

from shared.models import RunLog
from shared.wire import encode, read_frame_sync
from sqlalchemy.orm import Session

from manager import internal


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
