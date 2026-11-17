"""The hook interface.

A hook is a function ``(data: str, options: SomeOptions) -> str`` where ``SomeOptions`` is a
pydantic model subclassing ``HookOptions``. Registering it makes it usable in a watcher's
``filter:`` list like any built-in urlwatch filter::

    filter:
      - my_hook:
          some_option: value

To add a hook, create ``tripwire/hooks/<name>.py``: declare the options model, decorate the
function with ``@hook("name")``. Modules in that package are discovered automatically.
The options model is the single source of truth: it validates watcher YAML at ``check``
time and at run time, and documents the options (``python -m tripwire hooks``).
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from typing import Any, get_type_hints

from pydantic import BaseModel, ConfigDict, ValidationError
from urlwatch.filters import FilterBase


class HookOptions(BaseModel):
    """Base class for hook options. Unknown keys are rejected so typos fail loudly."""

    model_config = ConfigDict(extra="forbid")


HOOKS: dict[str, type[HookOptions]] = {}


def format_errors(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or 'options'}: {e['msg']}" for e in error.errors()
    )


def validate_options(name: str, raw: Any) -> HookOptions:
    """Parse raw YAML options for hook ``name``; raises ValueError with a readable message."""
    try:
        return HOOKS[name].model_validate(raw or {})
    except ValidationError as e:
        raise ValueError(f"{name}: {format_errors(e)}") from e


def hook(name: str) -> Callable[[Callable[..., str]], Callable[..., str]]:
    """Register ``fn`` as a urlwatch filter called ``name``.

    Raise ``ValueError`` for unparseable input so urlwatch reports an error instead of
    recording a bogus empty snapshot.
    """

    def register(fn: Callable[..., str]) -> Callable[..., str]:
        model = get_type_hints(fn).get("options")
        if not (isinstance(model, type) and issubclass(model, HookOptions)):
            raise TypeError(f"hook '{name}': 'options' must be annotated with a HookOptions model")

        def filter_method(self: FilterBase, data: str, subfilter: Any) -> str:
            return fn(data, validate_options(name, subfilter))

        attrs: dict[str, Any] = {
            "__kind__": name,
            "__doc__": (fn.__doc__ or name).strip().splitlines()[0],
            "filter": filter_method,
        }
        if model.model_fields:
            attrs["__supported_subfilters__"] = {
                key: field.description or "" for key, field in model.model_fields.items()
            }
        else:
            attrs["__no_subfilter__"] = True
        type(f"Hook_{name}", (FilterBase,), attrs)
        HOOKS[name] = model
        return fn

    return register


def load_all() -> list[str]:
    """Import every module in ``tripwire.hooks`` so its hooks get registered."""
    package = importlib.import_module("tripwire.hooks")
    names = sorted(m.name for m in pkgutil.iter_modules(package.__path__))
    for module in names:
        importlib.import_module(f"tripwire.hooks.{module}")
    return names
