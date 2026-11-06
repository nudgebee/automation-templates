---
name: ledger
description: Keep the install's one ledger issue - find or open it, read the team's comments, post this run's changes, and on the report day post the weekly report.
---
# Ledger

One open issue per install, titled `SQL DBA: <databases setting>`, in `tracker_project`.

1. **Find it.**
   - GitHub: `gh issue list -R <tracker_project> --state open --search "SQL DBA: in:title" --json number,title,url`
   - GitLab: `glab issue list -R <tracker_project> --search "SQL DBA:"`
   - Jira: `ticket_master_v2` `list_tickets`, then match the title.

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
   - Per check: this week's value, the value in the comment from 7 days ago, and the change.
   - Then what needs a person (critical findings, oldest first).
   - Take last week's values from that comment as written. Don't recompute them.

If `open_ledger` comes back waiting for approval, the issue doesn't exist yet: skip step 5, and
the next run comments.
