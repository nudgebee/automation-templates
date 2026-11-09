---
name: count-deploys
description: Count every production deploy in the report period, per namespace and workload, where a deploy is a new container image. Use first in every run; the other metrics are built on it.
---
# Count deploys

A deploy is a new container image on a production workload. A replica change, an env-only or
config-only change, or a restart is not a deploy, even when it rolled out new pods.

Read counts, never lists: a busy cluster deploys hundreds of times a week, and a list of them fills
the run's budget before any number is worked out. Every read below answers one row per workload or
namespace.

## 0. Check the namespaces exist
One call: `kubectl get ns <ns1> <ns2> ...`. A namespace that doesn't exist is a setup problem, not a
quiet week: leave it out, and report "namespace <ns> not found on this cluster" under what could not
be read. If none of them exist, every metric is "not available: none of the production namespaces
(<list>) exist on this cluster", never 0, and the run's verdict is `none`.

## 1. Count from the cluster's metrics (first choice)
One `prometheus_execute` call, `instant: true`, `end_time` = the period's end, where `<ns_regex>` is the
production namespaces joined with `|` and `<period>` is the period's length (`7d`, or hours such as
`80h` for a partial period):

```
max by (namespace, workload) (
  count by (namespace, workload, container) (
    count by (namespace, workload, container, image) (
      label_replace(label_replace(
        max_over_time(kube_pod_container_info{namespace=~"<ns_regex>"}[<period>]),
        "workload", "$1", "pod", "^(.+)-[0-9]+$"),
        "workload", "$1", "pod", "^(.+)-[a-z0-9]{6,10}-[a-z0-9]{5}$")))) - 1
```

Each row is a workload and the number of images it ran in the period less one: the image it was
already running when the period began. That is its deploys. A row with an empty `workload` is pods of
a Job, CronJob or DaemonSet; leave it out and say DaemonSet deploys are not counted.

- The metric is missing, or the query errors: go to step 2.
- A workload created during the period counts its first image as a deploy only when it replaced
  another; say so if one looks new.

## 2. Fall back to ReplicaSets
Only when step 1 could not be read. One call per namespace; only `grep`, `cut`, `sort`, `uniq`, `head`,
`tail`, `wc` and `tr` may follow a pipe (loops, `awk` and scripts are refused), where `<days>` is the
period's dates joined with `|`, for example `2026-03-02|2026-03-03|...`:

```
kubectl get rs -n <ns> -o jsonpath='{range .items[*]}{.metadata.creationTimestamp}{" "}{.metadata.ownerReferences[0].name}{" "}{.spec.template.spec.containers[*].image}{"\n"}{end}' | grep -E '^(<days>)' | cut -d' ' -f2- | sort -u | cut -d' ' -f1 | uniq -c | sort -rn
```

Each line is a count of new image sets per Deployment. Kubernetes keeps only the last
`revisionHistoryLimit` ReplicaSets (10 by default), so a workload that deployed more often than that
shows fewer: also run `kubectl get rs -n <ns> --sort-by=.metadata.creationTimestamp -o custom-columns='CREATED:.metadata.creationTimestamp' --no-headers | head -1`, and when the oldest kept one is newer
than the period's start, report deployment frequency as "at least <n>" for that namespace and add a
finding (see the agent). StatefulSets and DaemonSets are not counted on this path; say so.

## 3. Keep, per workload
Namespace, workload and its deploy count, and which step it came from. Deployment frequency is the
sum over workloads divided by the period's days.
