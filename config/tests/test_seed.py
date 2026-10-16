import hashlib
import uuid

from config.security import hash_password, verify_password
from config.seed import SEED_SPEC, TENANT_NAME, seed
from shared.models import Account, AgentDefinition, AuthSession, Membership, ReleaseArtifact, Tenant
from shared.spec import AgentSpec
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from config import db

KEY = "test-seed-key"


def admin_email() -> str:
    return f"admin-{uuid.uuid4().hex[:8]}@example.com"


def clear_accounts():
    """The bootstrap admin fires only on an empty account table; earlier suites have rows."""
    with Session(db.engine) as session, session.begin():
        session.execute(delete(AuthSession))
        session.execute(delete(Membership))
        session.execute(delete(Account))


def test_seed_idempotent():
    seed(db.engine, KEY, admin_email(), "admin-pw")
    seed(db.engine, KEY, admin_email(), "admin-pw")

    with Session(db.engine) as session:
        agent_id = session.scalar(select(AgentDefinition.id).where(AgentDefinition.name == SEED_SPEC.name))
        tenants = session.scalar(select(func.count()).select_from(Tenant).where(Tenant.name == TENANT_NAME))
        agents = session.scalar(
            select(func.count()).select_from(AgentDefinition).where(AgentDefinition.name == SEED_SPEC.name)
        )
        artifacts = session.scalar(
            select(func.count())
            .select_from(ReleaseArtifact)
            .where(ReleaseArtifact.target_kind == "agent", ReleaseArtifact.target_id == agent_id)
        )

    assert tenants == 1
    assert agents == 1
    assert artifacts == 1


def test_snapshot_round_trips_through_db():
    seed(db.engine, KEY, admin_email(), "admin-pw")

    with Session(db.engine) as session:
        artifact = session.scalar(select(ReleaseArtifact))

    assert AgentSpec.model_validate(artifact.snapshot) == SEED_SPEC


def test_key_hash_is_sha256():
    seed(db.engine, KEY, admin_email(), "admin-pw")

    with Session(db.engine) as session:
        agent = session.scalar(select(AgentDefinition))

    assert agent.key_hash == hashlib.sha256(KEY.encode()).hexdigest()
    assert agent.key_version == 1


def test_seed_creates_bootstrap_admin():
    clear_accounts()
    email = admin_email()
    result = seed(db.engine, KEY, email, "admin-pw-123")

    with Session(db.engine) as session:
        account = session.scalar(select(Account).where(Account.email == email))
        tenant_id = session.scalar(select(Tenant.id).where(Tenant.name == TENANT_NAME))
        membership = session.scalar(select(Membership).where(Membership.account_id == account.id))

    assert result.admin_created is True
    assert verify_password("admin-pw-123", account.password_hash) is True
    assert membership.tenant_id == tenant_id
    assert membership.role == "admin"


def test_seed_admin_idempotent():
    clear_accounts()
    email = admin_email()
    assert seed(db.engine, KEY, email, "admin-pw-123").admin_created is True
    assert seed(db.engine, KEY, email, "admin-pw-123").admin_created is False

    with Session(db.engine) as session:
        accounts = session.scalar(select(func.count()).select_from(Account).where(Account.email == email))

    assert accounts == 1


def test_seed_skips_admin_when_any_account_exists():
    with Session(db.engine) as session, session.begin():
        session.add(Account(email=f"u-{uuid.uuid4().hex[:8]}@example.com", password_hash=hash_password("x")))

    email = admin_email()
    result = seed(db.engine, KEY, email, "admin-pw-123")

    with Session(db.engine) as session:
        admin = session.scalar(select(Account).where(Account.email == email))

    assert result.admin_created is False
    assert admin is None
