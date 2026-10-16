import uuid

import config.app
from config.security import hash_password
from fastapi.testclient import TestClient
from shared.models import Account, AuthSession, Membership, Tenant
from sqlalchemy import select
from sqlalchemy.orm import Session

from config import db

PASSWORD = "pw-123456"


def make_teams(count: int) -> list[uuid.UUID]:
    with Session(db.engine) as session, session.begin():
        ids = []
        for _ in range(count):
            tenant = Tenant(name=f"t-{uuid.uuid4().hex[:8]}")
            session.add(tenant)
            session.flush()
            ids.append(tenant.id)
    return ids


def make_account(teams: list[tuple[uuid.UUID, str]], password: str = PASSWORD) -> str:
    email = f"u-{uuid.uuid4().hex[:8]}@example.com"
    with Session(db.engine) as session, session.begin():
        account = Account(email=email, password_hash=hash_password(password))
        session.add(account)
        session.flush()
        for tenant_id, role in teams:
            session.add(Membership(account_id=account.id, tenant_id=tenant_id, role=role))
    return email


def client() -> TestClient:
    return TestClient(config.app.app)


def cookie_token(response) -> str:
    return response.headers["set-cookie"].split(";")[0].split("=", 1)[1]


def test_login_sets_cookie_and_returns_teams():
    tenant_ids = make_teams(1)
    email = make_account([(tenant_ids[0], "admin")])

    with client() as c:
        response = c.post("/api/auth/login", json={"email": email, "password": PASSWORD})

    assert response.status_code == 200
    body = response.json()
    assert body["account"]["email"] == email
    assert body["teams"][0]["tenant_id"] == str(tenant_ids[0])
    assert body["teams"][0]["role"] == "admin"
    assert body["current_tenant_id"] == str(tenant_ids[0])

    set_cookie = response.headers["set-cookie"]
    assert "agentops_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie or "samesite=lax" in set_cookie
    assert "Max-Age=604800" in set_cookie

    token = cookie_token(response)
    with Session(db.engine) as session:
        account = session.scalar(select(Account).where(Account.email == email))
        auth_session = session.scalar(select(AuthSession).where(AuthSession.account_id == account.id))

    assert account.password_hash.startswith("$argon2id$")
    assert len(auth_session.token_hash) == 64
    assert auth_session.token_hash != token


def test_login_rejects_wrong_password_and_unknown_email_identically():
    tenant_ids = make_teams(1)
    email = make_account([(tenant_ids[0], "viewer")])

    with client() as c:
        wrong = c.post("/api/auth/login", json={"email": email, "password": "nope"})
        unknown = c.post("/api/auth/login", json={"email": "ghost@example.com", "password": "nope"})

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"error": {"code": "invalid_credentials", "message": "invalid email or password"}}


def test_session_endpoint_returns_context_and_refreshes_cookie():
    tenant_ids = make_teams(1)
    email = make_account([(tenant_ids[0], "editor")])

    with client() as c:
        c.post("/api/auth/login", json={"email": email, "password": PASSWORD})
        response = c.get("/api/auth/session")

    assert response.status_code == 200
    assert response.json()["account"]["email"] == email
    assert "set-cookie" in response.headers

    with client() as c:
        assert c.get("/api/auth/session").status_code == 401


def test_logout_kills_the_session():
    tenant_ids = make_teams(1)
    email = make_account([(tenant_ids[0], "admin")])

    with client() as c:
        c.post("/api/auth/login", json={"email": email, "password": PASSWORD})
        with Session(db.engine) as session:
            account = session.scalar(select(Account).where(Account.email == email))
            assert session.scalar(select(AuthSession).where(AuthSession.account_id == account.id)) is not None

        assert c.post("/api/auth/logout").status_code == 204
        assert c.get("/api/auth/session").status_code == 401

    with Session(db.engine) as session:
        account = session.scalar(select(Account).where(Account.email == email))
        assert session.scalar(select(AuthSession).where(AuthSession.account_id == account.id)) is None


def test_switch_team_updates_current_membership():
    tenant_ids = make_teams(2)
    foreign = make_teams(1)[0]
    email = make_account([(tenant_ids[0], "viewer"), (tenant_ids[1], "editor")])

    with client() as c:
        c.post("/api/auth/login", json={"email": email, "password": PASSWORD})
        switched = c.post("/api/auth/switch-team", json={"tenant_id": str(tenant_ids[1])})
        assert switched.status_code == 200
        assert switched.json()["current_tenant_id"] == str(tenant_ids[1])
        assert len(switched.json()["teams"]) == 2

        assert c.get("/api/auth/session").json()["current_tenant_id"] == str(tenant_ids[1])

        rejected = c.post("/api/auth/switch-team", json={"tenant_id": str(foreign)})
    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "forbidden"


def test_switch_team_without_cookie_rejected():
    with client() as c:
        response = c.post("/api/auth/switch-team", json={"tenant_id": str(uuid.uuid4())})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "auth"


def test_second_login_keeps_a_concurrent_session():
    tenant_ids = make_teams(1)
    email = make_account([(tenant_ids[0], "admin")])

    with client() as first, client() as second:
        first.post("/api/auth/login", json={"email": email, "password": PASSWORD})
        second.post("/api/auth/login", json={"email": email, "password": PASSWORD})

        assert first.get("/api/auth/session").status_code == 200
        assert second.get("/api/auth/session").status_code == 200

        first.post("/api/auth/logout")
        assert first.get("/api/auth/session").status_code == 401
        assert second.get("/api/auth/session").status_code == 200


def test_cross_origin_unsafe_request_rejected():
    tenant_ids = make_teams(1)
    email = make_account([(tenant_ids[0], "admin")])

    with client() as c:
        rejected = c.post(
            "/api/auth/login",
            json={"email": email, "password": PASSWORD},
            headers={"Origin": "http://evil.example"},
        )
        assert rejected.status_code == 403
        assert rejected.json()["error"]["code"] == "origin"

        same_origin = c.post(
            "/api/auth/login",
            json={"email": email, "password": PASSWORD},
            headers={"Origin": "http://testserver"},
        )
    assert same_origin.status_code == 200
