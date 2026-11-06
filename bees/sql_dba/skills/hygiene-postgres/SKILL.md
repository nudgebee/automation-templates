---
name: hygiene-postgres
description: The daily hygiene checks for one PostgreSQL database. Each check names its catalogue entry, its count query, and its detail query.
---
# PostgreSQL hygiene

Every check below goes into `checked` as `{target: <database>, check: <name>}`.
- If its count is above 0: one finding.
  - `value` is the count, with the unit given below.
  - `detail` holds the top rows of the detail query (at most 20), each with what to do.
- If the count is 0: no finding.

Send each query as ONE line, with no semicolon.

| check | severity | unit |
|---|---|---|
| hygiene_monitoring_off | warning | settings |
| hygiene_unused_index | info | objects |
| hygiene_duplicate_index | warning | objects |
| hygiene_vacuum | warning | objects |
| hygiene_stats | info | objects |
| hygiene_xid_age | critical | databases |
| hygiene_long_tx | critical | sessions |

## hygiene_monitoring_off
`SELECT (CASE WHEN EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements') THEN 0 ELSE 1 END) + (CASE WHEN current_setting('log_lock_waits') = 'on' THEN 0 ELSE 1 END) AS n, current_setting('log_lock_waits') AS log_lock_waits`

Fix:
- `CREATE EXTENSION pg_stat_statements` (it must also be in `shared_preload_libraries`);
- set `log_lock_waits = on`. On RDS and Cloud SQL both go through the parameter group or flags.

## hygiene_unused_index
- First:
  `SELECT EXTRACT(EPOCH FROM now() - COALESCE((SELECT stats_reset FROM pg_stat_database WHERE datname = current_database()), pg_postmaster_start_time()))::bigint AS stats_age_s`
  Below 1209600 (14 days): one finding, severity `info`, "statistics younger than 14 days, unused
  indexes not judged", no `value`.
- Count: `SELECT count(*) AS n FROM pg_stat_user_indexes s JOIN pg_index i ON i.indexrelid = s.indexrelid WHERE s.idx_scan = 0 AND NOT i.indisunique AND NOT i.indisprimary AND pg_relation_size(s.indexrelid) > 1048576`
- Detail: the same, selecting `s.schemaname, s.relname, s.indexrelname, pg_size_pretty(pg_relation_size(s.indexrelid)) AS size`, ordered by `pg_relation_size(s.indexrelid) DESC`, `LIMIT 20`.

Fix: check replicas too (the counters are per server), then `DROP INDEX CONCURRENTLY`.

## hygiene_duplicate_index
- Count: `SELECT count(*) AS n FROM (SELECT indrelid, indkey::text, indclass::text, indoption::text, coalesce(indexprs::text,''), coalesce(indpred::text,'') FROM pg_index GROUP BY 1,2,3,4,5,6 HAVING count(*) > 1) d`
- Detail: `SELECT indrelid::regclass AS table_name, array_agg(indexrelid::regclass) AS indexes FROM pg_index GROUP BY indrelid, indkey::text, indclass::text, indoption::text, coalesce(indexprs::text,''), coalesce(indpred::text,'') HAVING count(*) > 1 LIMIT 20`

Fix: keep one of each group (prefer the one backing a constraint) and drop the others with
`DROP INDEX CONCURRENTLY`.

## hygiene_vacuum
- Count: `SELECT count(*) AS n FROM pg_stat_user_tables WHERE n_dead_tup > 10000 AND n_dead_tup > 0.2 * GREATEST(n_live_tup, 1)`
- Detail: the same, selecting `schemaname, relname, n_live_tup, n_dead_tup, last_autovacuum`, ordered by `n_dead_tup DESC`, `LIMIT 20`.

Fix: find why autovacuum falls behind. Look for long transactions or replication slots holding
old rows, then tune `autovacuum_vacuum_scale_factor` on the table.

## hygiene_stats
- Count: `SELECT count(*) AS n FROM pg_stat_user_tables WHERE n_live_tup > 10000 AND n_mod_since_analyze > 0.2 * n_live_tup`
- Detail: the same, selecting `schemaname, relname, n_live_tup, n_mod_since_analyze, GREATEST(last_analyze, last_autoanalyze) AS last_analyzed`, ordered by `n_mod_since_analyze DESC`, `LIMIT 20`.

Fix: `ANALYZE` the table, and lower `autovacuum_analyze_scale_factor` for it.

## hygiene_xid_age
`SELECT count(*) AS n FROM pg_database WHERE age(datfrozenxid) > 500000000`

Detail: `SELECT datname, age(datfrozenxid) AS xid_age FROM pg_database ORDER BY 2 DESC LIMIT 5`

Fix: urgent. Find what blocks freezing (old transactions, abandoned replication slots, prepared
transactions) and run `VACUUM (FREEZE)`. At 2 billion the database stops taking writes.

## hygiene_long_tx
- Count: `SELECT count(*) AS n FROM pg_stat_activity WHERE xact_start < now() - interval '10 minutes' AND state <> 'active' AND backend_type = 'client backend'`
- Detail: the same, selecting `pid, usename, application_name, state, xact_start, left(query, 120) AS last_query`, ordered by `xact_start`, `LIMIT 20`.

Fix: have the owning client commit, or set `idle_in_transaction_session_timeout`.
