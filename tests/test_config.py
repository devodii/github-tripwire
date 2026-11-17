import yaml
from conftest import ROOT

from tripwire.cli import render_config


def render_all(tmp_path, monkeypatch, **env):
    for key in ("TG_TOKEN", "TG_CHAT", "DATABASE_URL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    out = tmp_path / "out.yaml"
    render_config(ROOT / "urlwatch.yaml", out)
    return yaml.safe_load(out.read_text())["report"]


def render(tmp_path, monkeypatch, **env):
    return render_all(tmp_path, monkeypatch, **env)["telegram"]


def test_telegram_stays_off_without_secrets(tmp_path, monkeypatch):
    assert render(tmp_path, monkeypatch)["enabled"] is False


def test_telegram_stays_off_with_only_one_secret(tmp_path, monkeypatch):
    assert render(tmp_path, monkeypatch, TG_TOKEN="t")["enabled"] is False


def test_telegram_turns_on_and_expands_when_both_secrets_exist(tmp_path, monkeypatch):
    cfg = render(tmp_path, monkeypatch, TG_TOKEN="123:abc", TG_CHAT="-42")
    assert cfg == {"enabled": True, "bot_token": "123:abc", "chat_id": "-42"}


def test_postgres_reporter_is_off_without_a_database_url(tmp_path, monkeypatch):
    assert render_all(tmp_path, monkeypatch)["postgres"]["enabled"] is False


def test_postgres_reporter_turns_on_with_a_database_url(tmp_path, monkeypatch):
    url = "postgresql://u:p@localhost:5432/db"
    cfg = render_all(tmp_path, monkeypatch, DATABASE_URL=url)["postgres"]
    assert cfg == {"enabled": True, "dsn": url}
