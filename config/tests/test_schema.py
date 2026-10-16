import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from shared.models import Account, AuthSession, Membership, Tenant
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from config import db


def test_config_creates_only_its_tables(schema):
    names = set(inspect(db.engine).get_table_names())

    assert names == {
        "iam_tenants",
        "agent_definitions",
        "release_artifacts",
        "iam_accounts",
        "iam_memberships",
        "iam_sessions",
    }


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


def test_account_membership_session_round_trip():
    email = f"u-{uuid.uuid4().hex[:8]}@example.com"
    token_hash = hashlib.sha256(uuid.uuid4().hex.encode()).hexdigest()

    with Session(db.engine) as session, session.begin():
        tenant = Tenant(name=f"tenant-{uuid.uuid4().hex[:8]}")
        account = Account(email=email, password_hash="$argon2id$stub")
        session.add_all([tenant, account])
        session.flush()
        session.add_all(
            [
                Membership(account_id=account.id, tenant_id=tenant.id, role="admin"),
                AuthSession(
                    token_hash=token_hash,
                    account_id=account.id,
                    current_tenant_id=tenant.id,
                    expires_at=datetime.now(timezone.utc) + timedelta(days=7),
                ),
            ]
        )
        account_id, tenant_id = account.id, tenant.id

    with Session(db.engine) as session:
        stored_account = session.get(Account, account_id)
        stored_membership = session.scalar(select(Membership).where(Membership.account_id == account_id))
        stored_auth_session = session.scalar(select(AuthSession).where(AuthSession.token_hash == token_hash))

    assert stored_account.email == email
    assert stored_account.created_at is not None
    assert stored_membership.tenant_id == tenant_id
    assert stored_membership.role == "admin"
    assert stored_membership.created_at is not None
    assert stored_auth_session.current_tenant_id == tenant_id
    assert stored_auth_session.created_at is not None


def test_duplicate_account_email_rejected():
    email = f"u-{uuid.uuid4().hex[:8]}@example.com"
    with Session(db.engine) as session, session.begin():
        session.add(Account(email=email, password_hash="$argon2id$stub"))

    with pytest.raises(IntegrityError), Session(db.engine) as session, session.begin():
        session.add(Account(email=email, password_hash="$argon2id$other"))


def test_duplicate_membership_rejected():
    with Session(db.engine) as session, session.begin():
        tenant = Tenant(name=f"tenant-{uuid.uuid4().hex[:8]}")
        account = Account(email=f"u-{uuid.uuid4().hex[:8]}@example.com", password_hash="$argon2id$stub")
        session.add_all([tenant, account])
        session.flush()
        account_id, tenant_id = account.id, tenant.id
        session.add(Membership(account_id=account_id, tenant_id=tenant_id, role="viewer"))

    with pytest.raises(IntegrityError), Session(db.engine) as session, session.begin():
        session.add(Membership(account_id=account_id, tenant_id=tenant_id, role="editor"))
