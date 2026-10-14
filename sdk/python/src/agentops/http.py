import json
from collections.abc import Iterator
from typing import Self

import httpx

from agentops.errors import from_response


class Http:
    """Transport for contract v1: one JSON path and one SSE path."""

    def __init__(self, base_url: str, key: str, timeout: float = 10.0):
        self._client = httpx.Client(
            base_url=base_url, headers={"Authorization": f"Bearer {key}"}, timeout=httpx.Timeout(timeout, read=None)
        )

    def json(self, method: str, path: str, body: dict | None = None) -> dict:
        response = self._client.request(method, path, json=body)
        if response.status_code >= 300:
            envelope = response.json().get("error", {})
            raise from_response(response.status_code, envelope.get("code", ""), envelope.get("message", ""))
        return response.json()

    def events(self, path: str, params: dict) -> Iterator[tuple[str | None, dict]]:
        """Yield (frame_id, payload) per SSE frame; the stream never ends on its own."""
        with self._client.stream("GET", path, params=params) as response:
            if response.status_code >= 300:
                response.read()
                envelope = response.json().get("error", {})
                raise from_response(response.status_code, envelope.get("code", ""), envelope.get("message", ""))
            frame_id = None
            data_lines = []
            for line in response.iter_lines():
                if line == "":
                    if data_lines:
                        yield frame_id, json.loads("\n".join(data_lines))
                    frame_id, data_lines = None, []
                elif line.startswith("id: "):
                    frame_id = line[4:]
                elif line.startswith("data: "):
                    data_lines.append(line[6:])

    def close(self):
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc):
        self.close()
