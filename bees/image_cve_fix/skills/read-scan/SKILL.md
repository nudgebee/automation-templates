---
name: read-scan
description: Read a candidate scan from the scan_candidate automation and decide what it allows. Use before proposing any version change.
---
# Read a scan

A scan is one `scan_candidate` run: the candidate image against the image the workload runs now,
both scanned in your cluster by the same scanner, so the two sides are comparable.

## 1. Get the result
A scan runs in the background on the platform; `scan_candidate` answers its `scan_id` at once. Read it
with `read_scan` (`scan_id`), which answers `status` and, when done, the `result`. Keep each scan's
`scan_id` in the plan until its result is read.
- `status: done`: read the `result` (below). It came back this run.
- `status: running`: leave it open and read it again before you answer, and next run. The platform
  retries a slow scan on its own; never ask again while it says running.
- An error reading it (not found, refused): clear it. Asking again follows section 2's limits.
- `scan_candidate` came back waiting for approval and nothing ran within 24 hours: the ask went
  unanswered or was declined; you can't tell which. Ask again at most once.
- Never ask `scan_candidate` again for an image and version whose `scan_id` is in the plan from the
  last 24 hours and came back `scanned` or is running: read that one.
- Each ask adds one to the image's `scan_tries` for that version. At 2, stop asking: the image stays
  `pending` and its `next` says why it couldn't be scanned.

## 2. Read the outcome
- `scanned`: read on.
- `deferred`: the cluster had no room. Ask again next run (the 24-hour rule doesn't hold it back).
- `unscannable`: the image couldn't be pulled. Stop asking for that version; say so with the note.
- `failed`: as `deferred`, within the `scan_tries` limit.

Never read anything but `scanned` as clean.

## 3. What it says
- `counts`: `fixed` (on the running image, gone in the candidate), `left` (on both), `new` (only in
  the candidate). The lists `fixed`, `left`, `new` may be cut at 100; the counts are complete.
- `verdict`: `upgrade` when fixed > 0 and new = 0, else `hold`.
- `notes`: what the scan could not see. Copy every note into the plan and the pull request.

## 4. The rule
- `upgrade`: ask to upgrade. List what is left in the pull request.
- `hold` with new > 0: a plan entry with the new CVEs, not a pull request.
- `hold` with fixed = 0: record "nothing fixed in <version>"; don't propose it.
- `hold` with a note that OS packages couldn't be compared: a plan entry, not a pull request.
