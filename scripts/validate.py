#!/usr/bin/env python3
"""Validate every template file against the JSON schema, repo conventions, and the
workflow-engine semantics that runbook-server enforces at sync/apply time.

Two layers of checks:

1. Structural (JSON Schema in schema/template.schema.json) — shape, enums, required
   keys, slug == file name, no duplicate slugs.
2. Workflow semantics (this file) — the same rules the engine applies in
   runbook-server/internal/model/validation.go and internal/tasks/registry.go.
   These catch the failures the JSON schema can't: an unknown / mistyped task
   `type` (rejected at sync time as "task not found"), a `depends_on` pointing at a
   task that does not exist, duplicate task ids, dependency cycles, an unsupported
   trigger type, or a malformed duration. This is the offline equivalent of
   `nbctl workflow validate` — no backend or secrets required, so it also runs on
   fork PRs.

Run by CI on every PR (see .github/workflows/validate.yaml)."""
import glob
import json
import os
import re
import sys

import jsonschema
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(ROOT, "schema", "template.schema.json")
TEMPLATES_GLOB = os.path.join(ROOT, "templates", "*.yaml")

# Supported workflow task `type` strings. Source of truth:
# runbook-server/internal/tasks/registry.go (NewInitializedTaskRegistry). A
# template that references a type not in this set is rejected by the engine at
# sync time ("task not found: <type>"). Keep this list in sync when the engine
# registers a new task type; an unknown type here is a hard error, not a warning.
TASK_TYPES = {
    # core control flow
    "core.approval", "core.call-workflow", "core.foreach", "core.group",
    "core.print", "core.switch", "core.wait",
    # data
    "data.filter", "data.transform",
    # database
    "dbms.query", "dbms.redis.cli",
    # events / audit
    "events.store",
    # crypto
    "crypto.encode", "crypto.decode", "crypto.hash", "crypto.encrypt", "crypto.decrypt",
    # chat
    "google_chat.join_space", "slack.join_channel",
    # integrations / scripting
    "integrations.http", "integrations.ssh", "scripting.run_script",
    # notifications
    "notifications.add_reaction", "notifications.create_channel", "notifications.dm",
    "notifications.email", "notifications.im", "notifications.read_thread",
    # tickets / incidents
    "tickets.acknowledge", "tickets.add_comment", "tickets.assign", "tickets.create",
    "tickets.escalate", "tickets.get", "tickets.get_comments", "tickets.resolve",
    "tickets.transition", "tickets.update",
    # kubernetes
    "k8s.cli", "k8s.continuous_rightsize", "k8s.horizontal_rightsize",
    "k8s.node_graceful_shutdown", "k8s.pod_delete", "k8s.pv_rightsize",
    "k8s.vertical_rightsize", "k8s.workload_restart",
    # cloud CLIs (canonical + aliases)
    "cloud.aws.cli", "cloud.azure.cli", "cloud.gcp.cli", "cloud.k8s.cli",
    "aws.cli", "azure.cli", "gcp.cli",
    # cicd / mq / scm
    "cicd.argocd.cli", "mq.rabbitmqadmin.cli", "scm.github.cli", "scm.gitlab.cli",
    # observability
    "observability.logs", "observability.log_groups", "observability.metrics",
    "observability.traces",
    # network diagnostics
    "network.dns", "network.ntp", "network.ping", "network.ssl", "network.tcp",
    "network.traceroute", "network.whois",
    # ai / llm
    "llm.a2a_call", "llm.classify", "llm.event_investigate", "llm.investigate",
    "llm.mcp_call", "llm.nubi", "llm.router", "llm.summary",
    # internal optimization
    "vertical_rightsize_generate",
}

# Triggers the engine accepts (model.ValidateWorkflowTrigger).
TRIGGER_TYPES = {"schedule", "manual", "webhook", "event", "optimization"}

# Task id: alphanumeric / hyphen / underscore, 3-64 chars (model.ValidateTaskID).
TASK_ID_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
# Workflow name: starts/ends alphanumeric, spaces/hyphens/underscores inside,
# 3-50 chars (model.ValidateWorkflowName).
NAME_RE = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9 _-]*[a-zA-Z0-9])?$")
# Go time.ParseDuration units; durations may be concatenated (e.g. "1h30m").
DURATION_RE = re.compile(r"^(\d+(\.\d+)?(ns|us|µs|ms|s|m|h))+$")


def _is_duration(s):
    return isinstance(s, str) and bool(DURATION_RE.match(s))


def validate_workflow_semantics(doc, rel):
    """Return a list of error strings for the engine-level rules. Mirrors
    runbook-server/internal/model/validation.go."""
    errs = []

    name = doc.get("name", "")
    if not (3 <= len(name) <= 50) or not NAME_RE.match(name):
        errs.append(f"name {name!r} must be 3-50 chars, alphanumeric with spaces/_/- inside")

    definition = doc.get("definition") or {}

    version = definition.get("version")
    if version is not None and version != "v1":
        errs.append(f"definition.version must be \"v1\", got {version!r}")

    for trig in definition.get("triggers") or []:
        ttype = trig.get("type") if isinstance(trig, dict) else None
        if ttype not in TRIGGER_TYPES:
            errs.append(f"unsupported trigger type {ttype!r} (allowed: {sorted(TRIGGER_TYPES)})")

    tasks = definition.get("tasks") or []
    ids = [t.get("id") for t in tasks if isinstance(t, dict)]
    id_set = set()
    for t in tasks:
        if not isinstance(t, dict):
            errs.append("each task must be a mapping")
            continue
        tid = t.get("id", "")
        if not (3 <= len(str(tid)) <= 64) or not TASK_ID_RE.match(str(tid)):
            errs.append(f"task id {tid!r} must be 3-64 chars, [a-zA-Z0-9_-]")
        if tid in id_set:
            errs.append(f"duplicate task id {tid!r}")
        id_set.add(tid)

        ttype = t.get("type")
        if ttype not in TASK_TYPES:
            errs.append(f"task {tid!r}: unknown task type {ttype!r} (engine rejects at sync time)")

        if t.get("timeout") and not _is_duration(t["timeout"]):
            errs.append(f"task {tid!r}: invalid timeout {t['timeout']!r}")

        for dep in t.get("depends_on") or []:
            if dep == tid:
                errs.append(f"task {tid!r}: self-dependency")
            elif dep not in ids:
                errs.append(f"task {tid!r}: depends_on missing task {dep!r}")

    # Cycle detection over depends_on (DFS), matching the engine.
    adj = {t.get("id"): list(t.get("depends_on") or []) for t in tasks if isinstance(t, dict)}
    visited, rec = set(), set()

    def has_cycle(node):
        if node in rec:
            return True
        if node in visited:
            return False
        visited.add(node)
        rec.add(node)
        for d in adj.get(node, []):
            if has_cycle(d):
                return True
        rec.discard(node)
        return False

    for node in list(adj):
        if has_cycle(node):
            errs.append(f"circular dependency involving task {node!r}")
            break

    # Sum of task timeouts must not exceed the workflow timeout (engine rule).
    wf_timeout = definition.get("timeout")
    if _is_duration(wf_timeout):
        wf_secs = _duration_seconds(wf_timeout)
        total = sum(_duration_seconds(t["timeout"]) for t in tasks
                    if isinstance(t, dict) and _is_duration(t.get("timeout")))
        if total and total > wf_secs:
            errs.append(f"sum of task timeouts ({total}s) exceeds workflow timeout ({wf_secs}s)")

    return errs


_UNIT_SECS = {"ns": 1e-9, "us": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1, "m": 60, "h": 3600}


def _duration_seconds(s):
    return sum(float(num) * _UNIT_SECS[unit]
               for num, _frac, unit in re.findall(r"(\d+(\.\d+)?)(ns|us|µs|ms|s|m|h)", s))


def main():
    schema = json.load(open(SCHEMA_PATH))
    files = sorted(glob.glob(TEMPLATES_GLOB))
    if not files:
        print("no templates found", file=sys.stderr)
        sys.exit(1)

    errors = 0
    slugs = set()
    for path in files:
        name = os.path.basename(path)
        rel = os.path.relpath(path, ROOT)
        try:
            doc = yaml.safe_load(open(path, "rb").read())
        except yaml.YAMLError as e:
            print(f"::error file={path}::invalid YAML: {e}")
            errors += 1
            continue
        try:
            jsonschema.validate(doc, schema)
        except jsonschema.ValidationError as e:
            print(f"::error file={path}::schema validation failed: {e.message}")
            errors += 1
            continue
        stem = os.path.splitext(name)[0]
        if doc.get("slug") != stem:
            print(f"::error file={path}::slug {doc.get('slug')!r} must equal file name {stem!r}")
            errors += 1
        if doc.get("slug") in slugs:
            print(f"::error file={path}::duplicate slug {doc.get('slug')!r}")
            errors += 1
        slugs.add(doc.get("slug"))

        for msg in validate_workflow_semantics(doc, rel):
            print(f"::error file={path}::{msg}")
            errors += 1

    if errors:
        print(f"\n{errors} validation error(s).", file=sys.stderr)
        sys.exit(1)
    print(f"OK: {len(files)} template(s) valid.")


if __name__ == "__main__":
    main()
