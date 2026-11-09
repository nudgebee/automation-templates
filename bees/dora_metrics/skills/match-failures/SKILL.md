---
name: match-failures
description: Work out change failure rate (changes followed by a production failure) and time to restore (how long production failures took to resolve) from the platform's events. Use after count-deploys.
---
# Change failure rate and time to restore

Every number here comes from `events_execute`, as one SQL query each, copied as written with only
the `<...>` values filled in:
- `<namespaces>`: the production namespaces, quoted, e.g. `'payments', 'checkout'`;
- `<start>`, `<end>`: the period, ISO 8601 UTC;
- `<window>`: `failure_window_minutes`; `<end_plus_window>`: `<end>` plus that many minutes.

The tool returns at most 10 rows a call and refuses joins, subqueries and backslashes, which is why
the queries look the way they do. Don't rewrite them, don't add columns, and don't read events one by
one: a list of events fills the run's budget.

## What a failure is
An event with `finding_type = 'issue'` and `priority = 'HIGH'` in a production namespace that started
in the period. MEDIUM and lower are not failures. A failure is one alert, not a grouped incident;
bees have no incident tool yet. Say that once in the report's method line.

## Changes
A change is any change the platform recorded to a production Deployment, StatefulSet or DaemonSet's
spec: a new image, and also an env, config or resources change. That is more than the deploys
count-deploys counted; say "N changes" for change failure rate, never "N deploys".

```
SELECT subject_namespace AS namespace, count(*) AS changes, count(DISTINCT subject_name) AS workloads
FROM events
WHERE finding_type = 'configuration_change' AND subject_type IN ('deployment', 'statefulset', 'daemonset') AND subject_namespace IN (<namespaces>)
  AND starts_at >= '<start>' AND starts_at < '<end>'
GROUP BY 1 ORDER BY 1
```

No row for a namespace means the platform recorded no change there. When count-deploys found deploys
in a namespace with no changes, the platform's change history is missing there: change failure rate
is "not available: no change history for <ns>" for it.

## Failed changes
A change failed when a failure starts on the same workload within `<window>` minutes after it, and
before that workload's next change: a failure is put on the latest change before it, never on two.

```
SELECT subject_namespace AS namespace, CASE WHEN finding_type = 'issue' THEN subject_owner ELSE subject_name END AS workload, starts_at AS changed_at,
  min(starts_at) FILTER (WHERE finding_type = 'issue') OVER (PARTITION BY subject_namespace, CASE WHEN finding_type = 'issue' THEN subject_owner ELSE subject_name END ORDER BY starts_at ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING) AS next_failure,
  CASE WHEN finding_type = 'configuration_change'
    AND min(starts_at) FILTER (WHERE finding_type = 'issue') OVER (PARTITION BY subject_namespace, CASE WHEN finding_type = 'issue' THEN subject_owner ELSE subject_name END ORDER BY starts_at ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING) <= starts_at + interval '<window> minutes'
    AND coalesce(min(starts_at) FILTER (WHERE finding_type = 'issue') OVER (PARTITION BY subject_namespace, CASE WHEN finding_type = 'issue' THEN subject_owner ELSE subject_name END ORDER BY starts_at ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING) < min(starts_at) FILTER (WHERE finding_type = 'configuration_change') OVER (PARTITION BY subject_namespace, CASE WHEN finding_type = 'issue' THEN subject_owner ELSE subject_name END ORDER BY starts_at ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING), true)
  THEN true ELSE false END AS failed,
  count(*) OVER () AS rows_read
FROM events
WHERE subject_namespace IN (<namespaces>) AND (
  (finding_type = 'configuration_change' AND subject_type IN ('deployment', 'statefulset', 'daemonset') AND starts_at >= '<start>' AND starts_at < '<end>')
  OR (finding_type = 'issue' AND priority = 'HIGH' AND starts_at >= '<start>' AND starts_at < '<end_plus_window>'))
ORDER BY failed DESC, starts_at, workload
LIMIT 10 OFFSET 0
```

Failed changes come first. Count the rows with `failed = true`. When all 10 rows are failed, run it
again with `OFFSET 10`, then `OFFSET 20`, and so on, until a page has a row that is not failed; stop
after 10 pages and report the count as "at least <n>". Keep each failed change (workload, time, the
failure's start) for the report.

Change failure rate = failed changes / changes, as a percentage with one decimal. With no changes,
report "no changes" and no rate.

## Time to restore
```
SELECT count(*) AS failures,
  count(*) FILTER (WHERE status IN ('RESOLVED', 'CLOSED') AND ends_at IS NOT NULL) AS restored,
  round((percentile_cont(0.5) WITHIN GROUP (ORDER BY date_part('epoch', ends_at - starts_at) / 60)
    FILTER (WHERE status IN ('RESOLVED', 'CLOSED') AND ends_at IS NOT NULL))::numeric, 1) AS median_minutes,
  round((max(date_part('epoch', ends_at - starts_at) / 60) FILTER (WHERE status IN ('RESOLVED', 'CLOSED') AND ends_at IS NOT NULL))::numeric, 1) AS longest_minutes
FROM events
WHERE finding_type = 'issue' AND priority = 'HIGH' AND subject_namespace IN (<namespaces>)
  AND starts_at >= '<start>' AND starts_at < '<end>'
```

Time to restore is `median_minutes`, with `longest_minutes` next to it. Failures that are not restored
(`failures - restored`) are left out of the median: list them as "not restored". With no failures,
report "no failures", not 0 minutes.

## The failures, for the report
```
SELECT subject_namespace AS namespace, subject_owner AS workload, title, count(*) AS failures,
  min(starts_at) AS first_start,
  count(*) FILTER (WHERE status NOT IN ('RESOLVED', 'CLOSED') OR ends_at IS NULL) AS not_restored
FROM events
WHERE finding_type = 'issue' AND priority = 'HIGH' AND subject_namespace IN (<namespaces>)
  AND starts_at >= '<start>' AND starts_at < '<end>'
GROUP BY 1, 2, 3 ORDER BY failures DESC, 2, 3 LIMIT 10
```

The top 10 alerts by count. Don't page further; say "top 10 of <failures>" when there are more.
