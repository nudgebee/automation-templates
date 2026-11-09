---
name: lead-time
description: Work out lead time for changes, from a commit's time to the time its image first ran in production, over the period's latest deploys. Use after count-deploys.
---
# Lead time for changes

Lead time is measured on the 10 latest deploys of the period that name a commit, not on every
deploy: a busy cluster deploys hundreds of times, and one commit read per deploy would fill the run's
budget. Say "median of the latest <n> deploys" in the report.

## 1. The latest deploys and when each image first ran
One `prometheus_execute` call, `instant: true`, `end_time` = the period's end, with `<ns_regex>` and
`<period>` as in count-deploys and `<start_epoch>` = the period's start in Unix seconds plus 900 (an
image already running when the period began is not a deploy):

```
topk(10, min by (namespace, workload, image) (
  label_replace(label_replace(
    min_over_time(timestamp(kube_pod_container_info{namespace=~"<ns_regex>"})[<period>:10m]),
    "workload", "$1", "pod", "^(.+)-[0-9]+$"),
    "workload", "$1", "pod", "^(.+)-[a-z0-9]{6,10}-[a-z0-9]{5}$")) >= <start_epoch>)
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
2. the workload's own repository, by find-deploy-source step 1 (`ci.nudgebee.com/git.repo`, or Argo
   CD's `repoURL`). For Argo CD this may be a config repository: report that as config lead time.

## 4. Read the commit time
- GitHub: `gh api repos/<owner>/<repo>/commits/<sha> --jq '.commit.committer.date'`;
- GitLab: `glab api "projects/<url-encoded repo>/repository/commits/<sha>" | jq -r .committed_date`.

- Not found (404, "No commit found"): try the next repository; with none left, "no commit".
- Refused (401, 403, "permission denied", no integration): report "lead time not available:
  <error>" and stop this skill; every other read would fail the same way.

## 5. Lead time
For each deploy with a commit time: first run − commit time, in hours. Report the median and the
longest, and how many of the 10 had a commit. A negative value means a clock or matching problem:
drop that deploy and list it. With fewer than 3 values, list them and report no median.
