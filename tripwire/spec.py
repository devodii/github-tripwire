"""The watcher spec: what a file in watchers/ may contain.

Each target lives in its own folder, ``watchers/<name>/``, next to its replay cases.
``watcher.yaml`` holds YAML documents (separated by ``---``). A document is either one job,
parsed into ``Watcher``, or a ``uses:`` document that stamps out the jobs of a template from
``templates/`` with parameters (``Use``). See docs/WATCHERS.md for the prose version.
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
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


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


class Use(BaseModel):
    """Stamp out the watchers of a template: ``uses: github-repo-feed`` plus ``with:`` values."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    uses: str = Field(min_length=1, description="template name: a file in templates/, no .yaml")
    with_: dict[str, str] = Field(
        default_factory=dict, alias="with", description="the template's parameters"
    )


class Param(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str
    default: str | None = Field(default=None, description="omit to make the parameter required")


class TemplateHeader(BaseModel):
    """The first document of a template file: which parameters it takes."""

    model_config = ConfigDict(extra="forbid")
    params: dict[str, Param]


_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")
_PARAM_VALUE = re.compile(r"[\w. -]+")


def expand_use(use: Use, templates_dir: Path = TEMPLATES_DIR) -> list[dict[str, Any]]:
    """The job documents of ``use``'s template with its ``{{parameters}}`` filled in."""
    file = templates_dir / f"{use.uses}.yaml"
    if not file.is_file():
        known = ", ".join(sorted(f.stem for f in templates_dir.glob("*.yaml"))) or "none"
        raise ValueError(f"unknown template '{use.uses}' (available: {known})")
    header, *jobs = [d for d in yaml.safe_load_all(file.read_text()) if d is not None]
    params = TemplateHeader.model_validate(header).params

    if unknown := sorted(set(use.with_) - set(params)):
        raise ValueError(
            f"template '{use.uses}' has no parameter {unknown}, takes {sorted(params)}"
        )
    values = {k: p.default for k, p in params.items() if p.default is not None} | use.with_
    if missing := sorted(set(params) - set(values)):
        raise ValueError(f"template '{use.uses}' needs {missing} under 'with:'")
    for key, value in values.items():
        if not _PARAM_VALUE.fullmatch(value):
            raise ValueError(
                f"parameter '{key}' may only contain letters, digits, . _ - and spaces"
            )

    def fill(node: Any) -> Any:
        if isinstance(node, str):

            def sub(match: re.Match[str]) -> str:
                if match.group(1) not in values:
                    raise ValueError(
                        f"template '{use.uses}' uses undeclared {{{{{match.group(1)}}}}}"
                    )
                return values[match.group(1)]

            return _PLACEHOLDER.sub(sub, node)
        if isinstance(node, list):
            return [fill(x) for x in node]
        if isinstance(node, dict):
            return {k: fill(v) for k, v in node.items()}
        return node

    return [fill(job) for job in jobs]


class SpecError(Exception):
    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


def load_watchers(path: Path, templates_dir: Path = TEMPLATES_DIR) -> list[Watcher]:
    """Parse a watcher file, or every ``watcher.yaml`` under a folder. Raises SpecError."""
    files = sorted(path.rglob(WATCHER_FILE)) if path.is_dir() else [path]
    watchers: list[Watcher] = []
    problems: list[str] = []
    seen: dict[str, Path] = {}
    for file in files:
        for n, doc in enumerate(d for d in yaml.safe_load_all(file.read_text()) if d is not None):
            label = f"{file}: job #{n + 1}"
            try:
                if isinstance(doc, dict) and "uses" in doc:
                    use = Use.model_validate(doc)
                    label += f" (template {use.uses})"
                    jobs = expand_use(use, templates_dir)
                else:
                    jobs = [doc]
                parsed = [Watcher.model_validate(job) for job in jobs]
            except ValidationError as e:
                problems.append(f"{label}: {format_errors(e)}")
                continue
            except ValueError as e:
                problems.append(f"{label}: {e}")
                continue
            for watcher in parsed:
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
