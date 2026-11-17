import pytest
from conftest import fixture

from tripwire.hooks.md_table import MdTableOptions, md_table

# stellar-protocol b193384 "SEP-53: Change Status to Final" and its parent 6c3fb12
BEFORE = fixture("ecosystem-readme.before.md")
AFTER = fixture("ecosystem-readme.after.md")
OPTS = {"key": "Number", "columns": ["Status"], "key_regex": r"(SEP-\d+)"}


def run(text: str, opts: dict) -> str:
    return md_table(text, MdTableOptions.model_validate(opts))


def rows(text: str, opts=OPTS) -> set[str]:
    return set(run(text, opts).splitlines())


def test_real_commit_changes_only_sep_53_and_survives_a_full_table_reflow():
    before, after = rows(BEFORE), rows(AFTER)
    assert before - after == {"SEP-0053 | Final Comment Period (Final)"}
    assert after - before == {"SEP-0053 | Final"}
    assert len(before) == len(after) == 53


def test_output_is_sorted():
    out = run(AFTER, OPTS).splitlines()
    assert out == sorted(out)
    assert "SEP-0001 | Active" in out


def test_multiple_columns_and_no_key_regex():
    out = run(AFTER, {"key": "Number", "columns": ["Title", "Status"]}).splitlines()
    assert "[SEP-0001](sep-0001.md) | Stellar Info File | Active" in out


def test_header_names_are_case_insensitive():
    assert run(AFTER, {**OPTS, "key": "number", "columns": ["STATUS"]}) == run(AFTER, OPTS)


def test_shifted_lines_do_not_change_output():
    assert run("# Intro\n\nnew\nlines\n\n" + AFTER, OPTS) == run(AFTER, OPTS)


def test_reordered_rows_do_not_change_output():
    lines = AFTER.splitlines()
    first = next(i for i, line in enumerate(lines) if line.startswith("| [SEP"))
    lines[first : first + 5] = reversed(lines[first : first + 5])
    assert run("\n".join(lines), OPTS) == run(AFTER, OPTS)


def test_reflowed_column_widths_do_not_change_output():
    squeezed = "\n".join(
        " ".join(line.split()) if line.startswith("|") else line for line in AFTER.splitlines()
    )
    assert run(squeezed, OPTS) == run(AFTER, OPTS)


def test_tables_without_the_wanted_columns_are_ignored():
    text = "| a | b |\n| - | - |\n| 1 | 2 |\n\n| Id | State |\n| -- | ----- |\n| x1 | open |\n"
    assert run(text, {"key": "Id", "columns": ["State"]}) == "x1 | open"


@pytest.mark.parametrize("opts", [{}, {"key": "Number"}, {"columns": ["Status"]}])
def test_missing_options_are_rejected(opts):
    with pytest.raises(ValueError, match="required"):
        run(AFTER, opts)


def test_no_matching_table_raises_instead_of_returning_empty():
    with pytest.raises(ValueError, match="no table with columns"):
        run("404: Not Found", OPTS)


def test_invalid_key_regex_is_rejected_when_options_are_parsed():
    with pytest.raises(ValueError, match="key_regex"):
        MdTableOptions.model_validate({**OPTS, "key_regex": "(unclosed"})


def test_unknown_option_is_rejected():
    with pytest.raises(ValueError, match="keyy"):
        MdTableOptions.model_validate({"keyy": "Number", "columns": ["Status"]})
