---
name: choose-resolution
description: Decide what to do about one image's CVEs, cheapest resolution first, and which of the six states it ends in. Use for every image in scope, every run.
---
# Choose a resolution

Go down this list and stop at the first one that applies. An upgrade is not always the answer;
removing what nobody uses often clears more.

First read what is really deployed: the workload's `spec` image, and the repository's file for it
(find-deploy-source). If they disagree, say so with both values in the plan. The fix may already be
merged and not deployed. Never call that fixed, and ask the owner how it gets deployed.

## 0. Why does this workload exist?
Read the pod's owner chain before anything else: `kubectl get pod <pod> -n <ns> -o jsonpath='{.metadata.ownerReferences}'`,
then its owner the same way, up to the top.
- A solver pod of a cert-manager ACME challenge (owner: a `Challenge`): the image is there only while
  the challenge is open. `kubectl get challenges.acme.cert-manager.io -n <ns>` and its `state`; one
  `pending` for more than a day is the real problem. Say so: the finding names the stuck challenge
  and its domain, and the next step is fixing or deleting that challenge, not the image. State `ops`.
- A Job or CronJob pod, or a workload whose name or labels mark it a test or fixture (`test`, `e2e`,
  `fixture`, `demo`): name it as such; its owner decides to turn it off (step 1) or keep it. State
  `ops`.
- Anything else: go on.

## 1. Is it needed at all? → `turn_off`
Ask only with hard evidence, one of:
- the repository already disables this component;
- 0 pods for 14 days, no Service or Ingress points at it, and no CPU or network use over that
  window (`prometheus_execute`).

Anything else, however likely it looks, is `own_fix` with "needs owner confirmation".

## 2. Is a dependency update already waiting? → `dependency_pr`
For a repository you own: `gh pr list -R <repo> --search "<package>" --json number,title,url`. Link
an open update. Else, if a direct dependency has a fixed version, open a fix. A lockfile you can't
change exactly goes in the plan with the command to run.

## 3. Upstream chart or image with a newer version → `upgrade_pr`
1. Find the newer versions: `gh api repos/<owner>/<repo>/releases`, and upstream chart values with
   `gh api repos/<owner>/<repo>/contents/<path>?ref=<tag>`. Use `helm_execute` only for
   `helm get values` on the running release.
2. For a chart, read which image tags the new chart version ships. Those are the candidates.
3. Scan the candidate against the running image with `scan_candidate` (read-scan).
4. Apply the rule in read-scan. On `upgrade`, judge what it could break, then run `assess_upgrade`:
   - **Release notes**, every release between the two versions: `gh api repos/<owner>/<repo>/releases`,
     one page at a time. `hard` for breaking changes, removals or a required migration; `soft` for a
     deprecation. The quote is the release's own line copied word for word, with the release URL; for
     `none`, name the releases you read. A summary in your words is not a quote. Pages you couldn't
     read make it `partial`.
   - **Chart values** (charts only): the keys this release overrides (`helm get values`) against the
     new chart's `values.yaml`. `hard` when an overridden key is removed or renamed.
   - **Kubernetes APIs** (charts only): the new chart's templates against the APIs the cluster serves,
     as in upgrade-checks. `hard` for an API the cluster doesn't serve.
   - `os_family_from`: the running image's OS package type in `security_view`; `os_family_to`: the
     `os-pkgs/<family>` entry in the scan's `analysers`. Leave empty what you can't find.
   Never pick the level yourself: `assess_upgrade` returns it. `Low` is a plan entry, never a pull
   request. Otherwise change the chart version, or the tag, with `open_fix_pr`, and put the level and
   every evidence line in `why`, marking AI-read lines as such. If the repository mirrors the image,
   the mirror definition moves too; a second pull request is fine.

An upgrade whose scan didn't finish, or was never approved, stays `pending` with `next` saying why, never a pull request.

## 4. Your own image → `own_fix`
Before calling it `own_fix`, rule out two cheaper moves:
- **A newer release.** Find the image's project (the repository named by the image path, e.g.
  `ghcr.io/<owner>/<name>` → `<owner>/<name>`, or the workload's repository annotation) and read
  `gh api repos/<owner>/<repo>/releases --jq '.[0:5][] | [.tag_name, .published_at] | @tsv'`. A newer
  release than the running tag is an upgrade (step 3): scan it. A mirror of an upstream project
  (`ghcr.io/<you>/qdrant` mirrors `qdrant/qdrant`) is the same: the newer upstream release is the
  candidate; if the mirror doesn't have it yet, the next step is mirroring it.
- **A newer build already made.** A tag that carries a date or a build time more than 90 days old,
  when the same repository has newer builds, is "deploy the newer build", not "rebuild" (state
  `own_fix`, with that as `next`).

Only then a plan entry naming the move: rebuild, change the base, remove a package, or retire it.
Split the findings by operating system and application package, and say which move clears how many.

## 5. A dead line → `replace`
The project is archived, unmaintained, or the distribution is out of support. A plan entry with a
replacement you scanned. If it can't be scanned, it stays `pending` with `next` saying why.

## 6. Nothing applies → `none`
No fix yet, or the risk is accepted. Suggest `patch_in_place`, `not_affected` or `accepted` for a
person to decide, with the reason.
