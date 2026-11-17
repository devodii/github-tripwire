"""Extract chosen 'Key: value' fields from a document."""

from __future__ import annotations

import re
from typing import Any

from tripwire import hook

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


@hook(
    "kv_block",
    options={
        "keys": "keys to keep, case-insensitive, emitted in this order (required)",
        "scope": "'fenced' (inside ``` blocks) or 'document' (whole file), default document",
        "anchor": "key that must be present for a block to be chosen, e.g. the preamble's 'SEP'",
    },
)
def kv_block(data: str, options: dict[str, Any]) -> str:
    """Reduce a document to 'Key: value' lines for the requested keys.

    The block is located by content (its anchor key), not line number, and only the first
    occurrence of each key in that block counts, so edits elsewhere never produce a diff.
    """
    keys = options.get("keys")
    scope = options.get("scope", "document")
    anchor = (options.get("anchor") or "").lower()
    if not keys:
        raise ValueError("kv_block: 'keys' is required")
    if scope not in ("fenced", "document"):
        raise ValueError("kv_block: scope must be 'fenced' or 'document'")

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
