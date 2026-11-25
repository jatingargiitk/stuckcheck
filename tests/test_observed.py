"""Check names against what GitHub actually reported.

tests/fixtures/observed-*/ hold probe workflows that were pushed to a branch of
this repository on 2026-11-25; check-runs.txt is the list of check-run names
GitHub created for that commit (``gh api repos/.../commits/<sha>/check-runs``).
"""

import os

import pytest

from stuckcheck.names import all_checks
from stuckcheck.workflow import load_workflows

HERE = os.path.join(os.path.dirname(__file__), "fixtures")
CASES = sorted(d for d in os.listdir(HERE) if d.startswith("observed-"))


@pytest.mark.parametrize("case", CASES)
def test_names_match_what_github_reported(case):
    root = os.path.join(HERE, case)
    with open(os.path.join(root, "check-runs.txt"), encoding="utf-8") as fh:
        observed = {line.rstrip("\n") for line in fh if line.strip()}
    _, wfs = load_workflows(root)
    checks = all_checks(root, wfs)
    exact = {c.name for c in checks if c.exact}
    patterns = [c for c in checks if not c.exact]
    assert exact <= observed, sorted(exact - observed)
    for name in observed:
        assert name in exact or any(p.matches(name) for p in patterns), name
