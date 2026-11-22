---
name: performance-sqlserver
description: The daily performance sweep for SQL Server - waits per server, the top queries and each database's load and waits, the install's critical queries over time, and the queries that got slower, with the plan and the code behind each.
---
# SQL Server performance

You report numbers and notes; the platform keeps them and its rules open findings
(`critical_query_slower`, `query_regression`). The only findings you write yourself are
`perf_top_sql` and `perf_lock_waits`. Never decide that something is slower than usual: the rules
do.

PostgreSQL and MySQL entries: this sweep doesn't check them yet. Run their preflight, then leave
them out, and say so in `evidence`. The verdict counts SQL Server entries only; an install with
none answers `none`.

Every query below runs read-only, with `database` = the entry's database. The Query Store queries end with
`OPTION (MAX_GRANT_PERCENT = 1)`: their sorts and aggregates ask for a memory grant, and on a server
short of memory they would queue behind the application's queries until the call times out. Send each database's
gate, top SQL, load, critical-query and regression queries together in one turn, and every database's
in the same turn when you can.

**Numbers in notes:** never write a number right after `=`, `<` or `>` (the platform masks such
numbers as SQL literals before it stores a note). Write "plan 48213", "1234 ms", and SQL
commands with positional arguments, as below.

## 1. Query Store, per database
`SELECT actual_state_desc, readonly_reason, interval_length_minutes, current_storage_size_mb, max_storage_size_mb, (SELECT MAX(end_time) FROM sys.query_store_runtime_stats_interval) AS last_interval_end, DATEDIFF(minute, (SELECT MAX(end_time) FROM sys.query_store_runtime_stats_interval), SYSDATETIMEOFFSET()) AS minutes_since_last_interval, CAST(SERVERPROPERTY('ProductMajorVersion') AS int) AS major, CAST(SERVERPROPERTY('EngineEdition') AS int) AS engine_edition FROM sys.database_query_store_options`

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

### The database's load and waits
In the same turn, the totals of the last 24 hours (a window of time, so a missed sweep doesn't
double the next day's numbers):
- CPU: `SELECT CAST(SUM(rs.avg_cpu_time * rs.count_executions) / 1000.0 AS float) AS db_cpu_ms FROM sys.query_store_runtime_stats rs JOIN sys.query_store_runtime_stats_interval i ON i.runtime_stats_interval_id = rs.runtime_stats_interval_id WHERE i.start_time >= DATEADD(hour, -24, SYSDATETIMEOFFSET()) OPTION (MAX_GRANT_PERCENT = 1)`
- Waits, only when step 1's `major` is 14 or more, or `engine_edition` is 5 or 8 (Azure SQL); SQL
  Server 2016 keeps no waits in Query Store:
  `SELECT CAST(SUM(ws.total_query_wait_time_ms) AS float) AS db_wait_ms, (SELECT wait_stats_capture_mode_desc FROM sys.database_query_store_options) AS wait_capture FROM sys.query_store_wait_stats ws JOIN sys.query_store_runtime_stats_interval i ON i.runtime_stats_interval_id = ws.runtime_stats_interval_id WHERE i.start_time >= DATEADD(hour, -24, SYSDATETIMEOFFSET()) AND ws.wait_category_desc NOT IN ('Idle', 'User Wait', 'Network IO') OPTION (MAX_GRANT_PERCENT = 1)`

Report:
- `{target, metric: db_cpu_ms, value: <db_cpu_ms>}`: the CPU time of the queries Query Store kept.
- `{target, metric: db_wait_ms, value: <db_wait_ms>}`: the time those queries waited (idle,
  application and client network waits left out), only when `wait_capture` is `ON`. On SQL Server
  2016, or with capture off, leave it out and say so in `evidence`.
- NULL (no query ran in 24 hours): report 0.

The platform keeps them to learn each database's usual load and waits; no rule reads them yet.

### Lock waits
Under the same condition as the waits total (major 14 or more, or Azure SQL), in the same turn:
how much of the last 24 hours' wait was queries waiting on locks, with the severity decided by
the query (critical: lock waits are an hour or more and at least half of all waits; warning: ten
minutes or more and at least a quarter):

`WITH w AS (SELECT ws.wait_category_desc, SUM(ws.total_query_wait_time_ms) AS wait_ms FROM sys.query_store_wait_stats ws JOIN sys.query_store_runtime_stats_interval i ON i.runtime_stats_interval_id = ws.runtime_stats_interval_id WHERE i.start_time >= DATEADD(hour, -24, SYSDATETIMEOFFSET()) AND ws.wait_category_desc NOT IN ('Idle', 'User Wait', 'Network IO') GROUP BY ws.wait_category_desc), t AS (SELECT SUM(wait_ms) AS total_ms, SUM(CASE WHEN wait_category_desc = 'Lock' THEN wait_ms ELSE 0 END) AS lock_ms FROM w) SELECT CAST(COALESCE(t.lock_ms, 0) / 1000.0 AS float) AS lock_wait_s, CAST(ROUND(100.0 * t.lock_ms / NULLIF(t.total_ms, 0), 1) AS float) AS lock_share_percent, CASE WHEN (SELECT wait_stats_capture_mode_desc FROM sys.database_query_store_options) <> 'ON' THEN 'not_captured' WHEN t.lock_ms >= 3600000 AND t.lock_ms * 2 >= t.total_ms THEN 'critical' WHEN t.lock_ms >= 600000 AND t.lock_ms * 4 >= t.total_ms THEN 'warning' ELSE 'ok' END AS severity, (SELECT wait_stats_capture_mode_desc FROM sys.database_query_store_options) AS wait_capture FROM t OPTION (MAX_GRANT_PERCENT = 1)`

- `severity` `not_captured` (wait capture is off): not checked; say so in `evidence`.
- `severity` `ok`: `checked` only, no finding.
- `warning` or `critical`: in the next turn, the queries that waited longest on locks:

  `WITH l AS (SELECT q.query_hash, SUM(ws.total_query_wait_time_ms) AS lock_ms FROM sys.query_store_wait_stats ws JOIN sys.query_store_runtime_stats_interval i ON i.runtime_stats_interval_id = ws.runtime_stats_interval_id JOIN sys.query_store_plan p ON p.plan_id = ws.plan_id JOIN sys.query_store_query q ON q.query_id = p.query_id WHERE i.start_time >= DATEADD(hour, -24, SYSDATETIMEOFFSET()) AND ws.wait_category_desc = 'Lock' GROUP BY q.query_hash), r AS (SELECT q.query_hash, SUM(rs.count_executions) AS executions, SUM(rs.avg_duration * rs.count_executions) AS dur_us, SUM(rs.avg_cpu_time * rs.count_executions) AS cpu_us, LEFT(MAX(qt.query_sql_text), 200) AS query_text FROM sys.query_store_runtime_stats rs JOIN sys.query_store_runtime_stats_interval i ON i.runtime_stats_interval_id = rs.runtime_stats_interval_id JOIN sys.query_store_plan p ON p.plan_id = rs.plan_id JOIN sys.query_store_query q ON q.query_id = p.query_id JOIN sys.query_store_query_text qt ON qt.query_text_id = q.query_text_id WHERE i.start_time >= DATEADD(hour, -24, SYSDATETIMEOFFSET()) GROUP BY q.query_hash) SELECT TOP 5 CONVERT(varchar(20), l.query_hash, 1) AS query_hash, CAST(l.lock_ms / 1000.0 AS float) AS lock_wait_s, CAST(r.executions AS float) AS executions, CAST(r.dur_us / NULLIF(r.executions, 0) / 1000.0 AS float) AS avg_ms, CAST(r.cpu_us / NULLIF(r.executions, 0) / 1000.0 AS float) AS avg_cpu_ms, r.query_text FROM l LEFT JOIN r ON r.query_hash = l.query_hash ORDER BY l.lock_ms DESC OPTION (MAX_GRANT_PERCENT = 1)`

  One finding: `check: perf_lock_waits`, `severity` as the first query returned, title
  "Lock waits are <lock_share_percent>% of wait time, last 24h" (the percent as read),
  `value` `lock_wait_s`, `unit` seconds. `detail`: the lock wait seconds and share, then each row
  as read (hash, lock wait seconds, executions, average ms, average CPU ms, the text; a NULL
  number, a query with no runtime row, is left out). Say that an
  average far above its CPU time means the query spent its time waiting, not working, and that the
  same cause usually shows as **Deadlock repeats** and **Blocking repeats** on this database,
  whose notes name the statements and the code: this finding is the cost, those are the cause.
  The fix, for a person: shorten what holds the locks (one long transaction around many rows or
  an outside call, updates of the same rows in different orders), and see the blocking numbers of
  the deadlocks sweep for who holds them. `critical` gets `next: ticket`.
- `checked`: `{target, check: perf_lock_waits}` whenever the first query ran.

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
For a hash that has no note in `<bee_metrics>`; whose note there is more than 7 days old (a query
that regressed again); or, with `repos` set, whose note's `code` says `code agent not asked yet`,
whose ask is due again, or whose ask has ended in a way its note's `code` doesn't show yet (the
agent's "The code agent" section). Its note is written whole again, so run 1 and 2 for it too. At most **one per database and three per run**, largest `added_ms`
first; the rest get theirs on a later run. Plan XML is read in one query only, for one plan (the
indexes query in 2); everything else is plan ids, plan hashes and numbers.

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
   - **The indexes the faster plan used**, when the plan shape changed and an earlier plan of the
     same query id was faster (that plan's id from 1):
     `WITH XMLNAMESPACES (DEFAULT 'http://schemas.microsoft.com/sqlserver/2004/07/showplan'), o AS (SELECT DISTINCT n.o.value('@Database', 'sysname') AS db, n.o.value('@Schema', 'sysname') + '.' + n.o.value('@Table', 'sysname') AS table_name, n.o.value('@Index', 'sysname') AS index_name FROM sys.query_store_plan p CROSS APPLY (SELECT TRY_CAST(p.query_plan AS xml) AS x) c CROSS APPLY c.x.nodes('//Object[@Index]') n(o) WHERE p.plan_id = <plan id>) SELECT table_name, index_name, CASE WHEN OBJECT_ID(table_name) IS NOT NULL THEN 1 ELSE 0 END AS table_exists, CASE WHEN EXISTS (SELECT 1 FROM sys.indexes i WHERE i.object_id = OBJECT_ID(table_name) AND QUOTENAME(i.name) = index_name) THEN 1 ELSE 0 END AS still_exists FROM o WHERE (db IS NULL OR db = QUOTENAME(DB_NAME())) AND table_name IS NOT NULL AND table_name NOT LIKE '%.[[]#%' OPTION (MAX_GRANT_PERCENT = 1)`
     Only this database's tables are listed (not other databases' or temporary tables).
     - `table_exists` 1 and `still_exists` 0: that index is gone (dropped or renamed); the faster
       plan can't run again.
     - `table_exists` 0: the table is gone, renamed, or the login can't see it. Say which can't be
       told apart; don't advise recreating anything, and don't offer that plan to force.
     - No rows: the plan read no index of this database, or its XML is too deep to read.
3. **The code.**
   - `module` from 1 is a procedure: that is the code in the database; name it in the evidence.
   - **With `repos` set**, the code agent finds the code: follow the agent's "The code agent"
     section, `open_fix_pr` with `kind: regression`, one call per repository for this run's
     regressions on the database, at the end of the run. The evidence per query is its hash, its
     text (cut, every literal value replaced by `?`), the procedure, the plans and timings from 1,
     and what 2 found: statistics, SQL Server's recommendation, and any index the faster plan used
     that is gone (its table and name).
   - **With `repos` not set**: `traces_execute`, the last 24 hours, SQL on `traces_view`: a
     database span's statement is in `endpoint` (there is no `db_statement` column). Spans whose
     `endpoint` matches (`ILIKE`) the query's main table and a distinctive part of its text, grouped
     by `service_name` and `endpoint`. That gives the service and the endpoint; nothing found:
     `code not found (repos not set)`. A tool that fails or returns nothing has not answered.

Write `{target, check: query_regression, subject: <hash>, detail, code, fix, links}`:
- `detail`: the text (as read, cut); before and after, executions and average ms per window and
  per plan; whether the **plan shape** changed: the `plan_hash` carrying most of the last 24 hours
  differs from the one that carried the week before (a new plan id with the same plan hash is a
  recompile, not a new plan); statistics updated or heavily modified since the slowdown began, or
  unreadable; any index the faster plan used that is gone; SQL Server's recommendation if it gave
  one.
- `fix`, from that evidence:
  - **An index the faster plan used is gone** (`table_exists` 1, `still_exists` 0): the fix is to
    recreate it, or to undo its rename, not to force that plan (forcing fails without the index).
    Name the index and its table, and the change that dropped or renamed it when the code agent
    finds one; its definition is in that change or in the repo's history.
  - **The plan shape changed**, and an earlier plan of the **same query id** was faster: it is a
    candidate to force when every index it used still exists (each row `still_exists` 1), it is
    still in Query Store, its
    `force_failure_count` is 0, its
    `compatibility_level` matches the current plan's, and no plan of that query id is forced
    already. Write the command for a person, never run it, with positional arguments:
    `EXEC sp_query_store_force_plan <query id>, <plan id>;` (undo:
    `EXEC sp_query_store_unforce_plan <query id>, <plan id>;`). Say what forcing risks: the earlier
    plan was chosen for the data and parameters of the week before. A faster plan under another
    query id (other SET options, or ad hoc versus a procedure) can't be forced for this one: say so.
  - **The plan shape didn't change:** forcing can't help. Name what the evidence points at
    instead: statistics updated or stale on its tables, a jump in executions, rows or reads per
    call, or blocking (the deadlocks sweep's blocking notes on this database).
- `code`: from its ask in the plan (the agent's "The code agent" section), with the procedure when
  there is one; or the service and endpoint, or `code not found (repos not set)`, without `repos`.
- `links`: the fix pull request when there is one, and any event from the event check.

If the run is near its end (about 35 turns used), stop writing notes and answer: the numbers
matter more than a note, and a run that runs out of turns keeps neither.
