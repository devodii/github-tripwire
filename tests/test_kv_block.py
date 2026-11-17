import pytest

from tripwire.hooks.kv_block import KvBlockOptions, kv_block

DOC = """# Release 4

## Metadata

```
Release: 4
Owner: platform
Status: Review (Final)
Version: 0.0.1
```

Body text.
"""
OPTS = {"scope": "fenced", "anchor": "Release", "keys": ["Status", "Version"]}


def run(text: str, opts: dict = OPTS) -> str:
    return kv_block(text, KvBlockOptions.model_validate(opts))


def test_extracts_requested_keys():
    assert run(DOC) == "Status: Review (Final)\nVersion: 0.0.1"


def test_keys_are_case_insensitive_and_keep_requested_order():
    assert run(DOC, {**OPTS, "keys": ["version", "STATUS"]}) == (
        "Version: 0.0.1\nStatus: Review (Final)"
    )


def test_a_value_change_is_reported():
    assert run(DOC.replace("Review (Final)", "Final")) == "Status: Final\nVersion: 0.0.1"


def test_key_moving_to_another_line_does_not_change_output():
    lines = DOC.splitlines()
    i = next(n for n, line in enumerate(lines) if line.startswith("Status:"))
    moved = lines[:i] + lines[i + 1 :]
    moved.insert(i + 1, lines[i])
    assert run("# Banner\n\ntext\n\n" + "\n".join(moved)) == run(DOC)


def test_same_key_elsewhere_in_the_file_is_ignored_in_fenced_scope():
    assert run(DOC + "\n\nStatus: Draft\n") == run(DOC)


def test_document_scope_takes_the_first_occurrence():
    assert run("x\nStatus: A\nStatus: B\n", {"keys": ["Status"]}) == "Status: A"


def test_anchor_picks_the_right_block():
    text = "```\nFoo: 1\n```\n\n```\nRelease: 9\nStatus: Draft\n```\n"
    assert run(text, {**OPTS, "keys": ["Status"]}) == "Status: Draft"


def test_missing_block_raises():
    with pytest.raises(ValueError, match="no fenced block"):
        run("404: Not Found")


def test_missing_key_raises():
    with pytest.raises(ValueError, match="Nope"):
        run(DOC, {**OPTS, "keys": ["Nope"]})


def test_keys_are_required():
    with pytest.raises(ValueError, match="Field required"):
        run(DOC, {})


def test_scope_must_be_a_known_value():
    with pytest.raises(ValueError, match="scope"):
        KvBlockOptions.model_validate({"keys": ["Status"], "scope": "page"})
