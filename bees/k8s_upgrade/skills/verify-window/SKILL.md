---
name: verify-window
description: How to record the baseline before an upgrade, and judge the window after it with numbers. Use when a cluster is ready to upgrade and again once the window has passed.
---
# Baseline and verify window

## Before the upgrade (the "ready" run)
Record a baseline in the tracking issue with the time it was taken (UTC):
- **Alerts:** Nudgebee alerts on this account by priority over the last `verify_hours` (`aggregate_events` grouped by priority, or `list_events` with a time range).
- **Restarts:** `kubectl get pods -A -o json`. Record the sum of `restartCount` per namespace and the list of pods with restarts.
- **Not ready:** `kubectl get deploy,sts,ds -A -o json`. List workloads with fewer ready than desired replicas.
- **API server errors:** if Prometheus has it (`prometheus_execute`): `sum(increase(apiserver_request_total{code=~"5.."}[<verify_hours>h]))`. Otherwise write "not available".

## When the upgrade is done
Done means the control plane and every node pool are at the target (`kubectl version`, every node's kubelet version). Write the time you first saw that into the issue: it starts the window.

## After `verify_hours`
Take the same numbers for the window. Compare them with the baseline, and write both side by side.
- For restarts, count only pods present in both readings, and new pods' own counts. Drained pods start at zero.

| Verdict | When |
|---|---|
| `regressed` | Critical or high alerts, or not-ready workloads, are higher at the end of the window than in the baseline |
| `verified` | No number rose by more than 20%, with small counts (under 3) treated as noise |
| `unsure` | Anything else; say which number a person should look at |

Never call a verdict without the numbers next to it.
