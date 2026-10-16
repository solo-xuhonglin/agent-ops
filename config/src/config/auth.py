import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Request
from shared.models import Account, AuthSession, Membership
from sqlalchemy import select
from sqlalchemy.orm import Session

from config import db
from config.errors import ApiError

SESSION_COOKIE = "agentops_session"
SESSION_TTL = timedelta(days=7)

_RANKS = {"viewer": 0, "editor": 1, "admin": 2}


def role_rank(role: str) -> int:
    try:
        return _RANKS[role]
    except KeyError:
        raise ValueError(f"unknown role: {role}") from None


def create_auth_session(session: Session, account_id: uuid.UUID, current_tenant_id: uuid.UUID | None) -> str:
    token = secrets.token_urlsafe(32)
    session.add(
        AuthSession(
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            account_id=account_id,
            current_tenant_id=current_tenant_id,
            expires_at=datetime.now(UTC) + SESSION_TTL,
        )
    )
    return token


@dataclass
class AuthContext:
    account: Account
    tenant_id: uuid.UUID | None
    role: str | None


def resolve_auth(session: Session, token: str | None) -> AuthContext:
    if not token:
        raise ApiError(401, "auth", "missing session")
    digest = hashlib.sha256(token.encode()).hexdigest()
    auth_session = session.scalar(select(AuthSession).where(AuthSession.token_hash == digest))
    if auth_session is None or auth_session.expires_at <= datetime.now(UTC):
        raise ApiError(401, "auth", "invalid session")

    # Sliding expiry; committed with the caller's transaction.
    auth_session.expires_at = datetime.now(UTC) + SESSION_TTL

    account = session.get(Account, auth_session.account_id)
    tenant_id = None
    role = None
    if auth_session.current_tenant_id is not None:
        membership = session.scalar(
            select(Membership).where(
                Membership.account_id == auth_session.account_id,
                Membership.tenant_id == auth_session.current_tenant_id,
            )
        )
        if membership is not None:
            tenant_id = membership.tenant_id
            role = membership.role
    return AuthContext(account=account, tenant_id=tenant_id, role=role)


def require_account(request: Request) -> AuthContext:
    token = request.cookies.get(SESSION_COOKIE)
    # expire_on_commit keeps the returned ORM objects readable after the session closes.
    with Session(db.engine, expire_on_commit=False) as session, session.begin():
        return resolve_auth(session, token)


def require_role(minimum: str):
    minimum_rank = role_rank(minimum)

    def dependency(request: Request) -> AuthContext:
        context = require_account(request)
        if context.tenant_id is None:
            raise ApiError(403, "no_team", "no current team")
        if role_rank(context.role) < minimum_rank:
            raise ApiError(403, "forbidden", "insufficient role")
        return context

    return dependency
