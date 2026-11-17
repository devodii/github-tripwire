"""Database tooling: start Postgres, create the role and database, apply the declarative schema.

Same approach as the home-database repo: the desired state is plain SQL under db/schema/
(tables/, indexes/, ...), pgschema diffs it against the live database, and
`plan` records the DDL it would run as a numbered file under db/migrations/.

    python -m tripwire.db up|down|plan|preview|apply [--url-var NAME]

`--url-var` picks which environment variable (or .env entry) holds the target database URL,
e.g. `apply --url-var PROD_DATABASE_URL`. The default is DATABASE_URL.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from dotenv import dotenv_values
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
DB_DIR = ROOT / "db"
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
LAYERS = ["extensions", "types", "functions", "tables", "constraints", "indexes", "triggers"]


class DbSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    postgres_superuser: str = "postgres"
    postgres_superuser_password: SecretStr | None = None  # only the local compose server needs it
    pgschema_image: str = "pgplex/pgschema:latest"


@dataclass(frozen=True)
class Target:
    """One database, parsed from a postgresql:// URL."""

    user: str
    password: str
    host: str
    port: int
    name: str
    sslmode: str = "disable"

    @property
    def is_local(self) -> bool:
        return self.host in LOCAL_HOSTS

    @classmethod
    def parse(cls, url: str) -> Target:
        u = urlparse(url)
        if u.scheme not in ("postgresql", "postgres") or not (u.username and u.path[1:]):
            raise ValueError("DATABASE_URL must look like postgresql://user:password@host:port/db")
        host = u.hostname or "localhost"
        default_ssl = "disable" if host in LOCAL_HOSTS else "require"
        sslmode = (parse_qs(u.query).get("sslmode") or [default_ssl])[0]
        return cls(
            unquote(u.username), unquote(u.password or ""), host,
            u.port or 5432, u.path[1:], sslmode,
        )  # fmt: skip

    def with_name(self, name: str) -> Target:
        return Target(self.user, self.password, self.host, self.port, name, self.sslmode)

    @property
    def url(self) -> str:
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"


def database_url(var: str = "DATABASE_URL") -> str:
    """The URL in environment variable `var`, falling back to .env. Real env vars win."""
    value = os.environ.get(var) or dotenv_values(ROOT / ".env").get(var)
    if not value:
        raise SystemExit(f"{var} is not set in the environment or .env")
    return value


def settings() -> DbSettings:
    return DbSettings()


def compose(*args: str, input: str | None = None, check: bool = True) -> int:
    cmd = ["docker", "compose", "-f", str(ROOT / "docker-compose.yml"), *args]
    return subprocess.run(cmd, cwd=ROOT, input=input, text=True, check=check).returncode


def ensure_database(target: Target) -> None:
    """Create (or refresh the password of) the role and the database it owns. Safe to rerun."""
    s = settings()
    if s.postgres_superuser_password is None:
        raise SystemExit("POSTGRES_SUPERUSER_PASSWORD is not set in the environment or .env")
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


def pgschema_command(command: str, target: Target, *extra: str) -> list[str]:
    """The `docker run` line for pgschema.

    A local target is reached through the host-published port (and needs the host-gateway
    alias). A remote one is reached directly, and must not get that alias.
    """
    host = "host.docker.internal" if target.is_local else target.host
    ssl = target.sslmode
    docker = ["docker", "run", "--rm", "-i"]
    if target.is_local:
        docker += ["--add-host", "host.docker.internal:host-gateway"]
    return [
        *docker, "-e", f"PGPASSWORD={target.password}", "-v", f"{DB_DIR}:/db", "-w", "/db",
        settings().pgschema_image, command,
        "--host", host, "--port", str(target.port), "--db", target.name, "--user", target.user,
        "--schema", "public", "--sslmode", ssl,
        # diff against this database itself instead of pgschema's embedded Postgres
        "--plan-host", host, "--plan-port", str(target.port), "--plan-db", target.name,
        "--plan-user", target.user, "--plan-password", target.password, "--plan-sslmode", ssl,
        *extra,
    ]  # fmt: skip


def run_pgschema(command: str, target: Target, *extra: str) -> int:
    return subprocess.run(pgschema_command(command, target, *extra), check=False).returncode


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


def preview(target: Target) -> int:
    """Print the DDL that `apply` would run, without writing a migration file or changing anything."""
    generate_manifest()
    return run_pgschema("plan", target, "--file", "/db/schema/manifest.sql")


def apply(target: Target) -> int:
    generate_manifest()
    return run_pgschema("apply", target, "--file", "/db/schema/manifest.sql", "--auto-approve")


def up() -> int:
    compose("up", "-d", "--wait")
    ensure_database(Target.parse(database_url()))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tripwire.db", description=__doc__)
    parser.add_argument("command", choices=["up", "down", "plan", "preview", "apply"])
    parser.add_argument("--url-var", default="DATABASE_URL", help="env var holding the target URL")
    args = parser.parse_args(argv)
    command = args.command
    if command == "up":
        return up()
    if command == "down":
        return compose("down")
    target = Target.parse(database_url(args.url_var))
    return {"plan": plan, "preview": preview, "apply": apply}[command](target)


if __name__ == "__main__":
    sys.exit(main())
