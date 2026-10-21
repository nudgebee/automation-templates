# K8s Upgrade Bee

Does the work of a Kubernetes minor version upgrade, one cluster at a time:

1. Opens a tracking issue for the cluster's upgrade: the plan.
2. Every run, re-runs every upgrade check: removed APIs still in use, Helm chart `kubeVersion`, add-on compatibility, drain-blocking PodDisruptionBudgets, kubelet skew, and the scanners' upgrade findings. It writes the evidence into the issue.
3. Fixes each blocker in the repository that deploys it (Argo CD, Flux, or nudgebee CI annotations), as one pull request per repository. It asks before opening each one. Things installed by hand get the exact commands instead.
4. When nothing blocks, it gives the exact upgrade commands for the control plane and each node pool, with other clusters' results for the same version jump, and asks a person to run them.
5. Compares alerts, restarts and readiness before and after, and writes the verdict with the numbers.

It never upgrades a cluster itself, never merges, and never reopens a pull request someone closed.

Needs: a tracker (GitHub, GitLab or Jira) for the plan. Optional: a GitHub or GitLab integration for the pull requests, and the Argo CD integration to read Applications. Without it, the agent needs read access to Applications and Flux objects through the agent's `runner.customClusterRoleRules`.

Before installing:
- Link the tracker's and the repos' GitHub or GitLab integration to every cluster account the bee is installed on. The bee reads the tracker and the repositories through that link, and without it those reads are refused.
- `tracker_project` must be exactly the repository (owner/name) or Jira key where the issues should go.

Limits in this version: pull requests go to github.com or GitLab (not GitHub Enterprise), and the step that opens them runs on amd64 nodes.
