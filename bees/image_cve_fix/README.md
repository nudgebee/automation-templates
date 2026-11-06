# Image CVE Fix Bee

Brings down one number: fixable Critical and High CVEs on the images running in the namespaces you choose.

1. Keeps one tracking issue per cluster: every image, its state and its evidence.
2. For each image it picks the cheapest fix that works: turn off what nobody uses, take a waiting dependency update, upgrade, rebuild, or replace a dead project.
3. Before asking to upgrade, it scans the newer version next to the running one. It asks only when CVEs are fixed and none are new, and lists what is left.
4. Opens the pull request. It asks every time, and never merges or reopens a pull request someone closed.
5. After the rollout, it proves the result on the running image.

It never changes a cluster, and works only in the namespaces you list. That list is an instruction to the agent, not a security limit: it does not restrict what the agent's tools can reach.

Needs: a GitHub tracker and a GitHub integration for the pull requests.

New versions are scanned in your cluster: `scan_candidate` pulls the candidate image onto a node and scans it without running it. It runs on its own outside prod and asks first on prod.

Before installing:
- Link the GitHub integration to every cluster account the bee is installed on.
- `tracker_project` must be exactly the repository (owner/name) for the issues.

Limits in this version: GitHub only (not GitHub Enterprise), whole-cluster runs and operating system upgrades are out of scope, and the step that opens pull requests runs on amd64 nodes.
