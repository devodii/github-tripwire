# github-tripwire

Watch files on GitHub (or any URL) for the *content* changes you care about and get alerted,
configured in YAML. Built on [urlwatch](https://thp.io/2008/urlwatch/): each run fetches every
watcher, extracts the part you care about, diffs it against the last run and reports changes.

- Add a watcher: [docs/WATCHERS.md](docs/WATCHERS.md)
- `watchers/<name>/`: one folder per watcher holding `watcher.yaml`, its `case.yaml` replay test and `fixtures/`.
- `tripwire/hooks/`: the few extractors YAML can't express, each a small typed function.
- `every:` in a watcher picks its schedule; one workflow per cadence runs `--every <cadence>`.
- Snapshots live on the `tripwire-state` branch so `main` history stays clean.

```sh
uv sync
uv run python -m tripwire check watchers
uv run python -m tripwire run watchers --every frequent
uv run pytest
```
