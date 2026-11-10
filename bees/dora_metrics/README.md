# DORA Metrics Bee

Once a week, works out the five DORA metrics for one cluster and adds them to a report issue.

Throughput:

1. **Deployment frequency:** image rollouts in production namespaces, read from Argo CD history, ReplicaSets or ControllerRevisions. Scaling, env-only changes and restarts don't count.
2. **Change lead time:** deploy time minus the time of the commit behind it. Needs the commit SHA (an Argo CD revision, or an image tag that is a SHA) and a GitHub or GitLab integration to read it.
3. **Failed deployment recovery time:** for each failed deploy, from the deploy until the alerts it caused resolved, or until it was rolled back. A failed deploy still failing at the end of a week is checked again the next week.

Instability:

4. **Change fail rate:** deploys that caused a HIGH alert on the same workload within the failure window, or were rolled back. One alert fails one deploy.
5. **Deployment rework rate:** deploys made because of a production failure: rollbacks, and hotfixes (deploys after which a firing HIGH alert on the same workload resolved within the failure window).

Every number comes with the deploys and alerts behind it, and with last period's number for comparison. A metric it can't read is reported as "not available" with the reason, never as zero.

Each weekly period starts where the last one ended and ends when the run starts, so a missed week is caught up, not skipped. Once a week's comment is posted, the platform keeps its numbers (`bee_metrics`), and the **DORA metrics** dashboard the install adds shows them week by week. A run started by hand reports the week so far as partial; it doesn't end the period, and its numbers aren't kept.

Needs: a tracker (GitHub, GitLab or Jira) for the report. Optional: a GitHub or GitLab integration for lead time, and the Argo CD integration for deploy history.

Limits in this version:
- A failure is one HIGH alert, not a grouped incident; bees have no incident tool yet.
- Rework is inferred: the cluster doesn't say whether a deploy was planned.
- A deploy is judged on what happened by the end of its week: an alert that fires, a rollback, or a hotfix's alert that resolves only in the next week doesn't change it.
- When comments can't be posted for weeks, a report covers at most four periods; older days are skipped and named.
- ReplicaSet and ControllerRevision history only goes back `revisionHistoryLimit` revisions.
- For Argo CD, lead time is measured from the config repository's commit, which may not be the application's code commit.
