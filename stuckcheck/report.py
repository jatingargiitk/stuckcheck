"""Text and JSON output."""

import json
import textwrap

from .analyze import summarize

WIDTH = 100


def _wrap(text, indent):
    return textwrap.fill(text, WIDTH, initial_indent=indent, subsequent_indent=indent + "  ",
                         break_on_hyphens=False, break_long_words=False)


def header(required, checks, workflows, base):
    n_wf = len([w for w in workflows if not w.error])
    bits = ["%d required check%s" % (len(required.checks), "" if len(required.checks) == 1 else "s")]
    if base:
        bits[0] += " on %s" % base
    bits.append("%d job check%s in %d workflow%s" % (len(checks), "" if len(checks) == 1 else "s",
                                                    n_wf, "" if n_wf == 1 else "s"))
    if required.merge_queue:
        bits.append("merge queue: on")
    return "stuckcheck: " + ", ".join(bits)


def text(required, checks, workflows, verdicts, base):
    lines = [header(required, checks, workflows, base)]
    for note in required.notes:
        lines.append("  note: " + note)
    for wf in workflows:
        if wf.error:
            lines.append("  skipped %s: %s" % (wf.file, wf.error))
    lines.append("")
    for v in verdicts:
        tag = v.verdict.ljust(5)
        lines.append("%s %s" % (tag, v.req.context))
        if v.producers:
            where = ", ".join(sorted(set(c.path for c in v.producers)))
            lines.append(_wrap("from " + where, "      "))
        for i in v.issues:
            if i.kind == "app":
                lines.append(_wrap(i.message, "      "))
                continue
            prefix = "" if i.severity == v.verdict else i.severity.lower() + ": "
            lines.append(_wrap(prefix + i.message, "      "))
            if i.fix:
                lines.append(_wrap("fix: " + i.fix, "        "))
        if v.suggestions:
            lines.append(_wrap("closest job names: " + ", ".join(v.suggestions), "      "))
    c = summarize(verdicts)
    lines.append("")
    lines.append("%d fail, %d warn, %d note, %d ok, %d skipped" % (c["FAIL"], c["WARN"], c["NOTE"], c["OK"], c["SKIP"]))
    return "\n".join(lines)


def as_json(required, checks, workflows, verdicts, base):
    return json.dumps({
        "branch": base,
        "merge_queue": required.merge_queue,
        "sources": required.sources,
        "notes": required.notes,
        "workflows": [{"file": w.file, "name": w.name, "error": w.error} for w in workflows],
        "required": [v.to_json() for v in verdicts],
        "summary": summarize(verdicts),
    }, indent=2)


def names_text(checks, workflows):
    lines = []
    by_wf = {}
    for c in checks:
        by_wf.setdefault(c.workflow.file, []).append(c)
    for wf in workflows:
        if wf.error:
            lines.append("%s: skipped (%s)" % (wf.file, wf.error))
            continue
        if wf.file not in by_wf:
            continue
        label = ' "%s"' % wf.name if wf.name else ""
        lines.append("%s%s  on: %s" % (wf.file, label, ", ".join(wf.events)))
        for c in by_wf[wf.file]:
            extra = "   (pattern: %s)" % (c.note or "value only known at run time") if not c.exact else ""
            lines.append("  " + c.name + extra)
    return "\n".join(lines)
