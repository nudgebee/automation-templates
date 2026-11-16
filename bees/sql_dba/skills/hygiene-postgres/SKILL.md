---
name: hygiene-postgres
description: The daily hygiene checks for one PostgreSQL database. Each check names its catalogue entry, its count query, and its detail query.
---
# PostgreSQL hygiene

Every check below goes into `checked` as `{target: <database>, check: <name>}`.
- If its count is above 0: one finding.
  - `value` is the count, with the unit given below.
  - `detail` holds the top rows of the detail query (at most 20) by name, each with what to do.
    Run the detail query for every check that found something. A count with no names is not a
    finding a person can act on.
- If the count is 0: no finding.

Send each query as ONE line, with no semicolon.

| check | severity | unit |
|---|---|---|
| hygiene_monitoring_off | warning | settings |
| index_unused | info | objects |
| index_duplicate | warning | objects |
| hygiene_vacuum | warning | objects |
| hygiene_stats | info | objects |
| hygiene_xid_age | critical | databases |
| hygiene_long_tx | critical | sessions |

## hygiene_monitoring_off
`SELECT (CASE WHEN EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_stat_statements') THEN 0 ELSE 1 END) + (CASE WHEN current_setting('log_lock_waits') = 'on' THEN 0 ELSE 1 END) AS n, current_setting('log_lock_waits') AS log_lock_waits`

Fix:
- `CREATE EXTENSION pg_stat_statements` (it must also be in `shared_preload_libraries`);
- set `log_lock_waits = on`. On RDS and Cloud SQL both go through the parameter group or flags.

## index_unused
- First:
  `SELECT EXTRACT(EPOCH FROM now() - COALESCE((SELECT stats_reset FROM pg_stat_database WHERE datname = current_database()), pg_postmaster_start_time()))::bigint AS stats_age_s`
  Below 1209600 (14 days), the statistics are too young: don't judge this check (it is not run).
- Count: `SELECT count(*) AS n FROM pg_stat_user_indexes s JOIN pg_index i ON i.indexrelid = s.indexrelid WHERE s.idx_scan = 0 AND NOT i.indisunique AND NOT i.indisprimary AND pg_relation_size(s.indexrelid) > 1048576 AND s.schemaname NOT IN ('hdb_catalog') AND NOT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conindid = i.indexrelid) AND NOT EXISTS (SELECT 1 FROM pg_constraint f WHERE f.contype = 'f' AND f.conrelid = i.indrelid AND (i.indkey::int2[])[0:array_length(f.conkey, 1) - 1] @> f.conkey) AND NOT EXISTS (SELECT 1 FROM pg_depend dep WHERE dep.classid = 'pg_class'::regclass AND dep.objid = i.indrelid AND dep.deptype = 'e')`
- Detail: `SELECT s.schemaname, s.relname, s.indexrelname, pg_size_pretty(pg_relation_size(s.indexrelid)) AS size FROM pg_stat_user_indexes s JOIN pg_index i ON i.indexrelid = s.indexrelid WHERE s.idx_scan = 0 AND NOT i.indisunique AND NOT i.indisprimary AND pg_relation_size(s.indexrelid) > 1048576 AND s.schemaname NOT IN ('hdb_catalog') AND NOT EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conindid = i.indexrelid) AND NOT EXISTS (SELECT 1 FROM pg_constraint f WHERE f.contype = 'f' AND f.conrelid = i.indrelid AND (i.indkey::int2[])[0:array_length(f.conkey, 1) - 1] @> f.conkey) AND NOT EXISTS (SELECT 1 FROM pg_depend dep WHERE dep.classid = 'pg_class'::regclass AND dep.objid = i.indrelid AND dep.deptype = 'e') ORDER BY pg_relation_size(s.indexrelid) DESC LIMIT 20`
- Never listed: primary keys, unique indexes, indexes backing a constraint, an index whose
  leading columns are a foreign key's columns (dropping it slows deletes and updates on the parent
  table), extensions' tables and the `hdb_catalog` schema.

Fix: check replicas too (the counters are per server), then `DROP INDEX CONCURRENTLY`.

## index_duplicate
Indexes on the same table with the same columns, operator classes, order, expressions and
predicate. In each group the query names the one to keep: a valid one first (a failed
`CREATE INDEX CONCURRENTLY` leaves an invalid one), then the one backing a constraint, the primary
key, a unique index, the most scanned, the oldest. An index backing a constraint (primary key,
unique, exclusion, or the one a foreign key references) is never one to drop.
- Count: `WITH ix AS (SELECT i.indexrelid, i.indrelid, i.indkey::text || ' ' || i.indclass::text || ' ' || i.indoption::text || ' ' || coalesce(i.indexprs::text, '') || ' ' || coalesce(i.indpred::text, '') AS sig, EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conindid = i.indexrelid) AS backs_constraint, i.indisvalid, i.indisprimary, i.indisunique, coalesce(s.idx_scan, 0) AS scans FROM pg_index i JOIN pg_class t ON t.oid = i.indrelid JOIN pg_namespace n ON n.oid = t.relnamespace LEFT JOIN pg_stat_user_indexes s ON s.indexrelid = i.indexrelid WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'hdb_catalog') AND n.nspname NOT LIKE 'pg_toast%' AND NOT EXISTS (SELECT 1 FROM pg_depend dep WHERE dep.classid = 'pg_class'::regclass AND dep.objid = t.oid AND dep.deptype = 'e')), r AS (SELECT ix.*, row_number() OVER w AS rn, count(*) OVER (PARTITION BY indrelid, sig) AS cnt, first_value(indexrelid) OVER w AS keep FROM ix WINDOW w AS (PARTITION BY indrelid, sig ORDER BY indisvalid DESC, backs_constraint DESC, indisprimary DESC, indisunique DESC, scans DESC, indexrelid)) SELECT count(*) AS n FROM r WHERE cnt > 1 AND rn > 1 AND NOT backs_constraint`
- Detail: `WITH ix AS (SELECT i.indexrelid, i.indrelid, i.indkey::text || ' ' || i.indclass::text || ' ' || i.indoption::text || ' ' || coalesce(i.indexprs::text, '') || ' ' || coalesce(i.indpred::text, '') AS sig, EXISTS (SELECT 1 FROM pg_constraint c WHERE c.conindid = i.indexrelid) AS backs_constraint, i.indisvalid, i.indisprimary, i.indisunique, coalesce(s.idx_scan, 0) AS scans FROM pg_index i JOIN pg_class t ON t.oid = i.indrelid JOIN pg_namespace n ON n.oid = t.relnamespace LEFT JOIN pg_stat_user_indexes s ON s.indexrelid = i.indexrelid WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'hdb_catalog') AND n.nspname NOT LIKE 'pg_toast%' AND NOT EXISTS (SELECT 1 FROM pg_depend dep WHERE dep.classid = 'pg_class'::regclass AND dep.objid = t.oid AND dep.deptype = 'e')), r AS (SELECT ix.*, row_number() OVER w AS rn, count(*) OVER (PARTITION BY indrelid, sig) AS cnt, first_value(indexrelid) OVER w AS keep FROM ix WINDOW w AS (PARTITION BY indrelid, sig ORDER BY indisvalid DESC, backs_constraint DESC, indisprimary DESC, indisunique DESC, scans DESC, indexrelid)) SELECT indrelid::regclass AS table_name, indexrelid::regclass AS drop_index, keep::regclass AS keep_index, scans, pg_size_pretty(pg_relation_size(indexrelid)) AS size FROM r WHERE cnt > 1 AND rn > 1 AND NOT backs_constraint ORDER BY pg_relation_size(indexrelid) DESC LIMIT 20`
- `detail`: per row, "drop `drop_index` (duplicates `keep_index`)", with its scans and size.

Fix: `DROP INDEX CONCURRENTLY <drop_index>`, for a person to run after checking replicas.

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

Detail: `SELECT datname, age(datfrozenxid) AS xid_age FROM pg_database WHERE age(datfrozenxid) > 500000000 ORDER BY 2 DESC LIMIT 20`

Fix: urgent. Find what blocks freezing (old transactions, abandoned replication slots, prepared
transactions) and run `VACUUM (FREEZE)`. At 2 billion the database stops taking writes.

## hygiene_long_tx
- Count (any client transaction open longer than 10 minutes, running or idle, as on the other engines): `SELECT count(*) AS n FROM pg_stat_activity WHERE xact_start < now() - interval '10 minutes' AND backend_type = 'client backend'`
- Detail: the same, selecting `pid, usename, application_name, state, xact_start, left(query, 120) AS last_query`, ordered by `xact_start`, `LIMIT 20`.

Fix: have the owning client commit, or set `idle_in_transaction_session_timeout`.
