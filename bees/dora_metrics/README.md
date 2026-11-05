# DORA Metrics Bee

Once a week, works out the four DORA metrics for one cluster and adds them to a report issue:

1. **Deployment frequency:** image rollouts in production namespaces, read from Argo CD history, ReplicaSets or ControllerRevisions. Scaling, env-only changes and restarts don't count.
2. **Lead time for changes:** deploy time minus the time of the commit behind it. Needs the commit SHA (an Argo CD revision, or an image tag that is a SHA) and a GitHub or GitLab integration to read it.
3. **Change failure rate:** deploys followed by a HIGH alert on the same workload within the failure window, or rolled back.
4. **Time to restore:** how long HIGH alerts in production took to resolve.

Every number comes with the deploys and alerts behind it, and with last period's number for comparison. A metric it can't read is reported as "not available" with the reason, never as zero.

Needs: a tracker (GitHub, GitLab or Jira) for the report. Optional: a GitHub or GitLab integration for lead time, and the Argo CD integration for deploy history.

Limits in this version:
- A failure is one HIGH alert, not a grouped incident; bees have no incident tool yet.
- ReplicaSet and ControllerRevision history only goes back `revisionHistoryLimit` revisions.
- For Argo CD, lead time is measured from the config repository's commit, which may not be the application's code commit.
