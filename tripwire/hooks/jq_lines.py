"""Run a jq query over JSON and emit the results as plain text lines."""

from __future__ import annotations

import json

import jq
from pydantic import Field, field_validator

from tripwire import HookOptions, hook


class JqLinesOptions(HookOptions):
    query: str = Field(description="jq program, run over the whole JSON document")

    @field_validator("query")
    @classmethod
    def _compiles(cls, value: str) -> str:
        try:
            jq.compile(value)
        except ValueError as e:
            raise ValueError(f"not a valid jq program: {e}") from e
        return value


@hook("jq_lines")
def jq_lines(data: str, options: JqLinesOptions) -> str:
    """Run a jq query and print each result on its own line, strings raw (like jq -r).

    urlwatch's built-in jq filter JSON-encodes every result, so text comes out quoted with
    emoji and accents escaped. This one keeps strings as they are and encodes only other
    values, which makes the output readable in an alert.
    """
    try:
        document = json.loads(data)
    except ValueError as e:
        raise ValueError(f"jq_lines: the response is not JSON: {e}") from e
    try:
        results = jq.compile(options.query).input_value(document).all()
    except ValueError as e:
        raise ValueError(f"jq_lines: the query failed on this response: {e}") from e
    return "\n".join(
        r if isinstance(r, str) else json.dumps(r, ensure_ascii=False) for r in results
    )
