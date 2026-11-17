---
name: lead-time
description: Work out lead time for changes, from the first commit of a change to the time its image first ran in production, over the latest commit-tagged deploy of each workload in the period. Use after count-deploys.
---
# Lead time for changes

Lead time runs from a change's first commit to its image first running in production. It is
measured on the latest deploy of each workload whose image tag names a commit (at most 20
workloads), not on every deploy: a busy cluster deploys hundreds of times, and a few reads per
deploy would fill the run's budget. One deploy per workload keeps a single busy workload, or one
long-lived pull request, from setting the whole number. Say "median of the latest deploy of <n>
workloads" in the report.

## 1. The latest deploy of each workload and when its image first ran
One `prometheus_execute` call, `instant: true`, `end_time` = the period's end, with `<ns_regex>` and
`<period>` as in count-deploys and `<period_seconds>` the period's length in seconds (`604800` for 7
days). `time()` is the query's `end_time`, so the last line keeps images first seen after the period's
first 15 minutes: one already running when the period began is not a deploy. The `image_spec`
matchers keep only tags that can name a commit (step 2), so a sidecar or a version-tagged image
never stands in for a workload's own deploy; all-digit tags (dates) are dropped. The inner `topk by`
keeps each workload's newest such image; the outer `topk(20, ...)` the 20 workloads that deployed
last. Work no dates out with shell commands; the shell refuses them.

```
topk(20, topk by (namespace, workload) (1, min by (namespace, workload, image_spec) (
  min_over_time(timestamp(kube_pod_container_info{namespace=~"<ns_regex>", image_spec=~".*([:_-][0-9a-f]{7,12}|[0-9a-f]{40})", image_spec!~".*[:_-][0-9]{7,12}"})[<period>:10m])
  * on (namespace, pod) group_left(workload) max by (namespace, pod, workload) (
      label_replace(max_over_time(kube_pod_owner{namespace=~"<ns_regex>", owner_kind="ReplicaSet"}[<period>]), "workload", "$1", "owner_name", "^(.+)-[a-z0-9]{6,10}$")
      or label_replace(max_over_time(kube_pod_owner{namespace=~"<ns_regex>", owner_kind=~"StatefulSet|DaemonSet"}[<period>]), "workload", "$1", "owner_name", "^(.+)$"))
) >= time() - <period_seconds> + 900))
```

Each row is an image and the time (Unix seconds, to within 10 minutes) it first ran. A rollback to
an image that ran before the period is not seen: the row is the newest image first seen in it.

When the metric is missing, read the period's ReplicaSets instead, one call per namespace, with
`<days>` as in count-deploys step 2:

```
kubectl get rs -n <ns> -o jsonpath='{range .items[*]}{.metadata.creationTimestamp}{" "}{.metadata.ownerReferences[0].name}{" "}{.spec.template.spec.containers[*].image}{"\n"}{end}' | grep -E '^(<days>)' | sort -r | head -200
```

Keep, per workload, the newest line whose image tag names a commit (step 2), at most 20 workloads;
its creation time stands for when the image first ran.

## 2. The commit
The commit is a hex string in the image tag: 40 characters, or 7 to 12 after a `-`, `_` or `:`
(`2026-03-02T10-15-00_3f9c...e1`, `1.4.0-b92c0194`). A tag with none (a version like `1.31.1`, a
date) has no commit: count it as "no commit" and go on.

## 3. The repository
Try, in order, and keep the first that has the commit for every later deploy of the same workload:
1. the repositories in the `code_repositories` setting;
2. the workload's own repository, read without the whole object:
   `kubectl get <kind> <name> -n <ns> -o jsonpath='{.metadata.annotations.ci\.nudgebee\.com/git\.repo} {.metadata.labels.argocd\.argoproj\.io/instance}'`.
   For an Argo CD instance, the Application's `repoURL` is the repository; it may be a config
   repository: report that as config lead time.

## 4. When the change began
The commit in the tag is often a merge, whose time is when the change was merged, not when the work
on it started. Read the change's pull request and its first commit:
- GitHub: `gh api repos/<owner>/<repo>/commits/<sha>/pulls --jq '.[0].number'`, then
  `gh api repos/<owner>/<repo>/pulls/<number>/commits --jq '.[0].commit.author.date | fromdateiso8601'`;
- GitLab: `glab api "projects/<url-encoded repo>/repository/commits/<sha>/merge_requests" | jq -r '.[0].iid'`,
  then `glab api "projects/<url-encoded repo>/merge_requests/<iid>/commits" | jq -r '.[-1].created_at | sub("\\.[0-9]+"; "") | sub("[+-]00:00$"; "Z") | fromdateiso8601'`.

Send each step's reads for all deploys in one turn. With no pull request (a direct push), use the
commit's own time: `gh api repos/<owner>/<repo>/commits/<sha> --jq '.commit.author.date | fromdateiso8601'`.

These answer Unix seconds, like the times in step 1, so lead time is one subtraction. Don't use other
tools to convert dates: the SQL and shell tools refuse arithmetic-only queries.

- Not found (404, "No commit found"): try the next repository; with none left, "no commit".
- Refused (401, 403, "permission denied", no integration): report "lead time not available:
  <error>" and stop this skill; every other read would fail the same way.

## 5. Lead time
For each deploy with a start time: (first run − first commit) / 3600, in hours. Report the median and the
longest, how many of the deploys read had a commit, and how many came from a pull request. A negative value
means a clock or matching problem: drop that deploy and list it. With fewer than 3 values, list them
and report no median.
