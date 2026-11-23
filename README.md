# github-tripwire

Sends you a Telegram message when something you care about on GitHub changes.
For example: a SEP's status goes from Draft to Final, someone comments on an issue,
a new discussion starts.

## How it works

1. You describe what to watch in a YAML file. That file is a **watcher**: a URL, plus which
   part of the response you care about.
2. The **engine** (the Python in `tripwire/`) fetches the URL, cuts the response down to that
   part, and compares it with what it saw last time.
3. If it's different, you get an alert. If not, nothing happens.

The first run only remembers what it sees. It never alerts about things that were already there.

## What's where

| Path | What it is |
| --- | --- |
| `watchers/<name>/watcher.yaml` | What to watch. This is the file you edit to add things. |
| `watchers/<name>/case-*.yaml` | The tests for it: pretend the page changed, check the right alert comes out. |
| `templates/` | A watcher written once, reusable for any repo (see below). |
| `tripwire/hooks/` | Small filters written in Python (see below). |
| `tripwire/` | The engine. You shouldn't need to touch it to add a watcher. |
| `urlwatch.yaml` | How alerts look and where they go. |
| `db/` | The Postgres tables, as plain SQL files. |
| `.github/workflows/` | Runs the tests, and runs the slow watchers on a schedule. |

## Hooks

A watcher's `filter:` is a list of steps that cut a big response down to the part you care
about. urlwatch has some built in (`grep`, `jq`, `html2text`). We added three, because the
built-in ones weren't enough:

- **`md_table`** reads a markdown table and gives one line per row, like `SEP-0053 | Final`.
  Rows are found by name, so someone re-ordering the table or fixing the spacing doesn't alert.
- **`kv_block`** pulls `Key: value` lines out of a document, like `Status: Final` from the top
  of a SEP. It finds them by name, not by line number, so if the line moves nothing happens.
- **`jq_lines`** takes a JSON API response and prints one readable line per item
  (`💬 ada on #2027: looks good https://...`). This is how the activity feed works.

Each one validates its options (a typo fails with a clear message before anything runs).
To add a hook: put one file in `tripwire/hooks/`. See `docs/WATCHERS.md`.

## Templates

`templates/` holds watchers written once. `github-path-commits` follows every merged commit that
touches a folder (for example all SEPs). `github-repo-feed` is five watchers: new issues and PRs, comments,
review comments, discussions, discussion comments. To watch any repo, you write this:

```yaml
uses: github-repo-feed
with: {name: rust, owner: rust-lang, repo: rust}
```

## Two speeds, both on GitHub Actions

- **Every 30 minutes** (`watch-frequent.yml`): watchers with `every: frequent`. Good for things
  that change rarely, like a status. GitHub can delay cron runs, so it's not instant.
- **Live, about a minute** (`watch-live.yml`): watchers with `every: live`. One job polls once a
  minute for 50 minutes, saves what it saw, and starts the next run of itself, so one is always
  running. It uses GitHub's built-in token, so there's no token of yours to manage. You can't get
  real-time webhooks for repos you don't own, so polling is as fast as it gets.

To stop the live feed: cancel the running job and disable the workflow. To run it on your own
machine instead: `uv run python -m tripwire serve watchers --every live` (needs `GH_READ_TOKEN`).

If a watcher keeps failing, you get one error message an hour for it, not one a minute.

## Setup

```sh
uv sync
cp .env.example .env     # then fill it in
task db:up               # starts Postgres in Docker
uv run python -m tripwire check watchers
uv run pytest
```

Secrets, in `.env` locally and in GitHub repo secrets for Actions:

| Name | What for |
| --- | --- |
| `TG_TOKEN` | Telegram bot token (from @BotFather). |
| `TG_CHAT` | Your Telegram chat id. Send the bot `/start`, then it shows up in the bot's updates. |
| `GH_READ_TOKEN` | Only when running the live feed on your own machine. In GitHub Actions the built-in token is used. |
| `DATABASE_URL` | Optional. Postgres where every alert is saved, so you can query the history. |

## Where data is kept

- **What it saw last time:** `state/*.db`. In GitHub Actions this lives on the `tripwire-state`
  branch, so `main` stays clean.
- **Alert history:** the `changes` table in Postgres, when `DATABASE_URL` is set.
- **Test pages:** tests use real GitHub commits as fixtures. They're fetched once and kept in
  the `snapshots` table in Postgres, not in this repo.

## Commands

```sh
uv run python -m tripwire check watchers                  # are the watchers valid?
uv run python -m tripwire run watchers --every frequent   # run one pass
uv run python -m tripwire serve watchers --every live     # run forever, once a minute
uv run python -m tripwire hooks                           # list hooks and their options
task schema:plan / task schema:apply                      # change the database tables
```

More detail on writing watchers and tests: [docs/WATCHERS.md](docs/WATCHERS.md).
