import socket
import time

import pytest
from shared.wire import encode

from manager import internal


@pytest.fixture(autouse=True)
def fast_internal(monkeypatch):
    monkeypatch.setenv("MANAGER_INTERNAL_PORT", "0")


def connect_worker(client) -> socket.socket:
    sock = socket.create_connection(("127.0.0.1", internal.port), timeout=2)
    return sock


def test_worker_registration(client):
    sock = connect_worker(client)
    try:
        sock.sendall(encode("state", {"worker_id": "w-test", "running": []}))
        time.sleep(0.3)
        assert "w-test" in internal.workers
    finally:
        sock.close()
    time.sleep(0.3)
    assert "w-test" not in internal.workers


def test_run_connection_registers(client):
    run_id = "11111111-1111-1111-1111-111111111111"
    sock = connect_worker(client)
    try:
        sock.sendall(encode("state", {"worker_id": "w-test", "run_id": run_id, "epoch": 1}))
        time.sleep(0.3)
        assert run_id in internal.run_conns
    finally:
        sock.close()
    time.sleep(0.3)
    assert run_id not in internal.run_conns


def test_heartbeat_keeps_registration(client):
    sock = connect_worker(client)
    try:
        sock.sendall(encode("state", {"worker_id": "w-hb", "running": []}))
        time.sleep(0.2)
        sock.sendall(encode("state", {"worker_id": "w-hb"}))
        time.sleep(0.3)
        assert "w-hb" in internal.workers
    finally:
        sock.close()


def test_unknown_frame_closes_connection(client):
    import time

    sock = connect_worker(client)
    try:
        sock.sendall(encode("bogus", {}))
        sock.settimeout(2)
        # The internal link errors on unknown names (docs/06 §1.2): the
        # server must drop the connection instead of ignoring the frame.
        assert sock.recv(1) == b""
    finally:
        sock.close()
        time.sleep(0.2)
