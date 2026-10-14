import uuid

from shared.models import ExecutionRun, RunLog
from sqlalchemy import select
from sqlalchemy.orm import Session

_TERMINAL_TYPES = {"run_finished", "run_failed"}


def execute(engine, dispatch: dict) -> None:
    """Run the stub: no model, at most run_started plus one terminal row.

    Idempotent across re-dispatches: existing rows are kept, seq continues
    from the tail, and a stale epoch (the manager re-dispatched elsewhere)
    writes nothing.
    """
    run_id = uuid.UUID(dispatch["run_id"])
    with Session(engine) as session:
        run = session.scalar(select(ExecutionRun).where(ExecutionRun.id == run_id))
        if run is None or run.epoch != dispatch["epoch"]:
            return

        tail = session.scalar(select(RunLog).where(RunLog.run_id == run_id).order_by(RunLog.seq.desc()).limit(1))
        last_seq = tail.seq if tail is not None else 0
        terminal = tail is not None and tail.type in _TERMINAL_TYPES

    if terminal:
        return

    entries = []
    if last_seq == 0:
        payload = dispatch["input"]
        entries.append(
            RunLog(
                run_id=run_id,
                seq=1,
                action_id=None,
                type="run_started",
                payload={
                    "content": payload["content"],
                    "params": payload["params"],
                    "output_schema": payload["output_schema"],
                    "session_id": dispatch["session_id"],
                    "caller": payload["caller"],
                },
            )
        )

    with Session(engine) as session:
        run = session.scalar(select(ExecutionRun).where(ExecutionRun.id == run_id))
        cancelled = run.cancel_requested if run is not None else True

    entries.append(
        RunLog(
            run_id=run_id,
            seq=last_seq + 1 if last_seq > 0 else 2,
            action_id=None,
            type="run_finished",
            payload=(
                {"status": "cancelled", "result": None}
                if cancelled
                else {"status": "succeeded", "result": "stub: " + dispatch["input"]["content"]}
            ),
        )
    )
    with Session(engine) as session, session.begin():
        session.add_all(entries)
