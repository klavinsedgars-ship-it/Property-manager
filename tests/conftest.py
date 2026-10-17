"""Tests run on SQLite by default. Set PM_TEST_PG=postgresql://user@host:port/postgres
to run the same suite against PostgreSQL (a throwaway database per test)."""
import os
import uuid

import pytest

from app import create_app
from app.db import get_db

PG = os.environ.get("PM_TEST_PG")


@pytest.fixture
def database(tmp_path):
    if not PG:
        yield str(tmp_path / "t.db")
        return
    import psycopg
    name = f"pm_test_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(PG, autocommit=True) as c:
        c.execute(f"CREATE DATABASE {name}")
    yield PG.rsplit("/", 1)[0] + "/" + name
    with psycopg.connect(PG, autocommit=True) as c:
        c.execute(f"DROP DATABASE {name} WITH (FORCE)")


@pytest.fixture
def app(tmp_path, database):
    return create_app({"DATABASE": database, "UPLOAD_DIR": str(tmp_path / "up"), "TESTING": True})


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def db(app):
    with app.app_context():
        yield get_db()
