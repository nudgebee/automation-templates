---
name: write-pr
description: Write the description of an upgrade or configuration pull request so a reviewer can decide in a minute what it fixes, what could break, and how sure the bee is. Use before every open_fix_pr.
---
# Write the pull request

Write it for the person who has to approve it: what changes, why it is worth it, what could go
wrong, and what you checked. Plain words, short lines, no filler. Every number comes from a tool
result of this run; never round, estimate or retype from memory.

## Shape
Use these sections, in this order. Leave out a section only when it has nothing to say.

1. **One line on top:** `Bumps <image> from <from> to <to>, run by <namespace>/<workload>.` When one
   change moves several images (a Helm chart bump), name the chart and every image it moves.
2. **Summary table:**
   | | |
   |---|---|
   | Confidence | 🟢 High / 🟡 Medium / 🔴 Low, the level `assess_upgrade` returned, unchanged |
   | CVEs fixed | the scan's `counts.fixed`, with how many are Critical and High |
   | CVEs left | `counts.left` |
   | CVEs added | `counts.new` |
   | Version change | e.g. minor, 1.18 to 1.19 |

   A change that moves several images gets one row of numbers per image, each from that image's own
   scan, and a total. An image it moves that you didn't scan is a row saying "not scanned".
3. **Why this upgrade:** two or three sentences. Which notable CVEs it fixes (the worst ones, by
   name and package), and why this version rather than a newer one if you scanned more than one.
4. **What could break:** one line per check from `assess_upgrade`'s `evidence`: the check, whether
   code or you read it, the result (✅ no risk, ⚠️ check, ❌ risk, ➖ not checked) and the evidence.
   Then, in your own words, what a reviewer should look at: e.g. a deprecated option this deployment
   uses, a data migration, a changed default. If nothing, say what you read that shows it.
5. **Release notes:** every release between the two versions, each as a link. Under each one you read
   this run, the lines that matter quoted word for word, or "nothing that affects this deployment".
   One you didn't read says "not read". Never one sentence about all of them.
6. **CVEs fixed:** a table `CVE | Package | Severity | Fixed in`, worst first, from the scan's
   `fixed` list. More than 15 rows go inside `<details><summary>All N</summary> … </details>`. Link
   each CVE or GHSA id to `https://osv.dev/vulnerability/<id>`.
7. **CVEs added**, if any, the same way. With any, there should be no pull request (read-scan).
8. **How it reaches the cluster:** whether merging deploys it. Look for what deploys the changed file
   (a CI workflow, an Argo CD or Flux app). When nothing does (a version kept in a comment, a
   `helm upgrade` run by hand), say so first in this section and give the exact command to run after
   merging.
9. **How this was checked:** both images scanned in the cluster by the same scanner, when, and each
   scan's whole `scan_id`; each of the scan's `notes`; and that after rollout you check the new image
   runs, the pods are healthy and the fixed CVEs are gone.

## Configuration shape
A `config_pr` (choose-resolution step 0) removes a domain instead of moving a version. Use these
sections instead, in this order:

1. **One line on top:** `Removes <domain> from <file>: its certificate can't renew and expires on <date>.`
2. **What fails, since when:** the challenge, its state, its `reason` quoted from the tool output, and
   when it was created.
3. **The certificate:** its name and `notAfter`; after that date the domain serves an expired
   certificate whether or not this is merged.
4. **Two ways out:** restore the domain's DNS and close this pull request (the bee won't propose it
   again), or merge it to stop serving the domain. Name anything else in the file that still mentions
   the domain; it is left alone.
5. **CVEs:** the solver image and its fixable Critical and High counts, which go with the challenge.
6. **How it reaches the cluster:** as section 8 above. When the workflow that deploys the file runs
   only on other changes, say it ships with that workflow's next deploy and how to start one.

## Rules
- The confidence level is `assess_upgrade`'s. Never raise it, and never open a pull request for Low.
- Say what you couldn't check. "Not checked" is an answer; a guess is not.
- A quote is the source's own words with its link. Your summary of it is not a quote.
- No tracker or run ids beyond the scans' `scan_id`s; no account names or internal hostnames in a
  public repository.
