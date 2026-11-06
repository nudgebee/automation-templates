# SQL DBA Bee

Checks SQL Server, PostgreSQL and MySQL databases every day the way a DBA would. Each finding lives
as one row per database and check until it is fixed. Plans and decisions are kept in one ledger
issue, and each week the bee reports whether things got better. Every fix is a plan for a person.
The bee sends only read queries, and before each run it checks that its login can do nothing more
than read. That check is not a guarantee: the guarantee is giving it a read-only login.

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
- **Install settings:** `databases` as `name:engine:database` entries, and `tracker_project`.
- **At install, pick one integration for each engine you use,** even when there are several.
  That choice gives the bee read access to that engine's integrations. An engine left without a
  pick has every database report `unreachable`.
