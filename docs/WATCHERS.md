# Writing a watcher

A watcher says **what to fetch**, **what part of it you care about**, and nothing else. The
engine fetches it on a schedule, keeps only the part you extracted, and alerts when that part
changes. No code is needed for most watchers.

Files live in `watchers/<cadence>/<anything>.yaml`. The folder is the cadence: each folder gets
its own workflow, its own cron and its own snapshot db. One file can hold many watchers,
separated by `---`. Every watcher is validated against the spec below (`python -m tripwire
check watchers`), and `schema/watcher.schema.json` gives editors autocomplete.

## Fields

| field     | required | meaning                                                                    |
| --------- | -------- | -------------------------------------------------------------------------- |
| `name`    | yes      | unique across all watchers, shown in alerts                                |
| `url`     | yes      | what to fetch (raw file, API endpoint, atom feed)                          |
| `filter`  | no       | steps applied in order. Each is `name` or `name: {options}`                |
| `method`  | no       | `GET` (default) or `POST`                                                  |
| `headers` | no       | request headers, e.g. an `Authorization` token for private repos           |
| `data`    | no       | request body for `POST`                                                    |

Unknown fields and unknown options are errors, so typos fail in `check`, not silently in prod.

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

### `kv_block`: `Key: value` fields

```yaml
filter:
  - kv_block:
      keys: [Status, Version] # emitted in this order, case-insensitive
      scope: fenced           # `fenced` = inside ``` blocks, `document` = whole file (default)
      anchor: Release         # a key the block must contain, to pick the right block
```

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

Add `cases/<name>/case.yaml`. A case replays fixture files through your real watcher YAML, one
stage at a time, and checks what gets reported. The harness (`tests/test_cases.py`) runs every
case, so there is nothing else to wire up.

```yaml
watchers: ../../watchers/frequent/my-watcher.yaml
stages:
  - name: baseline is recorded silently
    serve:
      https://example.com/status.md: fixtures/status.before.md   # sticky until changed
    expect: {silent: true}
  - name: a state flips
    serve:
      https://example.com/status.md: fixtures/status.after.md
    expect:
      reports: ["-api | Stable", "+api | Degraded"]   # substrings of the alert
      changed_lines: 2                                # count of +/- diff lines
  - name: upstream reflows the file
    serve: {https://example.com/status.md: fixtures/status.reflowed.md}
    expect: {silent: true}
```

Good fixtures are real: take the file at a commit before and after a change you care about
(`curl https://raw.githubusercontent.com/<owner>/<repo>/<sha>/<path>`). Always include a stage
where the file moves around without changing a value, and one where it breaks.

## Running

```sh
uv sync
uv run python -m tripwire check watchers            # validate
uv run python -m tripwire run watchers/frequent     # fetch, diff, report to stdout
uv run pytest                                       # engine tests and every case
```

The first run only records a baseline. Telegram turns on by itself when `TG_TOKEN` and
`TG_CHAT` are set in the environment (repository secrets in Actions).
