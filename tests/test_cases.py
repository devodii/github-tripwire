"""Runs every watchers/*/case*.yaml against the watcher.yaml beside it. Knows no watcher."""

import pytest
from conftest import ROOT
from replay import Snapshots, run_case

CASES = sorted((ROOT / "watchers").glob("*/case*.yaml"))


def test_there_is_at_least_one_case():
    assert CASES, "watchers/*/case*.yaml not found"


@pytest.mark.parametrize("case_file", CASES, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_case(case_file, tmp_path, capfd, database):
    run_case(case_file, tmp_path, capfd, Snapshots(database))
