---
name: count-deploys
description: List every production deploy in the report period, one row per workload rollout, with the image and (where it can be read) the commit it came from. Use first in every run; the other metrics are built on this list.
---
# Count deploys

A deploy is a rollout that changed a workload's container image in a production namespace. A
replica count change, an env-only change or a restart is not a deploy. Work only from what you
can read; when a source is unreadable, say so and use the next one.

## 1. Pick the source, in this order
| Source | When | What one deploy is |
|---|---|---|
| Argo CD history | The workload carries `argocd.argoproj.io/instance` or `argocd.argoproj.io/tracking-id` (find-deploy-source's markers), and `argocd_execute` works | One entry of `argocd app history <app> -o json` with `deployedAt` in the period whose revision differs from the entry before it. The entry's `revision` is the commit. |
| ReplicaSets | A Deployment, without Argo CD | `kubectl get rs -n <ns> -o json`: one ReplicaSet owned by the Deployment with `creationTimestamp` in the period. Its `deployment.kubernetes.io/revision` annotation orders them; its pod template images are the deploy's images. |
| ControllerRevisions | A StatefulSet or DaemonSet | `kubectl get controllerrevisions -n <ns> -o json`: one revision owned by it, created in the period, whose images differ from the revision before it. |
| Change events | None of the above is readable | `list_events` with `finding_type: configuration_change`, `subject_namespace` = the namespaces, and the period as `start_time`/`end_time`. Keep only events whose title or evidence (`get_event_by_id`) shows an image change. |

ReplicaSets and ControllerRevisions only keep the last `revisionHistoryLimit` (10 by default). If
the oldest kept one is newer than the period's start, say "deploys before <time> are not
visible for <workload>" in the report.

## 2. Keep one row per deploy
For each deploy record: workload (kind/namespace/name), time (UTC), old image → new image, and
the commit SHA when one is known (the Argo CD revision, or an image tag that is a 7-40 character
hex string). Mark the source each row came from.

A ReplicaSet that only differs in a `kubectl.kubernetes.io/restartedAt` annotation is a restart,
not a deploy. Drop it.

## 3. Rollbacks
A deploy whose new image equals the image of a deploy before it in the period is a rollback.
Keep it as a deploy, mark it `rollback`, and name the deploy it undid.
