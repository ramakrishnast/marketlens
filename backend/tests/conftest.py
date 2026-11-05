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
    yield tmp_path
    db.reset_connection()
    reset_settings()


@pytest.fixture()
def client(fresh_db):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
