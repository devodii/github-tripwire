import pytest
from conftest import fixture

from tripwire.hooks.kv_block import KvBlockOptions, kv_block

# stellar-protocol b193384 "SEP-53: Change Status to Final" and its parent 6c3fb12
BEFORE = fixture("sep-0053.before.md")
AFTER = fixture("sep-0053.after.md")
OPTS = {"scope": "fenced", "anchor": "SEP", "keys": ["Status", "Version"]}


def run(text: str, opts: dict) -> str:
    return kv_block(text, KvBlockOptions.model_validate(opts))


def test_real_commit_status_and_version_transition():
    assert run(BEFORE, OPTS) == "Status: Final Comment Period (Final)\nVersion: 0.0.1"
    assert run(AFTER, OPTS) == "Status: Final\nVersion: 1.0.0"


def test_keys_are_case_insensitive_and_keep_requested_order():
    out = run(AFTER, {**OPTS, "keys": ["version", "STATUS"]})
    assert out == "Version: 1.0.0\nStatus: Final"


def test_status_moving_to_another_line_does_not_change_output():
    lines = AFTER.splitlines()
    i = next(n for n, line in enumerate(lines) if line.startswith("Status:"))
    moved = lines[:i] + lines[i + 1 :]
    moved.insert(i + 3, lines[i])
    assert run("# Heading\n\ntext\n\n" + "\n".join(moved), OPTS) == run(AFTER, OPTS)


def test_same_key_elsewhere_in_the_file_is_ignored_in_fenced_scope():
    assert run(AFTER + "\n\nStatus: Draft\n", OPTS) == run(AFTER, OPTS)


def test_document_scope_takes_the_first_occurrence():
    assert run("x\nStatus: A\nStatus: B\n", {"keys": ["Status"]}) == "Status: A"


def test_anchor_picks_the_right_block():
    text = "```\nFoo: 1\n```\n\n```\nSEP: 9\nStatus: Draft\n```\n"
    assert run(text, OPTS | {"keys": ["Status"]}) == "Status: Draft"


def test_missing_block_raises():
    with pytest.raises(ValueError, match="no fenced block"):
        run("404: Not Found", OPTS)


def test_missing_key_raises():
    with pytest.raises(ValueError, match="Nope"):
        run(AFTER, {**OPTS, "keys": ["Nope"]})


def test_keys_are_required():
    with pytest.raises(ValueError, match="required"):
        run(AFTER, {})


def test_scope_must_be_a_known_value():
    with pytest.raises(ValueError, match="scope"):
        KvBlockOptions.model_validate({"keys": ["Status"], "scope": "page"})
