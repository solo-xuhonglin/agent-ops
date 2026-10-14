import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.engine import make_url

load_dotenv(Path(__file__).resolve().parent / "deploy" / ".env")

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if TEST_DATABASE_URL is None:
    raise RuntimeError("TEST_DATABASE_URL is not set; copy deploy/.env.example to deploy/.env")

database = make_url(TEST_DATABASE_URL).database
if database is None or not database.endswith("_test"):
    raise RuntimeError(f"TEST_DATABASE_URL must point at a *_test database, got {database!r}")

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
