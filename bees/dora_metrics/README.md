# DORA Metrics Bee

Once a week, works out the four DORA metrics for one cluster, adds them to a report issue, and keeps
them on the bee's **DORA metrics** dashboard, week over week:

1. **Deployment frequency:** new container images on production workloads per day, counted from the
   cluster's metrics (`kube_pod_container_info`, each pod named by its owner from `kube_pod_owner`),
   so it is right however often a workload deploys.
   Without those metrics it falls back to ReplicaSets, which keep only the last
   `revisionHistoryLimit` rollouts and give a lower bound.
2. **Lead time for changes:** time from a change's first commit (the first commit of its pull
   request, so a squash merge doesn't hide the work before it) to its image first running in
   production, over the latest deploy of each workload whose image tag names a commit (at most 20
   workloads), so one busy workload, one long-lived pull request or a sidecar's own update doesn't
   set the number. Needs a GitHub or GitLab integration;
   set **Code repositories** when the commits are not in the workload's own repository.
3. **Change failure rate:** production changes (any recorded change to a Deployment, StatefulSet or
   DaemonSet spec) followed by a HIGH alert on the same workload within the failure window, before
   the workload's next change.
4. **Time to restore:** median time HIGH alerts in production took to resolve, every such alert,
   not only those a deploy caused.

These are DORA's original four keys. DORA's current model has five, with failed deployment recovery
time (recovery from a deploy that failed) instead of time to restore, and deployment rework rate.
Both need each deploy, its rollbacks and the alerts it caused, which the bee can't read within its
budget on a busy cluster: the platform keeps no per-rollout record it can query, and its change
events can't be told apart by image cheaply. The report and the dashboard name the four as they are
measured, so they aren't read as DORA's current definitions or compared with its benchmarks.

A failure is a HIGH alert; **Alerts that are not failures** leaves out alerts by title, such as test
alerts. Each report covers the `report_days` days before it ran, so weekly reports are back to back
and a run by hand never shifts them. A report's first line says whether it is weekly or by hand. A
weekly report works out the change since the last report a run by hand didn't write, so a run by
hand in between doesn't stop it; a report by hand works out no change.

Every number comes from one aggregate read (the platform's events, the cluster's metrics), so a
cluster that deploys hundreds of times a week costs the same as a quiet one. A metric it can't read
is reported as "not available" with the reason, never as zero, and is listed on the dashboard under
**Findings**, next to each workload whose changes keep failing (3 or more failed changes in a
week, a finding the platform derives from the weekly counts). A quiet week (no changes, no failures) is worked out, with no
number.

Needs: a tracker (GitHub, GitLab or Jira) for the report. Optional: a GitHub or GitLab integration
for lead time, and the Argo CD integration for config-repository lead time.

Limits in this version:
- A failure is one HIGH alert, not a grouped incident; bees have no incident tool yet.
- Change failure rate counts every spec change, not only image deploys, since the platform's change
  events can't be told apart by image cheaply.
- A rollback to an image run earlier in the period is not counted again; one build on two workloads
  is two deploys; lead time's deploy time is accurate to 10 minutes.
- Lead time doesn't see a rollback to an image that ran before the period: a workload's latest
  deploy is the newest image first seen in the period, even if it was rolled back after.
- Only scheduled runs add a point to the dashboard; a run by hand updates the report issue only.
