from replay import Snapshots, run_case

WATCHER = """\
name: status page
every: test
url: http://status.invalid/page
filter:
  - kv_block:
      keys: [web]
"""

CASE = """\
stages:
  - name: baseline
    serve: {http://status.invalid/page: {text: "api: up\\nweb: up\\n"}}
    expect: {silent: true}
  - name: web goes down
    serve: {http://status.invalid/page: {text: "api: up\\nweb: down\\n"}}
    expect: {reports: ["-web: up", "+web: down"]}
  - name: page stops looking like the watcher expects
    serve: {http://status.invalid/page: {text: "maintenance"}}
    expect: {reports: ["ERROR"]}
"""


def make_case(tmp_path, case: str = CASE):
    folder = tmp_path / "case"
    folder.mkdir()
    (folder / "watcher.yaml").write_text(WATCHER)
    (folder / "case.yaml").write_text(case)
    work = tmp_path / "work"
    work.mkdir()
    return folder / "case.yaml", work


def test_every_new_changed_and_failed_result_is_recorded(tmp_path, capfd, database):
    case_file, work = make_case(tmp_path)
    run_case(case_file, work, capfd, Snapshots(database))

    rows = database.execute(
        "SELECT watcher, url, verb, old_content, new_content, diff FROM changes ORDER BY id"
    ).fetchall()
    assert [r[2] for r in rows] == ["new", "changed", "error"]
    assert {r[0] for r in rows} == {"status page"}
    new, changed, error = rows
    assert (new[3], new[4]) == (None, "web: up")
    assert (changed[3], changed[4]) == ("web: up", "web: down")
    assert "-web: up" in changed[5] and "+web: down" in changed[5]
    assert error[5].startswith("ValueError: kv_block:")


def test_unchanged_runs_record_nothing(tmp_path, capfd, database):
    baseline_only = CASE.split("  - name: web goes down")[0]
    case_file, work = make_case(
        tmp_path, baseline_only + "  - name: same again\n    expect: {silent: true}\n"
    )
    run_case(case_file, work, capfd, Snapshots(database))
    assert [r[0] for r in database.execute("SELECT verb FROM changes").fetchall()] == ["new"]


def test_a_database_outage_never_silences_the_alert(tmp_path, capfd, database, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody:x@127.0.0.1:1/none")  # nothing listens
    case_file, work = make_case(tmp_path)
    runs = run_case(case_file, work, capfd, Snapshots(database))  # asserts the alerts still print
    assert "postgres reporter: could not record" in runs[1].err
