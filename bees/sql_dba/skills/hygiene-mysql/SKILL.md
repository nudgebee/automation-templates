---
name: hygiene-mysql
description: The daily hygiene checks for one MySQL database. Each check names its catalogue entry, its count query, and its detail query.
---
# MySQL hygiene

Every check below goes into `checked` as `{target: <database>, check: <name>}`.
- If its count is above 0: one finding.
  - `value` is the count, with the unit given below.
  - `detail` holds the top rows of the detail query (at most 20), each with what to do.
- If the count is 0: no finding.

`SYS` below means the schemas to leave out:
`('mysql','sys','performance_schema','information_schema')`.

| check | severity | unit |
|---|---|---|
| hygiene_monitoring_off | warning | settings |
| hygiene_duplicate_index | warning | objects |
| hygiene_unused_index | info | objects |
| hygiene_no_primary_key | warning | objects |
| hygiene_fragmentation | info | objects |
| hygiene_long_tx | critical | sessions |

## hygiene_monitoring_off
`SELECT (@@performance_schema = 0) + (@@innodb_print_all_deadlocks = 0) AS n, @@performance_schema AS performance_schema, @@innodb_print_all_deadlocks AS print_all_deadlocks`

Fix:
- turn on `performance_schema` (needs a restart);
- turn on `innodb_print_all_deadlocks` (dynamic; on RDS, through the parameter group). Without it,
  only the last deadlock is kept.

## hygiene_duplicate_index
- Count: `SELECT COUNT(*) AS n FROM sys.schema_redundant_indexes WHERE table_schema NOT IN SYS`
- Detail: `SELECT table_schema, table_name, redundant_index_name, redundant_index_columns, dominant_index_name, sql_drop_index FROM sys.schema_redundant_indexes WHERE table_schema NOT IN SYS LIMIT 20`

Fix: drop the redundant index after checking that no query hint names it. Quote `sql_drop_index`
as the statement to review; never run it.

## hygiene_unused_index
- First run `SHOW GLOBAL STATUS LIKE 'Uptime'`. Below 1209600 seconds (14 days), the usage counters
  are too young to trust: give one finding with severity `info`, title "server up less than 14
  days, unused indexes not judged", and no `value`.
- Count: `SELECT COUNT(*) AS n FROM sys.schema_unused_indexes WHERE object_schema NOT IN SYS`
- Detail: `SELECT object_schema, object_name, index_name FROM sys.schema_unused_indexes WHERE object_schema NOT IN SYS LIMIT 20`

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
