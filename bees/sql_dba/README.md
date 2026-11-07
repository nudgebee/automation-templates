# SQL DBA Bee

Checks SQL Server, PostgreSQL and MySQL databases the way a DBA would: daily hygiene, and every six
hours the deadlocks and blocking that repeat, with the code behind them. Findings live on the Bee
page; the team hears about them in the report channel only when something changed. Every fix is a
plan for a person. The bee sends only read queries, and before each run it checks that its login
can do nothing more than read. That check is not a guarantee: the guarantee is giving it a
read-only login.

## v0.2 (this version)
- **Preflight:** the database is reachable, and its login can only read. A database whose login can
  write is skipped.
- **Daily hygiene sweep:** per-engine checks in `skills/hygiene-*`, one catalogue per engine.
- **Deadlocks sweep, every six hours** (`skills/deadlocks`):
  - reads each engine's deadlock counter and how long blocked sessions have waited. When the
    counter has not moved since the last sweep, the run stops there: a few turns;
  - when it moved, reads the deadlocks themselves (SQL Server `system_health`; PostgreSQL and
    MySQL from their logs on RDS, Cloud SQL or the account's log store) and groups them by
    pattern;
  - the first time a pattern is seen, follows it to the code: the app's error line, traces, then
    the file in `repos`. The note says what deadlocks, where in the code, and how to fix it.
- **The platform decides what repeats:** the bee reports numbers; rules open a finding when a
  pattern deadlocks twice in 7 days (critical at twice a day), when the counter moves twice in 7
  days and the statements can't be read, and when a sweep finds a session blocked for 30s or more
  twice in 7 days. Live blocking alerts are the product's alert path, not the bee's.
- **Report channel:** a sweep posts only new, worse and cleared findings. Nothing changed, no post.
- **Database health dashboard** (`dashboards/database_health.yaml`): open and critical findings,
  objects to fix, checks not run, findings per day; deadlocks this week, per day by database and
  by pattern, and blocked seconds by database.

v0.1's ledger issue and weekly report are gone: the Bee page and the report channel replace them.

**Later:** an index advisor from the workload, query regressions and plans, and baselines on load,
connections and waits.

## Setup
- **One SQL integration per database,** named after it. On RDS and Cloud SQL, use the instance
  identifier: the deadlocks sweep finds the instance's logs by that name.
- **A read-only login on each database:**
  - SQL Server: `VIEW SERVER STATE`, `VIEW DATABASE STATE`, and `SQLAgentReaderRole` in msdb for the
    jobs check.
  - PostgreSQL: `pg_monitor`.
  - MySQL: `SELECT`, `PROCESS`.
- **Deadlock statements need the database's logs** (PostgreSQL and MySQL):
  - RDS: log export to CloudWatch on (`postgresql`, or `error` for MySQL), read through the AWS
    account;
  - Cloud SQL: Cloud Logging, read through the GCP account (`roles/logging.viewer`);
  - MySQL anywhere: `innodb_print_all_deadlocks = ON`.

  Without logs the bee still counts deadlocks and says the statements are unavailable.
- **Install settings:** `databases` as `name:engine:database` entries; `repos` (owner/name, comma
  separated) to name the code; `report_channel` for the change posts.
- **At install, pick one integration for each engine you use,** even when there are several.
  That choice gives the bee read access to that engine's integrations. An engine left without a
  pick has every database report `unreachable`. Pick the GitHub integration that can read `repos`.
