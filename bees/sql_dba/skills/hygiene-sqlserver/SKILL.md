---
name: hygiene-sqlserver
description: The daily hygiene checks for one SQL Server database. Each check names its catalogue entry, its count query, and its detail query.
---
# SQL Server hygiene

Every check below goes into `checked` as `{target: <database>, check: <name>}`.
- If its count is above 0: one finding.
  - `value` is the count, with the unit given below.
  - `detail` holds the top rows of the detail query (at most 20), each with what to do.
- If the count is 0: no finding.

Database-level queries run in the integration's database. Pass `database` when the
integration's default is `master` and the team's database is another one.

| check | severity | unit |
|---|---|---|
| hygiene_monitoring_off | warning | settings |
| hygiene_unused_index | info | objects |
| hygiene_duplicate_index | warning | objects |
| hygiene_missing_index | info | objects |
| hygiene_stats | info | objects |
| hygiene_log_space | critical | databases |
| hygiene_long_tx | critical | sessions |
| hygiene_agent_jobs | warning | jobs |

## hygiene_monitoring_off
`SELECT CASE WHEN actual_state_desc = 'READ_WRITE' THEN 0 ELSE 1 END AS n, actual_state_desc FROM sys.database_query_store_options`

Fix: `ALTER DATABASE ... SET QUERY_STORE = ON (OPERATION_MODE = READ_WRITE)`. Without it,
regressions and plan changes can't be seen.

## hygiene_unused_index
- First: `SELECT DATEDIFF(day, sqlserver_start_time, SYSDATETIME()) AS up_days FROM sys.dm_os_sys_info`.
  Below 14: one finding, severity `info`, "server up less than 14 days, unused indexes not
  judged", no `value`.
- Count: `SELECT COUNT(*) AS n FROM sys.indexes i JOIN sys.objects o ON o.object_id = i.object_id LEFT JOIN sys.dm_db_index_usage_stats u ON u.object_id = i.object_id AND u.index_id = i.index_id AND u.database_id = DB_ID() WHERE o.is_ms_shipped = 0 AND i.type_desc = 'NONCLUSTERED' AND i.is_primary_key = 0 AND i.is_unique = 0 AND COALESCE(u.user_seeks + u.user_scans + u.user_lookups, 0) = 0`
- Detail: the same, selecting `TOP 20 OBJECT_SCHEMA_NAME(i.object_id) AS schema_name, o.name AS table_name, i.name AS index_name, COALESCE(u.user_updates, 0) AS writes`, ordered by `writes DESC`.

Fix: confirm no monthly job uses it, then drop it.

## hygiene_duplicate_index
- Count: `SELECT COUNT(*) AS n FROM (SELECT object_id, keys FROM (SELECT i.object_id, STRING_AGG(CAST(ic.column_id AS varchar(10)), ',') WITHIN GROUP (ORDER BY ic.key_ordinal) AS keys FROM sys.indexes i JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id AND ic.is_included_column = 0 JOIN sys.objects o ON o.object_id = i.object_id WHERE o.is_ms_shipped = 0 AND i.type > 0 GROUP BY i.object_id, i.index_id) k GROUP BY object_id, keys HAVING COUNT(*) > 1) g`
- Detail: the same grouping, selecting `TOP 20 OBJECT_NAME(object_id) AS table_name, keys`.

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
`SELECT COUNT(*) AS n FROM sys.dm_db_log_space_usage WHERE used_log_space_in_percent > 80`

Also run it once in `tempdb` (`database: tempdb`).

Detail: `SELECT DB_NAME(database_id) AS db, used_log_space_in_percent FROM sys.dm_db_log_space_usage`.

Fix: find what holds the log (`log_reuse_wait_desc` in `sys.databases`): an open transaction,
replication, or a missing log backup.

## hygiene_long_tx
- Count: `SELECT COUNT(*) AS n FROM sys.dm_tran_active_transactions t JOIN sys.dm_tran_session_transactions s ON s.transaction_id = t.transaction_id WHERE t.transaction_begin_time < DATEADD(minute, -10, SYSDATETIME())`
- Detail: the same, adding `s.session_id, t.transaction_begin_time` and the session's
  `login_name` and `program_name` from `sys.dm_exec_sessions`, `TOP 20`.

Fix: have the owning client commit or end it. Old transactions hold locks and log.

## hygiene_agent_jobs
`SELECT COUNT(*) AS n FROM msdb.dbo.sysjobhistory h WHERE h.step_id = 0 AND h.run_status = 0 AND h.run_date >= CONVERT(int, CONVERT(char(8), DATEADD(day, -1, GETDATE()), 112))`
(Since yesterday's date, so at most 48 hours. Don't use `msdb.dbo.agent_datetime`: RDS denies EXECUTE on it.)

Detail: the failed jobs' names (join `msdb.dbo.sysjobs`), `TOP 20`.

If msdb can't be read (the login lacks `SQLAgentReaderRole`): one finding, severity `info`,
"could not read Agent job history", and no `value`.
