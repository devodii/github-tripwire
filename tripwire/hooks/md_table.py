"""Extract rows from markdown tables into one stable line per row."""

from __future__ import annotations

import re

from pydantic import Field, field_validator

from tripwire import HookOptions, hook

_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in _UNESCAPED_PIPE.split(line.strip()[1:-1])]


def _is_separator(cells: list[str]) -> bool:
    return all(re.fullmatch(r":?-+:?", c) for c in cells)


class MdTableOptions(HookOptions):
    key: str = Field(description="header of the column that identifies a row")
    columns: list[str] = Field(min_length=1, description="headers of the columns to keep")
    key_regex: str | None = Field(
        default=None, description="regex run on the key cell, group 1 (or whole match) is the key"
    )

    @field_validator("key_regex")
    @classmethod
    def _compiles(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                re.compile(value)
            except re.error as e:
                raise ValueError(f"not a valid regex: {e}") from e
        return value


@hook("md_table")
def md_table(data: str, options: MdTableOptions) -> str:
    """Reduce markdown tables to sorted 'key | value | value' lines.

    Columns are found by header name and rows are keyed and sorted, so reflowed tables,
    moved rows and shifted line numbers never produce a diff. Every table that has the
    key and all requested columns is merged into one output.
    """
    key, columns = options.key, options.columns
    key_regex = re.compile(options.key_regex) if options.key_regex else None
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
