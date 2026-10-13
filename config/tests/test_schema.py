import uuid

import pytest
from config.models import Tenant
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import db


def test_tenant_round_trip():
    name = f"tenant-{uuid.uuid4().hex[:8]}"
    tenant = Tenant(name=name)
    with Session(db.engine) as session, session.begin():
        session.add(tenant)
        session.flush()
        tenant_id = tenant.id

    with Session(db.engine) as session:
        stored = session.scalar(select(Tenant).where(Tenant.name == name))

    assert stored is not None
    assert stored.id == tenant_id
    assert stored.created_at is not None


def test_duplicate_tenant_name_rejected():
    name = f"tenant-{uuid.uuid4().hex[:8]}"
    with Session(db.engine) as session, session.begin():
        session.add(Tenant(name=name))

    with pytest.raises(IntegrityError), Session(db.engine) as session, session.begin():
        session.add(Tenant(name=name))
