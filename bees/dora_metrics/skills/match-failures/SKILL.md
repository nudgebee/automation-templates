---
name: match-failures
description: Work out change failure rate (changes followed by a production failure) and time to restore (how long production failures took to resolve) from the platform's events. Use after count-deploys.
---
# Change failure rate and time to restore

Every number here comes from `events_execute`, as one SQL query each, copied as written with only
the `<...>` values filled in:
- `<namespaces>`: the production namespaces, quoted, e.g. `'payments', 'checkout'`;
- `<start>`, `<end>`: the period, ISO 8601 UTC;
- `<window>`: `failure_window_minutes`; `<end_plus_window>`: `<end>` plus that many minutes;
- `<exclude>`: the `failure_exclude` setting as lower-case patterns, each wrapped in `%`, e.g.
  `ARRAY['%e2e test%', '%log spike%']`; `ARRAY[]::text[]` when the setting is empty.

The tool refuses joins, subqueries and backslashes, which is why the queries look the way they do.
Don't rewrite them, don't add columns, and don't read events one by one: a list of events fills the
run's budget. Each query asks for up to 100 rows, the most the tool returns in one call.

## What a failure is
An event with `finding_type = 'issue'` and `priority = 'HIGH'` in a production namespace that started
in the period, and whose title matches none of `failure_exclude` (test alerts, or alerts the team
does not treat as an outage). MEDIUM and lower are not failures. A failure is one alert, not a
grouped incident; bees have no incident tool yet. Say that once in the report's method line, and list
the exclusions when there are any.

## Changes
A change is any change the platform recorded to a production Deployment, StatefulSet or DaemonSet's
spec: a new image, and also an env, config or resources change. That is more than the deploys
count-deploys counted; say "N changes" for change failure rate, never "N deploys".

```
SELECT subject_namespace AS namespace, count(*) AS changes, count(DISTINCT subject_name) AS workloads
FROM events
WHERE finding_type = 'configuration_change' AND subject_type IN ('deployment', 'statefulset', 'daemonset') AND subject_namespace IN (<namespaces>)
  AND starts_at >= '<start>' AND starts_at < '<end>'
GROUP BY 1 ORDER BY 1 LIMIT 100
```

No row for a namespace means the platform recorded no change there. When count-deploys found deploys
in a namespace with no changes, the platform's change history is missing there: work change failure
rate out over the namespaces that have it, and add a `warning` caveat naming the others.

## Failed changes
A change failed when a failure starts on the same workload within `<window>` minutes after it, and
before that workload's next change: a failure is put on the latest change before it, never on two.

```
SELECT subject_namespace AS namespace, CASE WHEN finding_type = 'issue' THEN coalesce(nullif(subject_owner, ''), subject_name) ELSE subject_name END AS workload, starts_at AS changed_at,
  min(starts_at) FILTER (WHERE finding_type = 'issue') OVER (PARTITION BY subject_namespace, CASE WHEN finding_type = 'issue' THEN coalesce(nullif(subject_owner, ''), subject_name) ELSE subject_name END ORDER BY starts_at, id ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING) AS next_failure,
  CASE WHEN finding_type = 'configuration_change'
    AND min(starts_at) FILTER (WHERE finding_type = 'issue') OVER (PARTITION BY subject_namespace, CASE WHEN finding_type = 'issue' THEN coalesce(nullif(subject_owner, ''), subject_name) ELSE subject_name END ORDER BY starts_at, id ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING) <= starts_at + interval '<window> minutes'
    AND coalesce(min(starts_at) FILTER (WHERE finding_type = 'issue') OVER (PARTITION BY subject_namespace, CASE WHEN finding_type = 'issue' THEN coalesce(nullif(subject_owner, ''), subject_name) ELSE subject_name END ORDER BY starts_at, id ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING) < min(starts_at) FILTER (WHERE finding_type = 'configuration_change') OVER (PARTITION BY subject_namespace, CASE WHEN finding_type = 'issue' THEN coalesce(nullif(subject_owner, ''), subject_name) ELSE subject_name END ORDER BY starts_at, id ROWS BETWEEN 1 FOLLOWING AND UNBOUNDED FOLLOWING), true)
  THEN true ELSE false END AS failed,
  count(*) OVER () AS rows_read
FROM events
WHERE subject_namespace IN (<namespaces>) AND (
  (finding_type = 'configuration_change' AND subject_type IN ('deployment', 'statefulset', 'daemonset') AND starts_at >= '<start>' AND starts_at < '<end>')
  OR (finding_type = 'issue' AND priority = 'HIGH' AND NOT (lower(title) LIKE ANY (<exclude>)) AND starts_at >= '<start>' AND starts_at < '<end_plus_window>'))
ORDER BY failed DESC, starts_at, id
LIMIT 100 OFFSET 0
```

Failed changes come first. Count the rows with `failed = true`. Only when all 100 rows are failed, run
it again with `OFFSET 100` (then 200), and stop after 3 pages with "at least <n>". Keep each failed
change (workload, time, the failure's start) for the report, and count them per workload for
`failed_changes`.

Change failure rate = failed changes / changes, as a percentage with one decimal. With no changes,
the rate has no number but the check was done: see the agent.

## Time to restore
```
SELECT count(*) AS failures,
  count(*) FILTER (WHERE status IN ('RESOLVED', 'CLOSED') AND ends_at IS NOT NULL) AS restored,
  round((percentile_cont(0.5) WITHIN GROUP (ORDER BY date_part('epoch', ends_at - starts_at) / 60)
    FILTER (WHERE status IN ('RESOLVED', 'CLOSED') AND ends_at IS NOT NULL))::numeric, 1) AS median_minutes,
  round((max(date_part('epoch', ends_at - starts_at) / 60) FILTER (WHERE status IN ('RESOLVED', 'CLOSED') AND ends_at IS NOT NULL))::numeric, 1) AS longest_minutes
FROM events
WHERE finding_type = 'issue' AND priority = 'HIGH' AND NOT (lower(title) LIKE ANY (<exclude>)) AND subject_namespace IN (<namespaces>)
  AND starts_at >= '<start>' AND starts_at < '<end>'
```

Time to restore is `median_minutes`, with `longest_minutes` next to it. Failures that are not restored
(`failures - restored`) are left out of the median: list them as "not restored". With no failures,
it has no number but the check was done: see the agent.

## The failures, per workload
```
SELECT subject_namespace AS namespace, coalesce(nullif(subject_owner, ''), subject_name) AS workload, count(*) AS failures,
  count(DISTINCT title) AS alerts, min(title) AS example_alert, min(starts_at) AS first_start,
  count(*) FILTER (WHERE status NOT IN ('RESOLVED', 'CLOSED') OR ends_at IS NULL) AS not_restored
FROM events
WHERE finding_type = 'issue' AND priority = 'HIGH' AND NOT (lower(title) LIKE ANY (<exclude>)) AND subject_namespace IN (<namespaces>)
  AND starts_at >= '<start>' AND starts_at < '<end>'
GROUP BY 1, 2 ORDER BY failures DESC, 1, 2 LIMIT 100
```

One row per workload: one `failures` metric each. The report shows the top 10.
