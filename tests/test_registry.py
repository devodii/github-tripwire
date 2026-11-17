import pytest
from pydantic import Field
from urlwatch.filters import FilterBase

from tripwire import HookOptions, hook
from tripwire.registry import HOOKS, validate_options


def test_registered_hooks_become_urlwatch_filters_with_documented_options():
    import hooks  # noqa: F401  (the file urlwatch loads)

    for name in ("md_table", "kv_block"):
        assert name in HOOKS
        supported = FilterBase.__subclasses__[name].__supported_subfilters__
        assert set(supported) == set(HOOKS[name].model_fields)
        assert all(supported.values()), "every option needs a description"


def test_a_new_hook_needs_only_a_model_and_a_function():
    class Opts(HookOptions):
        word: str = Field(description="what to shout")

    @hook("test_shout")
    def shout(data: str, options: Opts) -> str:
        """Upper-case the input and append a word."""
        return f"{data.upper()} {options.word}"

    filter_cls = FilterBase.__subclasses__["test_shout"]
    assert filter_cls(None, None).filter("hi", {"word": "!"}) == "HI !"
    with pytest.raises(ValueError, match="test_shout: word: Field required"):
        filter_cls(None, None).filter("hi", {})


def test_hook_without_an_options_model_is_refused():
    with pytest.raises(TypeError, match="HookOptions"):

        @hook("test_bad")
        def bad(data: str, options: dict) -> str:
            return data


def test_validate_options_reports_the_hook_and_field():
    with pytest.raises(ValueError, match=r"md_table: columns: .*at least 1"):
        validate_options("md_table", {"key": "Number", "columns": []})
