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
- `status: running`, asked less than 1 hour ago: leave it open; read it again before you answer, and
  next run.
- `status: running` after 1 hour, or an error: clear it, and ask again this run, at most twice per
  image and version. Then mark the image `not scanned` and say why.
- `scan_candidate` came back waiting for approval and nothing ran within 24 hours: the ask went
  unanswered or was declined; you can't tell which. Ask again at most once, then mark it
  `not scanned`.
- Never ask `scan_candidate` again for an image and version whose `scan_id` is in the plan from the
  last 24 hours: read that one.

## 2. Read the outcome
- `scanned`: read on.
- `deferred`: the cluster had no room. Ask again next run.
- `unscannable`: the image couldn't be pulled. Mark the version `not scanned` with the note.
- `failed`: as `deferred`, at most twice, then `not scanned`.

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
