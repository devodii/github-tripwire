"""`python -m tripwire check|run <path>`, plus `schema` and `hooks` for reference: thin wrapper that turns watcher YAML into a urlwatch run.

<path> is a watcher.yaml or the watchers/ folder. `--every NAME` runs only the watchers with
that `every:` cadence, so each workflow runs its own cadence. Each cadence gets its own
snapshot db under state/, so independent workflows never touch the same file.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml
from dotenv import load_dotenv

from tripwire import load_all
from tripwire.registry import HOOKS
from tripwire.spec import SpecError, Watcher, load_watchers, select

ROOT = Path(__file__).resolve().parent.parent


def write_urls(watchers: list[Watcher], out: Path) -> None:
    docs = (
        w.model_dump(mode="json", exclude={"every"}, exclude_defaults=True, exclude_none=True)
        for w in watchers
    )
    out.write_text("---\n".join(yaml.safe_dump(doc, sort_keys=False) for doc in docs))


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tripwire", description=__doc__)
    parser.add_argument("command", choices=["check", "run", "schema", "hooks"])
    parser.add_argument("path", type=Path, nargs="?", help="watchers folder or one watcher.yaml")
    parser.add_argument("--every", help="only the watchers of this cadence")
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
    with tempfile.TemporaryDirectory() as tmp:
        urls = Path(tmp) / "urls.yaml"
        write_urls(watchers, urls)
        config = Path(tmp) / "urlwatch.yaml"
        render_config(args.config, config)
        if args.command == "check":  # --list loads every job and filter, so typos fail here
            return urlwatch(urls, Path(tmp) / "check.db", config, "--list")
        return urlwatch(urls, cache, config)


if __name__ == "__main__":
    raise SystemExit(main())
