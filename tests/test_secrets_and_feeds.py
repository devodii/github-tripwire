"""Secrets in watcher headers, windowed feeds (additions_only) and the serve loop."""

import threading
import time

import pytest
from replay import Snapshots, Upstream, run_case

from tripwire.cli import serve
from tripwire.spec import SpecError, Watcher, expand_secrets, load_watchers


def watcher(**fields) -> Watcher:
    return Watcher.model_validate({"name": "w", "every": "t", "url": "http://x.invalid/", **fields})


def test_secrets_are_filled_into_headers_and_body_without_touching_the_original():
    original = watcher(headers={"Authorization": "Bearer ${TOKEN}"}, data='{"t": "${TOKEN}"}')
    (filled,) = expand_secrets([original], {"TOKEN": "s3cret"})
    assert filled.headers == {"Authorization": "Bearer s3cret"}
    assert filled.data == '{"t": "s3cret"}'
    assert original.headers == {"Authorization": "Bearer ${TOKEN}"}


@pytest.mark.parametrize("env", [{}, {"TOKEN": ""}])
def test_a_missing_or_empty_secret_names_the_watcher_and_the_variable(env):
    with pytest.raises(SpecError, match="watcher 'w' needs TOKEN"):
        expand_secrets([watcher(headers={"Authorization": "Bearer ${TOKEN}"})], env)


def test_watchers_without_placeholders_need_no_environment():
    assert expand_secrets([watcher(headers={"Accept": "application/json"})], {})


def test_check_never_needs_the_secrets(tmp_path):
    file = tmp_path / "watcher.yaml"
    file.write_text(
        "name: a\nevery: t\nurl: http://a.invalid/\nheaders: {Authorization: 'Bearer ${T}'}\n"
    )
    assert load_watchers(file)  # validation alone does not expand


POST_WATCHER = """\
name: graphql feed
every: test
url: http://api.invalid/graphql
method: POST
headers:
  Authorization: Bearer ${FEED_TOKEN}
  Content-Type: application/json
data: '{"query": "{ viewer { login } }"}'
"""

POST_CASE = """\
stages:
  - name: baseline
    serve: {http://api.invalid/graphql: {text: "first"}}
    expect: {silent: true}
  - name: new content
    serve: {http://api.invalid/graphql: {text: "second"}}
    expect: {reports: ["+second"]}
"""


def test_the_token_reaches_the_server_and_a_post_body_is_sent(
    tmp_path, capfd, database, monkeypatch
):
    monkeypatch.setenv("FEED_TOKEN", "s3cret")
    folder = tmp_path / "case"
    folder.mkdir()
    (folder / "watcher.yaml").write_text(POST_WATCHER)
    (folder / "case.yaml").write_text(POST_CASE)
    work = tmp_path / "work"
    work.mkdir()
    seen: list = []
    run_case(folder / "case.yaml", work, capfd, Snapshots(database), seen)

    assert seen and all(r["method"] == "POST" for r in seen)
    assert all(r["headers"]["authorization"] == "Bearer s3cret" for r in seen)
    assert all(r["body"] == '{"query": "{ viewer { login } }"}' for r in seen)


WINDOW_WATCHER = """\
name: window feed
every: test
url: http://feed.invalid/items
additions_only: true
"""

WINDOW_CASE = """\
stages:
  - name: baseline holds the newest three items
    serve: {http://feed.invalid/items: {text: "c3\\nc2\\nc1\\n"}}
    expect: {silent: true}
  - name: a new item arrives and the oldest falls out of the window
    serve: {http://feed.invalid/items: {text: "c4\\nc3\\nc2\\n"}}
    expect: {reports: ["+c4"], not_reports: ["-c1"], changed_lines: 1}
  - name: only an item leaving the window is not news
    serve: {http://feed.invalid/items: {text: "c4\\nc3\\n"}}
    expect: {silent: true}
"""


def test_additions_only_reports_new_items_and_ignores_ones_aging_out(tmp_path, capfd, database):
    folder = tmp_path / "case"
    folder.mkdir()
    (folder / "watcher.yaml").write_text(WINDOW_WATCHER)
    (folder / "case.yaml").write_text(WINDOW_CASE)
    work = tmp_path / "work"
    work.mkdir()
    run_case(folder / "case.yaml", work, capfd, Snapshots(database))


def serve_in_background(tmp_path, upstream, watcher_yaml, interval=1.0):
    path = tmp_path / "watcher.yaml"
    path.write_text(watcher_yaml.replace("http://feed.invalid", upstream.base))
    stop = threading.Event()
    thread = threading.Thread(
        target=serve,
        args=(load_watchers(path), tmp_path / "urlwatch.yaml", tmp_path / "c.db", interval, stop),
        daemon=True,
    )
    return stop, thread


def wait_for(predicate, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return False


def test_serve_alerts_on_the_next_pass_and_stops_cleanly(tmp_path, capfd):
    (tmp_path / "urlwatch.yaml").write_text(
        "display: {new: false}\nreport: {stdout: {enabled: true}}\n"
    )
    upstream = Upstream()
    upstream.files["/items"] = "one\n"
    stop, thread = serve_in_background(tmp_path, upstream, WINDOW_WATCHER)
    try:
        thread.start()
        assert wait_for(lambda: len(upstream.requests) >= 1), "first pass never fetched"
        upstream.files["/items"] = "two\none\n"
        out = ""

        def alerted():
            nonlocal out
            out += capfd.readouterr().out
            return "+two" in out

        assert wait_for(alerted), f"no alert after the feed changed:\n{out}"
    finally:
        stop.set()
        thread.join(timeout=20)
        upstream.server.shutdown()
    assert not thread.is_alive()
    assert "stopped" in out + capfd.readouterr().out


def test_serve_survives_a_failing_pass(tmp_path, capfd):
    (tmp_path / "urlwatch.yaml").write_text("report: {stdout: {enabled: true}}\n")
    upstream = Upstream()
    broken = WINDOW_WATCHER + "headers: {Authorization: 'Bearer ${NOT_SET_ANYWHERE}'}\n"
    stop, thread = serve_in_background(tmp_path, upstream, broken)
    err = ""
    try:
        thread.start()

        def failed_twice():
            nonlocal err
            err += capfd.readouterr().err
            return err.count("pass failed") >= 2

        assert wait_for(failed_twice), f"loop did not keep going after a failure:\n{err}"
        assert "needs NOT_SET_ANYWHERE" in err
    finally:
        stop.set()
        thread.join(timeout=20)
        upstream.server.shutdown()
    assert not thread.is_alive()
