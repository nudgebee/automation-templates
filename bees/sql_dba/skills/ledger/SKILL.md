---
name: ledger
description: Keep the install's one ledger issue - find or open it, read the team's comments, post this run's changes, and on the report day post the weekly report.
---
# Ledger

One open issue per account, titled exactly `SQL DBA: <account name>` (the account name from
`nudgebee_accounts_list`), in `tracker_project`. The title doesn't change when `databases`
changes, so editing the list never opens a second ledger.

1. **Find it.**
   - GitHub: `gh issue list -R <tracker_project> --state open --search "\"SQL DBA: <account name>\" in:title" --json number,title,url`
   - GitLab: `glab issue list -R <tracker_project> --search "SQL DBA: <account name>"`
   - Jira: `ticket_master_v2` `list_tickets`.

   In every case keep only an issue whose title is exactly that string. Others, for example
   another account's ledger in the same project, are not this one.

   If you can't search the tracker, don't open one: skip the ledger, and say so in the summary.
   An unsearched tracker leads to a duplicate every run.
2. **None yet:** run `open_ledger` with that title and a description that lists the databases, the
   checks per engine, and how a person accepts a finding (step 4).
3. **Read the comments** since the last bee comment (`gh issue view <n> -R <project> --comments`, or
   the tracker's equivalent).
4. **A person's decision** is a comment line `accept <database> <check>: <reason>`.
   - Put it in that finding's `detail` as "accepted by <author> on <date>: <reason>".
   - Keep the finding and its severity: only a person on the Bee page can hide one. Never write an
     acceptance yourself.
5. **Post one comment** with `update_ledger`:
   - new findings and findings that cleared since the last bee comment (compare with that
     comment's table);
   - the full table: database, check, value and unit, severity, first seen.

   Copy the first-seen date of a finding from the previous table; a new one is today.
6. **On the report day** (`report_day`, in UTC), that comment starts with `Weekly report`.
   - Per check: this week's value and, beside it, the value as written in the comment from 7
     days ago. Don't work out the difference: the reader compares the two.
   - Then what needs a person (critical findings, oldest first).
   - Take last week's values from that comment as written. Don't recompute them.

If `open_ledger` comes back waiting for approval, the issue doesn't exist yet: skip step 5, and
the next run comments.
