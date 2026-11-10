---
name: write-pr
description: Write the description of an upgrade pull request so a reviewer can decide in a minute what it fixes, what could break, and how sure the bee is. Use before every open_fix_pr.
---
# Write the pull request

Write it for the person who has to approve it: what changes, why it is worth it, what could go
wrong, and what you checked. Plain words, short lines, no filler. Every number comes from a tool
result of this run; never round, estimate or retype from memory.

## Shape
Use these sections, in this order. Leave out a section only when it has nothing to say.

1. **One line on top:** `Bumps <image> from <from> to <to>, run by <namespace>/<workload>.`
2. **Summary table:**
   | | |
   |---|---|
   | Confidence | 🟢 High / 🟡 Medium / 🔴 Low, the level `assess_upgrade` returned, unchanged |
   | CVEs fixed | the scan's `counts.fixed`, with how many are Critical and High |
   | CVEs left | `counts.left` |
   | CVEs added | `counts.new` |
   | Version change | e.g. minor, 1.18 to 1.19 |
3. **Why this upgrade:** two or three sentences. Which notable CVEs it fixes (the worst ones, by
   name and package), and why this version rather than a newer one if you scanned more than one.
4. **What could break:** one line per check from `assess_upgrade`'s `evidence`: the check, whether
   code or you read it, the result (✅ no risk, ⚠️ check, ❌ risk, ➖ not checked) and the evidence.
   Then, in your own words, what a reviewer should look at: e.g. a deprecated option this deployment
   uses, a data migration, a changed default. If nothing, say what you read that shows it.
5. **Release notes:** the releases between the two versions you read, each as a link, with the lines
   that matter quoted word for word.
6. **CVEs fixed:** a table `CVE | Package | Severity | Fixed in`, worst first, from the scan's
   `fixed` list. More than 15 rows go inside `<details><summary>All N</summary> … </details>`. Link
   each CVE or GHSA id to `https://osv.dev/vulnerability/<id>`.
7. **CVEs added**, if any, the same way. With any, there should be no pull request (read-scan).
8. **How it reaches the cluster:** whether merging deploys it. Look for what deploys the changed file
   (a CI workflow, an Argo CD or Flux app). When nothing does (a version kept in a comment, a
   `helm upgrade` run by hand), say so first in this section and give the exact command to run after
   merging.
9. **How this was checked:** both images scanned in the cluster by the same scanner, when, and the
   scan's execution id; each of the scan's `notes`; and that after rollout you check the new image
   runs, the pods are healthy and the fixed CVEs are gone.

## Rules
- The confidence level is `assess_upgrade`'s. Never raise it, and never open a pull request for Low.
- Say what you couldn't check. "Not checked" is an answer; a guess is not.
- A quote is the source's own words with its link. Your summary of it is not a quote.
- No tracker or run ids beyond the scan's execution id; no account names or internal hostnames in a
  public repository.
