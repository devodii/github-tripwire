# Writing a watcher

A watcher says **what to fetch**, **what part of it you care about**, and nothing else. The
engine fetches it on a schedule, keeps only the part you extracted, and alerts when that part
changes. No code is needed for most watchers.

Everything about one watcher lives in one folder:

```
watchers/my-watcher/
  watcher.yaml   what to watch (one or more watchers, separated by ---)
  case.yaml      its replay test
  fixtures/      the files the test serves
```

Adding a watcher is adding a folder, removing one is deleting it. Every watcher is validated
against the spec below (`python -m tripwire check watchers`), and `schema/watcher.schema.json`
gives editors autocomplete.

## Fields

| field     | required | meaning                                                                    |
| --------- | -------- | -------------------------------------------------------------------------- |
| `name`    | yes      | unique across all watchers, shown in alerts                                |
| `every`   | yes      | cadence name, e.g. `frequent`. A workflow runs `--every <name>`            |
| `url`     | yes      | what to fetch (raw file, API endpoint, atom feed)                          |
| `filter`  | no       | steps applied in order. Each is `name` or `name: {options}`                |
| `method`  | no       | `GET` (default) or `POST`                                                  |
| `headers` | no       | request headers, e.g. an `Authorization` token for private repos           |
| `data`    | no       | request body for `POST`                                                    |
| `additions_only` | no | report only lines that newly appear (for feeds that show the latest N items) |

Unknown fields and unknown options are errors, so typos fail in `check`, not silently in prod.

`${NAME}` inside `headers` and `data` is filled from the environment when the watcher runs, so
tokens never sit in a file (`Authorization: Bearer ${GH_READ_TOKEN}`). A missing one stops the
run with a message naming the watcher. `check` does not need them.

## Filters

A filter step is either one of our hooks or a built-in urlwatch filter (`html2text`, `grep`,
`jq`, `re.sub`, `sort`, ...). `python -m tripwire hooks` lists our hooks and their options.

The goal of the filter chain is a **stable text**: it must only change when something you care
about changed. Key your extraction on content (headers, keys, ids), never on line numbers or
spacing, and the alert will not fire when upstream reshuffles the file.

### `md_table`: rows of a markdown table

```yaml
filter:
  - md_table:
      key: Service            # header of the column that identifies a row
      columns: [State]        # headers of the columns to keep
      key_regex: '\[(\w+)\]'  # optional: group 1 of this regex on the key cell is the key
```

Output is one sorted `key | value` line per row, from every table that has those headers.
Rows can move between tables or be reordered and columns can be re-padded without an alert.

### `jq_lines`: JSON as readable lines

```yaml
filter:
  - jq_lines:
      query: '.[] | "\(.user.login): \(.body[:80]) \(.html_url)"'
```

Runs a [jq](https://jqlang.github.io/jq/) program over a JSON response and prints each result on
its own line, strings as plain text. Use it for API responses. (urlwatch's own `jq` filter quotes
and escapes everything, which looks bad in an alert.)

### `kv_block`: `Key: value` fields

```yaml
filter:
  - kv_block:
      keys: [Status, Version] # emitted in this order, case-insensitive
      scope: fenced           # `fenced` = inside ``` blocks, `document` = whole file (default)
      anchor: Release         # a key the block must contain, to pick the right block
```

## Feeds and templates

An API feed such as "the newest 30 comments" is a window: new items push old ones out. Use
`additions_only: true` so an item leaving the window never alerts, only a new one does, and
reshape each item into one line with `jq_lines` so the alert reads well.

A template is a set of watchers written once with `{{parameters}}`, kept in `templates/`. A
watcher file uses it like this:

```yaml
uses: github-repo-feed
with: {name: rust, owner: rust-lang, repo: rust}
```

`github-repo-feed` gives you five watchers for any repo (issues and PRs, comments, review
comments, discussions, discussion comments). To make a template, write a file in `templates/`:
the first document lists the parameters (with a `default:` to make one optional), the rest are
ordinary watchers that use `{{name}}` wherever a parameter goes.

## Running live

`every:` names a schedule. Watchers with `every: frequent` run from a cron in GitHub Actions.
For alerts within a minute, run the `live` ones in a loop on a machine that stays on:

```sh
uv run python -m tripwire serve watchers --every live            # one pass a minute
uv run python -m tripwire serve watchers --every live --interval 30
```

It needs `GH_READ_TOKEN`. Webhooks (instant) are not possible for repos you don't own, so
polling is as fast as it gets. Unchanged responses cost nothing against GitHub's rate limit.

## Adding a hook

When YAML cannot express what you need, add a hook. It is one file in `tripwire/hooks/`,
discovered automatically:

```python
from pydantic import Field
from tripwire import HookOptions, hook


class WordCountOptions(HookOptions):
    word: str = Field(description="word to count")


@hook("word_count")
def word_count(data: str, options: WordCountOptions) -> str:
    """Count occurrences of a word."""
    return str(data.lower().split().count(options.word.lower()))
```

The options model validates the YAML in `check` and at run time and documents the options.
Raise `ValueError` when the input is not what you expect: the watcher then reports an error
instead of recording an empty snapshot. Add a unit test next to the other hook tests.

## Testing a watcher

Add `case.yaml` (or several: `case-sep.yaml`, `case-feed.yaml`) next to your `watcher.yaml`. A case is a short script of what the watched page
looks like over time and what you expect to be told. The harness (`tests/test_cases.py`)
runs every case, so there is nothing else to wire up.

```yaml
stages:
  - name: baseline is recorded silently
    serve:
      https://example.com/status.md: &before owner/repo@<40-hex-commit>:docs/status.md
    expect: {silent: true}

  - name: a state flips (the real commit)
    serve:
      https://example.com/status.md: owner/repo@<40-hex-commit>:docs/status.md
    expect:
      reports: ["-api | Stable", "+api | Degraded"]   # substrings of the alert
      changed_lines: 2                                # count of +/- diff lines

  - name: upstream reshuffles the file without changing a value
    serve:
      https://example.com/status.md:
        github: *before
        prepend: "# New banner\n\n"                    # also: move_line: {startswith: "Status:", down: 2}
    expect: {silent: true}

  - name: upstream breaks the format
    serve: {https://example.com/status.md: {text: "404: Not Found"}}
    expect: {reports: ["ERROR"]}
```

A case can also say `only: [watcher names]` to run just some of the watchers in the file, and
`env: {GH_READ_TOKEN: test-token}` for variables the watchers need. In `serve:`, the key is
a watcher name or its url; use the name when several watchers share a url (GraphQL does).

**Fixtures are real GitHub commits, not files in the repo.** A stage serves either
`owner/repo@<full commit sha>:path` or literal `text:`. The first time a commit is used it is
fetched from GitHub and stored in the `snapshots` table of the test database, then reused, so
tests are reproducible (a commit never changes) and nothing is committed. To find commits:
`gh api 'repos/<owner>/<repo>/commits?path=<file>'`.

Always include a stage where the file moves around without changing a value, and one where it
breaks.

## Database

Postgres holds the test snapshots and, when `DATABASE_URL` is set, a history of every alert
(`changes` table: watcher, url, new/changed/error, old and new content, diff).

```sh
cp .env.example .env          # then adjust passwords
task db:up                    # start Postgres, create the tripwire role and database
task schema:plan              # write pending DDL to db/migrations/NNNNNN.sql for review
task schema:apply             # apply db/schema/ to the live database
```

The schema is plain SQL in `db/schema/{tables,indexes,...}/`, one file per object, applied with
[pgschema](https://www.pgschema.com) exactly like the home-database repo. Edit the SQL, `plan`,
`apply`. `db/schema/manifest.sql` is generated, never edit it by hand.

## Running

```sh
uv sync
uv run python -m tripwire check watchers            # validate
uv run python -m tripwire run watchers --every frequent   # fetch, diff, report to stdout
task db:up && uv run pytest                         # engine tests and every case (needs Postgres)
```

The first run only records a baseline. Telegram turns on by itself when `TG_TOKEN` and
`TG_CHAT` are set in the environment (repository secrets in Actions).
