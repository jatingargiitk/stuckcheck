from stuckcheck.names import Check, all_checks, eval_expr, expand_matrix, fmt, render, UNKNOWN
from stuckcheck.workflow import load_workflows, parse_workflow


def test_yaml_on_is_not_a_boolean(tmp_path):
    p = tmp_path / "w.yml"
    p.write_text("name: CI\non:\n  pull_request:\njobs:\n  t:\n    runs-on: ubuntu-latest\n    steps: []\n")
    wf = parse_workflow(str(p), "w.yml")
    assert wf.error is None
    assert "pull_request" in wf.events


def test_unquoted_310_is_the_float_github_parses(tmp_path):
    # GitHub's YAML also turns 3.10 into 3.1; the job name is then "test (3.1)".
    p = tmp_path / "w.yml"
    p.write_text(
        "on: pull_request\n"
        "jobs:\n  test:\n    runs-on: ubuntu-latest\n"
        "    strategy:\n      matrix:\n        python: [3.9, 3.10, 3.11]\n"
        "    steps: []\n"
    )
    root, wfs = load_workflows(_wrap(tmp_path, [p]))
    names = [c.name for c in all_checks(root, wfs)]
    assert names == ["test (3.9)", "test (3.1)", "test (3.11)"]


def test_quoted_310_keeps_the_string(tmp_path):
    p = tmp_path / "w.yml"
    p.write_text(
        "on: pull_request\n"
        "jobs:\n  test:\n    runs-on: ubuntu-latest\n"
        "    strategy:\n      matrix:\n        python: ['3.9', '3.10']\n"
        "    steps: []\n"
    )
    root, wfs = load_workflows(_wrap(tmp_path, [p]))
    names = [c.name for c in all_checks(root, wfs)]
    assert names == ["test (3.9)", "test (3.10)"]


def test_named_matrix_without_matrix_in_the_name_appends_values(tmp_path):
    p = tmp_path / "w.yml"
    p.write_text(
        "on: pull_request\n"
        "jobs:\n  test:\n    name: Tests\n    runs-on: ubuntu-latest\n"
        "    strategy:\n      matrix:\n        os: [ubuntu-latest, macos-latest]\n"
        "        py: ['3.12']\n"
        "    steps: []\n"
    )
    root, wfs = load_workflows(_wrap(tmp_path, [p]))
    names = [c.name for c in all_checks(root, wfs)]
    assert names == ["Tests (ubuntu-latest, 3.12)", "Tests (macos-latest, 3.12)"]


def test_name_that_uses_matrix_is_not_suffixed(tmp_path):
    p = tmp_path / "w.yml"
    p.write_text(
        "on: pull_request\n"
        "jobs:\n  test:\n    name: test (${{ matrix.py }})\n    runs-on: ubuntu-latest\n"
        "    strategy:\n      matrix:\n        py: ['3.9', '3.12']\n"
        "    steps: []\n"
    )
    root, wfs = load_workflows(_wrap(tmp_path, [p]))
    names = [c.name for c in all_checks(root, wfs)]
    assert names == ["test (3.9)", "test (3.12)"]


def test_github_include_example():
    # https://docs.github.com/en/actions/using-jobs/using-a-matrix-for-your-jobs#expanding-or-adding-matrix-configurations
    m = {
        "fruit": ["apple", "pear"],
        "animal": ["cat", "dog"],
        "include": [
            {"color": "green"},
            {"color": "pink", "animal": "cat"},
            {"fruit": "apple", "shape": "circle"},
            {"fruit": "banana"},
            {"fruit": "banana", "animal": "cat"},
        ],
    }
    assert expand_matrix(m) == [
        {"fruit": "apple", "animal": "cat", "color": "pink", "shape": "circle"},
        {"fruit": "apple", "animal": "dog", "color": "green", "shape": "circle"},
        {"fruit": "pear", "animal": "cat", "color": "pink"},
        {"fruit": "pear", "animal": "dog", "color": "green"},
        {"fruit": "banana"},
        {"fruit": "banana", "animal": "cat"},
    ]


def test_exclude_drops_combinations():
    m = {"os": ["u", "w"], "py": [1, 2], "exclude": [{"os": "w", "py": 1}]}
    assert expand_matrix(m) == [{"os": "u", "py": 1}, {"os": "u", "py": 2}, {"os": "w", "py": 2}]


def test_fromjson_matrix_is_a_wildcard(tmp_path):
    p = tmp_path / "w.yml"
    p.write_text(
        "on: pull_request\n"
        "jobs:\n  test:\n    runs-on: ubuntu-latest\n"
        "    strategy:\n      matrix: ${{ fromJSON(needs.setup.outputs.m) }}\n"
        "    steps: []\n"
    )
    root, wfs = load_workflows(_wrap(tmp_path, [p]))
    checks = all_checks(root, wfs)
    assert len(checks) == 1 and checks[0].name == "test (*)" and checks[0].exact is False
    assert checks[0].matches("test (ubuntu-latest, 3.12)")
    assert not checks[0].matches("lint")


def test_reusable_workflow_prefixes_called_job_names(tmp_path):
    called = tmp_path / "called.yml"
    called.write_text(
        "on:\n  workflow_call:\n    inputs:\n      node:\n        type: string\n        required: true\n"
        "jobs:\n  browser:\n    name: ${{ matrix.browser }} on node ${{ inputs.node }}\n"
        "    runs-on: ubuntu-latest\n"
        "    strategy:\n      matrix:\n        browser: [chromium, firefox]\n"
        "    steps: []\n"
    )
    caller = tmp_path / "e2e.yml"
    caller.write_text(
        "on: pull_request\n"
        "jobs:\n  e2e:\n    uses: ./called.yml\n    with:\n      node: '22'\n"
    )
    # load_workflows looks under .github/workflows; parse directly
    from stuckcheck.names import job_checks
    from stuckcheck.workflow import parse_workflow
    wf = parse_workflow(str(caller), "e2e.yml")
    checks = list(job_checks(wf, str(tmp_path)))
    assert [c.name for c in checks] == [
        "e2e / chromium on node 22",
        "e2e / firefox on node 22",
    ]
    assert all(c.exact for c in checks)


def test_remote_reusable_workflow_is_a_wildcard(tmp_path):
    p = tmp_path / "w.yml"
    p.write_text("on: pull_request\njobs:\n  ci:\n    uses: org/repo/.github/workflows/ci.yml@v1\n")
    root, wfs = load_workflows(_wrap(tmp_path, [p]))
    checks = all_checks(root, wfs)
    assert checks[0].name == "ci / *" and checks[0].exact is False
    assert checks[0].matches("ci / lint")


def test_workflow_call_only_is_not_a_top_level_check(tmp_path):
    p = tmp_path / "w.yml"
    p.write_text("on: workflow_call\njobs:\n  inner:\n    runs-on: ubuntu-latest\n    steps: []\n")
    root, wfs = load_workflows(_wrap(tmp_path, [p]))
    assert all_checks(root, wfs) == []


def test_render_and_lookup():
    ctx = {"matrix": {"python-version": "3.12", "os": "ubuntu"}, "inputs": {"node": "22"}}
    assert render("test (${{ matrix.python-version }})", ctx) == ("test (3.12)", True)
    assert render("x ${{ matrix.missing }}", ctx) == ("x", True)  # missing property is null -> ''
    assert render("x ${{ github.sha }}", ctx) == ("x *", False)
    assert eval_expr("matrix['python-version']", ctx) == "3.12"
    assert fmt(True) == "true"
    assert fmt(False) == "false"
    assert fmt(None) == ""
    assert fmt(UNKNOWN) is None
    assert fmt(3.1) == "3.1"
    assert fmt(3.0) == "3"


def test_check_pattern_match():
    c = Check("test (*)", False, None, "p", ("f", ""), "t", [])
    assert c.matches("test (3.12)")
    assert c.matches("test (ubuntu-latest, 3.12)")
    assert not c.matches("lint")


def _wrap(tmp, files):
    """Put files into a fake repo's .github/workflows and return that root."""
    root = tmp / "repo"
    wdir = root / ".github" / "workflows"
    wdir.mkdir(parents=True, exist_ok=True)
    for f in files:
        (wdir / f.name).write_text(f.read_text())
    return str(root)


def test_include_values_added_to_a_combination_are_not_in_the_name(tmp_path):
    p = tmp_path / "w.yml"
    p.write_text(
        "on: pull_request\n"
        "jobs:\n  t:\n    runs-on: u\n"
        "    strategy:\n      matrix:\n        os: [one, two]\n"
        "        include:\n          - os: one\n            extra: e\n          - os: three\n            extra: z\n"
        "    steps: []\n"
    )
    root, wfs = load_workflows(_wrap(tmp_path, [p]))
    assert [c.name for c in all_checks(root, wfs)] == ["t (one)", "t (two)", "t (three, z)"]


def test_name_with_any_expression_gets_no_matrix_suffix(tmp_path):
    p = tmp_path / "w.yml"
    p.write_text(
        "on: pull_request\n"
        "jobs:\n  t:\n    name: Lint ${{ inputs.x }}\n    runs-on: u\n"
        "    strategy:\n      matrix:\n        v: [1, 2]\n"
        "    steps: []\n"
    )
    root, wfs = load_workflows(_wrap(tmp_path, [p]))
    checks = all_checks(root, wfs)
    assert {c.name for c in checks} == {"Lint *"}


def test_object_matrix_values_show_their_values(tmp_path):
    p = tmp_path / "w.yml"
    p.write_text(
        "on: pull_request\n"
        "jobs:\n  j:\n    runs-on: u\n"
        "    strategy:\n      matrix:\n        obj: [{a: 1, b: two}]\n        s: [x]\n"
        "    steps: []\n"
    )
    root, wfs = load_workflows(_wrap(tmp_path, [p]))
    assert [c.name for c in all_checks(root, wfs)] == ["j (1, two, x)"]
