import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Self

import httpx

from zszc.agentops.errors import RunCancelled, RunFailed, Timeout
from zszc.agentops.http import Http


@dataclass
class InputParam:
    key: str
    type: str
    required: bool
    description: str
    default: Any = None


@dataclass
class Agent:
    input: list[InputParam]


@dataclass
class Event:
    """One stream event; run_id/seq are None on stream frames."""

    type: str
    run_id: str | None
    seq: int | None
    data: dict


@dataclass
class Result:
    output: Any


class Run:
    """A run handle; snapshot fields are None until a view has been seen."""

    def __init__(self, client: "Client", run_id: str, session_id: str, view: dict | None = None):
        self._client = client
        self.id = run_id
        self.session_id = session_id
        self.status: str | None = view.get("status") if view else None
        self.result: Any = view.get("result") if view else None
        self.error: str | None = view.get("error") if view else None
        self.cancel_requested: bool | None = view.get("cancel_requested") if view else None

    def _adopt(self, view: dict) -> None:
        self.status = view["status"]
        self.result = view["result"]
        self.error = view["error"]
        self.cancel_requested = view["cancel_requested"]

    def cancel(self) -> None:
        self._adopt(self._client._http.json("POST", f"/v1/runs/{self.id}/cancel"))

    def wait(self, *, timeout: float | None = None) -> "Result":
        """Poll the run until terminal; polling reads the log tail, the source
        of truth, so a run whose wire events were missed still resolves.

        Timeout abandons the wait, never the run (docs/02 §6.3).
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            self._adopt(self._client._http.json("GET", f"/v1/runs/{self.id}"))
            if self.status == "succeeded":
                return Result(self.result)
            if self.status == "failed":
                raise RunFailed(self.error or "run failed")
            if self.status == "cancelled":
                raise RunCancelled(f"run {self.id} was cancelled")
            if deadline is not None and time.monotonic() >= deadline:
                raise Timeout(f"run {self.id} still {self.status} after {timeout}s")
            time.sleep(self._client.poll_interval)


class EventStream:
    """An events-only iterator over one session subscription.

    Built with the subscription already live: the manager's immediate state
    push has been consumed. State frames, silence detection, and reconnects
    stay internal; duplicates are suppressed by a per-run seq watermark.
    """

    def __init__(self, session: "Session", cursor: tuple[str, int] | None):
        self._session = session
        self._cursor = cursor
        self._watermark: dict[str, int] = {}
        self._frames = self._open()

    def _open(self) -> Iterator[tuple[str | None, dict]]:
        params = {} if self._cursor is None else {"from_run": self._cursor[0], "from_seq": self._cursor[1]}
        frames = self._session._client._http.events(
            f"/v1/sessions/{self._session.id}/events", params, read_timeout=self._session._client.idle_timeout
        )
        try:
            self._session._adopt_state(next(frames)[1]["data"])  # first frame is always the state push
        except StopIteration:  # EOF before the state push: a dead link
            raise httpx.TransportError("subscription closed before the state push") from None
        return frames

    def __iter__(self) -> "EventStream":
        return self

    def __next__(self) -> Event:
        while True:
            try:
                payload = next(self._frames)[1]
            except (StopIteration, httpx.TransportError):
                # The stream never ends on its own: EOF is a dropped link.
                # Silence past the idle timeout surfaces as ReadTimeout here too.
                time.sleep(self._session._client.reconnect_delay)
                try:
                    self._frames = self._open()
                except httpx.TransportError:  # redial failed: transient, keep the cursor and retry
                    continue
                continue
            if payload["type"] == "state":
                self._session._adopt_state(payload["data"])
                continue
            data = payload["data"]
            if "seq" not in data:
                # Stream frames apply directly: no watermark, no cursor.
                return Event(payload["type"], data.get("run_id"), None, data)
            if data["seq"] <= self._watermark.get(data["run_id"], 0):
                continue
            self._watermark[data["run_id"]] = data["seq"]
            self._cursor = (data["run_id"], data["seq"])
            return Event(payload["type"], data["run_id"], data["seq"], data)

    def close(self) -> None:
        self._frames.close()


class Session:
    """A conversation handle: submit, stream, and the current run (docs/02 §3)."""

    def __init__(self, client: "Client", session_id: str):
        self._client = client
        self.id = session_id
        self.current_run: Run | None = None

    def submit(
        self,
        content: str,
        *,
        params: dict | None = None,
        output_schema: dict | None = None,
        request_id: str | None = None,
    ) -> Run:
        body = {"content": content, "params": params or {}, "output_schema": output_schema, "request_id": request_id}
        view = self._client._http.json("POST", f"/v1/sessions/{self.id}/runs", body)
        return self._client._run(view)

    def _adopt_state(self, data: dict) -> None:
        run_id = data.get("run")
        self.current_run = None if run_id is None else Run(self._client, run_id, self.id)

    def stream(self, *, from_run: str | None = None, from_seq: int = 0) -> EventStream:
        """Subscribe to the session and return an events-only iterator.

        Returns once the subscription is live, so a submit right after cannot
        miss stream frames. HTTP errors raise here, no retry.
        """
        cursor = None if from_run is None else (from_run, from_seq)
        return EventStream(self, cursor)


class Client:
    """The agent handle; the key decides tenant, agent and principal."""

    def __init__(
        self,
        key: str,
        base_url: str,
        poll_interval: float = 0.5,
        reconnect_delay: float = 1.0,
        idle_timeout: float = 30.0,
    ):
        self._http = Http(base_url, key)
        self.poll_interval = poll_interval
        self.reconnect_delay = reconnect_delay
        self.idle_timeout = idle_timeout

    def get_agent(self) -> Agent:
        data = self._http.json("GET", "/v1/agent")
        return Agent([InputParam(**param) for param in data["input"]])

    def new_session(self) -> Session:
        data = self._http.json("POST", "/v1/sessions")
        return Session(self, data["session_id"])

    def get_session(self, session_id: str) -> Session:
        return Session(self, session_id)

    def get_run(self, run_id: str) -> Run:
        return self._run(self._http.json("GET", f"/v1/runs/{run_id}"))

    def _run(self, view: dict) -> Run:
        return Run(self, view["run_id"], view["session_id"], view)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def create_client(
    key: str,
    *,
    base_url: str,
    poll_interval: float = 0.5,
    reconnect_delay: float = 1.0,
    idle_timeout: float = 30.0,
) -> Client:
    return Client(key, base_url, poll_interval, reconnect_delay, idle_timeout)
