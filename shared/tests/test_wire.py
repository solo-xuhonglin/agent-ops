import json
import socket

import pytest
from shared.wire import encode, read_frame_sync


def recv_exact(sock: socket.socket, count: int) -> bytes:
    chunks = []
    while sum(len(chunk) for chunk in chunks) < count:
        chunk = sock.recv(count - sum(len(chunk) for chunk in chunks))
        if not chunk:
            raise ConnectionError("peer closed")
        chunks.append(chunk)
    return b"".join(chunks)


def test_round_trip():
    frame = encode("state", {"worker_id": "w1"})

    length = int.from_bytes(frame[:4], "big")
    payload = json.loads(frame[4:].decode("utf-8"))
    assert length == len(frame) - 4
    assert payload == {"type": "state", "data": {"worker_id": "w1"}}


def test_multiple_frames():
    pair = socket.socketpair()
    try:
        pair[0].sendall(encode("dispatch", {"run_id": "r1"}) + encode("cancel", {"run_id": "r1"}))

        assert read_frame_sync(pair[1]) == ("dispatch", {"run_id": "r1"})
        assert read_frame_sync(pair[1]) == ("cancel", {"run_id": "r1"})
    finally:
        pair[0].close()
        pair[1].close()


def test_partial_frame_blocks_until_complete():
    pair = socket.socketpair()
    try:
        pair[0].sendall(encode("state", {"ok": True})[:6])
        pair[1].settimeout(0.2)

        with pytest.raises(TimeoutError):
            read_frame_sync(pair[1])
    finally:
        pair[0].close()
        pair[1].close()
