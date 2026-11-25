"""Work out the check-run names a workflow's jobs report.

A job's check run is named after ``jobs.<id>.name`` (or the job id). A matrix
job whose name has no ``${{ }}`` expression gets the combination's values
appended, e.g. ``test (ubuntu-latest, 3.12)``. A job that calls a reusable
workflow reports ``<caller> / <called job>`` for every job in the called file.

Anything we can't evaluate statically (``fromJSON`` matrices, ``github.*``
expressions, remote reusable workflows) becomes a ``*`` wildcard and the name
is marked inexact.
"""

import itertools
import json
import os
import re

from .workflow import parse_workflow

EXPR = re.compile(r"\$\{\{\s*(.*?)\s*\}\}", re.S)
_PATH = re.compile(r"^(matrix|inputs)((?:\.[A-Za-z0-9_-]+|\[\s*'[^']*'\s*\])+)$")
_SEG = re.compile(r"\.([A-Za-z0-9_-]+)|\[\s*'([^']*)'\s*\]")
UNKNOWN = object()
MAX_DEPTH = 10


def fmt(value):
    """Render a value the way GitHub's expression engine turns it into text."""
    if value is UNKNOWN:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return repr(value)
    if isinstance(value, (int, str)):
        return str(value)
    return None  # objects and arrays: not rendered here


def _lookup(ctx, expr):
    m = _PATH.match(expr)
    if not m:
        return UNKNOWN
    root = ctx.get(m.group(1), UNKNOWN)
    if root is UNKNOWN:
        return UNKNOWN
    cur = root
    for dotted, quoted in _SEG.findall(m.group(2)):
        key = dotted or quoted
        if not isinstance(cur, dict):
            return None
        if key not in cur:
            return None  # a missing property evaluates to null -> ''
        cur = cur[key]
        if cur is UNKNOWN:
            return UNKNOWN
    return cur


def eval_expr(expr, ctx):
    expr = expr.strip()
    if len(expr) >= 2 and expr[0] == expr[-1] == "'":
        return expr[1:-1].replace("''", "'")
    if expr in ("true", "false"):
        return expr == "true"
    if expr == "null":
        return None
    if re.fullmatch(r"-?\d+(\.\d+)?", expr):
        return float(expr) if "." in expr else int(expr)
    return _lookup(ctx, expr)


def render(text, ctx):
    """Substitute ``${{ }}`` in ``text``. Returns (string, exact)."""
    exact = True
    out = []
    pos = 0
    for m in EXPR.finditer(text):
        out.append(text[pos:m.start()])
        s = fmt(eval_expr(m.group(1), ctx))
        if s is None:
            out.append("*")
            exact = False
        else:
            out.append(s)
        pos = m.end()
    out.append(text[pos:])
    return "".join(out).strip(), exact


def uses_matrix(text):
    return any(re.search(r"\bmatrix\b", m.group(1)) for m in EXPR.finditer(text))


def _is_expr(v):
    return isinstance(v, str) and EXPR.search(v) is not None


def expand_matrix(matrix):
    """Expand ``strategy.matrix`` into combinations (key order = insertion order).

    Returns a list of dicts, or None when the matrix is built at run time.
    """
    pairs = expand_matrix_shown(matrix)
    return None if pairs is None else [c for c, _ in pairs]


def expand_matrix_shown(matrix):
    """Like expand_matrix, but each item is ``(combo, keys shown in the job name)``.

    Checked against real runs: values that ``include`` adds to an existing
    combination are not shown in the name (``test (ubuntu)``), while a
    combination that ``include`` creates shows all of its values.
    """
    if not isinstance(matrix, dict):
        return None
    base_keys = [k for k in matrix if k not in ("include", "exclude")]
    for k in base_keys:
        if not isinstance(matrix[k], list):
            return None
    include = matrix.get("include", [])
    exclude = matrix.get("exclude", [])
    if _is_expr(include) or _is_expr(exclude):
        return None
    include = include if isinstance(include, list) else []
    exclude = exclude if isinstance(exclude, list) else []

    combos = []
    if base_keys:
        for values in itertools.product(*[matrix[k] for k in base_keys]):
            combos.append(dict(zip(base_keys, values)))
    for ex in exclude:
        if isinstance(ex, dict):
            combos = [c for c in combos if not all(k in c and c[k] == v for k, v in ex.items())]
    originals = [(c, dict(c)) for c in combos]
    extra = []
    for inc in include:
        if not isinstance(inc, dict):
            continue
        added = False
        for combo, orig in originals:
            if all(k not in orig or orig[k] == v for k, v in inc.items()):
                combo.update(inc)
                added = True
        if not added:
            extra.append(dict(inc))
    return [(c, list(base_keys)) for c in combos] + [(c, list(c)) for c in extra]


def _flat(value):
    """Values of a matrix entry as they appear in a job name; objects show their values."""
    if isinstance(value, dict):
        out = []
        for v in value.values():
            out.extend(_flat(v))
        return out
    return [fmt(value)]


class Check:
    """One check-run name a job reports."""

    def __init__(self, name, exact, workflow, path, scope, job_id, conds, note=None):
        self.name = name
        self.exact = exact
        self.workflow = workflow    # the top-level (triggering) Workflow
        self.path = path            # "ci.yml > test" style, through reusable calls
        self.scope = scope          # (file defining the job, name prefix)
        self.job_id = job_id
        self.conds = conds          # [(kind, value, where)]
        self.note = note

    def matches(self, context):
        if self.exact:
            return self.name == context
        rx = "".join(".*" if part == "*" else re.escape(part) for part in re.split(r"(\*)", self.name))
        return re.fullmatch(rx, context, re.S) is not None

    def to_json(self):
        return {"name": self.name, "exact": self.exact, "workflow": self.workflow.file,
                "job": self.path, "note": self.note}


def _job_label(jid, job, combo, shown, dynamic, ctx):
    """Check name for one job (one matrix combination).

    Checked against real runs: the matrix values are appended only when the job
    has no ``name`` or a ``name`` without any ``${{ }}`` expression. A name such
    as ``Lint ${{ github.event_name }}`` gets no suffix, even in a matrix.
    """
    raw = job.get("name")
    if isinstance(raw, str) and raw.strip():
        name, exact = render(raw, ctx)
        has_expr = EXPR.search(raw) is not None
    else:
        name, exact, has_expr = jid, True, False
    if "strategy" in job and isinstance(job.get("strategy"), dict) and "matrix" in job["strategy"] and not has_expr:
        if dynamic:
            return name + " (*)", False
        if combo:
            vals = []
            for k in shown:
                vals.extend(_flat(combo[k]))
            if any(v is None for v in vals):
                exact = False
            if vals:
                name += " (%s)" % ", ".join("*" if v is None else v for v in vals)
    return name, exact


def _short(path):
    return os.path.basename(path)


def job_checks(wf, root, top=None, prefix="", inputs=None, depth=0, chain="", conds=None, cache=None):
    """Yield Check objects for every job in ``wf`` (recursing into local reusable workflows)."""
    top = top or wf
    cache = {} if cache is None else cache
    conds = conds or []
    for jid, job in wf.jobs.items():
        strategy = job.get("strategy") if isinstance(job.get("strategy"), dict) else {}
        if "matrix" in strategy:
            combos = expand_matrix_shown(strategy["matrix"])
            dynamic = combos is None
            if dynamic:
                combos = [(None, [])]
            elif not combos:
                continue  # an empty matrix runs no jobs
        else:
            combos, dynamic = [(None, [])], False
        my_conds = list(conds)
        where = "%s > %s" % (_short(wf.file), jid)
        if job.get("if") is not None:
            my_conds.append(("if", str(job["if"]), where))
        needs = job.get("needs")
        if isinstance(needs, str):
            needs = [needs]
        if isinstance(needs, list) and needs:
            my_conds.append(("needs", [str(n) for n in needs], where))
        path = chain + " > " + jid if chain else where
        seen = set()
        for combo, shown in combos:
            ctx = {"matrix": UNKNOWN if dynamic else (combo or {}), "inputs": UNKNOWN if inputs is None else inputs}
            label, exact = _job_label(jid, job, combo, shown, dynamic, ctx)
            full = prefix + label
            key = (full, exact)
            if key in seen:
                continue
            seen.add(key)
            uses = job.get("uses")
            if isinstance(uses, str):
                if uses.startswith("./") and depth < MAX_DEPTH:
                    target = os.path.join(root, uses.split("@", 1)[0][2:])
                    if target not in cache:
                        cache[target] = parse_workflow(target, os.path.relpath(target, root).replace(os.sep, "/")) \
                            if os.path.isfile(target) else None
                    called = cache[target]
                    if called is None or called.error:
                        yield Check(full + " / *", False, top, path, (wf.file, prefix), jid, my_conds,
                                    note="reusable workflow %s could not be read" % uses)
                        continue
                    with_ = job.get("with") if isinstance(job.get("with"), dict) else {}
                    sub_inputs = {}
                    for k, v in with_.items():
                        if isinstance(v, str):
                            s, ok = render(v, ctx)
                            sub_inputs[str(k)] = s if ok else UNKNOWN
                        else:
                            sub_inputs[str(k)] = v
                    sub_inputs.update(_input_defaults(called, sub_inputs))
                    for c in job_checks(called, root, top, full + " / ", sub_inputs, depth + 1,
                                        path + " > " + _short(called.file), my_conds, cache):
                        yield c
                else:
                    yield Check(full + " / *", False, top, path, (wf.file, prefix), jid, my_conds,
                                note="calls remote reusable workflow %s; its job names aren't known here" % uses)
                continue
            yield Check(full, exact, top, path, (wf.file, prefix), jid, my_conds)


def _input_defaults(called, given):
    """Defaults declared under ``on.workflow_call.inputs`` for inputs the caller didn't pass."""
    cfg = called.events.get("workflow_call")
    out = {}
    if isinstance(cfg, dict) and isinstance(cfg.get("inputs"), dict):
        for k, spec in cfg["inputs"].items():
            if k in given:
                continue
            if isinstance(spec, dict) and "default" in spec:
                d = spec["default"]
                out[str(k)] = UNKNOWN if _is_expr(d) else d
            else:
                out[str(k)] = None
    return out


def is_called_only(wf):
    """True for workflows that only run when another workflow calls them."""
    return set(wf.events) == {"workflow_call"}


def all_checks(root, workflows):
    out = []
    cache = {}
    for wf in workflows:
        if wf.error or is_called_only(wf):
            continue
        out.extend(job_checks(wf, root, cache=cache))
    return out


def dump(checks):
    return json.dumps([c.to_json() for c in checks], indent=2)
