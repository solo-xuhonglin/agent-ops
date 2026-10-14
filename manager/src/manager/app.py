import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from shared.models import Base, ExecutionRun, ExecutionSession
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from manager import db
from manager.api import router
from manager.auth import ApiError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# The tables manager is the sole writer of (docs/03 §4.7).
MANAGER_TABLES = [ExecutionSession.__table__, ExecutionRun.__table__]


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(db.engine, tables=MANAGER_TABLES)
    log.info("manager started")
    yield


app = FastAPI(lifespan=lifespan)
app.include_router(router)


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
