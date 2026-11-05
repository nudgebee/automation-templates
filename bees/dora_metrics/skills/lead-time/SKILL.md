---
name: lead-time
description: Work out lead time for changes, from a commit's time to the time it was deployed to production. Use after count-deploys, for the deploys that have a commit SHA.
---
# Lead time for changes

Lead time needs the commit behind a deploy and the repository it lives in. Only deploys with a
SHA (count-deploys step 2) count.

## 1. Find the repository
Follow find-deploy-source for the workload. For Argo CD the repository is the Application's
`repoURL`. For an image-tag SHA, the repository is the one named by `ci.nudgebee.com/git.repo`;
without that annotation there is no repository, so skip the deploy and count it as "no commit".

Note: for Argo CD the revision may be a commit in a config repository (manifests or values), not in
the application's code. Report it as config lead time and say so.

## 2. Read the commit time
- GitHub: `gh api repos/<owner>/<repo>/commits/<sha> --jq '.commit.committer.date'`;
- GitLab: `glab api "projects/<url-encoded repo>/repository/commits/<sha>" | jq -r .committed_date`.

- Not found (404, or "No commit found"): the tag was not a commit, for example a date like
  `20241105`. Count that deploy as "no commit" and go on with the next.
- Refused (401, 403, "permission denied", no integration): report "lead time not available:
  <error>" and stop this skill; every other read would fail the same way.

## 3. Lead time
For each deploy: deploy time − commit time. Report the median and the longest, in hours, and how
many deploys had a commit out of all deploys. A negative or a zero value means a clock or
matching problem: drop that deploy and list it.
