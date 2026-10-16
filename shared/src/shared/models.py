import uuid
from datetime import datetime

from sqlalchemy import DateTime, PrimaryKeyConstraint, String, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "iam_tenants"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Account(Base):
    __tablename__ = "iam_accounts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Membership(Base):
    __tablename__ = "iam_memberships"
    __table_args__ = (UniqueConstraint("account_id", "tenant_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    role: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuthSession(Base):
    __tablename__ = "iam_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    current_tenant_id: Mapped[uuid.UUID | None]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentDefinition(Base):
    __tablename__ = "agent_definitions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    name: Mapped[str] = mapped_column(unique=True)
    key_hash: Mapped[str] = mapped_column(String(64))
    key_version: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReleaseArtifact(Base):
    __tablename__ = "release_artifacts"
    __table_args__ = (UniqueConstraint("target_kind", "target_id", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    target_kind: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    version: Mapped[int]
    # The published config as a structured dict; AgentSpec.model_validate reads it back.
    snapshot: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RunLog(Base):
    __tablename__ = "execution_run_log"
    __table_args__ = (PrimaryKeyConstraint("run_id", "seq"),)

    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    seq: Mapped[int] = mapped_column(primary_key=True)
    action_id: Mapped[str | None]
    type: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExecutionSession(Base):
    __tablename__ = "execution_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    caller: Mapped[str] = mapped_column(default="backend")
    active_run_id: Mapped[uuid.UUID | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExecutionRun(Base):
    __tablename__ = "execution_runs"
    __table_args__ = (UniqueConstraint("agent_id", "request_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    agent_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    artifact_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    request_id: Mapped[str | None]
    content: Mapped[str] = mapped_column(String)
    params: Mapped[dict] = mapped_column(JSONB)
    output_schema: Mapped[dict | None] = mapped_column(JSONB)
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    # Bumped by every dispatch; the fence against a stale worker writing the log.
    epoch: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
