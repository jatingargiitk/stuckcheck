import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEMO = os.path.join(ROOT, "examples", "demo")


def run(*args, env=None):
    e = dict(os.environ, PYTHONPATH=ROOT)
    if env:
        e.update(env)
    return subprocess.run([sys.executable, "-m", "stuckcheck", *args],
                          cwd=ROOT, env=e, capture_output=True, text=True)


def test_names_lists_demo_jobs():
    p = run("names", DEMO)
    assert p.returncode == 0
    assert "test (3.1)" in p.stdout
    assert "test (3.10)" not in p.stdout
    assert "e2e / chromium on node 22" in p.stdout
    assert "Browser tests" not in p.stdout  # workflow_call-only is not listed as a top-level check


def test_check_demo_exits_1_and_flags_the_traps():
    p = run("check", DEMO, "--rules", os.path.join(DEMO, "rules.json"), "--branch", "main")
    assert p.returncode == 1
    out = p.stdout
    assert "FAIL  test (3.10)" in out
    assert "closest job names: test (3.1)" in out
    assert "FAIL  Build docs" in out
    assert "paths" in out
    assert "FAIL  e2e / chromium on node 22" in out
    assert "synchronize" in out
    assert "FAIL  package" in out
    assert "workflow_dispatch" in out
    assert "SKIP  Vercel" in out
    assert "OK    lint" in out
    assert "WARN  build" in out


def test_json_and_fail_on_warn():
    p = run("check", DEMO, "--required", "lint", "--required", "build", "--json")
    assert p.returncode == 0
    data = json.loads(p.stdout)
    assert data["summary"]["WARN"] == 1 and data["summary"]["OK"] == 1
    p = run("check", DEMO, "--required", "lint", "--required", "build", "--fail-on", "warn")
    assert p.returncode == 1


def test_merge_queue_flag():
    p = run("check", DEMO, "--required", "lint", "--merge-queue")
    assert p.returncode == 1
    assert "merge_group" in p.stdout


def test_usage_when_nothing_is_required():
    p = run("check", DEMO)
    assert p.returncode == 2
    assert "--required" in p.stderr


def test_repo_needs_branch():
    p = run("check", DEMO, "--repo", "octo/repo")
    assert p.returncode == 2
    assert "--branch" in p.stderr


def test_empty_dir(tmp_path):
    p = run("check", str(tmp_path), "--required", "lint")
    assert p.returncode == 2
    assert "no workflow files" in p.stderr


def test_version():
    p = run("--version")
    assert p.returncode == 0 and p.stdout.strip() == "stuckcheck 0.1.0"


def test_if_notes_only_with_verbose(tmp_path):
    wdir = tmp_path / ".github" / "workflows"
    wdir.mkdir(parents=True)
    (wdir / "ci.yml").write_text(
        "on: pull_request\njobs:\n  lint:\n    if: github.repository == 'octo/repo'\n"
        "    runs-on: u\n    steps: []\n")
    p = run("check", str(tmp_path), "-r", "lint")
    assert p.returncode == 0 and "OK    lint" in p.stdout and "if:" not in p.stdout
    p = run("check", str(tmp_path), "-r", "lint", "-v")
    assert p.returncode == 0 and "NOTE  lint" in p.stdout and "skipped check counts as passing" in p.stdout
