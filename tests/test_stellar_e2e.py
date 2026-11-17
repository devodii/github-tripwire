"""Run the real watchers/stellar-protocol.yaml through urlwatch against replayed commits.

A local server stands in for raw.githubusercontent.com and serves the stellar-protocol
files from commit 6c3fb12 (before) and b193384 "SEP-53: Change Status to Final" (after).
"""

import http.server
import threading
import time

import pytest
from conftest import ROOT, fixture

from tripwire.cli import main

RAW = "https://raw.githubusercontent.com/stellar/stellar-protocol/"
WATCHER = ROOT / "watchers" / "stellar-protocol.yaml"
README, SEP53 = "/master/ecosystem/README.md", "/master/ecosystem/sep-0053.md"


@pytest.fixture
def upstream():
    """A mutable fake GitHub: tests assign `upstream.files[path] = text`."""
    files: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = files.get(self.path)
            self.send_response(200 if body is not None else 404)
            self.end_headers()
            self.wfile.write((body or "Not Found").encode())

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    files.update(
        {README: fixture("ecosystem-readme.before.md"), SEP53: fixture("sep-0053.before.md")}
    )
    files["base"] = f"http://127.0.0.1:{server.server_port}/"
    yield files
    server.shutdown()


@pytest.fixture
def watch(upstream, tmp_path, capfd):
    """Returns run() -> (exit code, stdout) for one urlwatch run of the real watcher file."""
    local = tmp_path / "watchers" / WATCHER.name
    local.parent.mkdir()
    local.write_text(WATCHER.read_text().replace(RAW, upstream["base"]))
    cache = tmp_path / "cache.db"

    last_run = [0.0]

    def run():
        # urlwatch picks the latest snapshot by whole-second timestamp, so space runs apart
        time.sleep(max(0.0, last_run[0] + 1.1 - time.monotonic()))
        last_run[0] = time.monotonic()
        capfd.readouterr()
        code = main(["run", str(local.parent), "--cache", str(cache)])
        return code, capfd.readouterr().out

    return run


def test_first_run_records_a_baseline_without_reporting(watch):
    code, out = watch()
    assert (code, out.strip()) == (0, "")


def test_unchanged_upstream_reports_nothing(watch):
    watch()
    assert watch() == (0, "")


def test_real_status_change_is_reported_as_exactly_that_change(watch, upstream):
    watch()
    upstream[README] = fixture("ecosystem-readme.after.md")
    upstream[SEP53] = fixture("sep-0053.after.md")
    code, out = watch()
    assert code == 0
    assert "-SEP-0053 | Final Comment Period (Final)" in out
    assert "+SEP-0053 | Final" in out
    assert "-Status: Final Comment Period (Final)" in out
    assert "+Version: 1.0.0" in out
    changed = [
        line
        for line in out.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---", "-- "))
    ]
    # index: one row out and one in. preamble: status out and in, version out and in.
    assert len(changed) == 2 + 4
    assert watch() == (0, "")  # the new state is now the baseline


def test_lines_shifting_upstream_does_not_report(watch, upstream):
    watch()
    after = fixture("ecosystem-readme.before.md")
    upstream[README] = "# Banner\n\nAnnouncement\nspanning\nlines\n\n" + after
    lines = fixture("sep-0053.before.md").splitlines()
    status = next(i for i, line in enumerate(lines) if line.startswith("Status:"))
    lines.insert(status + 2, lines.pop(status))  # status line moves down
    upstream[SEP53] = "Extra heading\n\n" + "\n".join(lines)
    assert watch() == (0, "")


def test_a_broken_upstream_is_reported_not_silently_ignored(watch, upstream):
    watch()
    upstream[README] = "404: Not Found"
    _, out = watch()
    assert "ERROR" in out
    assert "no table with columns" in out


def test_check_accepts_the_real_watchers():
    assert main(["check", str(ROOT / "watchers")]) == 0


def test_check_rejects_unknown_filter_options(tmp_path, capfd):
    bad = tmp_path / "bad.yaml"
    bad.write_text("name: x\nurl: http://x\nfilter:\n  - md_table:\n      keyy: Number\n")
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
