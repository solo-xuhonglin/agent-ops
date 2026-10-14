import config.app
from fastapi.testclient import TestClient
from sqlalchemy import create_engine


def test_health_reports_db():
    with TestClient(config.app.app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok"}


def test_health_503_when_db_down(monkeypatch):
    dead = create_engine(
        "postgresql+psycopg://agentops:x@127.0.0.1:1/agentops_test", connect_args={"connect_timeout": 1}
    )

    with TestClient(config.app.app) as client:
        monkeypatch.setattr(config.app.db, "engine", dead)
        response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["db"] == "unreachable"
