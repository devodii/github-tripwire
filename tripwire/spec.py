"""The watcher spec: what a file in watchers/ may contain.

Each watcher lives in its own folder, ``watchers/<name>/``, next to its replay case and
fixtures. ``watcher.yaml`` holds one or more YAML documents (separated by ``---``), one per
job. Every document is parsed into ``Watcher``; see docs/WATCHERS.md for the prose version.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError, field_validator
from urlwatch.filters import FilterBase

from tripwire.registry import HOOKS, format_errors, load_all, validate_options

FilterStep = str | dict[str, Any]
WATCHER_FILE = "watcher.yaml"


class Watcher(BaseModel):
    """One watched resource: fetch ``url``, run ``filter`` over it, alert when the result changes."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, description="unique, shown in alerts")
    every: str = Field(
        min_length=1, description="cadence name, a workflow runs the watchers of one cadence"
    )
    url: HttpUrl = Field(description="what to fetch")
    filter: list[FilterStep] = Field(
        default_factory=list,
        description="steps applied in order: a hook or urlwatch filter name, or {name: options}",
    )
    method: Literal["GET", "POST"] | None = None
    headers: dict[str, str] | None = Field(default=None, description="request headers")
    data: str | None = Field(default=None, description="request body for POST")
    additions_only: bool | None = Field(
        default=None,
        description="report only lines that appear, so items aging out of a feed never alert",
    )

    @field_validator("filter")
    @classmethod
    def _filters_exist_and_have_valid_options(cls, steps: list[FilterStep]) -> list[FilterStep]:
        load_all()
        for step in steps:
            if isinstance(step, dict) and len(step) != 1:
                raise ValueError(f"a filter step is a single 'name: options' pair, got {step}")
            name, options = next(iter(step.items())) if isinstance(step, dict) else (step, None)
            if name in HOOKS:
                validate_options(name, options)
            else:  # a built-in urlwatch filter such as html2text, grep, jq
                list(FilterBase.normalize_filter_list([step]))
        return steps


class SpecError(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


def load_watchers(path: Path) -> list[Watcher]:
    """Parse a watcher file, or every ``watcher.yaml`` under a folder. Raises SpecError."""
    files = sorted(path.rglob(WATCHER_FILE)) if path.is_dir() else [path]
    watchers: list[Watcher] = []
    problems: list[str] = []
    seen: dict[str, Path] = {}
    for file in files:
        for n, doc in enumerate(d for d in yaml.safe_load_all(file.read_text()) if d is not None):
            label = f"{file}: job #{n + 1}"
            try:
                watcher = Watcher.model_validate(doc)
            except ValidationError as e:
                problems.append(f"{label}: {format_errors(e)}")
                continue
            if watcher.name in seen:
                problems.append(
                    f"{label}: duplicate name '{watcher.name}' (also in {seen[watcher.name]})"
                )
            seen[watcher.name] = file
            watchers.append(watcher)
    if not watchers and not problems:
        problems.append(f"no watchers found in {path}")
    if problems:
        raise SpecError(problems)
    return watchers


def select(watchers: list[Watcher], every: str | None) -> list[Watcher]:
    """The watchers of one cadence, or all of them when ``every`` is None."""
    return [w for w in watchers if every is None or w.every == every]


_VAR = re.compile(r"\$\{(\w+)\}")


def expand_secrets(watchers: list[Watcher], env: Mapping[str, str] = os.environ) -> list[Watcher]:
    """Fill ``${NAME}`` in header values and the request body from the environment.

    This keeps tokens out of watcher files. A variable that is unset or empty is an error
    naming the watcher, because a request sent without its credentials would only fail later
    with a confusing 401.
    """
    expanded, problems = [], []
    for watcher in watchers:
        missing: list[str] = []

        def fill(text: str, missing: list[str] = missing) -> str:
            def sub(match: re.Match[str]) -> str:
                value = env.get(match.group(1), "")
                if not value:
                    missing.append(match.group(1))
                return value

            return _VAR.sub(sub, text)

        update: dict[str, Any] = {}
        if watcher.headers:
            update["headers"] = {k: fill(v) for k, v in watcher.headers.items()}
        if watcher.data:
            update["data"] = fill(watcher.data)
        if missing:
            problems.append(f"watcher '{watcher.name}' needs {', '.join(sorted(set(missing)))}")
        expanded.append(watcher.model_copy(update=update))
    if problems:
        raise SpecError(problems)
    return expanded
