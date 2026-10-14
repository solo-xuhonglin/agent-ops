import socket
import time
from uuid import UUID

import pytest
from shared.models import ExecutionRun
from shared.wire import encode
from sqlalchemy import select
from sqlalchemy.orm import Session
from util import FakeWorker, submit

from manager import internal


@pytest.fixture(autouse=True)
def fast_sweep(monkeypatch):
    monkeypatch.setenv("SWEEP_INTERVAL", "0.2")
    monkeypatch.setenv("DISPATCH_GRACE", "0.4")


def test_sweep_redispatches_with_next_epoch(client, auth_headers):
    worker = FakeWorker("w-sweep")
    try:
        session_id = client.post("/v1/sessions", headers=auth_headers).json()["session_id"]
        body = submit(client, auth_headers, session_id)

        first_type, first = worker.read()
        assert first_type == "dispatch" and first["epoch"] == 1

        second_type, second = None, None
        deadline = time.time() + 5
        while time.time() < deadline:
            second_type, second = worker.read()
            if second_type == "dispatch" and second["run_id"] == body["run_id"] and second["epoch"] == 2:
                break
        assert second_type == "dispatch"
        assert second["epoch"] == 2
    finally:
        worker.close()


def test_sweep_skips_connected_runs(client, auth_headers):
    worker = FakeWorker("w-connected")
    try:
        session_id = client.post("/v1/sessions", headers=auth_headers).json()["session_id"]
        body = submit(client, auth_headers, session_id)

        assert worker.read()[0] == "dispatch"

        run_sock = socket.create_connection(("127.0.0.1", internal.port), timeout=3)
        try:
            run_sock.sendall(encode("state", {"worker_id": "w-connected", "run_id": body["run_id"], "epoch": 1}))
            time.sleep(1.0)
            assert body["run_id"] in internal.run_conns

            # While the Run connection is up, several sweep ticks pass and
            # nothing new may arrive on the persistent link.
            worker.sock.settimeout(0.8)
            with pytest.raises(TimeoutError):
                worker.read()
        finally:
            run_sock.close()
            worker.sock.settimeout(3)
    finally:
        worker.sock.settimeout(3)
        worker.close()


def test_sweep_gated_on_registration(client, auth_headers, db_engine):
    session_id = client.post("/v1/sessions", headers=auth_headers).json()["session_id"]
    body = submit(client, auth_headers, session_id)
    time.sleep(1.2)

    with Session(db_engine) as session:
        stored = session.scalar(select(ExecutionRun).where(ExecutionRun.id == UUID(body["run_id"])))

    # No worker reported, so the sweep never dispatched: epoch untouched.
    assert stored.epoch == 0
