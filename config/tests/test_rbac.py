import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

import pytest
from config.auth import SESSION_COOKIE, AuthContext, create_auth_session, require_account, require_role
from config.errors import ApiError
from config.security import hash_password
from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from shared.models import Account, Membership, Tenant
from shared.models import AuthSession as AuthSessionModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from config import db


def make_member(role: str) -> str:
    """Create tenant + account + membership and return a live session token."""
    with Session(db.engine) as session, session.begin():
        tenant = Tenant(name=f"t-{uuid.uuid4().hex[:8]}")
        account = Account(email=f"u-{uuid.uuid4().hex[:8]}@example.com", password_hash=hash_password("pw"))
        session.add_all([tenant, account])
        session.flush()
        session.add(Membership(account_id=account.id, tenant_id=tenant.id, role=role))
        return create_auth_session(session, account.id, tenant.id)


def token_row(token: str) -> AuthSessionModel:
    digest = hashlib.sha256(token.encode()).hexdigest()
    with Session(db.engine) as session:
        return session.scalar(select(AuthSessionModel).where(AuthSessionModel.token_hash == digest))


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()

    @app.exception_handler(ApiError)
    async def api_error_handler(_: object, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message}})

    @app.get("/who")
    def who(context: Annotated[AuthContext, Depends(require_account)]):
        return {"email": context.account.email, "tenant_id": str(context.tenant_id), "role": context.role}

    @app.get("/edit")
    def edit(context: Annotated[AuthContext, Depends(require_role("editor"))]):
        return {"role": context.role}

    return TestClient(app)


def call(client: TestClient, path: str, token: str | None) -> object:
    if token is None:
        client.cookies.delete(SESSION_COOKIE)
    else:
        client.cookies.set(SESSION_COOKIE, token)
    response = client.get(path)
    if response.status_code < 300:
        return response.json()
    return response.json()["error"]["code"]


@pytest.mark.parametrize("token", [None, "garbage-token"])
def test_missing_or_unknown_token_rejected(client, token):
    assert call(client, "/who", token) == "auth"
    assert call(client, "/edit", token) == "auth"


def test_expired_session_rejected(client):
    token = make_member("admin")
    with Session(db.engine) as session, session.begin():
        row = session.scalar(
            select(AuthSessionModel).where(
                AuthSessionModel.token_hash == hashlib.sha256(token.encode()).hexdigest()
            )
        )
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    assert call(client, "/who", token) == "auth"


def test_account_route_reports_live_membership(client):
    token = make_member("viewer")

    body = call(client, "/who", token)
    assert body["role"] == "viewer"
    assert body["tenant_id"] != "None"


def test_editor_route_enforces_minimum_role(client):
    assert call(client, "/edit", make_member("viewer")) == "forbidden"
    assert call(client, "/edit", make_member("editor"))["role"] == "editor"
    assert call(client, "/edit", make_member("admin"))["role"] == "admin"


def test_membership_removed_after_login_downgrades_to_no_team(client):
    token = make_member("editor")
    with Session(db.engine) as session, session.begin():
        row = token_row(token)
        membership = session.scalar(select(Membership).where(Membership.account_id == row.account_id))
        session.delete(membership)

    assert call(client, "/who", token)["role"] is None
    assert call(client, "/edit", token) == "no_team"


def test_role_promotion_takes_effect_without_relogin(client):
    token = make_member("viewer")
    assert call(client, "/edit", token) == "forbidden"

    with Session(db.engine) as session, session.begin():
        row = token_row(token)
        membership = session.scalar(select(Membership).where(Membership.account_id == row.account_id))
        membership.role = "admin"

    assert call(client, "/edit", token)["role"] == "admin"
