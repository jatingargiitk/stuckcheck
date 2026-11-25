# stuckcheck

Find the required status checks your pull requests will never get.

A ruleset or branch protection rule stores a required check as a plain name, like `test (3.10)`.
GitHub Actions makes that name from the job: its `name:` or id, the matrix values, and the caller
for reusable workflows. Rename a job, drop a Python version from the matrix or add a
`paths:` filter, and the required check stops coming. Every PR then sits at
**"Expected — Waiting for status to be reported"** until someone edits the rule.

`stuckcheck` reads `.github/workflows/*.yml` and the rule's required checks, then tells you for each one:

* **no job reports this name** (with the closest names that do exist, e.g. the matrix renamed it)
* the workflow has a **`paths` / `paths-ignore` / `branches` filter**, so it skips some PRs and the check stays Pending
* the workflow runs on an event whose checks **don't count for a PR** (`workflow_dispatch`, `workflow_run`, `schedule`...)
* the `pull_request` `types` leave out **`synchronize`**, so nothing reports on the PR's latest commit
* the branch has a **merge queue** but the workflow has no `merge_group` trigger
* the job `needs` other jobs and has no `always()`: if they fail it is **skipped, and a skipped check counts as passing**

The rules come from GitHub's
[Troubleshooting required status checks](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/collaborating-on-repositories-with-code-quality-features/troubleshooting-required-status-checks)
page. The naming rules were checked against real check runs on GitHub, and
[`tests/fixtures/observed-*`](tests/fixtures) holds those workflows next to the names GitHub reported.

## Install

You need Python 3.9+ and one dependency, [PyYAML](https://pypi.org/project/PyYAML/). It isn't on PyPI, so install it from GitHub:

```sh
pip install git+https://github.com/jatingargiitk/stuckcheck
# or
pipx install git+https://github.com/jatingargiitk/stuckcheck
```

Reading the rules from GitHub (`--repo`) goes through the [GitHub CLI](https://cli.github.com/) (`gh api`),
so it uses the `gh auth` you already have. stuckcheck never reads or stores a token itself.
Everything else works offline.

## Usage

The examples use [`examples/demo`](examples/demo/.github/workflows), five workflows with the usual mistakes.
Every `console` block below is the real output of the command shown. CI re-runs them and fails if the
README drifts (`python scripts/check_readme.py`).

### What names do my jobs report?

```console
$ stuckcheck names examples/demo
.github/workflows/ci.yml "CI"  on: pull_request, push
  lint
  test (3.9)
  test (3.1)
  test (3.11)
  build
.github/workflows/docs.yml "Docs"  on: pull_request
  Build docs
.github/workflows/e2e.yml "E2E"  on: pull_request
  e2e / chromium on node 22
  e2e / firefox on node 22
.github/workflows/release.yml "Release"  on: workflow_dispatch
  package
```

`python: [3.9, 3.10, 3.11]` is unquoted, so YAML reads `3.10` as the number 3.1, and GitHub names
the job `test (3.1)`. `e2e / ...` comes from a job that calls the reusable workflow
`browser-tests.yml`, which only runs on `workflow_call` and so isn't listed by itself.

### Check the required checks

[`examples/demo/rules.json`](examples/demo/rules.json) is what
`gh api repos/OWNER/REPO/rules/branches/main` returns for a branch with a ruleset:

```console
$ stuckcheck examples/demo --rules examples/demo/rules.json --branch main
stuckcheck: 7 required checks on main, 9 job checks in 5 workflows

OK    lint
      from ci.yml > lint
FAIL  test (3.10)
      no workflow job reports this name, and the rule expects it from GitHub Actions: PRs sit at
        "Waiting for status to be reported" and can't merge
      closest job names: test (3.1), test (3.11), test (3.9)
WARN  build
      from ci.yml > build
      ci.yml > build needs ['lint', 'test']; if one of them fails this job is skipped, and a skipped
        check counts as passing, so the PR can merge (not required themselves: test (3.9), test
        (3.1), test (3.11))
        fix: add `if: ${{ always() && !contains(needs.*.result, 'failure') &&
          !contains(needs.*.result, 'cancelled') }}` and fail the job yourself, or require the jobs
          it needs
FAIL  Build docs
      from docs.yml > docs
      pull_request has paths ['docs/**', 'mkdocs.yml']: a PR that changes no matching file never
        starts this workflow, the check stays Pending, and PRs sit at "Waiting for status to be
        reported" and can't merge
        fix: drop the filter from the trigger and skip inside the job instead (dorny/paths-filter or
          a git diff step); a job skipped by `if:` reports Success
FAIL  e2e / chromium on node 22
      from e2e.yml > e2e > browser-tests.yml > browser
      pull_request types ['opened', 'reopened'] leave out synchronize: after the next push to the PR
        nothing reports on the new head commit, and PRs sit at "Waiting for status to be reported"
        and can't merge
        fix: add synchronize to types (the default is opened, synchronize, reopened)
FAIL  package
      from release.yml > package
      .github/workflows/release.yml runs on workflow_dispatch; checks from those events never count
        for a pull request, so PRs sit at "Waiting for status to be reported" and can't merge
        fix: trigger it with pull_request (and merge_group if you use a merge queue)
SKIP  Vercel
      set by app id 8329, not GitHub Actions; not checked

4 fail, 1 warn, 0 note, 1 ok, 1 skipped
```

The exit code is 1 because there is at least one `FAIL`. Use `--fail-on warn` to fail on warnings too.
`integration_id` / `app_id` in the rule says which app must set the check. If it is GitHub Actions
(15368), a missing name is a `FAIL`. If any source is allowed, a missing name is a `WARN`, because
another app or a commit status may set it. Checks pinned to another app are skipped.

### Merge queues

```console
$ stuckcheck examples/demo -r lint -r "test (3.9)" --merge-queue
stuckcheck: 2 required checks, 9 job checks in 5 workflows, merge queue: on

FAIL  lint
      from ci.yml > lint
      the branch uses a merge queue but .github/workflows/ci.yml has no merge_group trigger, so the
        check is never reported for a queued merge and the merge fails
        fix: add `merge_group:` to `on:`
FAIL  test (3.9)
      from ci.yml > test
      the branch uses a merge queue but .github/workflows/ci.yml has no merge_group trigger, so the
        check is never reported for a queued merge and the merge fails
        fix: add `merge_group:` to `on:`

2 fail, 0 warn, 0 note, 0 ok, 0 skipped
```

With `--repo`, a `merge_queue` rule is detected from the ruleset, so you don't need `--merge-queue`.

### Read the rules from GitHub

Run it from a checkout of the repo:

```sh
stuckcheck --repo OWNER/REPO --branch main
```

This calls `gh api repos/OWNER/REPO/rules/branches/main` (rulesets, readable on public repos) and
`gh api repos/OWNER/REPO/branches/main/protection/required_status_checks` (classic branch
protection, needs admin access; a 403 or 404 there is only a note). This is the real output for a
sparse checkout of fastapi/fastapi's `.github`, run on 25 Nov 2026:

```text
$ stuckcheck --repo fastapi/fastapi --branch master
stuckcheck: 5 required checks on master, 45 job checks in 18 workflows
  note: classic branch protection not read (gh: Not Found (HTTP 404))

OK    docs-all-green
      from build-docs.yml > docs-all-green
OK    test-redistribute-alls-green
      from test-redistribute.yml > test-redistribute-alls-green
OK    pre-commit-alls-green
      from pre-commit.yml > pre-commit-alls-green
OK    test-alls-green
      from test.yml > test-alls-green
SKIP  latest-changes/label
      set by app id 4497293, not GitHub Actions; not checked

0 fail, 0 warn, 0 note, 4 ok, 1 skipped
```

### In CI

Check that the names you require still exist whenever a workflow changes:

```yaml
on:
  pull_request:
    paths: ['.github/workflows/**']   # fine here: this check itself isn't required
jobs:
  stuckcheck:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install git+https://github.com/jatingargiitk/stuckcheck
      - run: stuckcheck --repo ${{ github.repository }} --branch ${{ github.base_ref }}
        env:
          GH_TOKEN: ${{ github.token }}
```

### Options

| option | |
|---|---|
| `-r, --required NAME` | a required check name (repeatable) |
| `--rules FILE` | JSON from `gh api .../rules/branches/B`, `.../rulesets/ID`, `.../branches/B/protection` or `.../protection/required_status_checks` (repeatable) |
| `--repo OWNER/REPO --branch B` | read the rules for B with `gh api` |
| `-b, --branch B` | the protected base branch, used for `branches` filters |
| `--merge-queue` | the branch uses a merge queue |
| `--fail-on fail\|warn` | exit 1 on FAIL (default) or on WARN too |
| `-v, --verbose` | also show jobs with an `if:` that can skip them (a skipped check passes) |
| `--json` | JSON output, notes included |

Exit codes: `0` nothing failed, `1` a required check fails (or warns, with `--fail-on warn`), `2` bad input.

## How names are worked out

Checked against real runs:

* no `name:` → the job id; a matrix adds `(value, value)` from the matrix keys, in order.
* `name:` without any `${{ }}` → that name, plus the matrix values.
* `name:` with any `${{ }}` → just the rendered name, with no matrix values, even in a matrix.
* values that `include` adds to an existing combination aren't shown. A combination that `include`
  creates shows all of its values.
* unquoted `3.10` shows as `3.1`, `3.0` as `3`, `true` as `true`, and an object as its values.
* a job calling a local reusable workflow reports `caller / called-job` for each job in it, with
  `inputs` filled in from `with:` (a matrix on the caller gives `caller (p) / called-job`).

When a value is only known at run time (a `fromJSON` matrix, `github.*`, a remote reusable workflow),
that part becomes `*` and the required check is matched as a pattern. The result is a `WARN` that says so.

## Limits

* It reads the workflow files in your checkout, not the ones on the base branch on GitHub. A PR that
  changes the workflows is checked as changed.
* It doesn't look at commit-message skips (`[skip ci]`) or at checks set by other apps and commit statuses.
* The `paths` / `branches` verdicts say that *some* PRs will be stuck, not which ones. To check one
  specific diff, use [wouldrun](https://github.com/jatingargiitk/wouldrun).
* `if:` conditions aren't evaluated. With `-v` they are listed, because a job skipped by `if:` reports
  success.

## License

MIT
