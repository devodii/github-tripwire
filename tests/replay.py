"""Replays a case.yaml through its watcher.yaml and checks what gets reported.

A stage serves content in place of the watcher's real URLs. Content is either literal
(`text:`) or a file at an exact GitHub commit (`github: owner/repo@<40 hex sha>:path`).
Commit content never changes, so it is fetched once, stored in the `snapshots` table of the
test database and reused, which keeps fixture files out of the repo.
"""

from __future__ import annotations

import http.server
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import NamedTuple

import psycopg
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from tripwire.cli import main

GITHUB_REF = re.compile(r"(?P<repo>[\w.-]+/[\w.-]+)@(?P<sha>[0-9a-f]{40}):(?P<path>.+)")


class MoveLine(BaseModel):
    model_config = ConfigDict(extra="forbid")
    startswith: str = Field(description="the first line starting with this is moved")
    down: int = Field(description="by this many lines (the value stays, only its position moves)")


class Serve(BaseModel):
    model_config = ConfigDict(extra="forbid")
    github: str | None = Field(default=None, description="owner/repo@<40 hex sha>:path")
    text: str | None = Field(default=None, description="literal content")
    prepend: str = Field(default="", description="text added above the content")
    move_line: MoveLine | None = None

    @model_validator(mode="before")
    @classmethod
    def _shorthand(cls, value):
        return {"github": value} if isinstance(value, str) else value

    @model_validator(mode="after")
    def _exactly_one_source(self):
        if (self.github is None) == (self.text is None):
            raise ValueError("serve needs exactly one of 'github' or 'text'")
        if self.github is not None and not GITHUB_REF.fullmatch(self.github):
            raise ValueError("github must look like owner/repo@<40 hex sha>:path/in/repo")
        return self


class Expect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    silent: bool = False
    reports: list[str] = Field(default_factory=list, description="substrings that must appear")
    not_reports: list[str] = Field(default_factory=list)
    changed_lines: int | None = Field(default=None, description="count of +/- diff lines")


class Stage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    serve: dict[str, Serve] = Field(default_factory=dict, description="url -> content, sticky")
    expect: Expect


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stages: list[Stage] = Field(min_length=1)


class Run(NamedTuple):
    out: str
    err: str


class Snapshots:
    """Commit contents cached in Postgres, fetched from GitHub on first use."""

    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def github(self, ref: str) -> str:
        m = GITHUB_REF.fullmatch(ref)
        assert m, ref
        source, sha, path = f"github:{m['repo']}", m["sha"], m["path"]
        row = self.conn.execute(
            "SELECT content FROM snapshots WHERE source = %s AND ref = %s AND path = %s",
            (source, sha, path),
        ).fetchone()
        if row:
            return row[0]
        url = f"https://raw.githubusercontent.com/{m['repo']}/{sha}/{path}"
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                content = response.read().decode()
        except OSError as e:
            raise RuntimeError(
                f"{ref} is not in the snapshots table and GitHub is unreachable: {e}"
            )
        self.conn.execute(
            "INSERT INTO snapshots (source, ref, path, content) VALUES (%s, %s, %s, %s)"
            " ON CONFLICT DO NOTHING",
            (source, sha, path, content),
        )
        return content

    def resolve(self, serve: Serve) -> str:
        content = serve.text if serve.text is not None else self.github(serve.github)
        if serve.move_line:
            lines = content.splitlines()
            i = next(
                n for n, line in enumerate(lines) if line.startswith(serve.move_line.startswith)
            )
            lines.insert(i + serve.move_line.down, lines.pop(i))
            content = "\n".join(lines) + "\n"
        return serve.prepend + content


class Upstream:
    """A local stand-in for the watched URLs."""

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


def run_case(case_file: Path, tmp_path: Path, capfd, snapshots: Snapshots) -> list[Run]:
    """Run every stage in order, asserting its expectations. Returns each stage's output."""
    case = Case.model_validate(yaml.safe_load(case_file.read_text()))
    text = (case_file.parent / "watcher.yaml").read_text()

    urls = list(dict.fromkeys(url for stage in case.stages for url in stage.serve))
    local = {url: f"/r{i}" for i, url in enumerate(urls)}
    upstream = Upstream()
    outputs, last_run = [], 0.0
    try:
        for url in sorted(urls, key=len, reverse=True):  # longest first, so prefixes are safe
            text = text.replace(url, upstream.base + local[url])
        watchers = tmp_path / "watchers"
        watchers.mkdir()
        (watchers / "watcher.yaml").write_text(text)

        for stage in case.stages:
            for url, serve in stage.serve.items():
                upstream.files[local[url]] = snapshots.resolve(serve)
            # urlwatch picks the latest snapshot by whole-second timestamp, so space runs apart
            time.sleep(max(0.0, last_run + 1.1 - time.monotonic()))
            last_run = time.monotonic()
            capfd.readouterr()
            code = main(["run", str(watchers), "--cache", str(tmp_path / "cache.db")])
            out, err = capfd.readouterr()
            outputs.append(Run(out, err))
            where, expect = f"stage '{stage.name}'", stage.expect

            assert code == 0, where
            if expect.silent:
                assert out.strip() == "", f"{where} should be silent but reported:\n{out}"
            for needle in expect.reports:
                assert needle in out, f"{where} should report {needle!r}:\n{out}"
            for needle in expect.not_reports:
                assert needle not in out, f"{where} should not report {needle!r}"
            if expect.changed_lines is not None:
                assert len(diff_lines(out)) == expect.changed_lines, f"{where}:\n{out}"
    finally:
        upstream.server.shutdown()
    return outputs
