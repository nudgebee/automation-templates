# automation-templates

System runbook templates for [Nudgebee](https://nudgebee.com). Each file under
`templates/` is one reusable workflow template (approval → remediate → verify, or a
read-only diagnostic). Nudgebee's `runbook-server` periodically syncs this repo —
pinned to an immutable commit SHA — into the `workflow_templates` table, where
tenants instantiate them.

This repo is the source of truth for **system** templates. A vendored snapshot is
also embedded in the runbook-server image as an offline/air-gapped fallback.

## Layout

```
templates/<slug>.yaml          # one template per file; file name MUST equal `slug`
manifest.yaml                  # generated index [{slug, path, sha256}] — runbook-server verifies each file against this
schema/template.schema.json    # JSON Schema; CI validates every template
scripts/gen_manifest.py        # regenerate manifest.yaml (run before committing template changes)
scripts/validate.py            # local equivalent of the CI validation (schema + workflow semantics)
```

## Validation layers

`scripts/validate.py` (run in CI on every PR, including fork PRs — no secrets or
backend required) checks two things:

1. **Structure** — the JSON Schema in `schema/template.schema.json`: required keys,
   enums, `slug` == file name, no duplicate slugs.
2. **Workflow semantics** — the same rules the engine enforces in
   `runbook-server/internal/model/validation.go` and the task registry: every task
   `type` is a real engine task type (an unknown/mistyped type is rejected at sync
   time as `task not found`), `depends_on` resolves to an existing task, no
   duplicate task ids, no dependency cycles, supported trigger types, and
   well-formed durations. This is the offline equivalent of `nbctl workflow
   validate` — it needs no backend, so it gates external contributions too.

When the engine registers a new task type, add it to the `TASK_TYPES` set in
`scripts/validate.py` (its source of truth is `runbook-server`'s task registry).

## Authoring a template

1. Add or edit `templates/<slug>.yaml`. The `slug` field must match the file name
   and is the immutable key used to upsert/supersede the row — never rename it.
2. Use block scalars for multi-line commands/messages; keep keys snake_case
   (`input_ref`, `event_sources`, `depends_on`, …).
3. Only reference task types the engine supports (e.g. `core.approval`,
   `cloud.k8s.cli`, `cloud.aws.cli`, `tickets.create`). Unknown task types are
   rejected at sync time.
4. Regenerate the manifest and validate:
   ```bash
   pip install pyyaml jsonschema
   python3 scripts/gen_manifest.py
   python3 scripts/validate.py
   ```
5. Open a PR. CI re-runs validation and checks the manifest is current. Merges
   require review (see `CODEOWNERS`).

## How it's consumed

runbook-server resolves a configured ref to a commit SHA, downloads the tarball at
that SHA, verifies every file's sha256 against `manifest.yaml`, then upserts each
template idempotently (matched by `slug`). Syncing writes *definitions* only —
execution always requires a tenant user to instantiate a template.
