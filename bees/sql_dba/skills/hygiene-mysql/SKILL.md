---
name: hygiene-mysql
description: The daily hygiene checks for one MySQL database. Each check names its catalogue entry, its count query, and its detail query.
---
# MySQL hygiene

Every check below goes into `checked` as `{target: <database>, check: <name>}`.
- If its count is above 0: one finding.
  - `value` is the count, with the unit given below.
  - `detail` holds the top rows of the detail query (at most 20) by name, each with what to do.
    Run the detail query for every check that found something. A count with no names is not a
    finding a person can act on.
- If the count is 0: no finding.

`SYS` below means the schemas to leave out:
`('mysql','sys','performance_schema','information_schema')`.

| check | severity | unit |
|---|---|---|
| hygiene_monitoring_off | warning | settings |
| index_duplicate | warning | objects |
| index_unused | info | objects |
| hygiene_no_primary_key | warning | objects |
| hygiene_fragmentation | info | objects |
| hygiene_long_tx | critical | sessions |

## hygiene_monitoring_off
`SELECT (@@performance_schema = 0) + (@@innodb_print_all_deadlocks = 0) AS n, @@performance_schema AS performance_schema, @@innodb_print_all_deadlocks AS print_all_deadlocks`

Fix:
- turn on `performance_schema` (needs a restart);
- turn on `innodb_print_all_deadlocks` (dynamic; on RDS, through the parameter group). Without it,
  only the last deadlock is kept.

## index_duplicate
An index whose columns another index on the table already starts with (`sys.schema_redundant_indexes`
names both: the redundant one and the dominant one to keep). The primary key and unique indexes are
never the one to drop.
- Count: `SELECT COUNT(DISTINCT r.table_schema, r.table_name, r.redundant_index_name) AS n FROM sys.schema_redundant_indexes r JOIN information_schema.statistics s ON s.table_schema = r.table_schema AND s.table_name = r.table_name AND s.index_name = r.redundant_index_name AND s.seq_in_index = 1 WHERE r.table_schema NOT IN SYS AND r.redundant_index_name <> 'PRIMARY' AND s.non_unique = 1`
- Detail: `SELECT r.table_schema, r.table_name, r.redundant_index_name, MIN(r.redundant_index_columns) AS redundant_index_columns, MIN(r.dominant_index_name) AS dominant_index_name, MIN(r.sql_drop_index) AS sql_drop_index FROM sys.schema_redundant_indexes r JOIN information_schema.statistics s ON s.table_schema = r.table_schema AND s.table_name = r.table_name AND s.index_name = r.redundant_index_name AND s.seq_in_index = 1 WHERE r.table_schema NOT IN SYS AND r.redundant_index_name <> 'PRIMARY' AND s.non_unique = 1 GROUP BY r.table_schema, r.table_name, r.redundant_index_name LIMIT 20`
- `detail`: per row, "drop `redundant_index_name` (`dominant_index_name` covers it)". An index
  covered by several others is one row, naming one of them.

Fix: drop the redundant index after checking that no query hint names it. Quote `sql_drop_index`
as the statement to review; never run it.

## index_unused
- Don't judge this check (it is not run) in two cases:
  - `performance_schema` is off: the view is then always empty;
  - `SHOW GLOBAL STATUS LIKE 'Uptime'` is below 1209600 seconds (14 days): the counters are too
    young to trust.
- Count: `SELECT COUNT(*) AS n FROM sys.schema_unused_indexes u JOIN information_schema.statistics s ON s.table_schema = u.object_schema AND s.table_name = u.object_name AND s.index_name = u.index_name AND s.seq_in_index = 1 WHERE u.object_schema NOT IN SYS AND s.non_unique = 1 AND NOT EXISTS (SELECT 1 FROM information_schema.KEY_COLUMN_USAGE k WHERE k.TABLE_SCHEMA = s.table_schema AND k.TABLE_NAME = s.table_name AND k.REFERENCED_TABLE_NAME IS NOT NULL AND k.ORDINAL_POSITION = 1 AND k.COLUMN_NAME = s.column_name)`
- Detail: `SELECT u.object_schema, u.object_name, u.index_name FROM sys.schema_unused_indexes u JOIN information_schema.statistics s ON s.table_schema = u.object_schema AND s.table_name = u.object_name AND s.index_name = u.index_name AND s.seq_in_index = 1 WHERE u.object_schema NOT IN SYS AND s.non_unique = 1 AND NOT EXISTS (SELECT 1 FROM information_schema.KEY_COLUMN_USAGE k WHERE k.TABLE_SCHEMA = s.table_schema AND k.TABLE_NAME = s.table_name AND k.REFERENCED_TABLE_NAME IS NOT NULL AND k.ORDINAL_POSITION = 1 AND k.COLUMN_NAME = s.column_name) LIMIT 20`
- Never listed: the primary key, unique indexes, and an index that starts with a foreign key's
  column (InnoDB needs an index on a foreign key's columns, and it slows deletes on the parent
  table without one).

Fix: confirm with the owning team that no monthly job uses it, then drop it. Unused indexes still
cost every write.

## hygiene_no_primary_key
- Count: `SELECT COUNT(*) AS n FROM information_schema.tables t WHERE t.table_type = 'BASE TABLE' AND t.table_schema NOT IN SYS AND NOT EXISTS (SELECT 1 FROM information_schema.table_constraints c WHERE c.table_schema = t.table_schema AND c.table_name = t.table_name AND c.constraint_type = 'PRIMARY KEY')`
- Detail: the same query, selecting `t.table_schema, t.table_name, t.table_rows`, ordered by
  `t.table_rows DESC`, `LIMIT 20`.

Fix: add a primary key. InnoDB otherwise uses a hidden one, and row-based replication slows
down.

## hygiene_fragmentation
- Count: `SELECT COUNT(*) AS n FROM information_schema.tables WHERE table_schema NOT IN SYS AND engine = 'InnoDB' AND data_free > 104857600 AND data_free > 0.2 * (data_length + index_length)`
- Detail: the same query, selecting `table_schema, table_name, ROUND(data_free/1048576) AS free_mb, ROUND((data_length+index_length)/1048576) AS size_mb`, ordered by `data_free DESC`, `LIMIT 20`.

Fix: `OPTIMIZE TABLE` (or `ALTER TABLE ... FORCE`) in a quiet window. It rebuilds the table.

## hygiene_long_tx
- Count: `SELECT COUNT(*) AS n FROM information_schema.innodb_trx WHERE trx_started < NOW() - INTERVAL 10 MINUTE`
- Detail: `SELECT trx_id, trx_started, trx_state, trx_rows_locked, trx_mysql_thread_id FROM information_schema.innodb_trx WHERE trx_started < NOW() - INTERVAL 10 MINUTE ORDER BY trx_started LIMIT 20`

Fix: find the client holding the transaction (thread id → `performance_schema.threads`) and have
its owner commit or end it. Old transactions hold locks and stop purge.
