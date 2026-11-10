---
name: find-deploy-source
description: Find the git repository, path and file that deploy a live Kubernetes resource, so a fix goes in as a pull request instead of a change in the cluster. Use before proposing any change to a resource.
---
# Find where a resource is deployed

Work only from what the resource and its owners say about themselves. Never guess a repository
from a name. When you can't find it, say which step stopped you and why.

## 1. Read the resource's markers
`kubectl get <kind> <name> [-n <namespace>] -o json` and look at `metadata.labels` and
`metadata.annotations`. Check them in this order; the first one found decides.

| Marker | Means |
|---|---|
| annotation `ci.nudgebee.com/git.repo` (+ `git.branch`, `helm.values.filePath`) | That repo and branch deploy it; the values file is named. Go to step 4. |
| label `argocd.argoproj.io/instance`, or annotation `argocd.argoproj.io/tracking-id` | Argo CD. Go to step 2. |
| labels `kustomize.toolkit.fluxcd.io/name` + `/namespace` | Flux Kustomization. Go to step 3. |
| labels `helm.toolkit.fluxcd.io/name` + `/namespace` | Flux HelmRelease. Go to step 3. |
| annotation `meta.helm.sh/release-name` only | Installed with `helm` from outside git. No repo: the fix is a `helm upgrade` command. |
| annotation `kubectl.kubernetes.io/last-applied-configuration` only | Applied by hand with `kubectl apply`. No repo: the fix is the exact manifest change plus the command. |
| none | Unknown. Say so. |

Do not use `app.kubernetes.io/instance`: plain Helm sets it too.

## 2. Argo CD
- **Application name:**
  - The `instance` label is the name.
  - In the `tracking-id` (`<instance>:<group>/<Kind>:<namespace>/<name>`) the name is the text before the first `:`.
  - If the instance contains `_`, it is `<app-namespace>_<app-name>`; write it as `<app-namespace>/<app-name>`.
- **Read the Application, in this order:**
  1. `argocd_execute`: `argocd app get <app> -o json` (needs the Argo CD integration);
  2. else `kubectl get applications.argoproj.io -A --field-selector metadata.name=<app-name> -o json`. This works only where the cluster's admin gave the agent read on Applications through `runner.customClusterRoleRules`.

  If both fail, report: "Argo CD application <app> owns it; its source could not be read. Connect Argo CD to Nudgebee, or allow the agent to read Applications (runner.customClusterRoleRules, or a newer agent)."
- **From `spec.source`, or each entry of `spec.sources`:**
  - a source with `path`: the repo is `repoURL`, the directory is `path`, the ref is `targetRevision`;
  - a source with `chart`: a Helm chart from a chart repository (name `chart`, version `targetRevision`). Its version is set in the Application itself, so find where the Application is defined: repeat step 1 on the Application object;
  - `helm.valueFiles` entries starting with `$<ref>/` live in the source whose `ref` is `<ref>`: that source's `repoURL`, the path after `$<ref>/`.

## 3. Flux
- **Kustomization:** `kubectl get kustomizations.kustomize.toolkit.fluxcd.io <name> -n <ns> -o json`.
  - The directory is `spec.path`.
  - `spec.sourceRef` names a `GitRepository`: `kubectl get gitrepositories.source.toolkit.fluxcd.io <name> -n <ns> -o json`.
  - From the GitRepository: the repo is `spec.url`, the ref is `spec.ref.branch`, `tag`, `semver` or `commit`.
- **HelmRelease:** `kubectl get helmreleases.helm.toolkit.fluxcd.io <name> -n <ns> -o json`. The chart is `spec.chart.spec.chart`, version `spec.chart.spec.version`.
  - The chart version is set in the HelmRelease manifest, so find where the HelmRelease is defined: repeat step 1 on the HelmRelease object.
- These reads work only where the cluster's admin gave the agent read on Flux objects (`runner.customClusterRoleRules`). If they fail, say so and name that fix.

## 4. Find the file
- **Repo name:** take the repo from the URL: `https://host/owner/name(.git)`, `git@host:owner/name(.git)` or `ssh://git@host/owner/name`. GitLab groups stay in the path.
- **List and read files at the ref:**
  - GitHub: `gh api "repos/<owner>/<name>/contents/<dir>?ref=<ref>"`;
  - GitLab: `glab api "projects/<url-encoded path>/repository/tree?path=<dir>&ref=<ref>"`.

  Quote URLs that contain `&`.
- **Pick the file:**
  - For a manifest, it's the file that has the resource's `kind` and `metadata.name`.
  - For a Helm value, it's the values file named in step 2 or 3, or `values.yaml` in the chart directory.
- Read the whole file before changing it. A change is an exact replacement: copy the `old` text from the file exactly, whitespace included, and make sure it occurs only once. Change only what must change.

## 5. Report
Report:
- the manager (ci_annotation, argocd, flux, helm, kubectl or unknown);
- repo, ref, path and file;
- for a chart: name, version, and where its version is set;
- the evidence: which label or annotation, and which command;
- or the reason there is no repo.

One pull request per repository: gather every change for the same repo before opening it.
