import json

from stuckcheck.analyze import FAIL, SKIP, WARN, analyze
from stuckcheck.names import all_checks
from stuckcheck.required import RequiredSet, load_json
from stuckcheck.workflow import load_workflows


def _repo(tmp, files):
    root = tmp / "repo"
    wdir = root / ".github" / "workflows"
    wdir.mkdir(parents=True)
    for name, body in files.items():
        (wdir / name).write_text(body)
    return str(root)


def _run(tmp, files, required, branch="main", merge_queue=False, app=None):
    root = _repo(tmp, files)
    _, wfs = load_workflows(root)
    checks = all_checks(root, wfs)
    rs = RequiredSet()
    rs.merge_queue = merge_queue
    for name in required:
        rs.add(name, app, "test")
    return analyze(rs, checks, branch)


def test_path_filter_fails_the_required_check(tmp_path):
    v = _run(tmp_path, {
        "docs.yml": "on:\n  pull_request:\n    paths: ['docs/**']\njobs:\n  docs:\n    runs-on: u\n    steps: []\n"
    }, ["docs"])[0]
    assert v.verdict == FAIL
    assert any(i.kind == "paths" for i in v.issues)


def test_paths_ignore_fails_too(tmp_path):
    v = _run(tmp_path, {
        "ci.yml": "on:\n  pull_request:\n    paths-ignore: ['**.md']\njobs:\n  lint:\n    runs-on: u\n    steps: []\n"
    }, ["lint"])[0]
    assert v.verdict == FAIL and any(i.kind == "paths" for i in v.issues)


def test_missing_synchronize_fails(tmp_path):
    v = _run(tmp_path, {
        "e2e.yml": "on:\n  pull_request:\n    types: [opened, reopened]\njobs:\n  e2e:\n    runs-on: u\n    steps: []\n"
    }, ["e2e"])[0]
    assert v.verdict == FAIL and any(i.kind == "types" for i in v.issues)


def test_branches_that_exclude_the_protected_branch(tmp_path):
    v = _run(tmp_path, {
        "ci.yml": "on:\n  pull_request:\n    branches: [develop]\njobs:\n  lint:\n    runs-on: u\n    steps: []\n"
    }, ["lint"], branch="main")[0]
    assert v.verdict == FAIL and any(i.kind == "branches" for i in v.issues)


def test_branches_that_include_the_protected_branch_are_ok(tmp_path):
    v = _run(tmp_path, {
        "ci.yml": "on:\n  pull_request:\n    branches: [main, develop]\njobs:\n  lint:\n    runs-on: u\n    steps: []\n"
    }, ["lint"], branch="main")[0]
    assert v.verdict == "OK"


def test_branches_ignore_matching_protected_branch(tmp_path):
    v = _run(tmp_path, {
        "ci.yml": "on:\n  pull_request:\n    branches-ignore: [main]\njobs:\n  lint:\n    runs-on: u\n    steps: []\n"
    }, ["lint"], branch="main")[0]
    assert v.verdict == FAIL


def test_workflow_dispatch_never_counts(tmp_path):
    v = _run(tmp_path, {
        "rel.yml": "on: workflow_dispatch\njobs:\n  package:\n    runs-on: u\n    steps: []\n"
    }, ["package"])[0]
    assert v.verdict == FAIL and any(i.kind == "event" for i in v.issues)


def test_missing_name_is_fail_when_actions_is_the_expected_app(tmp_path):
    v = _run(tmp_path, {
        "ci.yml": "on: pull_request\njobs:\n  lint:\n    runs-on: u\n    steps: []\n"
    }, ["lnt"], app=15368)[0]
    assert v.verdict == FAIL and v.suggestions == ["lint"]


def test_missing_name_is_warn_when_any_app_is_allowed(tmp_path):
    v = _run(tmp_path, {
        "ci.yml": "on: pull_request\njobs:\n  lint:\n    runs-on: u\n    steps: []\n"
    }, ["Vercel"])[0]
    assert v.verdict == WARN and any(i.kind == "missing" for i in v.issues)


def test_other_apps_are_skipped(tmp_path):
    v = _run(tmp_path, {
        "ci.yml": "on: pull_request\njobs:\n  lint:\n    runs-on: u\n    steps: []\n"
    }, ["Vercel"], app=8329)[0]
    assert v.verdict == SKIP


def test_push_only_is_a_warning(tmp_path):
    v = _run(tmp_path, {
        "ci.yml": "on:\n  push:\n    branches: [main]\njobs:\n  lint:\n    runs-on: u\n    steps: []\n"
    }, ["lint"])[0]
    assert v.verdict == WARN and any(i.kind == "push-only" for i in v.issues)


def test_tag_only_push_fails(tmp_path):
    v = _run(tmp_path, {
        "ci.yml": "on:\n  push:\n    tags: ['v*']\njobs:\n  lint:\n    runs-on: u\n    steps: []\n"
    }, ["lint"])[0]
    assert v.verdict == FAIL


def test_merge_queue_needs_merge_group(tmp_path):
    v = _run(tmp_path, {
        "ci.yml": "on: pull_request\njobs:\n  lint:\n    runs-on: u\n    steps: []\n"
    }, ["lint"], merge_queue=True)[0]
    assert v.verdict == FAIL and any(i.kind == "merge_group" for i in v.issues)


def test_merge_queue_ok_when_merge_group_is_present(tmp_path):
    v = _run(tmp_path, {
        "ci.yml": "on:\n  pull_request:\n  merge_group:\njobs:\n  lint:\n    runs-on: u\n    steps: []\n"
    }, ["lint"], merge_queue=True)[0]
    assert v.verdict == "OK"


def test_needs_without_always_warns(tmp_path):
    v = _run(tmp_path, {
        "ci.yml": (
            "on: pull_request\njobs:\n"
            "  lint:\n    runs-on: u\n    steps: []\n"
            "  test:\n    runs-on: u\n    steps: []\n"
            "  build:\n    needs: [lint, test]\n    runs-on: u\n    steps: []\n"
        )
    }, ["build"])[0]
    assert v.verdict == WARN and any(i.kind == "needs" for i in v.issues)


def test_needs_with_always_does_not_warn(tmp_path):
    v = _run(tmp_path, {
        "ci.yml": (
            "on: pull_request\njobs:\n"
            "  lint:\n    runs-on: u\n    steps: []\n"
            "  build:\n    needs: lint\n    if: always()\n    runs-on: u\n    steps: []\n"
        )
    }, ["build"])[0]
    assert v.verdict == "OK"


def test_matrix_name_typo_suggests_the_real_names(tmp_path):
    v = _run(tmp_path, {
        "ci.yml": (
            "on: pull_request\njobs:\n  test:\n    runs-on: u\n"
            "    strategy:\n      matrix:\n        py: ['3.9', '3.10']\n    steps: []\n"
        )
    }, ["test"], app=15368)[0]
    assert v.verdict == FAIL
    assert v.suggestions[:2] == ["test (3.9)", "test (3.10)"]


def test_fromjson_name_matches_as_a_pattern(tmp_path):
    v = _run(tmp_path, {
        "ci.yml": (
            "on: pull_request\njobs:\n  test:\n    runs-on: u\n"
            "    strategy:\n      matrix: ${{ fromJSON(vars.m) }}\n    steps: []\n"
        )
    }, ["test (ubuntu-latest, 3.12)"])[0]
    assert v.producers and not v.producers[0].exact
    assert any(i.kind == "pattern" for i in v.issues)


def test_ruleset_json_roundtrip():
    rs = RequiredSet()
    load_json([
        {"type": "required_status_checks",
         "parameters": {"required_status_checks": [
             {"context": "lint", "integration_id": 15368},
             {"context": "Vercel", "integration_id": 8329},
         ]}},
        {"type": "merge_queue"},
    ], rs, "rules")
    assert rs.merge_queue is True
    assert [r.context for r in rs.checks] == ["lint", "Vercel"]
    assert rs.checks[0].app_id == 15368


def test_classic_protection_json():
    rs = RequiredSet()
    load_json({
        "checks": [{"context": "lint", "app_id": 15368}],
        "contexts": ["lint", "legacy"],
    }, rs, "prot")
    assert [r.context for r in rs.checks] == ["lint", "legacy"]
