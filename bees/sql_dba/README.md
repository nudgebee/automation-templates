# SQL DBA Bee

Checks SQL Server, PostgreSQL and MySQL databases the way a DBA would: daily hygiene, every six
hours the deadlocks and blocking that repeat, and every day on SQL Server the waits, the top
queries, the critical queries over time and the queries that got slower, with the code behind them. Findings live on the Bee
page; the team hears about them in the report channel only when something changed. Every fix is a
plan for a person. The bee sends only read queries, and before each run it checks that its login
can do nothing more than read. That check is not a guarantee: the guarantee is giving it a
read-only login.

## v0.4 (this version)
- **Preflight:** the database is reachable, and its login can only read. A database whose login can
  write is skipped.
- **Daily hygiene sweep:** per-engine checks in `skills/hygiene-*`, one catalogue per engine.
  On SQL Server, besides indexes, statistics, Query Store, long transactions (counted only in the
  database they touch) and file space per database, it checks each server once:
  - **log space** and **failed Agent jobs**;
  - **tempdb** files near their cap, and a **version store** that holds half of tempdb (one long
    snapshot transaction keeps it from being cleaned);
  - **disks** under 15% free (critical under 5%), every volume that holds a database file, from
    SQL Server itself, so it works without an AWS account. On RDS, it also says whether storage autoscaling will grow the volume;
  - **memory pressure:** queries waiting for memory for 10% of the wait time, a query waiting
    now, or SQL Server told physical memory is low;
  - **edition facts** on Enterprise: CPUs and memory against Standard's limits for the server's
    version, Resource Governor, and each database's Enterprise features, marked where Standard
    allows them. These are facts for a review, not a
    recommendation; the saving is the platform's to work out.
- **Deadlocks sweep, every six hours** (`skills/deadlocks`):
  - reads each engine's deadlock counter and how long blocked sessions have waited. When the
    counter has not moved since the last sweep, the run stops there: a few turns;
  - when it moved, reads the deadlocks themselves (SQL Server `system_health`; PostgreSQL and
    MySQL from their logs on RDS, Cloud SQL or the account's log store) and groups them by
    pattern;
  - SQL Server's counter is the whole server's, so it only decides whether to look. The bee reads
    only the deadlocks that involve each database (a session in it, or a lock on its rows), so a
    deadlock on one database of a server isn't charged to the others. `system_health` keeps a few
    files of events, so on a busy server a database's count is a floor;
  - the first time a pattern is seen, follows it to the code: the app's error line, traces, then
    the file in `repos`. The note says what deadlocks, where in the code, and how to fix it.
- **The platform decides what repeats:** the bee reports numbers; rules open a finding when a
  pattern deadlocks twice in 7 days (critical at twice a day), when the counter moves twice in 7
  days and the statements can't be read (PostgreSQL and MySQL; on SQL Server, where
  `system_health` runs by default, a run that can't read it is `partial` and says why), and when
  a sweep finds a session blocked for 30s or more twice in 7 days. Live blocking alerts are the product's alert path, not the bee's.
- **Performance sweep, daily, SQL Server** (`skills/performance-sqlserver`, from Query Store and
  `sys.dm_os_wait_stats`):
  - **waits** per server: the wait types with at least 1% of the server's wait time, as a
    counter per wait type, for the dashboard. No "waits above usual" finding yet: a counter's day
    is "since the last read", so a missed sweep would double a day and look like a spike. That
    needs the platform to scale a counter's amount by the time between reads;
  - **top queries**: the ten that took the most time in the last 24 hours, as one info finding per
    database, with each query's hash;
  - **critical queries**: time, CPU, reads and executions of the hashes in `critical_queries`,
    every day; "Critical query slower than usual" against its own 4 weeks, once 7 days have a
    sweep;
  - **regressions**: a query averaging twice its week before, and costing 10 s a day more, opens
    "Query regressed". The note
    says whether the plan changed, what else changed (statistics, SQL Server's own advice),
    whether forcing an earlier plan of the same query looks safe (the command is written for a
    person, never run),
    and where the code is. At most one new note per database and three per run, so a first run on
    a busy server stays in budget; the rest follow on later days;
  - PostgreSQL and MySQL: not yet.
- **Report channel:** a sweep posts only new, worse and cleared findings. Nothing changed, no post.
- **Database health dashboard** (`dashboards/database_health.yaml`): open and critical findings,
  objects to fix, checks not run, findings per day; deadlocks this week, per day by database and
  by pattern, and blocked seconds by database; SQL Server wait time by wait type, critical
  queries' average time, and regressed queries per day.

v0.1's ledger issue and weekly report are gone: the Bee page and the report channel replace them.

Baselines (critical queries) only ever warn: a platform rule of that kind has no critical level.
Query Store must be collecting: a full (read-only) store is reported as not checked, not as an
idle day.

**Later:** an index advisor from the workload, the performance sweep on PostgreSQL and MySQL, and
baselines on load and connections.

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
- **Query Store on** for each SQL Server database the performance sweep should read
  (`ALTER DATABASE ... SET QUERY_STORE = ON`); the hygiene sweep flags those where it's off.
- **Install settings:** `databases` as `name:engine:database` entries; `repos` (owner/name, comma
  separated) to name the code; `report_channel` for the change posts; `critical_queries`
  (`name:0xHASH`, from the Top queries finding) for the queries to follow day by day.
- **Several databases on one SQL Server:** add the same server label to each entry
  (`orders:mssql:orders:prod-sql-1`, on RDS the instance identifier), so the server checks and the
  waits run once per server. Without it the bee says which entries share a server, and each
  entry reports the server's checks again.
- **At install, pick one integration for each engine you use,** even when there are several.
  That choice gives the bee read access to that engine's integrations. An engine left without a
  pick has every database report `unreachable`. Pick the GitHub integration that can read `repos`.
