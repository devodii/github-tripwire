import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from tripwire import db


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """No test sees real secrets or the developer's .env."""
    for key in ("TG_TOKEN", "TG_CHAT", "DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("tripwire.cli.load_dotenv", lambda *a, **k: False)


@pytest.fixture(scope="session")
def test_db_url() -> str:
    """A separate `tripwire_test` database with the real schema applied through pgschema."""
    target = db.Target.parse(db.settings().database_url).with_name("tripwire_test")
    try:
        db.ensure_database(target)
    except (subprocess.CalledProcessError, OSError) as e:
        pytest.fail(f"Postgres is not reachable ({e}). Start it with `task db:up`.")
    assert db.apply(target) == 0, "applying db/schema to the test database failed"
    return target.url


@pytest.fixture
def database(test_db_url, monkeypatch):
    """Point tripwire at the test database (switching the postgres reporter on)."""
    monkeypatch.setenv("DATABASE_URL", test_db_url)
    with psycopg.connect(test_db_url, autocommit=True) as conn:
        conn.execute("TRUNCATE changes")  # snapshots stay: they are the cache
        yield conn
