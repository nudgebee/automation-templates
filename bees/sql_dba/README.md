# SQL DBA Bee

Checks SQL Server, PostgreSQL and MySQL databases every day the way a DBA would. Each finding lives
as one row per database and check until it is fixed. Plans and decisions are kept in one ledger
issue, and each week the bee reports whether things got better. It only reads: every fix is a plan
for a person.

## v0.1 (this version)
- **Preflight:** the database is reachable, and its login can only read. A database whose login can
  write is skipped.
- **Daily hygiene:** per-engine checks in `skills/hygiene-*`, one catalogue per engine.
- **Ledger and weekly report:** in the tracker, through `open_ledger` and `update_ledger`.

**Later:** repeat deadlocks and blocking, query regressions, cost (RDS), and fix PRs.

## Setup
- **One SQL integration per database,** named after it. On RDS, use the DB instance identifier.
- **A read-only login on each database:**
  - SQL Server: `VIEW SERVER STATE`, `VIEW DATABASE STATE`, and `SQLAgentReaderRole` in msdb for the
    jobs check.
  - PostgreSQL: `pg_monitor`.
  - MySQL: `SELECT`, `PROCESS`.
- **Install settings:** `databases` (those integration names) and `tracker_project`.
