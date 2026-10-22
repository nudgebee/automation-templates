---
name: upgrade-checks
description: The full list of checks for a Kubernetes minor upgrade (current → current+1), the command for each, and what counts as a blocker. Run every check on every run.
---
# Upgrade checks

Run **every** check below on every run, even ones that passed last time. A blocker is gone only
when its check no longer finds it, not when a pull request merges. Report each check with the
command you ran and what it showed. A check that couldn't run is `not checked: <why>`, never a pass.

Target = current minor + 1. Never plan a jump of more than one minor.

## 0. Versions and support
- Current version: `nudgebee_agent_health_get` (`k8s_version`, `k8s_provider`), or `kubectl version -o json` (`serverVersion`).
- End of standard support:
  - **Provider API first, when the run can read the cloud account.** EKS: `aws eks describe-cluster-versions --cluster-versions <x.y>` (`endOfStandardSupportDate`).
  - **Otherwise, endoflife.date's data on GitHub** (public, through `github_execute`):
    `gh api repos/endoflife-date/endoflife.date/contents/products/<product>.md --jq .content | base64 -d`
    - `<product>` is `google-kubernetes-engine` (GKE), `amazon-eks` (EKS), `azure-kubernetes-service` (AKS), or `kubernetes` (anything else).
    - Find the `releaseCycle` matching the cluster's minor version.
    - `eoas` is the end of standard support. `eol` is the end of all support (or of extended support).
    - Cite `https://github.com/endoflife-date/endoflife.date/blob/master/products/<product>.md`.
  - **Due** when the end of standard support is within `notice_days`, or already past.
  - Only when neither source has the version: say "end of support unknown", and plan only when a person started the run.

## 1. What the scanners already found (quote these verbatim)
`recommendation_execute`:
```sql
SELECT rule_name, status,
       CAST(recommendation AS JSONB)->'deleted_items' AS deleted_items,
       CAST(recommendation AS JSONB)->'deprecated_items' AS deprecated_items,
       CAST(recommendation AS JSONB)->>'kind' AS kind,
       CAST(recommendation AS JSONB)->>'deleted_version' AS removed_in,
       CAST(recommendation AS JSONB)->>'replacement' AS replacement
FROM recommendation_view
WHERE category = 'InfraUpgrade' AND status = 'Open'
```
- Repeat the query for `helm_chart_upgrade`, `k8s_helm_compatibility`, `kube_proxy_version` and `eks_add_ons_version`, with their own JSON fields.
- Select JSON fields under their own names: a column named `recommendation` is cut to 500 characters. The column is text, so cast it: `CAST(recommendation AS JSONB)`.
- Skip the `pre_flight` and `post_flight` rows: they are planner snapshots, not findings.
- Every open row that concerns the target version goes into the plan as found. You may add context; you may not drop a row.

## 2. Removed APIs still in use
- **Which APIs:** the target's removed group/versions come from the Kubernetes deprecation guide:
  `gh api repos/kubernetes/website/contents/content/en/docs/reference/using-api/deprecation-guide.md --jq .content | base64 -d`, the section "v<target>".
- **Which objects:** for each removed kind, `kubectl get <resource>.<group> -A -o json`. For each object, look at:
  - `metadata.managedFields[].apiVersion`, plus each entry's `manager` and `operation`. This survives server-side apply;
  - the `apiVersion` inside the `kubectl.kubernetes.io/last-applied-configuration` annotation.

  The apiVersion the API server shows you is the storage version and proves nothing.
- **What decides it is the source that keeps writing the object:**
  - **Source found** (`find-deploy-source`): read the manifest at its revision. If it names a removed version, it's a blocker; if it already names a served version, it's fixed.
  - `managedFields` keeps the version of the last apply that changed something. A no-op apply after the fix leaves it on the old version, so a stale entry there is not a blocker when the source is fixed.
  - **No source found:** a `managedFields` or `last-applied` entry with a removed version is a blocker. Fix it with the exact commands to re-apply the object with a served version.
- **After an apiVersion change, Argo CD may show `ComparisonError ... unknown conversion`** (its server-side diff meets the old managedFields entry). A sync of that Application clears it. Say so in the plan; don't treat it as a new blocker.
- **Helm releases:** a chart's manifest still names the removed version (`helm get manifest <release> -n <ns>`). Report that release as a blocker too. Never read Helm's release Secrets with kubectl.

## 3. Helm charts
- `helm list -A -o json`. If that times out, find the namespaces that have Helm-managed objects with
  `kubectl get deploy,sts,ds -A -l app.kubernetes.io/managed-by=Helm -o jsonpath='{range .items[*]}{.metadata.namespace}{"\n"}{end}'`,
  then run `helm list -n <ns> -o json` for each one. For each release:
  - `helm get metadata <release> -n <ns> -o json` gives the chart and version;
  - if the chart's repository is known: `helm show chart <repo>/<chart> --version <v>` gives `kubeVersion`.
- Charts rendered by Argo CD or Flux have no Helm release, so `helm list` misses them. For **every** Argo CD
  Application (`argocd app list`, or `kubectl get applications.argoproj.io -A`) and every Flux HelmRelease, find its source
  (`find-deploy-source`). If the source directory has a `Chart.yaml`, read `kubeVersion` from it. For a chart from a chart
  repository, use `helm show chart <repo>/<chart> --version <v>`.
- A `kubeVersion` that the target doesn't satisfy is a blocker. No `kubeVersion` means compatibility unknown: look in the chart's release notes, otherwise say unknown.

## 4. Add-ons
- EKS managed add-ons: `aws eks describe-addon-versions --kubernetes-version <target> --addon-name <name>`, against the version you see running (`aws eks list-addons`, `describe-addon`).
- Other add-ons:
  - find them in `kubectl get deploy,ds -n kube-system,cert-manager,ingress-nginx,monitoring -o json` (image tags);
  - check the project's compatibility statement in its GitHub releases or docs (`gh api repos/<org>/<repo>/releases`).
- Every compatibility claim names its source URL, or says `unknown`.

## 5. Drain blockers
- `kubectl get pdb -A -o json`: `status.disruptionsAllowed == 0` with `status.expectedPods > 0` blocks node upgrades.
- A PDB with `maxUnavailable: 0`, or `minAvailable` equal to the replica count, always will.

## 6. Version skew
`kubectl get nodes -o json`: every node's `status.nodeInfo.kubeletVersion` must be within three minors of the target. Node pools are upgraded after the control plane.

## 7. Release notes
- Read the target's changelog highlights: `gh api repos/kubernetes/kubernetes/contents/CHANGELOG/CHANGELOG-<target>.md`, the "Urgent Upgrade Notes" section only.
- On EKS, also read `aws eks list-insights --cluster-name <name>` (then `describe-insight` for each non-passing one).
- List anything that needs action, with its source.

## Reporting a blocker
For each blocker give:
- the kind, namespace and name;
- why it blocks, with the check's command and output;
- the source (a URL, or "check output");
- the fix: a PR (repo found by `find-deploy-source`) or exact commands (no repo).
