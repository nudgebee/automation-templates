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
    "events.store", "events.add_evidence",
    # crypto
    "crypto.encode", "crypto.decode", "crypto.hash", "crypto.encrypt", "crypto.decrypt",
    # chat
    "google_chat.join_space", "slack.join_channel",
    # integrations / scripting
    "integrations.http", "integrations.ssh", "scripting.run_script",
    # notifications
    "notifications.add_channel_members", "notifications.add_reaction",
    "notifications.create_channel", "notifications.dm",
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

    workflow_hooks = [a for v in (definition.get("hooks") or {}).values() if isinstance(v, list) for a in v]
    errs.extend(_required_param_errors(list(tasks) + workflow_hooks))
    errs.extend(_cron_errors(definition.get("triggers")))

    return errs


# Params each task's InputSchema marks Required with no Default in runbook-server
# (types.Schema.Validate, "missing required parameter"). Keep in sync with the engine.
REQUIRED_PARAMS = {
    "aws.cli": ["command"], "azure.cli": ["command"], "gcp.cli": ["command"],
    "cloud.aws.cli": ["command"], "cloud.azure.cli": ["command"],
    "cloud.gcp.cli": ["command"], "cloud.k8s.cli": ["command"], "k8s.cli": ["command"],
    "cicd.argocd.cli": ["command", "integration_id"],
    "core.approval": ["message"], "core.call-workflow": ["workflow_name"],
    "core.foreach": ["items", "tasks"], "core.group": ["tasks"], "core.print": ["message"],
    "core.switch": ["expression"], "core.wait": ["duration"],
    "crypto.decode": ["algorithm", "data"], "crypto.decrypt": ["data", "key"],
    "crypto.encode": ["algorithm", "data"], "crypto.encrypt": ["data", "key"],
    "crypto.hash": ["algorithm", "data"],
    "data.filter": ["condition", "list"], "data.transform": ["expression", "input"],
    "dbms.query": ["command", "dbms_type", "integration_id"],
    "dbms.redis.cli": ["command", "integration_id"],
    "events.add_evidence": ["event_id"], "events.store": ["event"],
    "google_chat.join_space": ["channel_id"], "slack.join_channel": ["channel_id"],
    "integrations.http": ["url"], "integrations.ssh": ["command", "integration_id"],
    "k8s.continuous_rightsize": ["account_id", "kind", "name", "namespace"],
    "k8s.horizontal_rightsize": ["direction", "kind", "name", "namespace"],
    "k8s.node_graceful_shutdown": ["name"], "k8s.pod_delete": ["name", "namespace"],
    "k8s.profile": ["namespace", "pod_name"],
    "k8s.pv_rightsize": ["gitops_config", "name", "namespace"],
    "k8s.vertical_rightsize": ["direction", "kind", "name", "namespace"],
    "k8s.workload_restart": ["kind", "name", "namespace"],
    "llm.a2a_call": ["method", "url"], "llm.classify": ["options", "prompt"],
    "llm.event_investigate": ["account_id", "event_id"], "llm.investigate": ["message"],
    "llm.mcp_call": ["connection_mode", "tool_name"], "llm.nubi": ["message"],
    "llm.router": ["prompt"], "llm.summary": ["message"],
    "mq.rabbitmqadmin.cli": ["command", "integration_id"],
    "network.dns": ["domain"], "network.ping": ["host"], "network.ssl": ["host"],
    "network.tcp": ["host", "port"], "network.traceroute": ["host"], "network.whois": ["domain"],
    "notifications.add_channel_members": ["channel_id", "provider", "user_ids"],
    "notifications.add_reaction": ["channel_id", "emoji", "message_id", "provider"],
    "notifications.create_channel": ["name", "provider"],
    "notifications.dm": ["message", "provider", "user_id"],
    "notifications.email": ["body", "recipients", "subject"],
    "notifications.im": ["channel", "message", "provider"],
    "notifications.read_thread": ["channel_id", "provider", "thread_ts"],
    "observability.logs": ["query"], "observability.metrics": ["queries"],
    "scm.github.cli": ["command", "integration_id"], "scm.gitlab.cli": ["command", "integration_id"],
    "scripting.run_script": ["script"],
    "tickets.acknowledge": ["integration_id", "ticket_id"],
    "tickets.add_comment": ["comment", "integration_id", "ticket_id"],
    "tickets.assign": ["assignee", "integration_id", "ticket_id"],
    "tickets.create": ["description", "integration_id", "project_key", "title"],
    "tickets.escalate": ["integration_id", "ticket_id"], "tickets.get": ["integration_id", "ticket_id"],
    "tickets.get_comments": ["ticket_id"], "tickets.resolve": ["integration_id", "ticket_id"],
    "tickets.transition": ["integration_id", "status", "ticket_id"],
    "tickets.update": ["integration_id", "ticket_id"],
    "vertical_rightsize_generate": ["account_id", "namespace"],
}


def _nested_tasks(task):
    """Child tasks of a group / foreach / switch, and hook tasks."""
    params = task.get("params") if isinstance(task.get("params"), dict) else {}
    for v in list(task.values()) + list(params.values()) + list((task.get("hooks") or {}).values()):
        if isinstance(v, list):
            yield from (c for c in v if isinstance(c, dict) and "type" in c)


def _required_param_errors(tasks):
    errs = []
    for t in tasks or []:
        if not isinstance(t, dict):
            continue
        params = t.get("params") if isinstance(t.get("params"), dict) else {}
        for name in REQUIRED_PARAMS.get(t.get("type"), []):
            if params.get(name) in (None, ""):
                owner = f"task {t['id']!r}" if t.get("id") else "hook"
                errs.append(f"{owner}: missing required parameter {name!r} for {t.get('type')}")
        errs.extend(_required_param_errors(list(_nested_tasks(t))))
    return errs


def _cron_errors(triggers):
    # The engine checks the cron when the automation is saved, before any input
    # is resolved, so a templated cron like {{ Inputs.cron }} never validates.
    errs = []
    for trig in triggers or []:
        if not isinstance(trig, dict) or trig.get("type") != "schedule":
            continue
        params = trig.get("params") if isinstance(trig.get("params"), dict) else {}
        cron = str(params.get("cron", "")).strip()
        spec = re.sub(r"^(CRON_TZ|TZ)=\S+\s+", "", cron)  # the engine strips this before parsing
        if "{{" in cron or not (spec.startswith("@") or len(spec.split()) == 5):
            errs.append(f"schedule trigger cron {cron!r} must be a literal cron expression (5 fields or @descriptor)")
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
