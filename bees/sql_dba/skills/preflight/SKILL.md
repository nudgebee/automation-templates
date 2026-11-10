---
name: preflight
description: Before checking a database, make sure the bee reached the right one and that its login can only read.
---
# Preflight

Run this first for each database, with the engine's tool and `instance` = the entry's name.

## One query per engine
It returns `can_write` (a count of rights beyond reading), the connected database, and the login.
- **SQL Server:**
  `SELECT (SELECT COUNT(*) FROM fn_my_permissions(NULL, 'SERVER') WHERE permission_name NOT IN ('CONNECT SQL','VIEW SERVER STATE','VIEW ANY DATABASE','VIEW ANY DEFINITION','VIEW SERVER PERFORMANCE STATE','VIEW SERVER SECURITY STATE','VIEW ANY ERROR LOG','VIEW ANY PERFORMANCE DEFINITION','VIEW ANY SECURITY DEFINITION')) + (SELECT COUNT(*) FROM fn_my_permissions(NULL, 'DATABASE') WHERE permission_name NOT IN ('CONNECT','VIEW DATABASE STATE','VIEW DEFINITION','SELECT','SHOWPLAN','REFERENCES','VIEW DATABASE PERFORMANCE STATE','VIEW DATABASE SECURITY STATE','VIEW PERFORMANCE DEFINITION','VIEW SECURITY DEFINITION','VIEW ANY COLUMN ENCRYPTION KEY DEFINITION','VIEW ANY COLUMN MASTER KEY DEFINITION','VIEW CRYPTOGRAPHIC KEY')) + (SELECT COUNT(*) FROM sys.objects o WHERE o.is_ms_shipped = 0 AND o.type IN ('U','V','P') AND (HAS_PERMS_BY_NAME(QUOTENAME(SCHEMA_NAME(o.schema_id)) + '.' + QUOTENAME(o.name), 'OBJECT', 'INSERT') = 1 OR HAS_PERMS_BY_NAME(QUOTENAME(SCHEMA_NAME(o.schema_id)) + '.' + QUOTENAME(o.name), 'OBJECT', 'UPDATE') = 1 OR HAS_PERMS_BY_NAME(QUOTENAME(SCHEMA_NAME(o.schema_id)) + '.' + QUOTENAME(o.name), 'OBJECT', 'DELETE') = 1 OR (o.type = 'P' AND HAS_PERMS_BY_NAME(QUOTENAME(SCHEMA_NAME(o.schema_id)) + '.' + QUOTENAME(o.name), 'OBJECT', 'EXECUTE') = 1))) AS can_write, DB_NAME() AS db, SUSER_SNAME() AS login, CAST(SERVERPROPERTY('ServerName') AS nvarchar(256)) AS server_identity`
- **PostgreSQL** (one line):
  `SELECT (CASE WHEN r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolbypassrls THEN 1 ELSE 0 END) + (SELECT count(*) FROM pg_roles w WHERE w.rolname IN ('pg_write_all_data','rds_superuser','cloudsqlsuperuser','azure_pg_admin') AND pg_has_role(current_user, w.oid, 'MEMBER')) + (SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind IN ('r','p') AND n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' AND has_table_privilege(c.oid, 'INSERT,UPDATE,DELETE,TRUNCATE')) + (SELECT count(*) FROM pg_namespace n WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' AND n.nspname NOT LIKE 'pg_temp%' AND has_schema_privilege(n.oid, 'CREATE')) AS can_write, current_database() AS db, current_user AS login FROM pg_roles r WHERE r.rolname = current_user`
- **MySQL:**
  `SELECT (SELECT COUNT(*) FROM information_schema.USER_PRIVILEGES WHERE GRANTEE = CONCAT('''', SUBSTRING_INDEX(CURRENT_USER(), '@', 1), '''@''', SUBSTRING_INDEX(CURRENT_USER(), '@', -1), '''') AND PRIVILEGE_TYPE NOT IN ('USAGE','SELECT','PROCESS','SHOW VIEW','SHOW DATABASES','REPLICATION CLIENT')) + (SELECT COUNT(*) FROM information_schema.SCHEMA_PRIVILEGES WHERE GRANTEE = CONCAT('''', SUBSTRING_INDEX(CURRENT_USER(), '@', 1), '''@''', SUBSTRING_INDEX(CURRENT_USER(), '@', -1), '''') AND PRIVILEGE_TYPE NOT IN ('SELECT','SHOW VIEW')) + (SELECT COUNT(*) FROM information_schema.TABLE_PRIVILEGES WHERE GRANTEE = CONCAT('''', SUBSTRING_INDEX(CURRENT_USER(), '@', 1), '''@''', SUBSTRING_INDEX(CURRENT_USER(), '@', -1), '''') AND PRIVILEGE_TYPE NOT IN ('SELECT','SHOW VIEW')) + (SELECT COUNT(*) FROM information_schema.APPLICABLE_ROLES) AS can_write, DATABASE() AS db, CURRENT_USER() AS login`

## What decides
1. **The tool call failed:**
   - one finding with `check: unreachable`, `severity: critical`, and the error in `detail`;
   - skip the database.
2. **`db` is not the entry's database:**
   - one finding with `check: identity`, `severity: critical`, and the title "connected to <db>, expected <database>";
   - skip the database.

   Two things cause this: a mistyped name, or an account with a single integration of that engine, where the tool uses that integration whatever `instance` says. Either way the results would be another database's.
3. **`can_write` above 0:**
   - one finding with `check: read_only`, `severity: critical`, title "login can do more than read, not checked", and the login in `detail`;
   - skip the database.

   The query counts every right outside a read-only allowlist: server and database permissions, write rights on any table, EXECUTE on procedures, schema CREATE, superuser-like roles, and (MySQL) granted roles, which it can't see into. Fix: give the bee a login with only:
   - SQL Server: `VIEW SERVER STATE`, `VIEW DATABASE STATE`, `VIEW DEFINITION`;
   - PostgreSQL: `pg_monitor`;
   - MySQL: `SELECT`, `PROCESS`, `SHOW VIEW`.

   On PostgreSQL 14 and older, `public` lets everyone create tables. `REVOKE CREATE ON SCHEMA public FROM PUBLIC` fixes that.
4. **SQL Server: the server label** (in the sweeps that run server-wide checks: hygiene and
   performance). An entry's server is the fourth part of the entry
   (`name:mssql:database:server`), or the entry's name when there is none. Server-wide checks run
   once per label, with `target` = the label. Compare the labels and `server_identity` of the
   SQL Server entries that passed 1-3 (an entry that failed them takes no part):
   - **Same label, different `server_identity`:** a typo would merge two servers. Each of those
     entries gets `check: server_label`, `severity: warning`, title "server label <label> covers
     more than one server", the identities in `detail`. That label's server-wide checks don't
     run this sweep; the databases' own checks still do.
   - **Different labels, same `server_identity`:** each of those entries gets
     `check: shared_server`, `severity: info`, title "on one server with <the other entries'
     names>", and in `detail` the fix: give them one label, e.g.
     `orders:mssql:orders:prod-sql-1,billing:mssql:billing:prod-sql-1`. Until then server-wide
     checks repeat under each label.
   - Every SQL Server entry that took part goes into `checked` as `{target, check: shared_server}`
     and `{target, check: server_label}`, so a fixed label clears its finding.

A skipped database gets only that one finding and no `checked` entries, so it shows as "not checked".

This is a check, not a guarantee. The guarantee is the read-only login. The bee still sends only reads, as its agent instructions say.
