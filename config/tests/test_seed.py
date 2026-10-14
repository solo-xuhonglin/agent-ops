import hashlib

from config.seed import SEED_SPEC, TENANT_NAME, seed
from shared.models import AgentDefinition, ReleaseArtifact, Tenant
from shared.spec import AgentSpec
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config import db

KEY = "test-seed-key"


def test_seed_idempotent():
    seed(db.engine, KEY)
    seed(db.engine, KEY)

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
    seed(db.engine, KEY)

    with Session(db.engine) as session:
        artifact = session.scalar(select(ReleaseArtifact))

    assert AgentSpec.model_validate(artifact.snapshot) == SEED_SPEC


def test_key_hash_is_sha256():
    seed(db.engine, KEY)

    with Session(db.engine) as session:
        agent = session.scalar(select(AgentDefinition))

    assert agent.key_hash == hashlib.sha256(KEY.encode()).hexdigest()
    assert agent.key_version == 1
