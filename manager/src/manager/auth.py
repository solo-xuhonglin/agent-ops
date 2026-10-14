import hashlib

from fastapi import Request
from shared.models import AgentDefinition
from sqlalchemy import select
from sqlalchemy.orm import Session

from manager.db import engine


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message


def require_agent(request: Request) -> AgentDefinition:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise ApiError(401, "auth", "missing bearer key")
    digest = hashlib.sha256(header.removeprefix("Bearer ").encode()).hexdigest()

    with Session(engine) as session:
        agent = session.scalar(select(AgentDefinition).where(AgentDefinition.key_hash == digest))
    if agent is None:
        raise ApiError(401, "auth", "unknown key")
    return agent
