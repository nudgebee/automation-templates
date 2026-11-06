---
name: preflight
description: Before checking a database, make sure the bee reaches it and its login can only read.
---
# Preflight

Run these on each database before anything else, as the first queries of its turn.

## 1. Reachable, and which engine
Run the engine's version query through its tool, with `instance` set:
- SQL Server: `SELECT @@VERSION AS version, DB_NAME() AS db`
- PostgreSQL: `SELECT version() AS version, current_database() AS db, current_user AS login`
- MySQL: `SELECT VERSION() AS version, DATABASE() AS db, CURRENT_USER() AS login`

A database whose tool call fails gets one finding:
- `check: unreachable`, `severity: critical`;
- the error in `detail`.

It is then skipped. Pick the tool by the integration's engine; if you don't know it, try in the
order mssql, postgres, mysql and keep the one that answers.

## 2. The login can only read
The login must have no write rights. Run the engine's query:
- SQL Server (count of user databases where the login can write):
  `SELECT COUNT(*) AS can_write FROM sys.databases d WHERE d.database_id > 4 AND (HAS_PERMS_BY_NAME(d.name, 'DATABASE', 'INSERT') = 1 OR HAS_PERMS_BY_NAME(d.name, 'DATABASE', 'ALTER') = 1)`
  plus `SELECT IS_SRVROLEMEMBER('sysadmin') AS is_sysadmin`
- PostgreSQL:
  `SELECT r.rolsuper, r.rolcreaterole, r.rolcreatedb, (SELECT count(*) FROM information_schema.table_privileges p WHERE p.grantee = current_user AND p.privilege_type IN ('INSERT','UPDATE','DELETE','TRUNCATE')) AS write_grants, current_setting('default_transaction_read_only') AS read_only_default FROM pg_roles r WHERE r.rolname = current_user`
- MySQL: `SHOW GRANTS`. It can write if any grant line has INSERT, UPDATE, DELETE, DROP, ALTER,
  CREATE, ALL PRIVILEGES or SUPER.

If any write right shows:
- one finding with `check: read_only`, `severity: critical`;
- the title "login can write, not checked";
- the rights you saw in `detail`.

Skip the database. Don't run any other check on it: a login that can write must be replaced
before the bee reads with it.

PostgreSQL note: a superuser or `cloudsqlsuperuser` member can write, whatever
`default_transaction_read_only` says.
