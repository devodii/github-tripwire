"""The hook interface.

A hook is a plain function ``(data, options) -> str`` registered under a name.
Once registered, it can be used in ``urls.yaml`` like any built-in urlwatch filter::

    filter:
      - my_hook
      - my_hook:
          some_option: value

To add a hook, create ``tripwire/hooks/<name>.py`` and decorate a function with
``@hook``. Modules in that package are discovered automatically.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable
from typing import Any

from urlwatch.filters import FilterBase

HookFn = Callable[[str, dict[str, Any]], str]


def hook(name: str, *, options: dict[str, str] | None = None) -> Callable[[HookFn], HookFn]:
    """Register ``fn`` as a urlwatch filter called ``name``.

    ``options`` maps each accepted option to a short description. urlwatch rejects
    unknown options at config load time, so typos in urls.yaml fail loudly.
    The function receives the option dict (empty when none were given) and must
    return the transformed text. Raise ``ValueError`` for unparseable input so
    urlwatch reports an error instead of recording a bogus empty snapshot.
    """

    def register(fn: HookFn) -> HookFn:
        def filter_method(self: FilterBase, data: str, subfilter: Any) -> str:
            return fn(data, subfilter if isinstance(subfilter, dict) else {})

        attrs: dict[str, Any] = {
            "__kind__": name,
            "__doc__": (fn.__doc__ or name).strip().splitlines()[0],
            "filter": filter_method,
        }
        if options:
            attrs["__supported_subfilters__"] = dict(options)
        else:
            attrs["__no_subfilter__"] = True
        type(f"Hook_{name}", (FilterBase,), attrs)
        return fn

    return register


def load_all() -> list[str]:
    """Import every module in ``tripwire.hooks`` so its hooks get registered."""
    package = importlib.import_module("tripwire.hooks")
    names = sorted(m.name for m in pkgutil.iter_modules(package.__path__))
    for module in names:
        importlib.import_module(f"tripwire.hooks.{module}")
    return names
