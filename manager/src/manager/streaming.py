import asyncio
import json
import os
from collections.abc import AsyncIterator
from uuid import UUID

from manager import internal, runs


def _frame(payload: dict, cursor: str | None = None) -> str:
    lines = []
    if cursor is not None:
        lines.append(f"id: {cursor}")
    lines.append("data: " + json.dumps(payload, separators=(",", ":")))
    return "\n".join(lines) + "\n\n"


def state_frame(session_id: str) -> str:
    run = internal.session_runs.get(session_id)
    return _frame({"type": "state", "data": {"session_id": session_id, "run": run}})


async def stream(session_id: UUID, from_run: UUID | None, from_seq: int) -> AsyncIterator[str]:
    """Subscribe to a session: catch up, then live, with periodic state.

    The queue registers before the replay read, so events landing in between
    arrive twice — clients dedup by (run_id, seq) per the contract.
    """
    sid = str(session_id)
    queue: asyncio.Queue = asyncio.Queue(maxsize=internal.SUBSCRIBER_QUEUE_LIMIT)
    internal.subscribe(sid, queue)
    interval = float(os.environ.get("STATE_INTERVAL", "10"))
    try:
        yield state_frame(sid)
        for event in await asyncio.to_thread(runs.replay_events, sid, from_run, from_seq):
            yield _frame(event, f"{event['data']['run_id']}:{event['data']['seq']}")
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=interval)
                yield _frame(item, f"{item['data']['run_id']}:{item['data']['seq']}")
            except TimeoutError:
                if queue not in internal.subscribers.get(sid, set()):
                    break
                yield state_frame(sid)
    finally:
        internal.unsubscribe(sid, queue)
