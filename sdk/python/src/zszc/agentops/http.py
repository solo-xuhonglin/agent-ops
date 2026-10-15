import json
from collections.abc import Iterator
from typing import Self

import httpx

from zszc.agentops.errors import Timeout, from_response


def _error_envelope(response: httpx.Response) -> dict:
    # A proxy in front (nginx) can answer with an HTML error page; the
    # envelope is best-effort, the status always maps.
    try:
        error = response.json().get("error")
    except ValueError:
        error = None
    if isinstance(error, dict) and error:
        return error
    return {"message": response.text[:200]}


class Http:
    """Transport for contract v1: one JSON path and one SSE path."""

    def __init__(self, base_url: str, key: str, timeout: float = 10.0):
        self._timeout = timeout
        self._client = httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {key}"}, timeout=timeout)

    def json(self, method: str, path: str, body: dict | None = None) -> dict:
        try:
            response = self._client.request(method, path, json=body)
        except httpx.TimeoutException as error:
            raise Timeout(f"{method} {path} timed out after {self._timeout}s") from error
        if response.status_code >= 300:
            envelope = _error_envelope(response)
            raise from_response(response.status_code, envelope.get("code", ""), envelope.get("message", ""))
        return response.json()

    def events(self, path: str, params: dict, *, read_timeout: float) -> Iterator[tuple[str | None, dict]]:
        """Yield (frame_id, payload) per SSE frame; the stream never ends on
        its own. read_timeout bounds the silence between frames — exceeding
        it raises ReadTimeout, the caller's cue to reconnect.
        """
        with self._client.stream(
            "GET", path, params=params, timeout=httpx.Timeout(self._timeout, read=read_timeout)
        ) as response:
            if response.status_code >= 300:
                response.read()
                envelope = _error_envelope(response)
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

    def __exit__(self, *exc) -> None:
        self.close()
