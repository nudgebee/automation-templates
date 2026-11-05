---
name: count-deploys
description: List every production deploy in the report period, one row per workload whose container image changed, with the image and (where it can be read) the commit it came from. Use first in every run; the other metrics are built on this list.
---
# Count deploys

A deploy is a rollout that changed a workload's container image in a production namespace. A
replica count change, an env-only or config-only change, or a restart is not a deploy, even when
it rolled out new pods. Work only from what you can read; when a source is unreadable, say so and
use the next one.

## 0. Check the namespaces exist
`kubectl get ns <ns>` for each production namespace. A namespace that doesn't exist is a setup
problem, not a quiet week: leave it out, and report "namespace <ns> not found on this cluster" under
what could not be read. If none of them exist, every metric is "not available: none of the
production namespaces (<list>) exist on this cluster", never 0, and the run's verdict is `none`.

## 1. Pick the source, per workload, in this order
Read the workload's markers with find-deploy-source step 1.

| Source | When | What one deploy is |
|---|---|---|
| Argo CD | The workload carries an Argo CD marker and `argocd_execute` works | See step 2. |
| ReplicaSets | A Deployment, without Argo CD | See step 3. |
| ControllerRevisions | A StatefulSet or DaemonSet | See step 3. |
| Change events | None of the above is readable | `list_events` with `finding_type: configuration_change`, `subject_namespace` = the namespaces, and the period as `start_time`/`end_time`. Keep only events whose evidence (`get_event_by_id`) shows an image change. |

## 2. Argo CD
History is per Application, not per workload, and a new revision may change only replicas or
config.
- `argocd app history <app> -o json`: take the entries with `deployedAt` in the period, and the
  entry just before the period.
- For each entry in the period, compare the workloads' images at its revision with the entry
  before it: `argocd app manifests <app> --revision <rev>`, then the `image` of every container in
  each Deployment, StatefulSet and DaemonSet.
- Emit one row per workload whose image changed. An entry where no image changed is not a deploy.
- The entry's `revision` is the commit for each of its rows.

## 3. ReplicaSets and ControllerRevisions
A rollout either creates a new ReplicaSet (or ControllerRevision) or, for a rollback or a redeploy
of an earlier template, reuses an old one and only raises its revision number. Read both:
- Deployment: `kubectl get rs -n <ns> -o json`, the ReplicaSets owned by it. A ReplicaSet became
  current in the period when either:
  - its `creationTimestamp` is in the period; or
  - it has a `deployment.kubernetes.io/revision-history` annotation, and the oldest pod it owns
    (`kubectl get pods -n <ns> -l pod-template-hash=<hash> -o json`) was created in the period.
- StatefulSet or DaemonSet: `kubectl get controllerrevisions -n <ns> -o json`, the revisions owned by
  it, ordered by `revision`; the same two conditions apply, with `controller-revision-hash` as the
  pod label.
- Order them by `deployment.kubernetes.io/revision` (or `revision`). Each one that became current in
  the period is a deploy only when its pod template's images differ from the one current before it.

History only keeps the last `revisionHistoryLimit` (10 by default). If the oldest kept one is newer
than the period's start, say "deploys before <time> are not visible for <workload>" in the report.

## 4. Keep one row per deploy
For each deploy record: workload (kind/namespace/name), time (UTC), old image → new image, and the
commit SHA when one is known (the Argo CD revision, or an image tag that is a 7-40 character hex
string). Mark the source each row came from.

## 5. Rollbacks
A deploy whose new image equals the image of a deploy before it in the period, or that reused an
older ReplicaSet or ControllerRevision (step 3, second condition), is a rollback. Keep it as a
deploy, mark it `rollback`, and name the deploy it undid.
