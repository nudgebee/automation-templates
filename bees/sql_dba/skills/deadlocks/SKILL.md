---
name: deadlocks
description: The deadlocks and blocking sweep for one database - the counter gate, the blocking snapshot, deadlock patterns from the engine or its logs, and the code behind a pattern the first time it is seen.
---
# Deadlocks and blocking

You report numbers; the platform counts, compares and opens findings (rules `deadlock_repeat`,
`deadlocks_repeat_any`, `blocking_repeat`). Never open a deadlock or blocking finding yourself,
and never decide that something repeats.

The run message has a `<bee_metrics>` block. Per database it gives:
- the last deadlocks sweep's `deadlocks_total` (SQL Server: `deadlocks_server_total`) as read, and
  that sweep's time: **since**;
- the subjects that already have a note, with the code each note names (after `code:`).

**until** is this sweep's time when `<bee_metrics>` gives it, else the `read_at` of the gate query.
Windows are `since <= t < until`, in UTC.

## 1. Gate, every database
One query (below, per engine). Report these metrics as read, whatever happens next:
- `{target, metric: deadlocks_total, value: <deadlocks_total>}`. SQL Server reports
  `deadlocks_server_total` instead (see its section), and only when the counter didn't move or
  step 3 read its window (a read cut at the page limit still counts as read: holding the counter
  back would make the next sweep count the same deadlocks again);
- `{target, metric: blocked_seconds, value: <blocked_seconds>}`;
- `{target, metric: long_blocks, value: <long_blocks>}` (1 when a session has waited 30s or more, else 0).
- `{target, metric: sessions, value: <sessions>}`: the clients connected to this database now
  (MySQL: to this schema), the bee's own session included. The platform keeps them to learn each
  database's usual number of connections; no rule reads them yet.

Then compare the counter with the value `<bee_metrics>` gives, by equality only:
- **Equal:** report `{target, metric: deadlock_events, subject: "", value: 0}` and stop deadlocks
  for this database. No logs, no code. Open findings stay open: the platform keeps them.
- **Different** (higher, or lower after a restart): read the patterns (step 3) from since to until.
- **No value given** (the first deadlocks sweep of this database): read the patterns of the last
  7 days. On SQL Server, a `deadlocks_total` given for the database (from a version before
  `deadlocks_server_total`) is the same counter: compare with it, and its time is **since**.
- **`deadlocks_total` came back NULL** (the engine does not count; MySQL with performance_schema
  off): report no `deadlocks_total`, read the patterns as for "different", and say why in `evidence`.

`blocked_sessions` above 0: run the blocking detail query (step 2).

### SQL Server
Pass `database` = the entry's database. The deadlock counter is per server (since its last
restart); the blocking numbers are this database's.

Report the counter as `deadlocks_server_total`, never `deadlocks_total`. It moves for a deadlock on
any database of the server, and `deadlocks_total` feeds `deadlocks_repeat_any`, a rule per
database: two databases on one server would both be charged with one deadlock. Here the counter
only decides whether to read the patterns; step 3 reads this database's deadlocks only, so
`deadlock_events` says which database had them.

`SELECT (SELECT cntr_value FROM sys.dm_os_performance_counters WHERE counter_name = 'Number of Deadlocks/sec' AND instance_name = '_Total') AS deadlocks_server_total, (SELECT COUNT(*) FROM sys.dm_exec_requests WHERE blocking_session_id <> 0 AND database_id = DB_ID()) AS blocked_sessions, (SELECT COALESCE(SUM(CAST(wait_time AS bigint)), 0) / 1000.0 FROM sys.dm_exec_requests WHERE blocking_session_id <> 0 AND database_id = DB_ID()) AS blocked_seconds, CASE WHEN EXISTS (SELECT 1 FROM sys.dm_exec_requests WHERE blocking_session_id <> 0 AND database_id = DB_ID() AND wait_time >= 30000) THEN 1 ELSE 0 END AS long_blocks, (SELECT COUNT(*) FROM sys.dm_exec_sessions WHERE is_user_process = 1 AND database_id = DB_ID()) AS sessions, SYSUTCDATETIME() AS read_at`

Despite its name, that counter is cumulative. Never count `system_health` events instead: that
buffer rolls over, so its count falls.

### PostgreSQL (one line, no semicolon)
`SELECT (SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()) AS deadlocks_total, (SELECT count(DISTINCT l.pid) FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid WHERE NOT l.granted AND a.datname = current_database()) AS blocked_sessions, (SELECT COALESCE(round(sum(EXTRACT(EPOCH FROM now() - w.ws))::numeric, 1), 0) FROM (SELECT l.pid, min(l.waitstart) AS ws FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid WHERE NOT l.granted AND a.datname = current_database() GROUP BY l.pid) w) AS blocked_seconds, (SELECT CASE WHEN EXISTS (SELECT 1 FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid WHERE NOT l.granted AND a.datname = current_database() AND l.waitstart < now() - interval '30 seconds') THEN 1 ELSE 0 END) AS long_blocks, (SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() AND backend_type = 'client backend') AS sessions, now() AS read_at`

PostgreSQL 13 and older have no `pg_locks.waitstart`: use `a.query_start` in its place (in `blocked_seconds` and `long_blocks`), and
say in `evidence` that blocked seconds count from the query's start.

### MySQL
The deadlock and blocking numbers are per server; `sessions` is this schema's. It needs the
`PROCESS` right the bee's login has: without it MySQL shows only the bee's own session, so a
`sessions` of 1 on a busy schema means the right is missing; say so in `evidence` and leave it out.

`SELECT (SELECT SUM_ERROR_RAISED FROM performance_schema.events_errors_summary_global_by_error WHERE ERROR_NAME = 'ER_LOCK_DEADLOCK') AS deadlocks_total, (SELECT COUNT(*) FROM information_schema.INNODB_TRX WHERE trx_state = 'LOCK WAIT') AS blocked_sessions, (SELECT COALESCE(SUM(TIMESTAMPDIFF(SECOND, trx_wait_started, NOW())), 0) FROM information_schema.INNODB_TRX WHERE trx_state = 'LOCK WAIT') AS blocked_seconds, (SELECT CASE WHEN EXISTS (SELECT 1 FROM information_schema.INNODB_TRX WHERE trx_state = 'LOCK WAIT' AND trx_wait_started < NOW() - INTERVAL 30 SECOND) THEN 1 ELSE 0 END) AS long_blocks, (SELECT COUNT(*) FROM information_schema.PROCESSLIST WHERE DB = DATABASE()) AS sessions, UTC_TIMESTAMP() AS read_at`

## 2. Blocking detail (only when blocked_sessions > 0)
- **SQL Server:** `SELECT TOP 20 r.session_id, r.blocking_session_id, r.wait_type, r.wait_time / 1000 AS wait_s, r.wait_resource, LEFT(t.text, 200) AS statement, LEFT(bt.text, 200) AS blocker_statement FROM sys.dm_exec_requests r OUTER APPLY sys.dm_exec_sql_text(r.sql_handle) t LEFT JOIN sys.dm_exec_connections bc ON bc.session_id = r.blocking_session_id OUTER APPLY sys.dm_exec_sql_text(bc.most_recent_sql_handle) bt WHERE r.blocking_session_id <> 0 AND r.database_id = DB_ID() ORDER BY r.wait_time DESC`
- **PostgreSQL:** `SELECT a.pid, pg_blocking_pids(a.pid) AS blocked_by, round(EXTRACT(EPOCH FROM now() - l.waitstart)::numeric, 1) AS wait_s, l.locktype, l.mode, l.relation::regclass AS relation, left(a.query, 120) AS waiting_query, b.state AS blocker_state, left(b.query, 120) AS blocker_query FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid LEFT JOIN pg_stat_activity b ON b.pid = (pg_blocking_pids(a.pid))[1] WHERE NOT l.granted AND a.datname = current_database() ORDER BY l.waitstart LIMIT 20`
- **MySQL:** `SELECT wait_age_secs, locked_table, locked_index, locked_type, waiting_pid, LEFT(waiting_query, 200) AS waiting_query, blocking_pid, LEFT(blocking_query, 200) AS blocking_query FROM sys.innodb_lock_waits ORDER BY wait_age_secs DESC LIMIT 20`

Write one note: `{target, check: blocking_repeat, subject: "", detail, fix}`. `detail` is the chain
as read (who waits on whom, the lock, both statements, the wait). A blocker that is `idle in
transaction` or sleeping points at a client that holds a transaction open: the fix is there.

## 3. Deadlock patterns
### The signature (the `subject`)
For each process in one deadlock, write `<kind> <table>`:
- **kind:** the statement's first keyword, lowercase: `select`, `insert`, `update`, `delete`,
  `merge`, or a DDL keyword (`alter`, `create`, `drop`, `truncate`, `lock`).
  - A statement that starts with `WITH`: the first `insert`, `update`, `delete` or `merge` after
    the CTEs, else `select`.
  - A procedure call: `exec`.
- **table:** the table the kind acts on.
  - `INSERT INTO t`, `UPDATE t`, `DELETE FROM t`, `MERGE INTO t`: `t`.
  - `SELECT`: the first table after `FROM`, skipping `ONLY`.
  - DDL: the object it names.
  - `exec`: the procedure's name.
  - Drop quotes and brackets, and the schema when it is the default (`public`, `dbo`). Lowercase.
  - Not shown: `?`.

The subject is the distinct parts, each once, sorted alphabetically and joined by ` / `, e.g.
`insert order_items / update orders`. Two sessions running the same statement are one part: a
deadlock of two `update orders` is `update orders`, and three sessions of `update customer_credit`
with two of `update inventory` are `update customer_credit / update inventory`, the same subject
as the two-session deadlock of those statements. No literals, ids or times: the same pattern gives
the same text every sweep, since the platform counts by it.

### Counting
Each deadlock is one occurrence, at its timestamp. Per subject, report
`{target, metric: deadlock_events, subject, value}`, where value is how many occurrences you read
in the window. In `evidence`, list each subject's timestamps; the value is how many are listed.
Reading stopped at a page limit: report what you read, say so, and the verdict is `partial`.

### SQL Server: `system_health`
Pass `database` = the entry's database. `system_health` holds the whole server's deadlocks, so
every query below keeps only the deadlocks that involve this database: a process whose session
was in it (`@currentdb`), or a locked resource in it (`@dbid`, which catches a three-part name
such as `orders.dbo.t` run from another database). A deadlock between two databases counts for
both; one on another database of the server counts for that database only.

Every query here ends with `OPTION (MAX_GRANT_PERCENT = 1)`. Shredding XML asks for a memory grant
(26 MB for these on a 1 GB RDS instance), and on a server short of memory it would queue behind the
application's queries (`RESOURCE_SEMAPHORE`) until the call times out. The cap is an upper limit,
so a large server loses nothing, and a monitoring query never competes with the workload.

First, how many of this database's deadlocks the window holds (SQL Server 2017 and later):

`SELECT COUNT(*) AS n FROM (SELECT CAST(event_data AS xml) AS e, DB_ID() AS db FROM sys.fn_xe_file_target_read_file('system_health*.xel', NULL, NULL, NULL) WHERE object_name = 'xml_deadlock_report' AND timestamp_utc >= '<since>' AND timestamp_utc < '<until>') f WHERE f.e.exist('event/data[@name="xml_report"]/value/deadlock[process-list/process/@currentdb = sql:column("f.db") or resource-list/*/@dbid = sql:column("f.db")]') = 1 OPTION (MAX_GRANT_PERCENT = 1)`

Then the processes of each deadlock (both statements, lock modes, the victim), at most 100
deadlocks a call. Above 100, call again from the last timestamp read, at most 5 calls:

`SELECT d.at_utc, d.victim, p.x.value('@id','varchar(50)') AS process, p.x.value('@lockMode','varchar(20)') AS lock_mode, p.x.value('@waitresource','varchar(200)') AS wait_resource, p.x.value('(executionStack/frame/@procname)[1]','nvarchar(256)') AS proc_name, LEFT(p.x.value('(inputbuf)[1]','nvarchar(max)'), 400) AS statement FROM (SELECT TOP 100 f.e.value('(event/@timestamp)[1]','datetime2') AS at_utc, f.e.value('(event/data[@name="xml_report"]/value/deadlock/victim-list/victimProcess/@id)[1]','varchar(50)') AS victim, f.e.query('event/data[@name="xml_report"]/value/deadlock/process-list/process') AS procs FROM (SELECT CAST(event_data AS xml) AS e, timestamp_utc, DB_ID() AS db FROM sys.fn_xe_file_target_read_file('system_health*.xel', NULL, NULL, NULL) WHERE object_name = 'xml_deadlock_report' AND timestamp_utc >= '<since>' AND timestamp_utc < '<until>') f WHERE f.e.exist('event/data[@name="xml_report"]/value/deadlock[process-list/process/@currentdb = sql:column("f.db") or resource-list/*/@dbid = sql:column("f.db")]') = 1 ORDER BY f.timestamp_utc) d CROSS APPLY d.procs.nodes('process') p(x) ORDER BY d.at_utc, process OPTION (MAX_GRANT_PERCENT = 1)`

And the objects and lock modes, for a new subject's note:

`SELECT d.at_utc, r.x.value('local-name(.)','varchar(40)') AS resource_type, r.x.value('@objectname','nvarchar(400)') AS object_name, r.x.value('@indexname','nvarchar(256)') AS index_name, r.x.value('@mode','varchar(20)') AS held_mode, r.x.value('(owner-list/owner/@id)[1]','varchar(50)') AS owner, r.x.value('(waiter-list/waiter/@id)[1]','varchar(50)') AS waiter, r.x.value('(waiter-list/waiter/@mode)[1]','varchar(20)') AS waiter_mode FROM (SELECT TOP 100 f.e.value('(event/@timestamp)[1]','datetime2') AS at_utc, f.e.query('event/data[@name="xml_report"]/value/deadlock/resource-list/*') AS res FROM (SELECT CAST(event_data AS xml) AS e, timestamp_utc, DB_ID() AS db FROM sys.fn_xe_file_target_read_file('system_health*.xel', NULL, NULL, NULL) WHERE object_name = 'xml_deadlock_report' AND timestamp_utc >= '<since>' AND timestamp_utc < '<until>') f WHERE f.e.exist('event/data[@name="xml_report"]/value/deadlock[process-list/process/@currentdb = sql:column("f.db") or resource-list/*/@dbid = sql:column("f.db")]') = 1 ORDER BY f.timestamp_utc) d CROSS APPLY d.res.nodes('*') r(x) ORDER BY d.at_utc OPTION (MAX_GRANT_PERCENT = 1)`

The event file can't be read (older versions, or no file target): read the ring buffer, same
fields, from `SELECT CAST(t.target_data AS xml) AS d FROM sys.dm_xe_session_targets t JOIN sys.dm_xe_sessions s ON s.address = t.event_session_address WHERE s.name = 'system_health' AND t.target_name = 'ring_buffer'`,
nodes `RingBufferTarget/event[@name="xml_deadlock_report"]`, filtered on `@timestamp` and, as
above, on a process `@currentdb` or a resource `@dbid` equal to `DB_ID()`. The ring buffer holds
megabytes of XML: cast it once, and end that query with `OPTION (MAX_GRANT_PERCENT = 1)` too.
Neither works: SQL Server's branch of step 4.

### PostgreSQL: the server log
Each deadlock writes `ERROR: deadlock detected` (SQLSTATE `40P01`), then a `DETAIL` line naming
every process, its lock and its statement (`Process 123: INSERT INTO ...`), then `CONTEXT` and
`STATEMENT`. Read the `DETAIL` lines: one per deadlock, with both statements. Keep only lines of
the entry's database when the line shows it (`db=<database>`, or `user@<database>` on RDS).

Find where the database runs once per run, only when a counter moved, with
`SELECT (SELECT count(*) FROM pg_settings WHERE name LIKE 'rds.%') AS rds, (SELECT count(*) FROM pg_settings WHERE name LIKE 'cloudsql.%') AS cloudsql`,
then use that source. One try each; at most two sources per database.
- **Cloud SQL** (`gcloud_execute`, `account` = the account's GCP connection; add `--project`
  when the instance is in another project):
  `gcloud logging read 'resource.type="cloudsql_database" AND resource.labels.database_id=~":<instance>$" AND textPayload:"blocked by process" AND timestamp>="<since>" AND timestamp<"<until>"' --order=asc --limit=200 --format="value(timestamp,textPayload)"`
  The `CONTEXT` line of one deadlock, for a new subject's note: the same filter with
  `textPayload:"CONTEXT:" AND textPayload:"[<pid>]"` and that second as the window.
- **RDS** (`aws_execute`; the times in epoch milliseconds):
  `aws logs filter-log-events --log-group-name /aws/rds/instance/<instance>/postgresql --start-time <since_ms> --end-time <until_ms> --filter-pattern '"blocked by process"' --max-items 200`
  When the event holds only the first line, read the rest from the same stream in that second
  (`--log-stream-names <stream> --start-time <t_ms> --end-time <t_ms + 1000>`).
  `logs_execute_v2` reads the same CloudWatch logs where the account has it as a log source.
- **Anywhere else** (`logs_execute_v2`): the database's own log lines containing
  `blocked by process` in the window, where the account's log store collects them.

`instance` is the entry's name (the instance identifier, per the `databases` setting).

### MySQL: the error log
With `innodb_print_all_deadlocks = ON`, every deadlock goes to the error log as
`Transactions deadlock detected, dumping detailed information`, then each transaction's
statement, the locks it holds and the lock it waits for.
- **RDS:** `aws_execute` `aws logs filter-log-events --log-group-name /aws/rds/instance/<instance>/error --start-time <since_ms> --end-time <until_ms> --filter-pattern '"deadlock detected"' --max-items 200`, then that second of the same stream.
- **Cloud SQL:** `gcloud_execute` `gcloud logging read 'resource.type="cloudsql_database" AND resource.labels.database_id=~":<instance>$" AND logName:"mysql.err" AND textPayload:"deadlock" AND timestamp>="<since>" AND timestamp<"<until>"' --order=asc --limit=200 --format="value(timestamp,textPayload)"`
- **Anywhere else:** `logs_execute_v2`, the same text.

Which source: the account's type (`nudgebee_accounts_list`, once per run): AWS → RDS, GCP →
Cloud SQL, otherwise `logs_execute_v2`.

No log: `SHOW ENGINE INNODB STATUS`, section `LATEST DETECTED DEADLOCK` (one deadlock only, its
time, both statements, the locks). It has no count, so report no `deadlock_events`: it goes into
the step 4 note.

## 4. Statements unavailable
**PostgreSQL and MySQL** (SQL Server: its own paragraph below). The counter moved and no source
answered (log export off, no logs tool works for the account, or the engine buffer can't be read):
- report no `deadlock_events` for the database (it was not measured);
- write a note `{target, check: deadlocks_repeat_any, subject: "", detail, fix}`: `detail` is
  "statements unavailable" with what you tried and what each answered (MySQL: plus the latest
  deadlock from `SHOW ENGINE INNODB STATUS`); `fix` is how to get them:
  - **RDS:** turn on log export for the instance (`postgresql` log, or `error` log for MySQL), and
    on MySQL set `innodb_print_all_deadlocks = 1` in the parameter group;
  - **Cloud SQL:** the logs are in Cloud Logging already; give the account's GCP connection
    `roles/logging.viewer`. MySQL also needs the `innodb_print_all_deadlocks` flag on;
  - **Self-managed:** ship the database's logs to the account's log store. PostgreSQL logs
    deadlocks by default; `log_lock_waits = on` adds the waits.

The rule `deadlocks_repeat_any` still opens from the counter alone, and this note says why there
are no statements.

**SQL Server** has no catch-all: its counter is the server's, not this database's. Azure SQL
Database (`SERVERPROPERTY('EngineEdition')` = 5) has no `system_health` to read: its deadlocks are
in `master`'s `dl` session, which this skill doesn't read yet; say that in `evidence`, and treat it
like the case below. When neither the event file nor the ring buffer can be read:
- report no `deadlock_events` and no `deadlocks_server_total`, and write no note. The next sweep
  then reads from the last window that was read, not from now;
- say why in `evidence`, from the error: the login (SQL Server 2019 and older need
  `VIEW SERVER STATE`; 2022 also accepts `VIEW SERVER PERFORMANCE STATE`), or `system_health` not
  running (`SELECT name FROM sys.dm_xe_sessions WHERE name = 'system_health'` returns no row: an
  admin stopped it);
- the verdict is `partial`.

`system_health` keeps a few files' worth of events. On a busy server, deadlocks older than its
oldest file are gone by the next sweep and read as none: this database's count is a floor, not
exact. The counter still moved, which the run says in `evidence`.

The logs or `system_health` were read but hold none of this database's deadlocks in the window
(the counter is per server and the deadlock was on another database, or the lines are no longer
kept): report `{target, metric: deadlock_events, subject: "", value: 0}` and say so in
`evidence`.

## 5. The code behind a subject
For a subject that is NOT listed with a note in `<bee_metrics>`, once per subject. A subject
listed with a note is done, even if it recurs, except when its note names no file (its `code:` is
`none`, "code not found", "code not looked up", or only a service and endpoint) and its age is
shown in days ("1 days" or more): trace it once more, and write its note again whether or not you
find the file, so its age restarts.
1. **Search the repos**, first, whenever `repos` is set and `github_execute` is one of your tools.
   This step is not optional: a note written without it is wrong, even when logs or traces would
   have answered. `github_execute` missing from your tools means the install gives this bee no
   GitHub access: skip this step and go on to step 2. For each statement
   of the pattern, each repo in turn until one has it:
   `github_execute` `gh search code "<a distinctive fragment of the statement>" --repo <owner/name> --limit 5 --json path,url`
   (the fragment as code would write it: the verb, the table and a column, never a value). Then
   get the statement's line from the file itself:
   `gh api repos/<owner>/<name>/contents/<path> --jq .content | base64 -d | grep -n -F '<the same fragment>'`.
   The number before the colon is the `line` in `path:line`; never count lines yourself (a counted
   line lands on a neighbour). No match (the statement is split across lines): grep for its table
   name instead. Then read the few lines around it without `grep`.
   Read only; quote the lines around the statement. Search each distinct statement once per run,
   even when several patterns share it (GitHub allows about 10 code searches a minute), and reuse
   the result for every pattern that has it.
2. **The app's error line**, only when step 1 found no file. `logs_execute_v2`, two minutes either
   side of one occurrence (`start_time`, `end_time`), for the driver's deadlock error: SQL Server
   `was deadlocked on lock resources`, PostgreSQL `deadlock detected`, MySQL `Deadlock found when
   trying to get lock`. Its `command` is a JSON filter, never LogQL or an Elasticsearch query:
   `{"where": {"message": {"_ilike": "%was deadlocked on lock resources%"}}, "limit": 20}`.
   A file:line or function in the line or its stack: read it from the repos as in step 1
   (without `github_execute`, give the file:line as the log has it).
3. **Traces**, only when steps 1 and 2 found no file. `traces_execute`, five minutes either side,
   SQL on `traces_view`. A database span's statement is in `endpoint` (there is no `db_statement`
   column):
   `SELECT service_name, endpoint, count(*) AS n FROM traces_view WHERE timestamp >= parseDateTimeBestEffort('<from>') AND timestamp < parseDateTimeBestEffort('<to>') AND endpoint ILIKE '%<table>%' GROUP BY service_name, endpoint ORDER BY n DESC LIMIT 20`.
   That gives the service, and the endpoint when the app's HTTP spans share the trace.
4. **Nothing found:** the note's `code` names the service and endpoint from step 3, or says
   "code not found", and in both cases ends with the repos whose `gh search code` ran without an
   error in this run: `code not found (searched acme/orders-api, acme/billing)`. Never name a repo
   you did not search in this run. With `repos` not set: `code not found (repos not set)`. Without
   `github_execute`: `code not looked up (no GitHub access)`, after the service and endpoint when
   step 3 found them. Say in `evidence` what each search returned, or that `github_execute` was
   not one of your tools.

Then write the note `{target, check: deadlock_repeat, subject, detail, code, fix, links}`:
- `detail`: both statements, the tables and indexes, the lock each held and waited for, and the
  victim, as read;
- `code`: `owner/name path:line` from step 1 or 2, or what step 4 says (with the repos searched
  in this run, or why none was);
- `fix`: from the evidence, the change that removes the cycle. Common ones:
  - two transactions take the same rows or tables in a different order → one order (sort the keys
    of a batch; touch the parent before the child everywhere);
  - PostgreSQL `ShareLock` on a transaction with a `FOR KEY SHARE` context → foreign-key checks
    against parent rows another batch is upserting: write parents and children in the same key
    order, or upsert the parents in their own transaction first;
  - SQL Server key lookup (a shared lock on a nonclustered index against an exclusive lock on the
    clustered one) → a covering index for the reader;
  - MySQL gap locks under `REPEATABLE READ` on inserts → a unique lookup, or `READ COMMITTED` for
    that path;
- `links`: the repo file URL, and any event from the agent's event check.

Never call `code_analyzer`. Never run `EXPLAIN ANALYZE`, and never send anything that writes.
