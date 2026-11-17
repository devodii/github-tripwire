from conftest import ROOT

from tripwire.cli import main


def test_check_accepts_every_committed_watcher():
    assert main(["check", str(ROOT / "watchers")]) == 0


def test_check_rejects_unknown_hook_options(tmp_path, capfd):
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\nurl: http://x\nfilter:\n  - md_table:\n      keyy: Id\n")
    assert main(["check", str(bad)]) == 1
    assert "keyy: Extra inputs are not permitted" in capfd.readouterr().err


def test_check_rejects_unknown_filters_and_unknown_job_keys(tmp_path, capfd):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "name: a\nurl: http://a\nfilter: [nope]\n---\nname: b\nurl: http://b\nextra: 1\n"
    )
    assert main(["check", str(bad)]) == 1
    err = capfd.readouterr().err
    assert "Unknown filter kind: nope" in err and "extra: Extra inputs" in err


def test_check_rejects_duplicate_and_unnamed_jobs(tmp_path, capfd):
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\nurl: http://x\n---\nname: x\nurl: http://y\n---\nurl: http://z\n")
    assert main(["check", str(bad)]) == 1
    err = capfd.readouterr().err
    assert "duplicate name 'x'" in err and "job #3: name: Field required" in err


def test_check_rejects_an_empty_folder(tmp_path, capfd):
    assert main(["check", str(tmp_path)]) == 1
    assert "no watchers found" in capfd.readouterr().err


def test_committed_json_schema_matches_the_spec(capfd):
    assert main(["schema"]) == 0
    assert capfd.readouterr().out == (ROOT / "schema" / "watcher.schema.json").read_text()
