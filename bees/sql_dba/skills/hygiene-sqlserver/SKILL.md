---
name: hygiene-sqlserver
description: The daily hygiene checks for SQL Server - per database (indexes, statistics, Query Store, long transactions, file space) and once per server (log, jobs, tempdb, version store, disks, memory, edition). Each check names its catalogue entry, its count query, and its detail query.
---
# SQL Server hygiene

Two kinds of check:
- **Database checks** run on every database, with `target` = the entry's name.
- **Server checks** read the whole server. They run once per server label (preflight step 4), on
  the label's first entry in `databases`, with `target` = the label. A label with a
  `server_label` warning skips them this sweep; its databases' own checks still run.

Every query runs in a database: pass `instance` = the entry's name and `database` = its database
on every call (for a server check, the label's first entry).

Every check that ran goes into `checked` as `{target, check: <name>}`.
- If its count is above 0: one finding.
  - `value` is the count, with the unit given below, unless the check names another column.
  - `detail` holds the top rows of the detail query (at most 20) by name, each with what to do.
    Run the detail query for every check that found something. A count with no names is not a
    finding a person can act on.
- If the count is 0: no finding.

| check | runs on | severity | unit |
|---|---|---|---|
| hygiene_monitoring_off | database | warning | settings |
| hygiene_unused_index | database | info | objects |
| hygiene_duplicate_index | database | warning | objects |
| hygiene_missing_index | database | info | objects |
| hygiene_stats | database | info | objects |
| hygiene_long_tx | database | critical | sessions |
| hygiene_file_space | database | warning | files |
| hygiene_log_space | server | critical | databases |
| hygiene_agent_jobs | server | warning | jobs |
| hygiene_tempdb | server | warning | files |
| hygiene_version_store | server | warning | megabytes |
| hygiene_storage | server | warning (critical below 5% free) | volumes |
| hygiene_memory_pressure | server | warning | percent |
| hygiene_edition_evidence | server | info | — |

## hygiene_monitoring_off
`SELECT CASE WHEN actual_state_desc = 'READ_WRITE' THEN 0 ELSE 1 END AS n, actual_state_desc FROM sys.database_query_store_options`

Fix: `ALTER DATABASE ... SET QUERY_STORE = ON (OPERATION_MODE = READ_WRITE)`. Without it,
regressions and plan changes can't be seen.

## hygiene_unused_index
- First: `SELECT DATEDIFF(day, sqlserver_start_time, SYSDATETIME()) AS up_days FROM sys.dm_os_sys_info`.
  Below 14, the counters are too young: don't judge this check (it is not run).
- Count: `SELECT COUNT(*) AS n FROM sys.indexes i JOIN sys.objects o ON o.object_id = i.object_id LEFT JOIN sys.dm_db_index_usage_stats u ON u.object_id = i.object_id AND u.index_id = i.index_id AND u.database_id = DB_ID() WHERE o.is_ms_shipped = 0 AND i.type_desc = 'NONCLUSTERED' AND i.is_primary_key = 0 AND i.is_unique = 0 AND COALESCE(u.user_seeks + u.user_scans + u.user_lookups, 0) = 0`
- Detail: the same, selecting `TOP 20 OBJECT_SCHEMA_NAME(i.object_id) AS schema_name, o.name AS table_name, i.name AS index_name, COALESCE(u.user_updates, 0) AS writes`, ordered by `writes DESC`.

Fix: confirm no monthly job uses it, then drop it.

## hygiene_duplicate_index
Two indexes are duplicates when the key columns (in order), the included columns and the filter
are all the same.
- Count: `SELECT COUNT(*) AS n FROM (SELECT object_id, keys FROM (SELECT i.object_id, i.name, CONCAT((SELECT STRING_AGG(CAST(ic.column_id AS varchar(10)), ',') WITHIN GROUP (ORDER BY ic.key_ordinal) FROM sys.index_columns ic WHERE ic.object_id = i.object_id AND ic.index_id = i.index_id AND ic.is_included_column = 0), '|', (SELECT STRING_AGG(CAST(ic.column_id AS varchar(10)), ',') WITHIN GROUP (ORDER BY ic.column_id) FROM sys.index_columns ic WHERE ic.object_id = i.object_id AND ic.index_id = i.index_id AND ic.is_included_column = 1), '|', COALESCE(i.filter_definition, '')) AS keys FROM sys.indexes i JOIN sys.objects o ON o.object_id = i.object_id WHERE o.is_ms_shipped = 0 AND i.type > 0) k GROUP BY object_id, keys HAVING COUNT(*) > 1) g`
- Detail: `SELECT TOP 20 OBJECT_SCHEMA_NAME(object_id) + '.' + OBJECT_NAME(object_id) AS table_name, STRING_AGG(name, ', ') AS indexes FROM (SELECT i.object_id, i.name, CONCAT((SELECT STRING_AGG(CAST(ic.column_id AS varchar(10)), ',') WITHIN GROUP (ORDER BY ic.key_ordinal) FROM sys.index_columns ic WHERE ic.object_id = i.object_id AND ic.index_id = i.index_id AND ic.is_included_column = 0), '|', (SELECT STRING_AGG(CAST(ic.column_id AS varchar(10)), ',') WITHIN GROUP (ORDER BY ic.column_id) FROM sys.index_columns ic WHERE ic.object_id = i.object_id AND ic.index_id = i.index_id AND ic.is_included_column = 1), '|', COALESCE(i.filter_definition, '')) AS keys FROM sys.indexes i JOIN sys.objects o ON o.object_id = i.object_id WHERE o.is_ms_shipped = 0 AND i.type > 0) k GROUP BY object_id, keys HAVING COUNT(*) > 1`

Fix: keep one per group (prefer the clustered or unique one) and drop the others.

## hygiene_missing_index
- Count: `SELECT COUNT(*) AS n FROM sys.dm_db_missing_index_group_stats s JOIN sys.dm_db_missing_index_groups g ON g.index_group_handle = s.group_handle JOIN sys.dm_db_missing_index_details d ON d.index_handle = g.index_handle WHERE d.database_id = DB_ID() AND s.avg_user_impact > 50 AND s.user_seeks + s.user_scans > 100`
- Detail: the same, selecting `TOP 20 d.statement AS table_name, d.equality_columns, d.inequality_columns, d.included_columns, s.avg_user_impact, s.user_seeks`, ordered by `s.avg_user_impact * s.user_seeks DESC`.

Fix: review each suggestion against existing indexes; never create them as listed without that
review.

## hygiene_stats
- Count: `SELECT COUNT(*) AS n FROM sys.stats st JOIN sys.objects o ON o.object_id = st.object_id CROSS APPLY sys.dm_db_stats_properties(st.object_id, st.stats_id) p WHERE o.is_ms_shipped = 0 AND p.rows > 10000 AND p.modification_counter > 0.2 * p.rows`
- Detail: the same, selecting `TOP 20 o.name AS table_name, st.name AS stats_name, p.rows, p.modification_counter, p.last_updated`, ordered by `p.modification_counter DESC`.

Fix: `UPDATE STATISTICS` on the table, or schedule it.

## hygiene_log_space
One query covers every database, tempdb included:
`SELECT COUNT(*) AS n FROM sys.dm_os_performance_counters WHERE counter_name = 'Percent Log Used' AND instance_name NOT IN ('_Total', 'mssqlsystemresource') AND cntr_value > 80`

Detail: `SELECT instance_name AS db, cntr_value AS percent_log_used FROM sys.dm_os_performance_counters WHERE counter_name = 'Percent Log Used' AND instance_name NOT IN ('_Total', 'mssqlsystemresource') AND cntr_value > 80`

Fix: find what holds the log (`log_reuse_wait_desc` in `sys.databases`): an open transaction,
replication, or a missing log backup.

## hygiene_long_tx
Transactions open over 10 minutes in this database. A transaction counts under the database its
session is in, and under each database it touched; not under the server's other databases.
- Count: `SELECT COUNT(*) AS n FROM sys.dm_tran_active_transactions t JOIN sys.dm_tran_session_transactions s ON s.transaction_id = t.transaction_id JOIN sys.dm_exec_sessions es ON es.session_id = s.session_id WHERE t.transaction_begin_time < DATEADD(minute, -10, SYSDATETIME()) AND (es.database_id = DB_ID() OR EXISTS (SELECT 1 FROM sys.dm_tran_database_transactions dt WHERE dt.transaction_id = t.transaction_id AND dt.database_id = DB_ID()))`
- Detail: the same, selecting `TOP 20 s.session_id, t.transaction_begin_time, es.login_name, es.host_name, es.program_name`, ordered by `t.transaction_begin_time`.

Fix: have the owning client commit or end it. Old transactions hold locks and log.

This is one look a day. The `sqlserver_blocking_long_running_watch` template looks every five
minutes, for long requests too, and raises an event while it lasts.

## hygiene_agent_jobs
First: `SELECT CAST(SERVERPROPERTY('EngineEdition') AS int) AS engine_edition`. 4 is Express, which
has no SQL Server Agent, so no job can fail: the check ran and found nothing (it goes into
`checked`, with no finding and no msdb read). Say "Express: no SQL Server Agent" in `evidence`.

Otherwise: `SELECT COUNT(DISTINCT h.job_id) AS n FROM msdb.dbo.sysjobhistory h WHERE h.step_id = 0 AND h.run_status = 0 AND h.run_date >= CONVERT(int, CONVERT(char(8), DATEADD(day, -1, GETDATE()), 112))` (jobs, not runs)
(Since yesterday's date, so at most 48 hours. Don't use `msdb.dbo.agent_datetime`: RDS denies EXECUTE on it.)

Detail: the failed jobs' names (join `msdb.dbo.sysjobs`), `TOP 20`.

If msdb can't be read, the check is not run. Give the error in `evidence`, and don't guess why.
On RDS the job history can stay closed even with `SQLAgentReaderRole`.

## hygiene_file_space
This database's data and log files that used 90% of what they can grow to: a capped
`max_size`, or their size when autogrowth is off. Files that can still grow fill the disk
instead: that is hygiene_storage.
- Count: `SELECT COUNT(*) AS n FROM sys.database_files WHERE (max_size > 0 AND FILEPROPERTY(name, 'SpaceUsed') >= 0.9 * max_size) OR (growth = 0 AND FILEPROPERTY(name, 'SpaceUsed') >= 0.9 * size)`
- Detail: `SELECT TOP 20 name AS file_name, type_desc, size / 128 AS size_mb, FILEPROPERTY(name, 'SpaceUsed') / 128 AS used_mb, CASE WHEN max_size = -1 THEN NULL ELSE max_size / 128 END AS max_mb, growth FROM sys.database_files WHERE (max_size > 0 AND FILEPROPERTY(name, 'SpaceUsed') >= 0.9 * max_size) OR (growth = 0 AND FILEPROPERTY(name, 'SpaceUsed') >= 0.9 * size)`

Fix: raise the cap (`ALTER DATABASE ... MODIFY FILE (NAME = ..., MAXSIZE = ...)`) or turn
autogrowth on. For a log file, first find what holds it (hygiene_log_space).

## hygiene_tempdb (server)
tempdb data files that used 90% of what they can grow to (a capped max size, or their size when
autogrowth is off). A full tempdb fails every query that sorts, hashes or uses a temp table. Its
log is in hygiene_log_space; files that can still grow fill the disk instead (hygiene_storage).
- Count: `SELECT COUNT(*) AS n FROM tempdb.sys.dm_db_file_space_usage u JOIN tempdb.sys.database_files f ON f.file_id = u.file_id WHERE u.database_id = 2 AND ((f.max_size > 0 AND u.allocated_extent_page_count >= 0.9 * f.max_size) OR (f.growth = 0 AND u.allocated_extent_page_count >= 0.9 * u.total_page_count))`
- Detail: every tempdb data file, with what holds its space: `SELECT f.name AS file_name, u.total_page_count / 128 AS size_mb, u.allocated_extent_page_count / 128 AS used_mb, CASE WHEN f.max_size = -1 THEN NULL ELSE f.max_size / 128 END AS max_mb, f.growth, u.version_store_reserved_page_count / 128 AS version_store_mb, u.user_object_reserved_page_count / 128 AS user_objects_mb, u.internal_object_reserved_page_count / 128 AS internal_objects_mb FROM tempdb.sys.dm_db_file_space_usage u JOIN tempdb.sys.database_files f ON f.file_id = u.file_id WHERE u.database_id = 2`

Fix, by what holds the space: the version store is hygiene_version_store; user objects are temp
tables a session keeps; internal objects are the sorts and hashes of large queries
(`sys.dm_db_session_space_usage` names the sessions). Then raise the cap.

## hygiene_version_store (server)
The version store holds at least 1 GB and half of tempdb's used space. It keeps row versions
for snapshot isolation, RCSI, online index builds and triggers, and it is cleaned only behind the
oldest transaction that may still need them: one long transaction lets it grow until tempdb or
the disk is full.
- Count: `SELECT CASE WHEN SUM(version_store_reserved_page_count) / 128 >= 1024 AND SUM(version_store_reserved_page_count) >= 0.5 * SUM(allocated_extent_page_count) THEN 1 ELSE 0 END AS n, SUM(version_store_reserved_page_count) / 128 AS version_store_mb FROM tempdb.sys.dm_db_file_space_usage WHERE database_id = 2`
- `value` is `version_store_mb`.
- Detail, both:
  - the databases whose versions it holds: `SELECT TOP 10 DB_NAME(database_id) AS db, reserved_space_kb / 1024 AS reserved_mb FROM sys.dm_tran_version_store_space_usage ORDER BY reserved_space_kb DESC`
    (SQL Server 2016 SP2 and later; on older servers leave it out);
  - the oldest snapshot transactions: `SELECT TOP 5 a.session_id, a.elapsed_time_seconds, s.login_name, s.host_name, s.program_name FROM sys.dm_tran_active_snapshot_database_transactions a JOIN sys.dm_exec_sessions s ON s.session_id = a.session_id ORDER BY a.elapsed_time_seconds DESC`.

Fix: have the oldest transaction's client commit or end it. A report that runs for hours under
RCSI belongs on a replica.

## hygiene_storage (server)
The disks that hold any of the server's database files (every database, tempdb included) with
less than 15% free. On RDS that is the instance's storage, and a full volume stops every
database on it.
- Count: `SELECT COUNT(*) AS n, COALESCE(SUM(CASE WHEN v.available_bytes < 0.05 * v.total_bytes THEN 1 ELSE 0 END), 0) AS critical FROM (SELECT vs.volume_mount_point, MAX(vs.total_bytes) AS total_bytes, MIN(vs.available_bytes) AS available_bytes, COUNT(*) AS files FROM sys.dm_io_virtual_file_stats(NULL, NULL) f CROSS APPLY sys.dm_os_volume_stats(f.database_id, f.file_id) vs GROUP BY vs.volume_mount_point) v WHERE v.available_bytes < 0.15 * v.total_bytes`
- Severity `critical` when `critical` is above 0, else `warning`.
- Detail: every volume: `SELECT v.volume_mount_point, CAST(ROUND(v.total_bytes / 1073741824.0, 1) AS float) AS total_gb, CAST(ROUND(v.available_bytes / 1073741824.0, 1) AS float) AS free_gb, CAST(ROUND(100.0 * v.available_bytes / v.total_bytes, 1) AS float) AS free_percent, v.files FROM (SELECT vs.volume_mount_point, MAX(vs.total_bytes) AS total_bytes, MIN(vs.available_bytes) AS available_bytes, COUNT(*) AS files FROM sys.dm_io_virtual_file_stats(NULL, NULL) f CROSS APPLY sys.dm_os_volume_stats(f.database_id, f.file_id) vs GROUP BY vs.volume_mount_point) v`
- On RDS, when the install's account reaches AWS, also read whether storage autoscaling is on:
  `aws_execute` `aws rds describe-db-instances --db-instance-identifier <label> --query 'DBInstances[0].{AllocatedGB:AllocatedStorage,MaxAllocatedGB:MaxAllocatedStorage}'`
  (the label is the instance identifier, as Setup says). `MaxAllocatedGB` above `AllocatedGB`
  means RDS grows the volume by itself up to it: say so in `detail`; the finding stays. If the
  call fails, leave it out.

Fix: find what grew (hygiene_file_space, hygiene_tempdb, the log in hygiene_log_space), then add
storage or turn storage autoscaling on.

## hygiene_memory_pressure (server)
SQL Server is short of memory: queries waited for memory (`RESOURCE_SEMAPHORE` to run,
`RESOURCE_SEMAPHORE_QUERY_COMPILE` to compile) for at least 10% of the server's wait time since it
started, a query is waiting for a grant now, or SQL Server's process is told physical memory is
low. The waits are filtered as in the performance sweep.
- Count: `WITH w AS (SELECT wait_type, wait_time_ms FROM sys.dm_os_wait_stats WHERE wait_time_ms > 0 AND wait_type NOT LIKE 'SLEEP[_]%' AND wait_type NOT LIKE 'BROKER[_]%' AND wait_type NOT LIKE 'XE[_]%' AND wait_type NOT LIKE 'QDS[_]%' AND wait_type NOT LIKE 'FT[_]IFTS%' AND wait_type NOT LIKE 'PARALLEL[_]REDO%' AND wait_type NOT LIKE 'DBMIRROR%' AND wait_type NOT LIKE 'SQLTRACE[_]%' AND wait_type NOT LIKE 'WAIT[_]XTP%' AND wait_type NOT LIKE 'PWAIT[_]%' AND wait_type NOT IN ('CHECKPOINT_QUEUE','CHKPT','CLR_AUTO_EVENT','CLR_MANUAL_EVENT','CLR_SEMAPHORE','CXCONSUMER','DIRTY_PAGE_POLL','DISPATCHER_QUEUE_SEMAPHORE','EXECSYNC','FSAGENT','HADR_CLUSAPI_CALL','HADR_FILESTREAM_IOMGR_IOCOMPLETION','HADR_LOGCAPTURE_WAIT','HADR_NOTIFICATION_DEQUEUE','HADR_TIMER_TASK','HADR_WORK_QUEUE','KSOURCE_WAKEUP','LAZYWRITER_SLEEP','LOGMGR_QUEUE','MEMORY_ALLOCATION_EXT','ONDEMAND_TASK_QUEUE','PREEMPTIVE_OS_FLUSHFILEBUFFERS','PREEMPTIVE_XE_GETTARGETSTATE','PVS_PREALLOCATE','REDO_THREAD_PENDING_WORK','REQUEST_FOR_DEADLOCK_SEARCH','RESOURCE_QUEUE','SERVER_IDLE_CHECK','SNI_HTTP_ACCEPT','SOS_WORK_DISPATCHER','SP_SERVER_DIAGNOSTICS_SLEEP','VDI_CLIENT_OTHER','WAIT_FOR_RESULTS','WAITFOR','WAITFOR_TASKSHUTDOWN')) SELECT CASE WHEN g.pending > 0 OR m.low = 1 OR s.pct >= 10 THEN 1 ELSE 0 END AS n, s.pct AS semaphore_percent, g.pending AS grants_pending, m.low AS physical_memory_low, m.in_use_mb, i.target_mb, i.physical_mb FROM (SELECT CAST(ROUND(100.0 * SUM(CASE WHEN wait_type IN ('RESOURCE_SEMAPHORE', 'RESOURCE_SEMAPHORE_QUERY_COMPILE') THEN wait_time_ms ELSE 0 END) / NULLIF(SUM(wait_time_ms), 0), 1) AS float) AS pct FROM w) s CROSS JOIN (SELECT COUNT(*) AS pending FROM sys.dm_exec_query_memory_grants WHERE grant_time IS NULL) g CROSS JOIN (SELECT CAST(process_physical_memory_low AS int) AS low, physical_memory_in_use_kb / 1024 AS in_use_mb FROM sys.dm_os_process_memory) m CROSS JOIN (SELECT committed_target_kb / 1024 AS target_mb, physical_memory_kb / 1024 AS physical_mb FROM sys.dm_os_sys_info) i`
- `value` is `semaphore_percent`. `detail` gives the other columns as read: queries waiting
  now, physical memory low, and SQL Server's memory in use, its target, and the server's memory,
  in MB.
- Detail, when `grants_pending` is above 0, the queries waiting: `SELECT TOP 10 g.session_id, g.requested_memory_kb / 1024 AS requested_mb, g.wait_time_ms, DB_NAME(r.database_id) AS db, LEFT(REPLACE(REPLACE(COALESCE(t.text, ''), CHAR(13), ' '), CHAR(10), ' '), 200) AS statement FROM sys.dm_exec_query_memory_grants g LEFT JOIN sys.dm_exec_requests r ON r.session_id = g.session_id OUTER APPLY sys.dm_exec_sql_text(g.sql_handle) t WHERE g.grant_time IS NULL ORDER BY g.wait_time_ms DESC`

Fix: a bigger instance class, or `max server memory` if it is set low; then the queries that ask
for large grants (missing indexes, bad row estimates: the performance sweep's top queries).

## hygiene_edition_evidence (server)
Facts for a review of Enterprise against Standard, on Enterprise edition only (not Developer).
Evidence, not advice: no recommendation and no saving. Pricing is the platform's.
- Count and the server's facts: `SELECT CASE WHEN SERVERPROPERTY('EngineEdition') = 3 AND CAST(SERVERPROPERTY('Edition') AS nvarchar(128)) NOT LIKE 'Developer%' THEN 1 ELSE 0 END AS n, CAST(SERVERPROPERTY('Edition') AS nvarchar(128)) AS edition, CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(32)) AS version, i.cpu_count AS logical_cpus, i.hyperthread_ratio, i.socket_count, CASE WHEN CAST(SERVERPROPERTY('ProductMajorVersion') AS int) >= 17 THEN 32 ELSE 24 END AS standard_max_cores, CAST(ROUND(i.physical_memory_kb / 1048576.0, 1) AS float) AS memory_gb, CAST(ROUND(i.committed_target_kb / 1048576.0, 1) AS float) AS target_gb, CASE WHEN CAST(SERVERPROPERTY('ProductMajorVersion') AS int) >= 17 THEN 256 ELSE 128 END AS standard_max_memory_gb, (SELECT CAST(is_enabled AS int) FROM sys.resource_governor_configuration) AS resource_governor_on, (SELECT COUNT(*) FROM sys.dm_resource_governor_resource_pools WHERE pool_id > 2) AS user_pools, CASE WHEN CAST(SERVERPROPERTY('ProductMajorVersion') AS int) >= 17 THEN 0 ELSE 1 END AS resource_governor_enterprise_only FROM sys.dm_os_sys_info i`
  (`socket_count` needs SQL Server 2016 SP2 or later; on older servers the query fails and the
  check is not run.)
- When `n` is 1, also:
  - the server's databases: `SELECT name FROM sys.databases WHERE database_id > 4 AND name <> 'rdsadmin'`;
  - in every database with this label (that entry's `instance` and `database`): `SELECT feature_name, CASE WHEN feature_name IN ('Partitioning', 'Compression', 'ColumnStoreIndex', 'InMemoryOLTP', 'ChangeCapture', 'MultipleFSContainers') AND (CAST(SERVERPROPERTY('ProductMajorVersion') AS int) > 13 OR (CAST(SERVERPROPERTY('ProductMajorVersion') AS int) = 13 AND CAST(SERVERPROPERTY('ProductBuild') AS int) >= 4001)) THEN 1 WHEN feature_name IN ('TransparentDatabaseEncryption', 'TransparentDataEncryption') AND CAST(SERVERPROPERTY('ProductMajorVersion') AS int) >= 15 THEN 1 ELSE 0 END AS allowed_on_standard FROM sys.dm_db_persisted_sku_features`.
- One finding, no `value` and no `unit`, title "Enterprise edition: facts for a Standard review".
  `detail`, as read:
  - logical CPUs (`logical_cpus`, `hyperthread_ratio` per core, `socket_count`) next to
    Standard's limit, the lesser of 4 sockets or `standard_max_cores` cores;
  - the server's memory and SQL Server's target next to Standard's `standard_max_memory_gb` GB
    buffer pool;
  - Resource Governor on and its own pools, marked "Enterprise only" when
    `resource_governor_enterprise_only` is 1. `resource_governor_on` empty means the login can't
    read it (VIEW ANY DEFINITION): say so;
  - each database's persisted features with `allowed_on_standard` as the query returned it, or
    "none". Databases in the server's list that are not in `databases` were not read: name them.
- What the bee can't see, named once: whether jobs or the application rely on Enterprise-only
  behaviour such as online index rebuilds or readable secondaries.
