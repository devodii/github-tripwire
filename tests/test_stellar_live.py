"""Hits raw.githubusercontent.com. Run with `pytest -m live`, skipped by default in CI."""

import urllib.request

import pytest
from conftest import ROOT, fixture

from tripwire.hooks.kv_block import KvBlockOptions, kv_block
from tripwire.hooks.md_table import MdTableOptions, md_table
from tripwire.spec import load_watchers

pytestmark = pytest.mark.live

RAW = "https://raw.githubusercontent.com/stellar/stellar-protocol"
BEFORE_SHA = "6c3fb1290ccd04f30352ffe83319d67eb992eb40"
AFTER_SHA = "b1933843d71eea2a5b537b5a1af6592c4bdc6afe"  # "SEP-53: Change Status to Final (#1963)"


def get(ref: str, path: str) -> str:
    try:
        with urllib.request.urlopen(f"{RAW}/{ref}/ecosystem/{path}", timeout=20) as r:
            return r.read().decode()
    except OSError as e:
        pytest.skip(f"network unavailable: {e}")


def options(watcher_name: str):
    watcher = next(w for w in load_watchers(ROOT / "watchers") if w.name == watcher_name)
    return next(iter(watcher.filter[0].values()))


def test_fixtures_are_byte_identical_to_the_pinned_commits():
    assert get(BEFORE_SHA, "README.md") == fixture("ecosystem-readme.before.md")
    assert get(AFTER_SHA, "sep-0053.md") == fixture("sep-0053.after.md")


def test_pinned_commits_produce_the_sep_53_transition_via_the_real_watcher_config():
    opts = MdTableOptions.model_validate(options("stellar SEP index statuses"))
    before = set(md_table(get(BEFORE_SHA, "README.md"), opts).splitlines())
    after = set(md_table(get(AFTER_SHA, "README.md"), opts).splitlines())
    assert before - after == {"SEP-0053 | Final Comment Period (Final)"}
    assert after - before == {"SEP-0053 | Final"}


def test_current_master_still_matches_what_the_watchers_expect():
    index = MdTableOptions.model_validate(options("stellar SEP index statuses"))
    assert "SEP-0001 | Active" in md_table(get("master", "README.md"), index).splitlines()
    sep = KvBlockOptions.model_validate(options("stellar SEP-0053 preamble"))
    assert kv_block(get("master", "sep-0053.md"), sep).startswith("Status: ")
