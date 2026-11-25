"""Decide, per required check, whether a pull request will ever get it reported.

Rules come from GitHub's "Troubleshooting required status checks" page:
https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/collaborating-on-repositories-with-code-quality-features/troubleshooting-required-status-checks

* only checks from workflows triggered by push, pull_request, pull_request_review,
  pull_request_target, deployment or deployment_status count for a pull request;
* a workflow skipped by a path or branch filter leaves its checks Pending, which blocks merging;
* a job skipped by ``if:`` reports Success, and so may a job skipped because a job it needs failed;
* a merge queue needs the ``merge_group`` event, or the queued merge waits for checks that never come;
* checks must pass on the latest commit, so a ``pull_request`` that drops ``synchronize``
  from ``types`` never reports on new pushes.
"""

import difflib
import re

from .globs import evaluate_patterns, first_match
from .required import ACTIONS_APP_ID

ELIGIBLE = ("push", "pull_request", "pull_request_review", "pull_request_target",
            "deployment", "deployment_status")
PR_EVENTS = ("pull_request", "pull_request_target")
DEFAULT_TYPES = ("opened", "synchronize", "reopened")
FAIL, WARN, NOTE, OK, SKIP = "FAIL", "WARN", "NOTE", "OK", "SKIP"
_RANK = {OK: 0, NOTE: 1, WARN: 2, FAIL: 3}
STUCK = 'PRs sit at "Waiting for status to be reported" and can\'t merge'


class Issue:
    def __init__(self, severity, kind, message, fix=None):
        self.severity = severity
        self.kind = kind
        self.message = message
        self.fix = fix

    def to_json(self):
        return {"severity": self.severity, "kind": self.kind, "message": self.message, "fix": self.fix}


def _list(v):
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v]
    return [str(v)]


def _pr_event_issues(ev, cfg, base):
    cfg = cfg if isinstance(cfg, dict) else {}
    out = []
    types = _list(cfg.get("types")) or list(DEFAULT_TYPES)
    if "synchronize" not in types:
        out.append(Issue(FAIL, "types",
                         "%s types %s leave out synchronize: after the next push to the PR nothing reports "
                         "on the new head commit, and %s" % (ev, types, STUCK),
                         "add synchronize to types (the default is opened, synchronize, reopened)"))
    for key in ("paths", "paths-ignore"):
        if key in cfg:
            out.append(Issue(FAIL, "paths",
                             "%s has %s %s: a PR that %s never starts this workflow, the check stays Pending, and %s"
                             % (ev, key, _list(cfg[key]),
                                "changes no matching file" if key == "paths" else "only changes ignored files",
                                STUCK),
                             "drop the filter from the trigger and skip inside the job instead "
                             "(dorny/paths-filter or a git diff step); a job skipped by `if:` reports Success"))
    if "branches" in cfg or "branches-ignore" in cfg:
        if base is None:
            pats = _list(cfg.get("branches")) or ["!" + p for p in _list(cfg.get("branches-ignore"))]
            out.append(Issue(WARN, "branches?",
                             "%s only runs for some base branches (%s); pass --branch to check the protected one"
                             % (ev, pats)))
        elif "branches" in cfg:
            ok, _ = evaluate_patterns(_list(cfg["branches"]), base)
            if not ok:
                out.append(Issue(FAIL, "branches",
                                 "%s branches %s don't match the base branch %r, so PRs into %s never start "
                                 "this workflow and %s" % (ev, _list(cfg["branches"]), base, base, STUCK),
                                 "add %r to branches" % base))
        else:
            hit = first_match(_list(cfg["branches-ignore"]), base)
            if hit:
                out.append(Issue(FAIL, "branches",
                                 "%s branches-ignore %r matches the base branch %r, so PRs into %s never start "
                                 "this workflow and %s" % (ev, hit, base, base, STUCK)))
    return out


def trigger_issues(wf, base, merge_queue):
    """Problems with how ``wf`` is triggered, as far as a PR into ``base`` is concerned."""
    events = wf.events
    out = []
    if merge_queue and "merge_group" not in events:
        out.append(Issue(FAIL, "merge_group",
                         "the branch uses a merge queue but %s has no merge_group trigger: queued merges "
                         "wait for this check until they time out" % wf.file,
                         "add `merge_group:` to `on:`"))
    pr = [e for e in PR_EVENTS if e in events]
    if pr:
        per_event = [_pr_event_issues(e, events[e], base) for e in pr]
        clean = [i for i in per_event if not any(x.severity in (FAIL, WARN) for x in i)]
        if clean:
            return out + clean[0]
        best = min(per_event, key=lambda iss: (max(_RANK[x.severity] for x in iss), len(iss)))
        return out + best
    if "push" in events:
        cfg = events["push"] if isinstance(events["push"], dict) else {}
        branch_keys = [k for k in ("branches", "branches-ignore") if k in cfg]
        if not branch_keys and ("tags" in cfg or "tags-ignore" in cfg):
            out.append(Issue(FAIL, "event",
                             "%s only runs on tag pushes, so it never reports on a PR branch and %s" % (wf.file, STUCK),
                             "add a pull_request trigger"))
            return out
        for key in ("paths", "paths-ignore"):
            if key in cfg:
                out.append(Issue(FAIL, "paths",
                                 "push has %s %s: a push that %s never starts this workflow and %s"
                                 % (key, _list(cfg[key]),
                                    "changes no matching file" if key == "paths" else "only changes ignored files",
                                    STUCK)))
        if "branches" in cfg:
            out.append(Issue(WARN, "push-branches",
                             "no pull_request trigger, and push only runs for branches %s: a PR whose head "
                             "branch doesn't match never gets this check" % _list(cfg["branches"]),
                             "add a pull_request trigger"))
        out.append(Issue(WARN, "push-only",
                         "no pull_request trigger: the check only comes from pushes to this repository, so "
                         "PRs from forks never get it",
                         "add a pull_request trigger"))
        return out
    other = [e for e in ELIGIBLE if e in events]
    if other:
        out.append(Issue(WARN, "event",
                         "%s only runs on %s, not on every push to a PR" % (wf.file, ", ".join(other))))
        return out
    out.append(Issue(FAIL, "event",
                     "%s runs on %s; checks from those events never count for a pull request, so %s"
                     % (wf.file, ", ".join(sorted(events)) or "nothing", STUCK),
                     "trigger it with pull_request (and merge_group if you use a merge queue)"))
    return out


_ALWAYS = re.compile(r"\b(always|failure|cancelled)\s*\(")


def condition_issues(check, required_names, checks_by_scope):
    out = []
    has_status_fn = False
    for kind, value, where in check.conds:
        if kind == "if" and _ALWAYS.search(value):
            has_status_fn = True
    for kind, value, where in check.conds:
        if kind == "if" and not _ALWAYS.search(value):
            out.append(Issue(NOTE, "if",
                             "%s has `if: %s`; when that is false the job is skipped and a skipped check "
                             "counts as passing" % (where, value.strip())))
        elif kind == "needs" and not has_status_fn:
            scope = check.scope
            needed = [c for c in checks_by_scope.get(scope, []) if c.job_id in value]
            unguarded = [c.name for c in needed if c.name not in required_names]
            if unguarded or not needed:
                out.append(Issue(WARN, "needs",
                                 "%s needs %s; if one of them fails this job is skipped, and a skipped "
                                 "check counts as passing, so the PR can merge%s"
                                 % (where, value,
                                    " (not required themselves: %s)" % ", ".join(unguarded) if unguarded else ""),
                                 "add `if: ${{ always() && !contains(needs.*.result, 'failure') "
                                 "&& !contains(needs.*.result, 'cancelled') }}` and fail the job yourself, "
                                 "or require the jobs it needs"))
    return out


def suggest(context, names, limit=4):
    """Likely intended names: same job with a matrix suffix added or dropped, then close spellings."""
    uniq = list(dict.fromkeys(names))
    out = [n for n in uniq if n.startswith(context + " (") or context.startswith(n + " (")]
    for n in difflib.get_close_matches(context, sorted(uniq), n=limit, cutoff=0.6):
        if n not in out:
            out.append(n)
    return out[:limit]


class Verdict:
    def __init__(self, req):
        self.req = req
        self.verdict = OK
        self.producers = []
        self.issues = []
        self.suggestions = []

    def to_json(self):
        return {"context": self.req.context, "app_id": self.req.app_id, "verdict": self.verdict,
                "producers": [c.to_json() for c in self.producers],
                "issues": [i.to_json() for i in self.issues], "suggestions": self.suggestions}


def _worst(issues):
    sev = OK
    for i in issues:
        if _RANK[i.severity] > _RANK[sev]:
            sev = i.severity
    return sev


def analyze(required, checks, base=None):
    names = [c.name for c in checks if c.exact]
    required_names = {r.context for r in required.checks}
    by_scope = {}
    for c in checks:
        by_scope.setdefault(c.scope, []).append(c)
    trig_cache = {}
    out = []
    for req in required.checks:
        v = Verdict(req)
        out.append(v)
        if req.app_id is not None and req.app_id != ACTIONS_APP_ID:
            v.verdict = SKIP
            v.issues.append(Issue(SKIP, "app", "set by app id %s, not GitHub Actions; not checked" % req.app_id))
            continue
        exact = [c for c in checks if c.exact and c.name == req.context]
        loose = [c for c in checks if not c.exact and c.matches(req.context)]
        v.producers = exact or loose
        if not v.producers:
            v.suggestions = suggest(req.context, names)
            if req.app_id == ACTIONS_APP_ID:
                v.issues.append(Issue(FAIL, "missing",
                                      "no workflow job reports this name, and the rule expects it from "
                                      "GitHub Actions: %s" % STUCK))
            else:
                v.issues.append(Issue(WARN, "missing",
                                      "no workflow job reports this name; fine only if another app or a "
                                      "commit status sets it, otherwise %s" % STUCK))
            v.verdict = _worst(v.issues)
            continue
        options = []
        for c in v.producers:
            key = c.workflow.file
            if key not in trig_cache:
                trig_cache[key] = trigger_issues(c.workflow, base, required.merge_queue)
            iss = list(trig_cache[key]) + condition_issues(c, required_names, by_scope)
            if not c.exact:
                iss.append(Issue(WARN, "pattern",
                                 "only matched the pattern %r (%s)" % (c.name, c.note or "a value is only known at run time")))
            options.append((c, iss))
        best_rank = min(_RANK[_worst(i)] for _, i in options)
        chosen = [(c, i) for c, i in options if _RANK[_worst(i)] == best_rank]
        v.producers = [c for c, _ in chosen]
        seen = set()
        for _, iss in chosen:
            for i in iss:
                if i.message not in seen:
                    seen.add(i.message)
                    v.issues.append(i)
        if len(set(c.path for c in v.producers)) > 1 and best_rank == 0:
            v.issues.append(Issue(NOTE, "duplicate", "reported by %d jobs with the same name"
                                  % len(set(c.path for c in v.producers))))
        v.verdict = _worst(v.issues)
    return out


def summarize(verdicts):
    counts = {FAIL: 0, WARN: 0, NOTE: 0, OK: 0, SKIP: 0}
    for v in verdicts:
        counts[v.verdict] += 1
    return counts


def hide_notes(verdicts):
    """Drop NOTE-level issues (for terse text output) and recompute each verdict."""
    for v in verdicts:
        if v.verdict == SKIP:
            continue
        v.issues = [i for i in v.issues if i.severity != NOTE]
        v.verdict = _worst(v.issues)
