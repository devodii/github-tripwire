"""Extract rows from markdown tables into one stable line per row."""

from __future__ import annotations

import re
from typing import Any

from tripwire import hook

_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in _UNESCAPED_PIPE.split(line.strip()[1:-1])]


def _is_separator(cells: list[str]) -> bool:
    return all(re.fullmatch(r":?-{2,}:?", c) for c in cells)


@hook(
    "md_table",
    options={
        "key": "header of the column that identifies a row (required)",
        "columns": "headers of the columns to keep (required)",
        "key_regex": "optional regex applied to the key cell, group 1 (or whole match) is the key",
    },
)
def md_table(data: str, options: dict[str, Any]) -> str:
    """Reduce markdown tables to sorted 'key | value | value' lines.

    Columns are found by header name and rows are keyed and sorted, so reflowed tables,
    moved rows and shifted line numbers never produce a diff. Every table that has the
    key and all requested columns is merged into one output.
    """
    key = options.get("key")
    columns = options.get("columns")
    if not key or not columns:
        raise ValueError("md_table: 'key' and 'columns' are required")
    key_regex = re.compile(options["key_regex"]) if options.get("key_regex") else None
    wanted = [key, *columns]

    lines: set[str] = set()
    index: dict[str, int] | None = None
    for raw in data.splitlines():
        if not raw.lstrip().startswith("|"):
            index = None
            continue
        cells = _cells(raw)
        if index is None:
            names = {c.lower(): i for i, c in enumerate(cells)}
            if all(w.lower() in names for w in wanted):
                index = {w: names[w.lower()] for w in wanted}
            else:
                index = {}  # a table we do not care about: skip until it ends
            continue
        if not index or _is_separator(cells):
            continue
        row_key = cells[index[key]]
        if key_regex:
            match = key_regex.search(row_key)
            if not match:
                continue
            row_key = match.group(1) if key_regex.groups else match.group(0)
        lines.add(" | ".join([row_key, *(cells[index[c]] for c in columns)]))

    if not lines:
        raise ValueError(f"md_table: no table with columns {wanted} found, has the format changed?")
    return "\n".join(sorted(lines))
