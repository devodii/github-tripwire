import pytest

from tripwire.db import Target, database_url, next_migration, pgschema_command


def test_local_urls_default_to_no_ssl_and_remote_urls_to_require():
    local = Target.parse("postgresql://u:p@localhost:5441/db")
    remote = Target.parse("postgresql://u:p@db.example.com/db")
    assert (local.is_local, local.sslmode, local.port) == (True, "disable", 5441)
    assert (remote.is_local, remote.sslmode, remote.port) == (False, "require", 5432)


def test_sslmode_in_the_url_wins_and_other_parameters_are_ignored():
    url = "postgresql://u:p@db.example.com/db?sslmode=verify-full&channel_binding=require"
    assert Target.parse(url).sslmode == "verify-full"


def test_credentials_are_url_decoded():
    t = Target.parse("postgresql://we%40ird:p%2Fw%3A@h:1/n")
    assert (t.user, t.password) == ("we@ird", "p/w:")


def test_with_name_keeps_everything_else():
    t = Target.parse("postgresql://u:p@db.example.com:6543/a?sslmode=require").with_name("b")
    assert (t.name, t.host, t.port, t.sslmode) == ("b", "db.example.com", 6543, "require")


@pytest.mark.parametrize("url", ["mysql://u:p@h/d", "postgresql://h/d", "postgresql://u:p@h"])
def test_unusable_urls_are_rejected(url):
    with pytest.raises(ValueError, match="DATABASE_URL must look like"):
        Target.parse(url)


def test_database_url_prefers_the_environment_and_says_what_is_missing(monkeypatch):
    monkeypatch.setenv("SOME_URL", "postgresql://u:p@h/d")
    assert database_url("SOME_URL") == "postgresql://u:p@h/d"
    monkeypatch.delenv("SOME_URL")
    with pytest.raises(SystemExit, match="SOME_URL is not set"):
        database_url("SOME_URL")


def test_next_migration_numbers_continue_from_the_highest(tmp_path):
    assert next_migration(tmp_path).name == "000001.sql"
    (tmp_path / "000001.sql").write_text("x")
    (tmp_path / "000007.sql").write_text("x")
    assert next_migration(tmp_path).name == "000008.sql"


def test_local_target_uses_the_host_gateway_alias():
    cmd = pgschema_command("apply", Target.parse("postgresql://u:p@localhost:5441/db"))
    assert ["--add-host", "host.docker.internal:host-gateway"] == cmd[cmd.index("--add-host") :][:2]
    assert cmd[cmd.index("--host") + 1] == "host.docker.internal"
    assert cmd[cmd.index("--sslmode") + 1] == "disable"


def test_remote_target_is_reached_directly_over_ssl_without_the_alias():
    cmd = pgschema_command("apply", Target.parse("postgresql://u:p@db.example.com/db"), "--x")
    assert "--add-host" not in cmd
    assert cmd[cmd.index("--host") + 1] == cmd[cmd.index("--plan-host") + 1] == "db.example.com"
    assert cmd[cmd.index("--sslmode") + 1] == cmd[cmd.index("--plan-sslmode") + 1] == "require"
    assert cmd[-1] == "--x"
