"""Extract chosen 'Key: value' fields from a document."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import Field

from tripwire import HookOptions, hook

_FIELD = re.compile(r"^([A-Za-z][\w-]*):[ \t]*(.*?)[ \t]*$")


def _blocks(data: str, scope: str) -> list[list[str]]:
    if scope == "document":
        return [data.splitlines()]
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in data.splitlines():
        if line.strip().startswith("```"):
            if current is not None:
                blocks.append(current)
            current = None if current is not None else []
        elif current is not None:
            current.append(line)
    return blocks


class KvBlockOptions(HookOptions):
    keys: list[str] = Field(
        min_length=1, description="keys to keep, case-insensitive, emitted in this order"
    )
    scope: Literal["fenced", "document"] = Field(
        default="document", description="'fenced' (inside ``` blocks) or 'document' (whole file)"
    )
    anchor: str | None = Field(
        default=None, description="key that must be present for a block to be chosen"
    )


@hook("kv_block")
def kv_block(data: str, options: KvBlockOptions) -> str:
    """Reduce a document to 'Key: value' lines for the requested keys.

    The block is located by content (its anchor key), not line number, and only the first
    occurrence of each key in that block counts, so edits elsewhere never produce a diff.
    """
    keys, scope, anchor = options.keys, options.scope, (options.anchor or "").lower()

    for block in _blocks(data, scope):
        found: dict[str, tuple[str, str]] = {}
        for line in block:
            match = _FIELD.match(line)
            if match:
                found.setdefault(match.group(1).lower(), (match.group(1), match.group(2)))
        if anchor and anchor not in found:
            continue
        missing = [k for k in keys if k.lower() not in found]
        if missing:
            raise ValueError(f"kv_block: block has no {missing} field(s)")
        return "\n".join(f"{found[k.lower()][0]}: {found[k.lower()][1]}" for k in keys)

    raise ValueError(f"kv_block: no {scope} block with anchor '{anchor}' found")
