"""Tests run fully offline on bundled sample data with no API keys."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ["DATA_MODE"] = "mock"
os.environ["OPENAI_API_KEY"] = ""
os.environ["MARKETLENS_TESTING"] = "1"
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="mltest-")
# Backend under test: SQLite by default; set TEST_DATABASE_URL=postgresql://... to run the same suite on Postgres.
TEST_PG = os.environ.get("TEST_DATABASE_URL", "").strip()
os.environ["DATABASE_URL"] = TEST_PG  # empty -> SQLite, and never inherit a developer's .env
os.environ["POSTGRES_HOST"] = ""


def _ensure_pg_database(url: str) -> None:
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    name = conninfo_to_dict(url)["dbname"]
    with psycopg.connect(make_conninfo(url, dbname="postgres"), autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_database WHERE datname=%s", (name,)).fetchone():
            c.execute(f'CREATE DATABASE "{name}"')


if TEST_PG:
    _ensure_pg_database(TEST_PG)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """Isolated database per test."""
    from app import db
    from app.config import reset_settings

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    reset_settings()
    db.reset_connection()
    if db.backend() == "postgres":
        db.wipe_postgres()  # every test starts from an empty database
    yield tmp_path
    db.reset_connection()
    reset_settings()


@pytest.fixture()
def client(fresh_db):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
