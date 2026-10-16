import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from shared.models import Account, AgentDefinition, AuthSession, Base, Membership, ReleaseArtifact, Tenant
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from config import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# The tables config is the sole writer of (docs/03 §4.7).
CONFIG_TABLES = [
    Tenant.__table__,
    Account.__table__,
    Membership.__table__,
    AuthSession.__table__,
    AgentDefinition.__table__,
    ReleaseArtifact.__table__,
]


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(db.engine, tables=CONFIG_TABLES)
    log.info("config started")
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    try:
        with db.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError:
        return JSONResponse(status_code=503, content={"status": "degraded", "db": "unreachable"})
    return {"status": "ok", "db": "ok"}
