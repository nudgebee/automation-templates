---
name: match-failures
description: Decide which deploys failed (change fail rate), how long each failed deploy took to recover from (failed deployment recovery time), and which deploys were rework (deployment rework rate). Use after count-deploys.
---
# Failed deploys, recovery time and rework

## Production failures
A failure is an event from `list_events` with `finding_type: issue`, `priority: [HIGH]` and a
production namespace. MEDIUM and lower are not failures. Repeats of the same `fingerprint` while
it is still firing are one failure. A failure ends at its `ends_at` from `get_event_by_id`, when
`status` is RESOLVED or CLOSED; otherwise it is still firing.

Read failures with `starts_at` from `report_days` days before the period's start to its end. Only
those that start in the period count for change fail rate, recovery time and the report's failure
list. The earlier ones are read only to find rework: a hotfix for a failure that started before
the period.

There is no incident tool for bees yet, so a failure is one alert, not a grouped incident. Say
that once in the report's method line.

A failure is on a workload when `subject_owner` equals the workload's name, or `subject_name` is a
pod of it: `<workload>-<pod-template-hash>-<suffix>` for a Deployment, `<workload>-<ordinal>` for a
StatefulSet. A plain prefix is not enough: workload `api` must not match `api-gateway`.

## Which deploy caused a failure
A failure that starts on a workload within `failure_window_minutes` after a deploy of it was caused
by one deploy, never several:
- when the failure's `subject_name` is a Deployment's pod, its pod-template-hash names the
  ReplicaSet: the cause is the latest deploy in the window that made that ReplicaSet current (a
  rollback reuses an older one). When no deploy in the window did, no deploy caused it;
- otherwise (a StatefulSet's pod, or the workload itself) the latest deploy of the workload before
  the failure started.

A failure that was already firing when the deploy went out was not caused by it.

## Failed deploys (change fail rate)
A deploy failed when either:
- it caused a failure; or
- it was rolled back (count-deploys step 5), even with no alert.

Change fail rate = failed deploys / deploys, as a percent. With no deploys, report "no deploys"
and no rate.

## Failed deployment recovery time
For every failed deploy, how long production took to recover from it:
- start is the deploy's time;
- end, when it caused failures, is the latest end among them;
- end, when only a rollback made it failed, is the rollback's time;
- a failure it caused that is still firing at the end of the period means "not restored". List
  it, and leave it out of the median.

Failed deploys that the last weekly comment lists as not restored: read their failures again with
`get_event_by_id`. One restored by now counts in this period's recovery time, marked as from an
earlier period. One still firing is listed again as not restored. A comment from before this
version lists alerts, not failed deploys: there is nothing to re-read from it.

Report the median and the longest, in minutes, over the failed deploys restored in this period.
With fewer than 3, list them instead of a median. With none, report "no restored failed deploys"
and no time.

A failure no deploy caused is not part of this metric. It is still listed with the failures in the
report.

## Rework deploys (deployment rework rate)
A rework deploy is an unplanned one, made because of a production failure. A deploy is rework when
either:
- it is a rollback (count-deploys step 5); or
- it ended a failure on the same workload: the failure was firing when the deploy went out and
  ended within `failure_window_minutes` after it (a hotfix).

A deploy made while a failure was firing that it didn't end is not rework: a long-running or noisy
alert doesn't make every deploy unplanned.

Deployment rework rate = rework deploys / deploys, as a percent. With no deploys, report "no
deploys" and no rate.

The cluster doesn't say whether a deploy was planned, so rework is inferred. Say so in the method
line: rollbacks, and deploys after which a firing failure on the same workload ended within the
failure window.
