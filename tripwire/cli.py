"""`python -m tripwire check|run|serve <path>`, plus `schema` and `hooks` for reference.

A thin wrapper that turns watcher YAML into a urlwatch run. `serve` runs the same thing in a
loop (`--interval` seconds), for watchers that should alert within a minute instead of waiting
for a cron slot.

<path> is a watcher.yaml or the watchers/ folder. `--every NAME` runs only the watchers with
that `every:` cadence, so each workflow runs its own cadence. Each cadence gets its own
snapshot db under state/, so independent workflows never touch the same file.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv

from tripwire import load_all
from tripwire.registry import HOOKS
from tripwire.spec import SpecError, Watcher, expand_secrets, load_watchers, select

ROOT = Path(__file__).resolve().parent.parent


# Keep only the "+" lines of a diff, but not the "+++ @ <date>" header urlwatch prepends.
ADDITIONS_ONLY = [{"grep": r"^\+(?!\+\+ @)"}]


def urlwatch_job(watcher: Watcher) -> dict:
    """A watcher as a urlwatch job. Our `every` and `additions_only` are translated or dropped."""
    job = watcher.model_dump(
        mode="json", exclude={"every", "additions_only"}, exclude_defaults=True, exclude_none=True
    )
    if watcher.additions_only:
        job["diff_filter"] = ADDITIONS_ONLY
    # urlwatch wants a unique url per job, but several watchers can share an endpoint (one
    # GraphQL url, many queries). A fragment from the name makes each unique and is never sent.
    # (Not user_visible_url: urlwatch derives a job's identity from it, which would collide.)
    slug = re.sub(r"[^a-z0-9]+", "-", watcher.name.lower()).strip("-")
    job["url"] = f"{job['url']}#{slug}"
    return job


def write_urls(watchers: list[Watcher], out: Path) -> None:
    out.write_text("---\n".join(yaml.safe_dump(urlwatch_job(w), sort_keys=False) for w in watchers))


def render_config(source: Path, out: Path) -> None:
    """Expand ${VAR}s from the environment and switch each reporter on when its settings exist."""
    config = yaml.safe_load(source.read_text())
    reporters = config.get("report", {})
    for name, keys in (("telegram", ("bot_token", "chat_id")), ("postgres", ("dsn",))):
        reporter = reporters.get(name)
        if reporter is None:
            continue
        for key in keys:
            reporter[key] = os.path.expandvars(str(reporter.get(key, "")))
        configured = all(reporter[k] and "$" not in reporter[k] for k in keys)
        reporter["enabled"] = bool(reporter.get("enabled")) or configured
    out.write_text(yaml.safe_dump(config, sort_keys=False))


def urlwatch(urls: Path, cache: Path, config: Path, *args: str) -> int:
    cache.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["urlwatch", "--urls", str(urls), "--config", str(config), "--cache", str(cache)]
    cmd += ["--hooks", str(ROOT / "hooks.py"), *args]
    return subprocess.run(cmd, check=False).returncode


def run_once(command: str, watchers: list[Watcher], config_path: Path, cache: Path) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        urls = Path(tmp) / "urls.yaml"
        config = Path(tmp) / "urlwatch.yaml"
        if command == "check":  # --list loads every job and filter, so typos fail here
            write_urls(watchers, urls)
            render_config(config_path, config)
            return urlwatch(urls, Path(tmp) / "check.db", config, "--list")
        try:
            write_urls(expand_secrets(watchers), urls)  # tokens exist only in this temp file
        except SpecError as e:
            print("\n".join(e.problems), file=sys.stderr)
            return 1
        render_config(config_path, config)
        return urlwatch(urls, cache, config)


def serve(
    watchers: list[Watcher], config_path: Path, cache: Path, interval: float, stop: threading.Event
) -> int:
    """One pass every `interval` seconds until `stop` is set (the caller wires up signals)."""
    names = ", ".join(w.name for w in watchers)
    print(f"serving {len(watchers)} watcher(s) every {interval:g}s: {names}", flush=True)
    while not stop.is_set():
        started = time.monotonic()
        code = run_once("run", watchers, config_path, cache)
        if code != 0:  # one bad pass must not end the service
            print(f"pass failed with exit code {code}, retrying", file=sys.stderr, flush=True)
        stop.wait(max(1.0, interval - (time.monotonic() - started)))
    print("stopped", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tripwire", description=__doc__)
    parser.add_argument("command", choices=["check", "run", "serve", "schema", "hooks"])
    parser.add_argument("path", type=Path, nargs="?", help="watchers folder or one watcher.yaml")
    parser.add_argument("--every", help="only the watchers of this cadence")
    parser.add_argument("--interval", type=float, default=60, help="serve: seconds between passes")
    parser.add_argument("--config", type=Path, default=ROOT / "urlwatch.yaml")
    parser.add_argument("--cache", type=Path, help="default: state/<path name>.db")
    args = parser.parse_args(argv)
    load_dotenv(ROOT / ".env")  # real environment variables win over the file

    if args.command == "schema":
        print(json.dumps(Watcher.model_json_schema(), indent=2))
        return 0
    if args.command == "hooks":
        load_all()
        for name, model in sorted(HOOKS.items()):
            print(f"{name}\n{json.dumps(model.model_json_schema()['properties'], indent=2)}")
        return 0
    if args.path is None:
        parser.error(f"{args.command} needs a path")
    try:
        watchers = load_watchers(args.path)
    except SpecError as e:
        print("\n".join(e.problems), file=sys.stderr)
        return 1

    watchers = select(watchers, args.every)
    if not watchers:
        print(f"no watcher has every: {args.every}", file=sys.stderr)
        return 1

    cache = args.cache or ROOT / "state" / f"{args.every or 'all'}.db"
    if args.command == "serve":
        if args.interval < 10:
            parser.error("--interval must be at least 10 seconds, GitHub rate limits are real")
        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        return serve(watchers, args.config, cache, args.interval, stop)
    return run_once(args.command, watchers, args.config, cache)


if __name__ == "__main__":
    raise SystemExit(main())
