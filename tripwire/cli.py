"""`python -m tripwire check|run <path>`: thin wrapper that turns watcher YAML into a urlwatch run.

<path> is a watcher file or a folder of them. Folders double as cadences: run
`watchers/frequent` from one workflow and `watchers/daily` from another. Each path gets
its own snapshot db under state/, so independent workflows never touch the same file.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml
from urlwatch.filters import FilterBase

from tripwire import load_all

ROOT = Path(__file__).resolve().parent.parent


def collect(path: Path) -> list[dict]:
    files = sorted(path.rglob("*.yaml")) if path.is_dir() else [path]
    jobs: list[dict] = []
    for file in files:
        for doc in yaml.safe_load_all(file.read_text()):
            if doc:
                jobs.append({**doc, "_file": file.as_posix()})
    return jobs


def problems(jobs: list[dict]) -> list[str]:
    load_all()
    found, seen = [], {}
    for job in jobs:
        where = job["_file"]
        name = job.get("name")
        if not name:
            found.append(f"{where}: job without a 'name'")
        elif name in seen:
            found.append(f"{where}: duplicate job name '{name}' (also in {seen[name]})")
        else:
            seen[name] = where
        if not (job.get("url") or job.get("command")):
            found.append(f"{where}: job '{name}' needs a 'url' or 'command'")
        try:
            list(FilterBase.normalize_filter_list(job.get("filter", [])))
        except ValueError as e:
            found.append(f"{where}: job '{name}' has an invalid filter: {e}")
    return found


def write_urls(jobs: list[dict], out: Path) -> None:
    clean = [{k: v for k, v in job.items() if k != "_file"} for job in jobs]
    out.write_text("---\n".join(yaml.safe_dump(job, sort_keys=False) for job in clean))


def urlwatch(urls: Path, cache: Path, config: Path, *args: str) -> int:
    cache.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["urlwatch", "--urls", str(urls), "--config", str(config), "--cache", str(cache)]
    cmd += ["--hooks", str(ROOT / "hooks.py"), *args]
    return subprocess.run(cmd, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tripwire", description=__doc__)
    parser.add_argument("command", choices=["check", "run"])
    parser.add_argument("path", type=Path)
    parser.add_argument("--config", type=Path, default=ROOT / "urlwatch.yaml")
    parser.add_argument("--cache", type=Path, help="default: state/<path name>.db")
    args = parser.parse_args(argv)

    jobs = collect(args.path)
    errors = problems(jobs)
    if errors or not jobs:
        print("\n".join(errors or [f"no watchers found in {args.path}"]), file=sys.stderr)
        return 1

    cache = args.cache or ROOT / "state" / f"{args.path.resolve().stem}.db"
    with tempfile.TemporaryDirectory() as tmp:
        urls = Path(tmp) / "urls.yaml"
        write_urls(jobs, urls)
        if args.command == "check":  # --list loads every job and filter, so typos fail here
            return urlwatch(urls, cache, args.config, "--list")
        return urlwatch(urls, cache, args.config)


if __name__ == "__main__":
    raise SystemExit(main())
