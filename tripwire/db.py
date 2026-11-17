"""Database tooling: start Postgres, create the role and database, apply the declarative schema.

Same approach as the home-database repo: the desired state is plain SQL under db/schema/
(tables/, indexes/, ...), pgschema diffs it against the live database, and
`plan` records the DDL it would run as a numbered file under db/migrations/.

    python -m tripwire.db up|down|plan|apply
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
DB_DIR = ROOT / "db"
LAYERS = ["extensions", "types", "functions", "tables", "constraints", "indexes", "triggers"]


class DbSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    postgres_superuser: str = "postgres"
    postgres_superuser_password: SecretStr
    pgschema_image: str = "pgplex/pgschema:latest"
    database_url: str


@dataclass(frozen=True)
class Target:
    """One database, parsed from a postgresql:// URL."""

    user: str
    password: str
    host: str
    port: int
    name: str

    @classmethod
    def parse(cls, url: str) -> Target:
        u = urlparse(url)
        if u.scheme not in ("postgresql", "postgres") or not (u.username and u.path[1:]):
            raise ValueError("DATABASE_URL must look like postgresql://user:password@host:port/db")
        return cls(
            unquote(u.username), unquote(u.password or ""), u.hostname or "localhost",
            u.port or 5432, u.path[1:],
        )  # fmt: skip

    def with_name(self, name: str) -> Target:
        return Target(self.user, self.password, self.host, self.port, name)

    @property
    def url(self) -> str:
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"


def settings() -> DbSettings:
    return DbSettings()  # type: ignore[call-arg]  # values come from .env or the environment


def compose(*args: str, input: str | None = None, check: bool = True) -> int:
    cmd = ["docker", "compose", "-f", str(ROOT / "docker-compose.yml"), *args]
    return subprocess.run(cmd, cwd=ROOT, input=input, text=True, check=check).returncode


def ensure_database(target: Target) -> None:
    """Create (or refresh the password of) the role and the database it owns. Safe to rerun."""
    s = settings()
    sql = (
        "SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'role', :'pw')\n"
        "WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'role')\\gexec\n"
        "SELECT format('ALTER ROLE %I PASSWORD %L', :'role', :'pw')\\gexec\n"
        "SELECT format('CREATE DATABASE %I OWNER %I', :'db', :'role')\n"
        "WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'db')\\gexec\n"
    )
    compose(
        "exec", "-T", "-e", f"PGPASSWORD={s.postgres_superuser_password.get_secret_value()}",
        "postgres", "psql", "-U", s.postgres_superuser, "-d", "postgres", "-v", "ON_ERROR_STOP=1",
        "-v", f"role={target.user}", "-v", f"pw={target.password}", "-v", f"db={target.name}",
        input=sql,
    )  # fmt: skip


def generate_manifest(schema_dir: Path = DB_DIR / "schema") -> None:
    """Rebuild manifest.sql, the ordered list of schema files pgschema reads. Never edit by hand."""
    lines = []
    for layer in LAYERS:
        folder = schema_dir / layer
        if folder.is_dir():
            lines += [f"\\i {layer}/{f.name}" for f in sorted(folder.glob("*.sql"))]
    (schema_dir / "manifest.sql").write_text("\n".join(lines) + "\n" if lines else "")


def next_migration(migrations: Path = DB_DIR / "migrations") -> Path:
    migrations.mkdir(parents=True, exist_ok=True)
    numbers = [int(f.stem) for f in migrations.glob("[0-9][0-9][0-9][0-9][0-9][0-9].sql")]
    return migrations / f"{max(numbers, default=0) + 1:06d}.sql"


def run_pgschema(command: str, target: Target, *extra: str) -> int:
    """Run pgschema in Docker against `target`. The container reaches the host-published port."""
    host = "host.docker.internal"
    cmd = [
        "docker", "run", "--rm", "-i", "--add-host", f"{host}:host-gateway",
        "-e", f"PGPASSWORD={target.password}", "-v", f"{DB_DIR}:/db", "-w", "/db",
        settings().pgschema_image, command,
        "--host", host, "--port", str(target.port), "--db", target.name, "--user", target.user,
        "--schema", "public", "--sslmode", "disable",
        # diff against this database itself instead of pgschema's embedded Postgres
        "--plan-host", host, "--plan-port", str(target.port), "--plan-db", target.name,
        "--plan-user", target.user, "--plan-password", target.password, "--plan-sslmode", "disable",
        *extra,
    ]  # fmt: skip
    return subprocess.run(cmd, check=False).returncode


def plan(target: Target) -> int:
    generate_manifest()
    path = next_migration()
    rc = run_pgschema(
        "plan", target, "--file", "/db/schema/manifest.sql", "--output-sql",
        f"/db/migrations/{path.name}",
    )  # fmt: skip
    if rc != 0:
        return rc
    if not path.is_file() or path.stat().st_size == 0:
        path.unlink(missing_ok=True)
        print("No changes detected.")
    else:
        print(f"Wrote migration plan to db/migrations/{path.name}")
    return 0


def apply(target: Target) -> int:
    generate_manifest()
    return run_pgschema("apply", target, "--file", "/db/schema/manifest.sql", "--auto-approve")


def up() -> int:
    compose("up", "-d", "--wait")
    ensure_database(Target.parse(settings().database_url))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tripwire.db", description=__doc__)
    parser.add_argument("command", choices=["up", "down", "plan", "apply"])
    command = parser.parse_args(argv).command
    if command == "up":
        return up()
    if command == "down":
        return compose("down")
    target = Target.parse(settings().database_url)
    return {"plan": plan, "apply": apply}[command](target)


if __name__ == "__main__":
    sys.exit(main())
