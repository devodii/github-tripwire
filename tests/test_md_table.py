import pytest

from tripwire.hooks.md_table import MdTableOptions, md_table

DOC = """# Status page

Some intro text.

| Service         | Owner | State    |
| --------------- | ----- | -------- |
| [api](api.md)   | core  | Stable   |
| [web](web.md)   | apps  | Beta     |
| [jobs](jobs.md) | core  | Degraded |

## Unrelated table

| Name | Colour |
| ---- | ------ |
| sky  | blue   |
"""
OPTS = {"key": "Service", "columns": ["State"], "key_regex": r"\[(\w+)\]"}


def run(text: str, opts: dict = OPTS) -> str:
    return md_table(text, MdTableOptions.model_validate(opts))


def test_extracts_sorted_keyed_rows_and_ignores_other_tables():
    assert run(DOC).splitlines() == ["api | Stable", "jobs | Degraded", "web | Beta"]


def test_multiple_columns_and_no_key_regex():
    out = run(DOC, {"key": "Service", "columns": ["Owner", "State"]}).splitlines()
    assert "[api](api.md) | core | Stable" in out


def test_header_names_are_case_insensitive():
    assert run(DOC, {**OPTS, "key": "service", "columns": ["STATE"]}) == run(DOC)


def test_a_value_change_is_the_only_difference():
    changed = DOC.replace("Beta    ", "Stable  ")
    assert set(run(DOC).splitlines()) ^ set(run(changed).splitlines()) == {
        "web | Beta",
        "web | Stable",
    }


def test_shifted_lines_do_not_change_output():
    assert run("# New heading\n\nnew\nlines\n\n" + DOC) == run(DOC)


def test_reordered_rows_do_not_change_output():
    lines = DOC.splitlines()
    first = next(i for i, line in enumerate(lines) if line.startswith("| [api"))
    lines[first : first + 3] = reversed(lines[first : first + 3])
    assert run("\n".join(lines)) == run(DOC)


def test_reflowed_column_widths_do_not_change_output():
    squeezed = "\n".join(" ".join(x.split()) if x.startswith("|") else x for x in DOC.splitlines())
    assert run(squeezed) == run(DOC)


def test_rows_moving_between_tables_do_not_change_output():
    first, second = "| Service | Owner | State |\n| - | - | - |\n", "\n\n"
    split = first + "| [api] | core | Stable |\n" + second + first + "| [web] | apps | Beta |\n"
    joined = first + "| [web] | apps | Beta |\n| [api] | core | Stable |\n"
    assert run(split) == run(joined)


def test_tables_without_the_wanted_columns_are_ignored():
    assert run("| a | b |\n| - | - |\n| 1 | 2 |\n", {"key": "a", "columns": ["b"]}) == "1 | 2"
    with pytest.raises(ValueError, match="no table with columns"):
        run("| a | b |\n| - | - |\n| 1 | 2 |\n")


def test_missing_options_are_rejected():
    for opts in ({}, {"key": "Id"}, {"columns": ["State"]}):
        with pytest.raises(ValueError, match="Field required"):
            run(DOC, opts)


def test_no_matching_table_raises_instead_of_returning_empty():
    with pytest.raises(ValueError, match="no table with columns"):
        run("404: Not Found")


def test_invalid_key_regex_is_rejected_when_options_are_parsed():
    with pytest.raises(ValueError, match="key_regex"):
        MdTableOptions.model_validate({**OPTS, "key_regex": "(unclosed"})


def test_unknown_option_is_rejected():
    with pytest.raises(ValueError, match="keyy"):
        MdTableOptions.model_validate({"keyy": "Id", "columns": ["State"]})
