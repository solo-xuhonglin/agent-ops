import hashlib
import uuid

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel
from shared.models import Account, AuthSession, Membership, Tenant
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from config import db
from config.auth import SESSION_COOKIE, SESSION_TTL, create_auth_session, resolve_auth
from config.errors import ApiError
from config.security import verify_password

router = APIRouter(prefix="/api/auth")


class LoginBody(BaseModel):
    email: str
    password: str


class SwitchTeamBody(BaseModel):
    tenant_id: uuid.UUID


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        path="/",
    )


def _session_view(session: Session, account_id: uuid.UUID, email: str, current_tenant_id: uuid.UUID | None) -> dict:
    memberships = session.scalars(
        select(Membership).where(Membership.account_id == account_id).order_by(Membership.created_at)
    ).all()
    teams = [
        {
            "tenant_id": str(membership.tenant_id),
            "tenant_name": session.scalar(select(Tenant.name).where(Tenant.id == membership.tenant_id)),
            "role": membership.role,
        }
        for membership in memberships
    ]
    return {
        "account": {"id": str(account_id), "email": email},
        "teams": teams,
        "current_tenant_id": str(current_tenant_id) if current_tenant_id is not None else None,
    }


def _first_membership_tenant(session: Session, account_id: uuid.UUID) -> uuid.UUID | None:
    membership = session.scalar(
        select(Membership).where(Membership.account_id == account_id).order_by(Membership.created_at)
    )
    return membership.tenant_id if membership else None


@router.post("/login")
def login(body: LoginBody, response: Response) -> dict:
    with Session(db.engine, expire_on_commit=False) as session, session.begin():
        account = session.scalar(select(Account).where(Account.email == body.email))
        if account is None or not verify_password(body.password, account.password_hash):
            raise ApiError(401, "invalid_credentials", "invalid email or password")
        current_tenant_id = _first_membership_tenant(session, account.id)
        token = create_auth_session(session, account.id, current_tenant_id)
        view = _session_view(session, account.id, account.email, current_tenant_id)

    _set_session_cookie(response, token)
    return view


@router.get("/session")
def current_session(request: Request, response: Response) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    with Session(db.engine, expire_on_commit=False) as session, session.begin():
        context = resolve_auth(session, token)
        view = _session_view(session, context.account.id, context.account.email, context.tenant_id)

    _set_session_cookie(response, token)
    return view


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        digest = hashlib.sha256(token.encode()).hexdigest()
        with Session(db.engine) as session, session.begin():
            session.execute(delete(AuthSession).where(AuthSession.token_hash == digest))
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.post("/switch-team")
def switch_team(body: SwitchTeamBody, request: Request, response: Response) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    with Session(db.engine, expire_on_commit=False) as session, session.begin():
        context = resolve_auth(session, token)
        digest = hashlib.sha256(token.encode()).hexdigest()
        member = session.scalar(
            select(Membership).where(
                Membership.account_id == context.account.id, Membership.tenant_id == body.tenant_id
            )
        )
        if member is None:
            raise ApiError(403, "forbidden", "not a member of this team")
        session.execute(update(AuthSession).where(AuthSession.token_hash == digest).values(current_tenant_id=body.tenant_id))
        view = _session_view(session, context.account.id, context.account.email, body.tenant_id)

    _set_session_cookie(response, token)
    return view
