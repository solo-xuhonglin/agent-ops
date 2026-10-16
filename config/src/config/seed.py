import hashlib
import logging
import os
from dataclasses import dataclass

from shared.models import Account, AgentDefinition, Base, Membership, ReleaseArtifact, Tenant
from shared.spec import AgentSpec, InputParam
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config.app import CONFIG_TABLES, db
from config.security import hash_password

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

SEED_SPEC = AgentSpec(
    mode="react",
    name="hello-agent",
    instructions="Greet the caller and echo the project id.",
    input=[InputParam(key="project_id", type="string", required=True, description="The project id, like PRJ-1042.")],
    model_id="stub",
    model_params={},
    max_iterations=3,
)

TENANT_NAME = "default"


@dataclass
class SeedResult:
    tenant_created: bool
    agent_created: bool
    artifact_created: bool
    admin_created: bool


def seed(engine, key: str, admin_email: str, admin_password: str) -> SeedResult:
    Base.metadata.create_all(engine, tables=CONFIG_TABLES)
    key_hash = hashlib.sha256(key.encode()).hexdigest()

    with Session(engine) as session, session.begin():
        tenant = session.scalar(select(Tenant).where(Tenant.name == TENANT_NAME))
        tenant_created = tenant is None
        if tenant_created:
            tenant = Tenant(name=TENANT_NAME)
            session.add(tenant)
            session.flush()

        agent = session.scalar(select(AgentDefinition).where(AgentDefinition.name == SEED_SPEC.name))
        agent_created = agent is None
        if agent_created:
            agent = AgentDefinition(tenant_id=tenant.id, name=SEED_SPEC.name, key_hash=key_hash, key_version=1)
            session.add(agent)
            session.flush()

        artifact = session.scalar(
            select(ReleaseArtifact).where(
                ReleaseArtifact.target_kind == "agent",
                ReleaseArtifact.target_id == agent.id,
                ReleaseArtifact.version == 1,
            )
        )
        artifact_created = artifact is None
        if artifact_created:
            session.add(
                ReleaseArtifact(
                    target_kind="agent",
                    target_id=agent.id,
                    version=1,
                    snapshot=SEED_SPEC.model_dump(),
                )
            )

        # The bootstrap admin only fires on an empty account table, so rotating
        # SEED_ADMIN_PASSWORD can never take over an existing installation.
        admin_created = session.scalar(select(func.count()).select_from(Account)) == 0
        if admin_created:
            account = Account(email=admin_email, password_hash=hash_password(admin_password))
            session.add(account)
            session.flush()
            session.add(Membership(account_id=account.id, tenant_id=tenant.id, role="admin"))

    result = SeedResult(tenant_created, agent_created, artifact_created, admin_created)
    log.info(
        "seed: tenant %s, agent %s, artifact %s, admin %s",
        "created" if result.tenant_created else "exists",
        "created" if result.agent_created else "exists",
        "created" if result.artifact_created else "exists",
        "created" if result.admin_created else "exists",
    )
    return result


if __name__ == "__main__":
    seed(
        db.engine,
        os.environ["SEED_AGENT_KEY"],
        os.environ["SEED_ADMIN_EMAIL"],
        os.environ["SEED_ADMIN_PASSWORD"],
    )
