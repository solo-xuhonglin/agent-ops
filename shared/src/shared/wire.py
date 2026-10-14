import asyncio
import json
import socket
import struct

_LENGTH = struct.Struct(">I")


def encode(type_: str, data: dict) -> bytes:
    payload = json.dumps({"type": type_, "data": data}, separators=(",", ":")).encode("utf-8")
    return _LENGTH.pack(len(payload)) + payload


async def read_frame(reader: asyncio.StreamReader) -> tuple[str, dict]:
    header = await reader.readexactly(_LENGTH.size)
    payload = await reader.readexactly(_LENGTH.unpack(header)[0])
    message = json.loads(payload.decode("utf-8"))
    return message["type"], message["data"]


def read_frame_sync(sock: socket.socket) -> tuple[str, dict]:
    header = _recv_exact(sock, _LENGTH.size)
    payload = _recv_exact(sock, _LENGTH.unpack(header)[0])
    message = json.loads(payload.decode("utf-8"))
    return message["type"], message["data"]


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    chunks = []
    while sum(len(chunk) for chunk in chunks) < count:
        chunk = sock.recv(count - sum(len(chunk) for chunk in chunks))
        if not chunk:
            raise ConnectionError("peer closed")
        chunks.append(chunk)
    return b"".join(chunks)
