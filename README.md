# github-tripwire

Watch files on GitHub (or any URL) for the *content* changes you care about and get alerted,
configured in YAML. Built on [urlwatch](https://thp.io/2008/urlwatch/): each run fetches every
watcher, extracts the part you care about, diffs it against the last run and reports changes.

- Add a watcher: [docs/WATCHERS.md](docs/WATCHERS.md)
- `watchers/<cadence>/*.yaml`: what to watch. One folder per schedule, one workflow per folder.
- `tripwire/hooks/`: the few extractors YAML can't express, each a small typed function.
- `cases/`: replayable fixtures that prove each watcher fires when it should and stays quiet when not.
- Snapshots live on the `tripwire-state` branch so `main` history stays clean.

```sh
uv sync
uv run python -m tripwire check watchers
uv run python -m tripwire run watchers/frequent
uv run pytest
```
