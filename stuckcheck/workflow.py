"""Load workflow files the way GitHub reads them."""

import glob
import os
import re

import yaml


class _Loader(yaml.SafeLoader):
    """SafeLoader that only treats ``true``/``false`` as booleans.

    Stock PyYAML implements YAML 1.1, where the bare key ``on`` (and ``yes``,
    ``no``, ``off``) become booleans. GitHub's parser does not do that.
    Numbers are left alone on purpose: GitHub also reads an unquoted ``3.10``
    as the number 3.1, and that is what shows up in a matrix job's name.
    """


_Loader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:bool"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Loader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false|True|TRUE|False|FALSE)$"),
    list("tTfF"),
)


class Workflow:
    def __init__(self, path, rel):
        self.path = path
        self.file = rel
        self.name = None
        self.events = {}   # event name -> config (dict, list or None)
        self.jobs = {}     # job id -> raw job mapping, in file order
        self.error = None

    @property
    def label(self):
        return self.name or self.file


def normalise_on(raw):
    """Return {event: config} for the string, list and map forms of ``on:``."""
    if isinstance(raw, str):
        return {raw: None}
    if isinstance(raw, list):
        return {str(e): None for e in raw}
    if isinstance(raw, dict):
        return {str(k): v for k, v in raw.items()}
    raise ValueError("`on:` must be a string, a list or a map, got %s" % type(raw).__name__)


def parse_workflow(path, rel):
    wf = Workflow(path, rel)
    try:
        with open(path, encoding="utf-8") as fh:
            doc = yaml.load(fh, Loader=_Loader)
    except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
        wf.error = "YAML error: %s" % str(exc).replace("\n", " ")
        return wf
    if not isinstance(doc, dict):
        wf.error = "workflow file is not a YAML mapping"
        return wf
    if isinstance(doc.get("name"), str) and doc["name"].strip():
        wf.name = doc["name"].strip()
    raw_on = doc["on"] if "on" in doc else doc.get(True)
    if raw_on is None:
        wf.error = "missing `on:` key"
        return wf
    try:
        wf.events = normalise_on(raw_on)
    except ValueError as exc:
        wf.error = str(exc)
        return wf
    jobs = doc.get("jobs")
    if isinstance(jobs, dict):
        wf.jobs = {str(k): (v if isinstance(v, dict) else {}) for k, v in jobs.items()}
    return wf


def find_root(path):
    """Accept a repo root or a ``.github/workflows`` dir; return (root, workflows_dir)."""
    path = os.path.abspath(path)
    wdir = os.path.join(path, ".github", "workflows")
    if os.path.isdir(wdir):
        return path, wdir
    if os.path.basename(path) == "workflows" and os.path.basename(os.path.dirname(path)) == ".github":
        return os.path.dirname(os.path.dirname(path)), path
    return path, wdir


def load_workflows(path):
    root, wdir = find_root(path)
    paths = sorted(glob.glob(os.path.join(wdir, "*.yml")) + glob.glob(os.path.join(wdir, "*.yaml")))
    return root, [parse_workflow(p, os.path.relpath(p, root).replace(os.sep, "/")) for p in paths]
