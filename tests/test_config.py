import yaml
from conftest import ROOT

from tripwire.cli import render_config


def render(tmp_path, monkeypatch, **env):
    for key in ("TG_TOKEN", "TG_CHAT"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    out = tmp_path / "out.yaml"
    render_config(ROOT / "urlwatch.yaml", out)
    return yaml.safe_load(out.read_text())["report"]["telegram"]


def test_telegram_stays_off_without_secrets(tmp_path, monkeypatch):
    assert render(tmp_path, monkeypatch)["enabled"] is False


def test_telegram_stays_off_with_only_one_secret(tmp_path, monkeypatch):
    assert render(tmp_path, monkeypatch, TG_TOKEN="t")["enabled"] is False


def test_telegram_turns_on_and_expands_when_both_secrets_exist(tmp_path, monkeypatch):
    cfg = render(tmp_path, monkeypatch, TG_TOKEN="123:abc", TG_CHAT="-42")
    assert cfg == {"enabled": True, "bot_token": "123:abc", "chat_id": "-42"}
