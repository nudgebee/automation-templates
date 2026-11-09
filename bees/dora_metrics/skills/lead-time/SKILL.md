---
name: lead-time
description: Work out lead time for changes, from the first commit of a change to the time its image first ran in production, over the period's latest deploys. Use after count-deploys.
---
# Lead time for changes

Lead time runs from a change's first commit to its image first running in production. It is
measured on the 10 latest deploys of the period that name a commit, not on every deploy: a busy
cluster deploys hundreds of times, and a few reads per deploy would fill the run's budget. Say
"median of the latest <n> deploys" in the report.

## 1. The latest deploys and when each image first ran
One `prometheus_execute` call, `instant: true`, `end_time` = the period's end, with `<ns_regex>` and
`<period>` as in count-deploys and `<period_seconds>` the period's length in seconds (`604800` for 7
days). `time()` is the query's `end_time`, so the last line keeps images first seen after the period's
first 15 minutes: one already running when the period began is not a deploy. Work no dates out with
shell commands; the shell refuses them.

```
topk(10, min by (namespace, workload, image_spec) (
  min_over_time(timestamp(kube_pod_container_info{namespace=~"<ns_regex>"})[<period>:10m])
  * on (namespace, pod) group_left(workload) max by (namespace, pod, workload) (
      label_replace(max_over_time(kube_pod_owner{namespace=~"<ns_regex>", owner_kind="ReplicaSet"}[<period>]), "workload", "$1", "owner_name", "^(.+)-[a-z0-9]{6,10}$")
      or label_replace(max_over_time(kube_pod_owner{namespace=~"<ns_regex>", owner_kind=~"StatefulSet|DaemonSet"}[<period>]), "workload", "$1", "owner_name", "^(.+)$"))
) >= time() - <period_seconds> + 900)
```

Each row is an image and the time (Unix seconds, to within 10 minutes) it first ran. When the metric
is missing, take the newest ReplicaSets' creation time and image from count-deploys step 2 instead.

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
longest, how many of the 10 had a commit, and how many came from a pull request. A negative value
means a clock or matching problem: drop that deploy and list it. With fewer than 3 values, list them
and report no median.
