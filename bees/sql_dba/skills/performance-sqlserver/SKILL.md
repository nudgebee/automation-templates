---
name: performance-sqlserver
description: The daily performance sweep for SQL Server - waits per server, the top queries, the install's critical queries over time, and the queries that got slower, with the plan and the code behind each.
---
# SQL Server performance

You report numbers and notes; the platform keeps them and its rules open findings
(`critical_query_slower`, `query_regression`). The only finding you write yourself is
`perf_top_sql`. Never decide that something is slower than usual: the rules do.

PostgreSQL and MySQL entries: this sweep doesn't check them yet. Run their preflight, then leave
them out, and say so in `evidence`. The verdict counts SQL Server entries only; an install with
none answers `none`.

Every query below runs read-only, with `database` = the entry's database. The Query Store queries end with
`OPTION (MAX_GRANT_PERCENT = 1)`: their sorts and aggregates ask for a memory grant, and on a server
short of memory they would queue behind the application's queries until the call times out. Send each database's
gate, top SQL, critical-query and regression queries together in one turn, and every database's
in the same turn when you can.

**Numbers in notes:** never write a number right after `=`, `<` or `>` (the platform masks such
numbers as SQL literals before it stores a note). Write "plan 48213", "1234 ms", and SQL
commands with positional arguments, as below.

## 1. Query Store, per database
`SELECT actual_state_desc, readonly_reason, interval_length_minutes, current_storage_size_mb, max_storage_size_mb, (SELECT MAX(end_time) FROM sys.query_store_runtime_stats_interval) AS last_interval_end, DATEDIFF(minute, (SELECT MAX(end_time) FROM sys.query_store_runtime_stats_interval), SYSDATETIMEOFFSET()) AS minutes_since_last_interval FROM sys.database_query_store_options`

Query Store is **current** when `actual_state_desc` is `READ_WRITE` or `READ_ONLY` and
`minutes_since_last_interval` is at most `interval_length_minutes` + 60. Then run 3 to 5.
Otherwise 3 to 5 don't run for this database (no `checked`, no metrics), and `evidence` says why:
- `OFF`, `ERROR` or no row: the hygiene sweep's `hygiene_monitoring_off` is the finding;
- `READ_ONLY` and not current: Query Store is full (`readonly_reason` 65536) or stopped
  collecting, so the last 24 hours would look idle. Fix: raise `MAX_STORAGE_SIZE_MB` or lower
  `STALE_QUERY_THRESHOLD_DAYS`.

`interval_length_minutes` above 60: the windows below are only as fine as one interval; say so
in `evidence`.

## 2. Waits, once per server label
The server label comes from preflight. For each label whose identity check passed, use any one
of its databases and report under `target` = the label. These numbers are for the dashboard (wait
time per day by wait type); no rule reads them yet.

`WITH w AS (SELECT wait_type, wait_time_ms FROM sys.dm_os_wait_stats WHERE wait_time_ms > 0 AND wait_type NOT LIKE 'SLEEP[_]%' AND wait_type NOT LIKE 'BROKER[_]%' AND wait_type NOT LIKE 'XE[_]%' AND wait_type NOT LIKE 'QDS[_]%' AND wait_type NOT LIKE 'FT[_]IFTS%' AND wait_type NOT LIKE 'PARALLEL[_]REDO%' AND wait_type NOT LIKE 'DBMIRROR%' AND wait_type NOT LIKE 'SQLTRACE[_]%' AND wait_type NOT LIKE 'WAIT[_]XTP%' AND wait_type NOT LIKE 'PWAIT[_]%' AND wait_type NOT IN ('CHECKPOINT_QUEUE','CHKPT','CLR_AUTO_EVENT','CLR_MANUAL_EVENT','CLR_SEMAPHORE','CXCONSUMER','DIRTY_PAGE_POLL','DISPATCHER_QUEUE_SEMAPHORE','EXECSYNC','FSAGENT','HADR_CLUSAPI_CALL','HADR_FILESTREAM_IOMGR_IOCOMPLETION','HADR_LOGCAPTURE_WAIT','HADR_NOTIFICATION_DEQUEUE','HADR_TIMER_TASK','HADR_WORK_QUEUE','KSOURCE_WAKEUP','LAZYWRITER_SLEEP','LOGMGR_QUEUE','MEMORY_ALLOCATION_EXT','ONDEMAND_TASK_QUEUE','PREEMPTIVE_OS_FLUSHFILEBUFFERS','PREEMPTIVE_XE_GETTARGETSTATE','PVS_PREALLOCATE','REDO_THREAD_PENDING_WORK','REQUEST_FOR_DEADLOCK_SEARCH','RESOURCE_QUEUE','SERVER_IDLE_CHECK','SNI_HTTP_ACCEPT','SOS_WORK_DISPATCHER','SP_SERVER_DIAGNOSTICS_SLEEP','VDI_CLIENT_OTHER','WAIT_FOR_RESULTS','WAITFOR','WAITFOR_TASKSHUTDOWN')) SELECT wait_type, wait_time_ms FROM w WHERE wait_time_ms >= 0.01 * (SELECT SUM(wait_time_ms) FROM w) ORDER BY wait_time_ms DESC`

Every row it returns (the wait types with at least 1% of the server's wait time since it
started; a stable set, so each day's share is measured against the day before) is
`{target: <label>, metric: wait_ms, subject: <wait_type>, value: <wait_time_ms>}`, as read. It
is cumulative; the platform works out each day's share. Never subtract one reading from another.

## 3. Top SQL, per database
The ten queries that took the most time in the last 24 hours:

`SELECT TOP 10 CONVERT(varchar(20), q.query_hash, 1) AS query_hash, SUM(rs.count_executions) AS executions, CAST(SUM(rs.avg_duration * rs.count_executions) / 1000.0 AS decimal(18,0)) AS total_ms, CAST(SUM(rs.avg_duration * rs.count_executions) / NULLIF(SUM(rs.count_executions), 0) / 1000.0 AS decimal(18,2)) AS avg_ms, CAST(SUM(rs.avg_cpu_time * rs.count_executions) / NULLIF(SUM(rs.count_executions), 0) / 1000.0 AS decimal(18,2)) AS avg_cpu_ms, CAST(SUM(rs.avg_logical_io_reads * rs.count_executions) / NULLIF(SUM(rs.count_executions), 0) AS decimal(18,0)) AS avg_reads, LEFT(MAX(qt.query_sql_text), 200) AS query_text FROM sys.query_store_runtime_stats rs JOIN sys.query_store_runtime_stats_interval i ON i.runtime_stats_interval_id = rs.runtime_stats_interval_id JOIN sys.query_store_plan p ON p.plan_id = rs.plan_id JOIN sys.query_store_query q ON q.query_id = p.query_id JOIN sys.query_store_query_text qt ON qt.query_text_id = q.query_text_id WHERE i.start_time >= DATEADD(hour, -24, SYSDATETIMEOFFSET()) GROUP BY q.query_hash ORDER BY SUM(rs.avg_duration * rs.count_executions) DESC OPTION (MAX_GRANT_PERCENT = 1)`

- `checked`: `{target, check: perf_top_sql}`.
- Rows: one finding, `check: perf_top_sql`, `severity: info`, title "Top queries by time, last
  24h", no `value`. `detail` lists each row as read: hash, executions, total and average ms, CPU
  ms, reads, and the text. It is the same finding every day, so the report channel doesn't repeat
  it.
- No rows (an idle database): no finding, still `checked`.

A hash from this list is what goes into the `critical_queries` setting.

## 4. Critical queries, per database
`critical_queries` lists `name:0xHASH` entries. For this database's hashes (the entries whose
name is this entry's), the same numbers for the last 24 hours:

`SELECT CONVERT(varchar(20), q.query_hash, 1) AS query_hash, SUM(rs.count_executions) AS executions, CAST(SUM(rs.avg_duration * rs.count_executions) / NULLIF(SUM(rs.count_executions), 0) / 1000.0 AS decimal(18,2)) AS avg_ms, CAST(SUM(rs.avg_cpu_time * rs.count_executions) / NULLIF(SUM(rs.count_executions), 0) / 1000.0 AS decimal(18,2)) AS avg_cpu_ms, CAST(SUM(rs.avg_logical_io_reads * rs.count_executions) / NULLIF(SUM(rs.count_executions), 0) AS decimal(18,0)) AS avg_reads FROM sys.query_store_runtime_stats rs JOIN sys.query_store_runtime_stats_interval i ON i.runtime_stats_interval_id = rs.runtime_stats_interval_id JOIN sys.query_store_plan p ON p.plan_id = rs.plan_id JOIN sys.query_store_query q ON q.query_id = p.query_id WHERE i.start_time >= DATEADD(hour, -24, SYSDATETIMEOFFSET()) AND q.query_hash IN (<the hashes, as 0x... literals, comma-separated>) GROUP BY q.query_hash OPTION (MAX_GRANT_PERCENT = 1)`

Per hash, `subject` = the hash as written (`0x...`):
- a row: `query_executions` (its executions), `query_avg_ms`, `query_avg_cpu_ms`, `query_avg_reads`;
- no row (it didn't run in 24 hours): `query_executions` 0 and nothing else.

`critical_query_slower` opens when a day's `query_avg_ms` is well above the same query's 4 weeks
before (after 7 days with a sweep). A listed hash that isn't valid hex: leave it out and say so
in `evidence`.

## 5. Regressions, per database
Queries whose average time in the last 24 hours is at least twice their average over the 7 days
before, with at least 20 executions in each window, and that cost at least 10 seconds more in
the last 24 hours than at the old average (so a 0.3 ms seek that became a 300 ms scan counts,
and a 0.1 ms query that became 0.2 ms doesn't):

`WITH s AS (SELECT q.query_hash, rs.count_executions AS n, rs.avg_duration * rs.count_executions AS total_us, CASE WHEN i.start_time >= DATEADD(hour, -24, SYSDATETIMEOFFSET()) THEN 1 ELSE 0 END AS recent FROM sys.query_store_runtime_stats rs JOIN sys.query_store_runtime_stats_interval i ON i.runtime_stats_interval_id = rs.runtime_stats_interval_id JOIN sys.query_store_plan p ON p.plan_id = rs.plan_id JOIN sys.query_store_query q ON q.query_id = p.query_id WHERE i.start_time >= DATEADD(day, -8, SYSDATETIMEOFFSET())), a AS (SELECT query_hash, SUM(CASE WHEN recent = 1 THEN n END) AS recent_n, SUM(CASE WHEN recent = 1 THEN total_us END) / NULLIF(SUM(CASE WHEN recent = 1 THEN n END), 0) / 1000.0 AS recent_avg_ms, SUM(CASE WHEN recent = 0 THEN n END) AS prior_n, SUM(CASE WHEN recent = 0 THEN total_us END) / NULLIF(SUM(CASE WHEN recent = 0 THEN n END), 0) / 1000.0 AS prior_avg_ms FROM s GROUP BY query_hash) SELECT TOP 20 CONVERT(varchar(20), query_hash, 1) AS query_hash, recent_n, CAST(recent_avg_ms AS decimal(18,3)) AS recent_avg_ms, prior_n, CAST(prior_avg_ms AS decimal(18,3)) AS prior_avg_ms, CAST(recent_avg_ms / prior_avg_ms AS decimal(12,1)) AS ratio, CAST((recent_avg_ms - prior_avg_ms) * recent_n AS decimal(18,0)) AS added_ms FROM a WHERE recent_n >= 20 AND prior_n >= 20 AND prior_avg_ms > 0 AND recent_avg_ms >= 2 * prior_avg_ms AND (recent_avg_ms - prior_avg_ms) * recent_n >= 10000 ORDER BY added_ms DESC OPTION (MAX_GRANT_PERCENT = 1)`

Per row: `{target, metric: query_regressed, subject: <query_hash>, value: 1}`. No rows: report
`{target, metric: query_regressed, subject: "", value: 0}`, so the day counts as checked.
`query_regression` opens on one regressed day and clears after 7 days without one. A query that
stays slow for over a week stops being "twice the week before": its critical-query baseline (when
listed) and the top SQL are where it shows then.

### The note behind a regression
For a hash that has no note in `<bee_metrics>`, or whose note there is more than 7 days old (a
query that regressed again). At most **one per database and three per run**, largest `added_ms`
first; the rest get theirs on a later run. No plan XML in any query: plan ids, plan hashes and
numbers only.

1. **Its plans and text** (one turn, both queries):
   - `SELECT p.plan_id, p.query_id, p.is_forced_plan, p.force_failure_count, p.last_force_failure_reason_desc, p.compatibility_level, CONVERT(varchar(20), p.query_plan_hash, 1) AS plan_hash, SUM(CASE WHEN i.start_time >= DATEADD(hour, -24, SYSDATETIMEOFFSET()) THEN rs.count_executions END) AS recent_n, CAST(SUM(CASE WHEN i.start_time >= DATEADD(hour, -24, SYSDATETIMEOFFSET()) THEN rs.avg_duration * rs.count_executions END) / NULLIF(SUM(CASE WHEN i.start_time >= DATEADD(hour, -24, SYSDATETIMEOFFSET()) THEN rs.count_executions END), 0) / 1000.0 AS decimal(18,3)) AS recent_avg_ms, SUM(CASE WHEN i.start_time < DATEADD(hour, -24, SYSDATETIMEOFFSET()) THEN rs.count_executions END) AS prior_n, CAST(SUM(CASE WHEN i.start_time < DATEADD(hour, -24, SYSDATETIMEOFFSET()) THEN rs.avg_duration * rs.count_executions END) / NULLIF(SUM(CASE WHEN i.start_time < DATEADD(hour, -24, SYSDATETIMEOFFSET()) THEN rs.count_executions END), 0) / 1000.0 AS decimal(18,3)) AS prior_avg_ms, MIN(p.initial_compile_start_time) AS first_compiled, MAX(p.last_execution_time) AS last_used FROM sys.query_store_plan p JOIN sys.query_store_query q ON q.query_id = p.query_id JOIN sys.query_store_runtime_stats rs ON rs.plan_id = p.plan_id JOIN sys.query_store_runtime_stats_interval i ON i.runtime_stats_interval_id = rs.runtime_stats_interval_id WHERE q.query_hash = <hash> AND i.start_time >= DATEADD(day, -8, SYSDATETIMEOFFSET()) GROUP BY p.plan_id, p.query_id, p.is_forced_plan, p.force_failure_count, p.last_force_failure_reason_desc, p.compatibility_level, p.query_plan_hash ORDER BY recent_n DESC OPTION (MAX_GRANT_PERCENT = 1)`
   - `SELECT TOP 1 LEFT(qt.query_sql_text, 400) AS query_text, OBJECT_SCHEMA_NAME(q.object_id) + '.' + OBJECT_NAME(q.object_id) AS module FROM sys.query_store_query q JOIN sys.query_store_query_text qt ON qt.query_text_id = q.query_text_id WHERE q.query_hash = <hash> ORDER BY q.last_execution_time DESC OPTION (MAX_GRANT_PERCENT = 1)`
2. **What changed** (one turn):
   - SQL Server's own advice, when it has any (2017 and later; often empty). It needs the
     database's compatibility level at 130 or more (JSON functions): when it fails, leave it out.
     `SELECT TOP 50 name, reason, score, JSON_VALUE(state, '$.currentValue') AS state, JSON_VALUE(details, '$.planForceDetails.queryId') AS query_id, JSON_VALUE(details, '$.planForceDetails.regressedPlanId') AS regressed_plan_id, JSON_VALUE(details, '$.planForceDetails.recommendedPlanId') AS recommended_plan_id FROM sys.dm_db_tuning_recommendations`
     (keep only the rows whose `query_id` is one of the query ids from 1).
   - Statistics on the tables the text names (schema-qualified; `dbo` when it has none):
     `SELECT OBJECT_SCHEMA_NAME(s.object_id) + '.' + OBJECT_NAME(s.object_id) AS table_name, s.name AS stats_name, p.last_updated, p.rows, p.modification_counter FROM sys.stats s CROSS APPLY sys.dm_db_stats_properties(s.object_id, s.stats_id) p WHERE s.object_id IN (OBJECT_ID(N'<schema.table>'), ...) ORDER BY p.last_updated DESC`
     No rows means the login can't read them (it needs SELECT on the table): say "statistics
     unreadable", never "statistics unchanged".
3. **The code** (until one step answers):
   - `module` from 1 is a procedure: that is the code in the database; also look for its name in
     `repos` (below) to find the caller.
   - `traces_execute`, the last 24 hours: spans whose `db.statement` names the query's main table
     and a distinctive part of its text, grouped by service and span name. That gives the service
     and the endpoint.
   - `github_execute`, each repo in `repos` in turn until one has it:
     `gh search code "<a distinctive fragment of the text or the procedure name>" --repo <owner/name> --limit 5 --json path,url`,
     then read the few lines around it (`gh api repos/<owner>/<name>/contents/<path> --jq .content | base64 -d`).
   - Nothing found: the note names the service and endpoint, or says "code not found".

Write `{target, check: query_regression, subject: <hash>, detail, code, fix, links}`:
- `detail`: the text (as read, cut); before and after, executions and average ms per window and
  per plan; whether the **plan shape** changed: the `plan_hash` carrying most of the last 24 hours
  differs from the one that carried the week before (a new plan id with the same plan hash is a
  recompile, not a new plan); statistics updated or heavily modified since the slowdown began, or
  unreadable; SQL Server's recommendation if it gave one.
- `fix`, from that evidence:
  - **The plan shape changed**, and an earlier plan of the **same query id** was faster: it is a
    candidate to force when it is still in Query Store, its `force_failure_count` is 0, its
    `compatibility_level` matches the current plan's, and no plan of that query id is forced
    already. Write the command for a person, never run it, with positional arguments:
    `EXEC sp_query_store_force_plan <query id>, <plan id>;` (undo:
    `EXEC sp_query_store_unforce_plan <query id>, <plan id>;`). Say what forcing risks: the earlier
    plan was chosen for the data and parameters of the week before. A faster plan under another
    query id (other SET options, or ad hoc versus a procedure) can't be forced for this one: say so.
  - **The plan shape didn't change:** forcing can't help. Name what the evidence points at
    instead: statistics updated or stale on its tables, a jump in executions, rows or reads per
    call, or blocking (the deadlocks sweep's blocking notes on this database).
- `code`: file:line in its repo, the procedure, or the service and endpoint, or "code not found".
- `links`: the repo file URL, and any event from the event check.

If the run is near its end (about 35 turns used), stop writing notes and answer: the numbers
matter more than a note, and a run that runs out of turns keeps neither.
