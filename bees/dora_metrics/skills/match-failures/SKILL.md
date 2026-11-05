---
name: match-failures
description: Decide which deploys failed (change failure rate) and how long production took to recover from each failure (time to restore). Use after count-deploys.
---
# Failed deploys and time to restore

## What a production failure is
An event from `list_events` with `finding_type: issue`, `priority: [HIGH]`, a production
namespace, and `starts_at` in the period. MEDIUM and lower are not failures. Repeats of the same
`fingerprint` while it is still firing are one failure.

There is no incident tool for bees yet, so a failure is one alert, not a grouped incident. Say
that once in the report's method line.

## Failed deploys (change failure rate)
A deploy failed when either:
- a failure starts on the same workload within `failure_window_minutes` after the deploy. Same
  workload means `subject_owner` equals the workload's name, or `subject_name` is a pod of it:
  `<workload>-<pod-template-hash>-<suffix>` for a Deployment, `<workload>-<ordinal>` for a
  StatefulSet. A plain prefix is not enough: workload `api` must not match `api-gateway`; or
- the deploy was rolled back (count-deploys step 3), even with no alert.

Change failure rate = failed deploys / deploys. With no deploys, report "no deploys" and no rate.

A failure that started before the deploy and was still firing is not caused by it. Don't count it.

## Time to restore
For every failure in the period, deploy-linked or not:
- start is `starts_at`;
- end is `ends_at` from `get_event_by_id`, when `status` is RESOLVED or CLOSED;
- still firing at the end of the period means "not restored". List it, and leave it out of the
  median.

Report the median and the longest, in minutes. With fewer than 3 restored failures, list them
instead of a median.
