"""Replays a case.yaml through its watcher.yaml and checks what gets reported.

A stage serves content in place of the watcher's real URLs. Content is either literal
(`text:`) or a file at an exact GitHub commit (`github: owner/repo@<40 hex sha>:path`).
Commit content never changes, so it is fetched once, stored in the `snapshots` table of the
test database and reused, which keeps fixture files out of the repo.
"""

from __future__ import annotations

import http.server
import os
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import NamedTuple
from unittest import mock

import psycopg
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from tripwire.cli import main
from tripwire.spec import load_watchers

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
    serve: dict[str, Serve] = Field(
        default_factory=dict, description="job name or url -> content, sticky"
    )
    expect: Expect


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid")
    only: list[str] = Field(
        default_factory=list, description="run just these watchers (default: all in watcher.yaml)"
    )
    env: dict[str, str] = Field(default_factory=dict, description="variables set during the case")
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
    """A local stand-in for the watched URLs. Every request it receives is kept in `requests`."""

    def __init__(self, requests: list | None = None):
        self.files: dict[str, str] = {}
        self.requests: list[dict] = requests if requests is not None else []
        files, seen = self.files, self.requests

        class Handler(http.server.BaseHTTPRequestHandler):
            def respond(self, request_body: str = ""):
                seen.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body": request_body,
                    }
                )
                body = files.get(self.path)
                self.send_response(200 if body is not None else 404)
                self.end_headers()
                self.wfile.write((body or "Not Found").encode())

            def do_GET(self):
                self.respond()

            def do_POST(self):
                length = int(self.headers.get("content-length") or 0)
                self.respond(self.rfile.read(length).decode())

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


def run_case(
    case_file: Path, tmp_path: Path, capfd, snapshots: Snapshots, requests: list | None = None
) -> list[Run]:
    """Run every stage in order, asserting its expectations. Returns each stage's output.

    Every job in watcher.yaml is pointed at the local server. A stage's `serve` key is a job
    name or a url (a url shared by several jobs serves all of them).
    """
    case = Case.model_validate(yaml.safe_load(case_file.read_text()))
    # load through the engine so `uses:` templates expand exactly as they do in production
    watchers_in_file = load_watchers(case_file.parent / "watcher.yaml")
    jobs = [
        w.model_dump(mode="json", exclude_defaults=True, exclude_none=True)
        for w in watchers_in_file
    ]
    if case.only:
        missing = set(case.only) - {job["name"] for job in jobs}
        assert not missing, f"'only' names watchers that do not exist: {sorted(missing)}"
        jobs = [job for job in jobs if job["name"] in case.only]
    local = [f"/r{i}" for i in range(len(jobs))]
    identities = [(job.get("name"), job.get("url")) for job in jobs]  # before urls are rewritten

    def targets(key: str) -> list[int]:
        found = [i for i, names in enumerate(identities) if key in names]
        assert found, f"serve key {key!r} matches no job name or url in watcher.yaml"
        return found

    for stage in case.stages:
        for key in stage.serve:
            targets(key)

    upstream = Upstream(requests)
    outputs, last_run = [], 0.0
    try:
        for i, job in enumerate(jobs):
            job["url"] = upstream.base + local[i]
        watchers = tmp_path / "watchers"
        watchers.mkdir()
        (watchers / "watcher.yaml").write_text(yaml.safe_dump_all(jobs, sort_keys=False))

        with mock.patch.dict(os.environ, case.env):
            for stage in case.stages:
                for key, serve in stage.serve.items():
                    content = snapshots.resolve(serve)
                    for i in targets(key):
                        upstream.files[local[i]] = content
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
                    assert needle not in out, f"{where} should not report {needle!r}:\n{out}"
                if expect.changed_lines is not None:
                    assert len(diff_lines(out)) == expect.changed_lines, f"{where}:\n{out}"
    finally:
        upstream.server.shutdown()
    return outputs
