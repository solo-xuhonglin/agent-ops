import uuid

import pytest
from shared.events import EventType
from shared.models import RunLog
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from worker import db


def test_worker_creates_only_its_tables(schema):
    names = set(inspect(db.engine).get_table_names())

    assert names == {"execution_run_log"}


def test_runlog_round_trip(schema):
    run_id = uuid.uuid4()
    with Session(db.engine) as session, session.begin():
        session.add(RunLog(run_id=run_id, seq=1, action_id=None, type=EventType.run_started, payload={"content": "hi"}))

    with Session(db.engine) as session:
        stored = session.scalar(select(RunLog).where(RunLog.run_id == run_id))

    assert stored is not None
    assert stored.seq == 1
    assert stored.type == EventType.run_started
    assert stored.payload == {"content": "hi"}
    assert stored.created_at is not None


def test_duplicate_seq_rejected(schema):
    run_id = uuid.uuid4()
    with Session(db.engine) as session, session.begin():
        session.add(RunLog(run_id=run_id, seq=1, action_id=None, type=EventType.run_started, payload={}))

    with pytest.raises(IntegrityError), Session(db.engine) as session, session.begin():
        session.add(RunLog(run_id=run_id, seq=1, action_id=None, type=EventType.run_finished, payload={}))
