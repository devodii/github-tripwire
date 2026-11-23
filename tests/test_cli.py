import pytest
from conftest import ROOT

from tripwire.cli import main
from tripwire.spec import SpecError, load_watchers, select


def test_check_accepts_every_committed_watcher():
    assert main(["check", str(ROOT / "watchers")]) == 0


def test_check_rejects_unknown_hook_options(tmp_path, capfd):
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\nevery: t\nurl: http://x\nfilter:\n  - md_table:\n      keyy: Id\n")
    assert main(["check", str(bad)]) == 1
    assert "keyy: Extra inputs are not permitted" in capfd.readouterr().err


def test_check_rejects_unknown_filters_and_unknown_job_keys(tmp_path, capfd):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "name: a\nevery: t\nurl: http://a\nfilter: [nope]\n---\nname: b\nevery: t\nurl: http://b\nextra: 1\n"
    )
    assert main(["check", str(bad)]) == 1
    err = capfd.readouterr().err
    assert "Unknown filter kind: nope" in err and "extra: Extra inputs" in err


def test_check_rejects_duplicate_and_unnamed_jobs(tmp_path, capfd):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "name: x\nevery: t\nurl: http://x\n---\nname: x\nevery: t\nurl: http://y\n---\nevery: t\nurl: http://z\n"
    )
    assert main(["check", str(bad)]) == 1
    err = capfd.readouterr().err
    assert "duplicate name 'x'" in err and "job #3: name: Field required" in err


def test_check_rejects_an_empty_folder(tmp_path, capfd):
    assert main(["check", str(tmp_path)]) == 1
    assert "no watchers found" in capfd.readouterr().err


def test_committed_json_schema_matches_the_spec(capfd):
    assert main(["schema"]) == 0
    assert capfd.readouterr().out == (ROOT / "schema" / "watcher.schema.json").read_text()


def test_every_selects_one_cadence_and_unknown_cadence_fails(tmp_path, capfd):
    two = tmp_path / "watcher.yaml"
    two.write_text(
        "name: a\nevery: fast\nurl: http://a\n---\nname: b\nevery: slow\nurl: http://b\n"
    )
    assert [w.name for w in select(load_watchers(two), "slow")] == ["b"]
    assert len(select(load_watchers(two), None)) == 2
    assert main(["check", str(two), "--every", "nope"]) == 1
    assert "no watcher has every: nope" in capfd.readouterr().err


def test_every_is_required(tmp_path):
    bad = tmp_path / "watcher.yaml"
    bad.write_text("name: a\nurl: http://a\n")
    with pytest.raises(SpecError, match="every: Field required"):
        load_watchers(bad)


def test_the_engine_does_not_need_the_venv_on_path(monkeypatch):
    # a service, a Docker CMD or `.venv/bin/python -m tripwire` has no `urlwatch` command on PATH
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    assert main(["check", str(ROOT / "watchers")]) == 0
