import logging
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from shared.models import Account, AgentDefinition, AuthSession, Base, Membership, ReleaseArtifact, Tenant
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from config import db
from config.errors import ApiError
from config.routers.auth import router as auth_router

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

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(db.engine, tables=CONFIG_TABLES)
    log.info("config started")
    yield


app = FastAPI(lifespan=lifespan)
app.include_router(auth_router)


@app.middleware("http")
async def origin_guard(request: Request, call_next):
    # Browsers always send Origin on cross-site unsafe requests; other clients omit it.
    origin = request.headers.get("Origin")
    if request.method in UNSAFE_METHODS and origin is not None and urlparse(origin).hostname != request.url.hostname:
        return JSONResponse(status_code=403, content={"error": {"code": "origin", "message": "cross-origin request rejected"}})
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    # Framework-level rejects (bad JSON, missing content, bad path types) speak the contract envelope too.
    message = "; ".join(f"{error['loc'][-1]}: {error['msg']}" for error in exc.errors())
    return JSONResponse(status_code=422, content={"error": {"code": "contract", "message": message}})


@app.exception_handler(ApiError)
async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message}})


@app.get("/health")
def health():
    try:
        with db.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError:
        return JSONResponse(status_code=503, content={"status": "degraded", "db": "unreachable"})
    return {"status": "ok", "db": "ok"}
