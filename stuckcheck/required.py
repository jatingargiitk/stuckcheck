"""Read required status checks from rules / branch-protection JSON, or from GitHub via ``gh api``."""

import json
import subprocess

ACTIONS_APP_ID = 15368  # the GitHub Actions app


class Required:
    def __init__(self, context, app_id=None, source=None):
        self.context = context
        self.app_id = app_id      # None = any source
        self.source = source

    def __repr__(self):
        return "Required(%r, app_id=%r)" % (self.context, self.app_id)


class RequiredSet:
    def __init__(self):
        self.checks = []
        self.merge_queue = False
        self.sources = []
        self.notes = []

    def add(self, context, app_id=None, source=None):
        if app_id in (-1, 0):
            app_id = None
        for r in self.checks:
            if r.context == context and r.app_id == app_id:
                return
        self.checks.append(Required(context, app_id, source))


def _from_rules_list(rules, rs, source):
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        kind = rule.get("type")
        params = rule.get("parameters") or {}
        if kind == "required_status_checks":
            for c in params.get("required_status_checks") or []:
                if isinstance(c, dict) and c.get("context"):
                    rs.add(c["context"], c.get("integration_id"), source)
        elif kind == "merge_queue":
            rs.merge_queue = True


def _from_status_checks(obj, rs, source):
    """The ``required_status_checks`` object of classic branch protection."""
    seen = set()
    for c in obj.get("checks") or []:
        if isinstance(c, dict) and c.get("context"):
            rs.add(c["context"], c.get("app_id"), source)
            seen.add(c["context"])
    for ctx in obj.get("contexts") or []:
        if isinstance(ctx, str) and ctx not in seen:
            rs.add(ctx, None, source)


def load_json(data, rs, source):
    """Accept any of the shapes ``gh api`` returns for rules or protection."""
    if isinstance(data, list):
        _from_rules_list(data, rs, source)
    elif isinstance(data, dict):
        if isinstance(data.get("rules"), list):               # a single ruleset
            _from_rules_list(data["rules"], rs, source)
        elif isinstance(data.get("required_status_checks"), dict):   # branch protection
            _from_status_checks(data["required_status_checks"], rs, source)
        elif "contexts" in data or "checks" in data:           # .../protection/required_status_checks
            _from_status_checks(data, rs, source)
        else:
            raise ValueError("%s: no rules, required_status_checks, contexts or checks found" % source)
    else:
        raise ValueError("%s: expected a JSON list or object" % source)


def load_file(path, rs):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    load_json(data, rs, path)


def _gh(endpoint):
    proc = subprocess.run(["gh", "api", endpoint], capture_output=True, text=True)
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout).strip().splitlines()
        return None, (msg[-1] if msg else "gh api failed")
    return json.loads(proc.stdout or "null"), None


def fetch(repo, branch, rs, gh=_gh):
    """Rules that apply to ``branch`` (rulesets) plus classic branch protection, if readable."""
    rules_ep = "repos/%s/rules/branches/%s" % (repo, branch)
    data, err = gh(rules_ep)
    if err:
        raise RuntimeError("gh api %s: %s" % (rules_ep, err))
    load_json(data or [], rs, "rulesets")
    rs.sources.append("rulesets for %s@%s" % (repo, branch))
    prot_ep = "repos/%s/branches/%s/protection/required_status_checks" % (repo, branch)
    data, err = gh(prot_ep)
    if err:
        rs.notes.append("classic branch protection not read (%s)" % err)
    elif data:
        load_json(data, rs, "branch protection")
        rs.sources.append("branch protection")
