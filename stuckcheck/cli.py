"""Command line interface."""

import argparse
import json
import sys

from . import __version__
from .analyze import FAIL, WARN, analyze, hide_notes, summarize
from .names import all_checks
from .report import as_json, names_text, text
from .required import RequiredSet, fetch, load_file
from .workflow import load_workflows

USAGE_EXIT = 2


def _parser():
    p = argparse.ArgumentParser(
        prog="stuckcheck",
        description="Find required status checks that a pull request will never get reported: "
                    "renamed jobs, matrix names that drifted, path-filtered workflows, "
                    "missing merge_group triggers.")
    p.add_argument("--version", action="version", version="stuckcheck " + __version__)
    sub = p.add_subparsers(dest="cmd")

    c = sub.add_parser("check", help="check required status checks against the workflows (default)")
    c.add_argument("path", nargs="?", default=".", help="repo root or .github/workflows dir (default: .)")
    c.add_argument("--required", "-r", action="append", default=[], metavar="NAME",
                   help="a required check name (repeatable)")
    c.add_argument("--rules", action="append", default=[], metavar="FILE",
                   help="JSON from `gh api repos/O/R/rules/branches/B`, a ruleset, or branch protection (repeatable)")
    c.add_argument("--repo", metavar="OWNER/REPO", help="read the rules for --branch from GitHub with `gh api`")
    c.add_argument("--branch", "-b", metavar="BRANCH", help="the protected base branch, e.g. main")
    c.add_argument("--merge-queue", action="store_true", help="the branch uses a merge queue")
    c.add_argument("--fail-on", choices=["fail", "warn"], default="fail",
                   help="exit 1 on FAIL (default) or on WARN too")
    c.add_argument("--verbose", "-v", action="store_true",
                   help="also show notes: required jobs with an `if:` that can skip them (a skipped check passes)")
    c.add_argument("--json", action="store_true", help="JSON output (always includes notes)")

    n = sub.add_parser("names", help="list the check names every job reports")
    n.add_argument("path", nargs="?", default=".", help="repo root or .github/workflows dir (default: .)")
    n.add_argument("--json", action="store_true", help="JSON output")
    return p


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or (argv[0] not in ("check", "names", "-h", "--help", "--version")):
        argv = ["check"] + argv
    args = _parser().parse_args(argv)

    root, workflows = load_workflows(args.path)
    if not workflows:
        print("stuckcheck: no workflow files under %s/.github/workflows" % root, file=sys.stderr)
        return USAGE_EXIT
    checks = all_checks(root, workflows)

    if args.cmd == "names":
        if args.json:
            print(json.dumps([c.to_json() for c in checks], indent=2))
        else:
            print(names_text(checks, workflows))
        return 0

    rs = RequiredSet()
    for name in args.required:
        rs.add(name, None, "--required")
    try:
        for path in args.rules:
            load_file(path, rs)
        if args.repo:
            if not args.branch:
                print("stuckcheck: --repo needs --branch", file=sys.stderr)
                return USAGE_EXIT
            fetch(args.repo, args.branch, rs)
    except (OSError, ValueError, RuntimeError) as exc:
        print("stuckcheck: %s" % exc, file=sys.stderr)
        return USAGE_EXIT
    if args.merge_queue:
        rs.merge_queue = True
    if not rs.checks:
        if args.repo:
            print("stuckcheck: %s@%s has no required status checks" % (args.repo, args.branch))
            return 0
        print("stuckcheck: no required checks given; use --required NAME, --rules FILE or --repo OWNER/REPO --branch B",
              file=sys.stderr)
        return USAGE_EXIT

    verdicts = analyze(rs, checks, args.branch)
    if not args.verbose and not args.json:
        hide_notes(verdicts)
    if args.json:
        print(as_json(rs, checks, workflows, verdicts, args.branch))
    else:
        print(text(rs, checks, workflows, verdicts, args.branch))
    counts = summarize(verdicts)
    if counts[FAIL] or (args.fail_on == "warn" and counts[WARN]):
        return 1
    return 0
