"""Generic replay harness: runs every watchers/*/case.yaml against the watcher.yaml beside it.

A case serves fixture files from a local server in place of the watcher's real URLs, runs
the real watcher YAML through urlwatch once per stage, and checks what got reported.
Nothing here knows about any particular watcher.
"""

import http.server
import threading
import time
from pathlib import Path

import pytest
import yaml
from conftest import ROOT
from pydantic import BaseModel, ConfigDict, Field

from tripwire.cli import main


class Expect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    silent: bool = False
    reports: list[str] = Field(default_factory=list, description="substrings that must appear")
    not_reports: list[str] = Field(default_factory=list)
    changed_lines: int | None = Field(default=None, description="count of +/- diff lines")


class Stage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    serve: dict[str, str] = Field(default_factory=dict, description="url -> fixture, sticky")
    expect: Expect


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stages: list[Stage] = Field(min_length=1)


CASES = sorted((ROOT / "watchers").glob("*/case.yaml"))


class Upstream:
    def __init__(self):
        self.files: dict[str, str] = {}

        files = self.files

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = files.get(self.path)
                self.send_response(200 if body is not None else 404)
                self.end_headers()
                self.wfile.write((body or "Not Found").encode())

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"


def diff_lines(out: str) -> list[str]:
    return [
        line
        for line in out.splitlines()
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---", "-- "))
    ]


def test_there_is_at_least_one_case():
    assert CASES, "watchers/*/case.yaml not found"


@pytest.mark.parametrize("case_file", CASES, ids=lambda p: p.parent.name)
def test_case(case_file: Path, tmp_path, capfd):
    case = Case.model_validate(yaml.safe_load(case_file.read_text()))
    watcher_file = case_file.parent / "watcher.yaml"

    urls = list(dict.fromkeys(url for stage in case.stages for url in stage.serve))
    local = {url: f"/r{i}" for i, url in enumerate(urls)}
    text = watcher_file.read_text()
    upstream = Upstream()
    try:
        for url in sorted(urls, key=len, reverse=True):  # longest first, so prefixes are safe
            text = text.replace(url, upstream.base + local[url])
        watchers = tmp_path / "watchers"
        watchers.mkdir()
        (watchers / "watcher.yaml").write_text(text)

        last_run = 0.0
        for stage in case.stages:
            for url, fixture in stage.serve.items():
                upstream.files[local[url]] = (case_file.parent / fixture).read_text()
            # urlwatch picks the latest snapshot by whole-second timestamp, so space runs apart
            time.sleep(max(0.0, last_run + 1.1 - time.monotonic()))
            last_run = time.monotonic()
            capfd.readouterr()
            code = main(["run", str(watchers), "--cache", str(tmp_path / "cache.db")])
            out = capfd.readouterr().out
            where = f"stage '{stage.name}'"

            assert code == 0, where
            if stage.expect.silent:
                assert out.strip() == "", f"{where} should be silent but reported:\n{out}"
            for needle in stage.expect.reports:
                assert needle in out, f"{where} should report {needle!r}:\n{out}"
            for needle in stage.expect.not_reports:
                assert needle not in out, f"{where} should not report {needle!r}"
            if stage.expect.changed_lines is not None:
                assert len(diff_lines(out)) == stage.expect.changed_lines, f"{where}:\n{out}"
    finally:
        upstream.server.shutdown()
