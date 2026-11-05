#!/usr/bin/env python3
"""Find Postgres queries that got slower and stayed slower, from Cloud SQL Query Insights.

Subcommands:
  fetch   pull hourly per-query numbers from Cloud Monitoring into a JSON file
  detect  list sustained regressions as of a given hour
  replay  run detect every N hours over a fetched range and group the flags into findings
  run     fetch the last days and check now (what a scheduled run calls)

detect --plans also compares each finding's sampled plans (Cloud Trace) before and in the window.

Standard library only. Auth: `gcloud auth print-access-token`, or $GCP_ACCESS_TOKEN.
"""
import argparse
import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone

API = "https://monitoring.googleapis.com/v3/projects/{project}/timeSeries"
TRACE_API = "https://cloudtrace.googleapis.com/v1/projects/{project}/traces"
MAX_PLAN_PAGES = 3  # up to 3000 sampled plans per statement
METRIC = "cloudsql.googleapis.com/database/postgresql/insights/perquery/"
HOUR = timedelta(hours=1)


# ---------- helpers ----------

def parse_time(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def fmt_time(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def floor_hour(t):
    return t.replace(minute=0, second=0, microsecond=0)


_PARAM = re.compile(r"\$\d+")
_PARAM_LIST = re.compile(r"\(\s*\$n(\s*,\s*\$n)*\s*\)")
_REPEATED_GROUP = re.compile(r"(\(\$n, \.\.\.\))(\s*,\s*\(\$n, \.\.\.\))+")
_SPACE = re.compile(r"\s+")


def normalise(sql):
    """One key per statement shape: parameter lists and multi-row VALUES collapse to one."""
    s = _SPACE.sub(" ", sql.strip())
    s = _PARAM.sub("$n", s)
    s = _PARAM_LIST.sub("($n, ...)", s)
    s = _REPEATED_GROUP.sub(r"\1", s)
    return s


def token():
    # An automation's script step gets the account's token in the environment.
    for name in ("GCP_ACCESS_TOKEN", "GOOGLE_OAUTH_ACCESS_TOKEN", "CLOUDSDK_AUTH_ACCESS_TOKEN"):
        if os.environ.get(name):
            return os.environ[name]
    return subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()


def list_series(project, filt, start, end, tok, value_reducer="REDUCE_SUM"):
    params = {
        "filter": filt,
        "interval.startTime": fmt_time(start),
        "interval.endTime": fmt_time(end),
        "aggregation.alignmentPeriod": "3600s",
        "aggregation.perSeriesAligner": "ALIGN_DELTA",
        "aggregation.crossSeriesReducer": value_reducer,
        "aggregation.groupByFields": ["metric.label.querystring", "resource.label.database"],
        "pageSize": "100000",
    }
    page = None
    while True:
        q = dict(params)
        if page:
            q["pageToken"] = page
        url = API.format(project=project) + "?" + urllib.parse.urlencode(q, doseq=True)
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + tok})
        with urllib.request.urlopen(req, timeout=120) as r:
            body = json.load(r)
        for s in body.get("timeSeries", []):
            yield s
        page = body.get("nextPageToken")
        if not page:
            return


# ---------- fetch ----------

def fetch(project, instance, hours, end, tok, log=sys.stderr):
    start = end - HOUR * hours
    base = f'resource.labels.resource_id="{project}:{instance}"'
    # key -> hour -> [calls, total_us, blocks, rows]
    series = {}
    texts = {}

    def cell(db, text, hour):
        key = db + "\x00" + normalise(text)
        texts.setdefault(key, text)
        return series.setdefault(key, {}).setdefault(hour, [0, 0.0, 0, 0])

    chunk = timedelta(days=7)
    t = start
    while t < end:
        u = min(t + chunk, end)
        for s in list_series(project, f'metric.type="{METRIC}latencies" AND {base}', t, u, tok):
            db = s["resource"]["labels"].get("database", "")
            text = s["metric"]["labels"].get("querystring", "")
            for p in s.get("points", []):
                d = p["value"]["distributionValue"]
                n = int(d.get("count", 0))
                if n:
                    c = cell(db, text, p["interval"]["endTime"])
                    c[0] += n
                    c[1] += n * float(d.get("mean", 0))
        for idx, m in ((2, "shared_blk_access_count"), (3, "row_count")):
            for s in list_series(project, f'metric.type="{METRIC}{m}" AND {base}', t, u, tok):
                db = s["resource"]["labels"].get("database", "")
                text = s["metric"]["labels"].get("querystring", "")
                for p in s.get("points", []):
                    v = int(p["value"].get("int64Value", 0))
                    if v:
                        cell(db, text, p["interval"]["endTime"])[idx] += v
        if log:
            print(f"fetched {fmt_time(t)} .. {fmt_time(u)}: {len(series)} statements", file=log)
        t = u
    return {
        "project": project,
        "instance": instance,
        "start": fmt_time(start),
        "end": fmt_time(end),
        "texts": texts,
        "series": series,
    }


def cmd_fetch(a):
    end = floor_hour(parse_time(a.end) if a.end else datetime.now(timezone.utc))
    out = fetch(a.project, a.instance, a.days * 24, end, token())
    series = out["series"]
    with open(a.out, "w") as f:
        json.dump(out, f)
    print(f"wrote {a.out}: {len(series)} statements", file=sys.stderr)


# ---------- detect ----------

def hourly(cells, hour):
    c = cells.get(fmt_time(hour))
    if not c or c[0] == 0:
        return None
    calls, total_us, blocks, rows = c
    return {"calls": calls, "ms": total_us / calls / 1000.0, "blocks": blocks / calls, "rows": rows / calls}


def p90(xs):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.9 * (len(xs) - 1))))]


def detect(data, now, cfg):
    """Regressions as of `now`: the `sustain` hourly buckets ending 1 to `sustain` hours before
    `now` (the bucket ending at `now` is left out, so a finding lags by an hour)."""
    now = floor_hour(now)
    bad_hours = [now - HOUR * (i + 1) for i in range(cfg.sustain)]
    found, considered, blocks_up = [], [], set()
    for key, cells in data["series"].items():
        cur = [hourly(cells, h) for h in bad_hours]
        if blocks_rose(cells, bad_hours, cur, cfg):
            blocks_up.add(first_table(data["texts"].get(key, key.split("\x00", 1)[1])))
        # Sustained: nearly every hour of the window has traffic, enough calls in total, and all
        # but one of those hours are slow. One quiet or one normal hour doesn't reset the clock.
        cur = [c for c in cur if c and c["calls"] >= cfg.min_baseline_calls]
        slack = cfg.sustain // 6  # one hour of slack per six
        if len(cur) < cfg.sustain - slack or sum(c["calls"] for c in cur) < cfg.min_calls * cfg.sustain:
            continue
        # Baseline: the same hour of day on each of the previous `baseline_days` days.
        base = []
        for h in bad_hours:
            for d in range(1, cfg.baseline_days + 1):
                b = hourly(cells, h - timedelta(days=d))
                if b and b["calls"] >= cfg.min_baseline_calls:
                    base.append(b)
        if len(base) < cfg.min_baseline_points:
            continue
        b_ms = [b["ms"] for b in base]
        med, top = statistics.median(b_ms), p90(b_ms)
        threshold = max(2 * med, top, med + cfg.min_ms_added)
        added_db_s = sum((c["ms"] - med) * c["calls"] for c in cur) / 1000.0 / cfg.sustain
        considered.append((key, sum(c["ms"] * c["calls"] for c in cur)))
        window_ms = sum(c["ms"] * c["calls"] for c in cur) / sum(c["calls"] for c in cur)
        if window_ms <= threshold or sum(c["ms"] > threshold for c in cur) < len(cur) - slack:
            continue
        if added_db_s < cfg.min_db_seconds_per_hour:
            continue
        b_blocks = statistics.median([b["blocks"] for b in base])
        b_rows = statistics.median([b["rows"] for b in base])
        b_calls = statistics.median([b["calls"] for b in base])
        c_blocks = statistics.median([c["blocks"] for c in cur])
        c_rows = statistics.median([c["rows"] for c in cur])
        c_calls = statistics.median([c["calls"] for c in cur])
        found.append({
            "key": key,
            "database": key.split("\x00", 1)[0],
            "text": data["texts"].get(key, key.split("\x00", 1)[1])[:300],
            "baseline_ms": round(med, 2),
            "baseline_p90_ms": round(top, 2),
            "now_ms": [round(c["ms"], 2) for c in cur],
            "ratio": round(window_ms / med, 1) if med else None,
            "added_db_s_per_hour": round(added_db_s, 1),
            "blocks_ratio": round(c_blocks / b_blocks, 2) if b_blocks else None,
            "rows_ratio": round(c_rows / b_rows, 2) if b_rows else None,
            "calls_ratio": round(c_calls / b_calls, 2) if b_calls else None,
            "class": classify(c_blocks, b_blocks, c_rows, b_rows),
            "table": first_table(data["texts"].get(key, key.split("\x00", 1)[1])),
        })
    # Instance-wide: most of the busiest statements are slow at once → the instance, not a query.
    busiest = {k for k, _ in sorted(considered, key=lambda x: -x[1])[: cfg.top_n]}
    flagged_busy = [f for f in found if f["key"] in busiest]
    instance_wide = len(busiest) >= 5 and len(flagged_busy) > len(busiest) / 2
    # Shared cause: several statements on different tables need more blocks per call at the same
    # time. A query or index change hits its own statements; this pattern is dead rows nobody can
    # vacuum (a long-open transaction), bloat, or stale statistics — not a query to fix.
    mine = [f for f in found if f["class"] != "waits_io_cpu_not_mine"]
    tables = blocks_up - {None}
    shared = bool(mine) and len(tables) >= cfg.shared_min_tables
    out = {"now": fmt_time(now), "instance_wide": instance_wide, "shared_cause": None, "findings": found}
    if instance_wide:
        out["findings"] = []
    elif shared:
        out["shared_cause"] = {"kind": "blocks_up_across_tables", "tables": sorted(t for t in tables if t),
                               "regressed_statements": len(mine)}
        out["findings"] = [f for f in found if f["class"] == "waits_io_cpu_not_mine"]
    return out


def blocks_rose(cells, bad_hours, cur, cfg):
    """Looser test for the shared-cause check: blocks per call at least doubled in most hours that
    have calls, whatever happened to time per call. Low-traffic statements count here."""
    seen = [c for c in cur if c and c["calls"] >= cfg.min_baseline_calls]
    if len(seen) < (len(bad_hours) + 1) // 2:
        return False
    base = []
    for h in bad_hours:
        for d in range(1, cfg.baseline_days + 1):
            b = hourly(cells, h - timedelta(days=d))
            if b and b["calls"] >= cfg.min_baseline_calls:
                base.append(b["blocks"])
    if len(base) < cfg.min_baseline_points:
        return False
    med = statistics.median(base)
    return med > 0 and sum(c["blocks"] >= 2 * med for c in seen) >= len(seen) * 0.75


_TABLE = re.compile(r"\b(?:from|into|update|join)\s+\"?([a-z_][a-z0-9_]*)\"?", re.I)


def first_table(sql):
    m = _TABLE.search(sql)
    return m.group(1).lower() if m else None


def classify(c_blocks, b_blocks, c_rows, b_rows):
    if b_blocks and c_blocks >= 1.5 * b_blocks:
        return "plan_or_access_path"
    if b_rows and c_rows >= 1.5 * b_rows:
        return "code_or_data_change"
    return "waits_io_cpu_not_mine"


def load(path):
    """Read fetched data, re-keying by the current normalise() so older files merge the same way."""
    with open(path) as f:
        data = json.load(f)
    series, texts = {}, {}
    for key, cells in data["series"].items():
        db, text = key.split("\x00", 1)
        nk = db + "\x00" + normalise(text)
        texts.setdefault(nk, data["texts"].get(key, text))
        into = series.setdefault(nk, {})
        for hour, c in cells.items():
            if hour in into:
                into[hour] = [x + y for x, y in zip(into[hour], c)]
            else:
                into[hour] = list(c)
    data["series"], data["texts"] = series, texts
    return data


def cmd_detect(a):
    data = load(a.data)
    now = parse_time(a.now) if a.now else parse_time(data["end"])
    r = detect(data, now, a)
    if a.plans:
        add_plans(data, r, a, token())
    for f in r["findings"]:
        f.pop("key")
    json.dump(r, sys.stdout, indent=2)
    print()


# ---------- plans ----------

def list_plans(project, instance, database, text, start, end, tok):
    """Sampled plans of one statement from Cloud Trace, as [(time, shape)]."""
    # The filter matches a label prefix; quotes and backslashes would need escaping, so cut before them.
    prefix = re.split(r'["\\]', text, maxsplit=1)[0][:120]
    params = {
        "filter": f'root:"Cloud SQL Query" instance:"{project}:{instance}" normalized_query:"{prefix}"',
        "startTime": fmt_time(start),
        "endTime": fmt_time(end),
        "view": "COMPLETE",
        "pageSize": "1000",
    }
    want = normalise(text)
    out, page = [], None
    for _ in range(MAX_PLAN_PAGES):
        q = dict(params)
        if page:
            q["pageToken"] = page
        req = urllib.request.Request(TRACE_API.format(project=project) + "?" + urllib.parse.urlencode(q),
                                     headers={"Authorization": "Bearer " + tok})
        with urllib.request.urlopen(req, timeout=60) as r:
            body = json.load(r)
        for t in body.get("traces", []):
            spans = t.get("spans", [])
            root = next((s for s in spans if s.get("name") == "Cloud SQL Query"), None)
            if not root or root.get("labels", {}).get("database") != database:
                continue
            got = normalise(root["labels"].get("normalized_query", ""))
            # The sampled text is cut short; its last few characters may normalise differently.
            n = min(len(got), len(want)) - 20
            if n <= 0 or got[:n] != want[:n]:
                continue
            out.append((parse_time(root["startTime"][:19] + "Z"), plan_shape(spans, root["spanId"])))
        page = body.get("nextPageToken")
        if not page:
            return out
    return out  # enough samples to compare; the rest are not fetched


def plan_shape(spans, root_id):
    """The plan tree as node types with their index or table, without costs or row counts."""
    kids = {}
    for s in spans:
        kids.setdefault(s.get("parentSpanId"), []).append(s)

    def node(s):
        lab = s.get("labels", {})
        me = lab.get("Node Type", "?")
        if lab.get("Index Name"):
            me += " using " + lab["Index Name"]
        elif lab.get("Relation Name"):
            me += " on " + lab["Relation Name"]
        inner = sorted(node(c) for c in kids.get(s["spanId"], []))
        return me + (" (" + ", ".join(inner) + ")" if inner else "")

    return ", ".join(sorted(node(c) for c in kids.get(root_id, [])))


def compare_plans(before, after, min_before=3, min_after=2):
    """plan_changed: most plans in the window were (almost) never used before.
    same_plan: the window's usual plan was the usual plan before too.
    mix_shift: the window uses plans seen before, in a different mix (often parameter skew).
    too_few_plans: not enough samples on one side to say."""
    out = {"before_samples": len(before), "after_samples": len(after)}
    if len(before) < min_before or len(after) < min_after:
        out["verdict"] = "too_few_plans"
        return out
    b, a = Counter(before), Counter(after)
    b_main, a_main = b.most_common(1)[0][0], a.most_common(1)[0][0]
    out["before_plan"], out["after_plan"] = b_main, a_main
    new = sum(n for shape, n in a.items() if b[shape] < 0.05 * len(before))
    if new >= 0.5 * len(after):
        out["verdict"] = "plan_changed"
    elif a_main == b_main:
        out["verdict"] = "same_plan"
    else:
        out["verdict"] = "mix_shift"
    return out


def add_plans(data, result, cfg, tok):
    """Attach a plan comparison to each finding; a changed plan makes it a plan regression."""
    now = parse_time(result["now"])
    start = now - HOUR * cfg.sustain
    for f in result["findings"]:
        text = data["texts"].get(f["key"], f["key"].split("\x00", 1)[1])
        try:
            plans = list_plans(data["project"], data["instance"], f["database"], text,
                               start - timedelta(days=cfg.baseline_days), now, tok)
        except (OSError, ValueError) as e:  # urllib errors are OSErrors; plans are an extra, not the finding
            f["plans"] = {"verdict": "unavailable", "error": str(e)[:200]}
            continue
        f["plans"] = compare_plans([s for t, s in plans if t < start], [s for t, s in plans if t >= start])
        if f["plans"]["verdict"] == "plan_changed":
            f["class"] = "plan_or_access_path"


# ---------- run (what the scheduled automation calls) ----------

def check(data, now, cfg, tok=None):
    """One scheduled check, needing no memory between runs.

    A statement is reported when the check `step_hours` earlier saw it too (confirmed), and is
    `new` when no check in the `quiet_hours` before was reporting it, so the automation acts once
    per episode even if it flickers. The same holds for a shared cause. Plans are compared for
    new findings only."""
    step = HOUR * cfg.step_hours
    if cfg.database:
        prefix = cfg.database + "\x00"
        data = dict(data, series={k: v for k, v in data["series"].items() if k.startswith(prefix)})
    runs = [detect(data, now - step * i, cfg) for i in range(2 + cfg.quiet_hours // cfg.step_hours)]
    cur, prev = runs[0], runs[1]
    flagged = [{f["key"] for f in r["findings"]} for r in runs]
    flagged_prev = flagged[1]
    # Reported at an earlier check = flagged then and at the check before it.
    reported_prev = set().union(*(flagged[i] & flagged[i + 1] for i in range(1, len(runs) - 1)))
    shared_prev = any(runs[i]["shared_cause"] and runs[i + 1]["shared_cause"] for i in range(1, len(runs) - 1))
    reported, watching, not_mine = [], [], []
    for f in cur["findings"]:
        if f["class"] == "waits_io_cpu_not_mine":
            not_mine.append(f)
        elif f["key"] in flagged_prev:
            f["new"] = f["key"] not in reported_prev
            reported.append(f)
        else:
            watching.append(f)
    cur["findings"] = [f for f in reported if f["new"]]
    if tok and cur["findings"]:
        add_plans(data, cur, cfg, tok)
    for f in reported + watching + not_mine:
        f["id"] = statement_id(f["key"])
        f.pop("key")
    for f in watching + not_mine:
        f.pop("now_ms", None)
    shared = None
    if cur["shared_cause"] and prev["shared_cause"]:
        shared = dict(cur["shared_cause"], new=not shared_prev)
    out = {"instance": data["instance"], "now": cur["now"], "instance_wide": cur["instance_wide"],
           "shared_cause": shared, "findings": reported, "watching": watching, "not_mine": not_mine,
           "new": bool(cur["findings"]) or bool(shared and shared["new"])}
    new = [f for f in reported if f["new"]]
    # The span a cause must fall in: from the start of the baseline to now. A gcloud filter for
    # instance operations in it, without the daily backups.
    since = parse_time(cur["now"]) - timedelta(days=cfg.baseline_days) - HOUR * cfg.sustain
    out["since"] = fmt_time(since)
    out["operations_filter"] = (f"operationType!=BACKUP_VOLUME AND insertTime>={fmt_time(since)} "
                                f"AND insertTime<={cur['now']}")
    out["tables"] = sorted({f["table"] for f in new if f["table"]} | set(shared["tables"] if shared else []))
    # For a SQL IN list in a later step. Table names come from first_table(), so only [a-z0-9_].
    out["tables_sql"] = ", ".join(f"'{t}'" for t in out["tables"]) or "''"
    out["report"] = report(out, new, cfg)
    return out


CLASS_TEXT = {
    "plan_or_access_path": "reads far more blocks per call: a plan or index change",
    "code_or_data_change": "returns far more rows per call: a code or data change",
}


def report(r, new, cfg):
    """The new findings as Markdown, for an issue or a chat message."""
    lines = [f"Instance `{r['instance']}`, checked at {r['now']} over the last {cfg.sustain} hours, "
             f"against the same hours on the previous {cfg.baseline_days} days."]
    sc = r["shared_cause"]
    if sc and sc["new"]:
        lines += ["", f"**Shared cause, not a query to fix:** blocks read per call at least doubled on "
                  f"{len(sc['tables'])} tables at once ({', '.join(sc['tables'])}). Usually dead rows "
                  "nobody can vacuum (a long-open transaction), bloat, or stale statistics."]
    for f in new:
        lines += ["", f"### `{f['id']}` on database `{f['database']}`" + (f", table `{f['table']}`" if f["table"] else ""),
                  "", "```sql", f["text"], "```", "",
                  f"- {f['baseline_ms']} ms per call before (p90 {f['baseline_p90_ms']} ms), "
                  f"{round(sum(f['now_ms']) / len(f['now_ms']), 1)} ms now ({f['ratio']}x)",
                  f"- {f['added_db_s_per_hour']} s of extra database time per hour",
                  f"- blocks per call x{f['blocks_ratio']}, rows per call x{f['rows_ratio']}, "
                  f"calls x{f['calls_ratio']}: {CLASS_TEXT.get(f['class'], f['class'])}"]
        p = f.get("plans")
        if p:
            if p["verdict"] == "unavailable":
                lines.append(f"- sampled plans: could not be read ({p['error']})")
            elif p["verdict"] == "too_few_plans":
                lines.append(f"- sampled plans: too few to compare ({p['before_samples']} before, {p['after_samples']} now)")
            else:
                lines.append(f"- sampled plans ({p['before_samples']} before, {p['after_samples']} now): "
                             f"{p['verdict'].replace('_', ' ')}")
                lines.append(f"  - before: `{p['before_plan']}`")
                if p["after_plan"] != p["before_plan"]:
                    lines.append(f"  - now: `{p['after_plan']}`")
    return "\n".join(lines)


def statement_id(key):
    """Short stable id of a statement (database + normalised text), for branch names and dedupe."""
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def cmd_run(a):
    # Query Insights points land a few minutes late; an hour that just ended may still be filling.
    end = floor_hour(datetime.now(timezone.utc) - timedelta(minutes=15))
    if a.at:
        end = min(end, floor_hour(parse_time(a.at)))
    tok = token()
    # Enough for the two earlier checks' windows and their baseline days. Quiet: an automation's
    # script step reads stdout and stderr as one stream, and stdout must be the JSON.
    hours = a.baseline_days * 24 + a.sustain + a.step_hours + a.quiet_hours
    data = fetch(a.project, a.instance, hours, end, tok, log=None)
    json.dump(check(data, end, a, tok if a.plans else None), sys.stdout, indent=1)
    print()


# ---------- replay ----------

def cmd_replay(a):
    data = load(a.data)
    start = parse_time(data["start"]) + timedelta(days=a.baseline_days) + HOUR * a.sustain
    end = parse_time(data["end"])
    findings = {}  # key -> list of open/closed runs
    ticks = instance_ticks = 0
    shared = []
    t = start
    while t <= end:
        ticks += 1
        r = detect(data, t, a)
        if r["instance_wide"]:
            instance_ticks += 1
        if r["shared_cause"]:
            sc = r["shared_cause"]
            if shared and t - parse_time(shared[-1]["last"]) <= timedelta(hours=a.merge_gap_hours):
                shared[-1]["last"] = fmt_time(t)
                shared[-1]["tables"] = sorted(set(shared[-1]["tables"]) | set(sc["tables"]))
            else:
                shared.append({"first": fmt_time(t), "last": fmt_time(t), "tables": sc["tables"]})
        for f in r["findings"]:
            runs = findings.setdefault(f["key"], [])
            if runs and t - parse_time(runs[-1]["last"]) <= timedelta(hours=a.merge_gap_hours):
                run = runs[-1]
                run["last"] = fmt_time(t)
                run["ticks"] += 1
                run["peak_ratio"] = max(run["peak_ratio"] or 0, f["ratio"] or 0)
            else:
                runs.append({
                    "first": fmt_time(t), "last": fmt_time(t), "ticks": 1,
                    "database": f["database"], "text": f["text"], "class": f["class"],
                    "baseline_ms": f["baseline_ms"], "now_ms": f["now_ms"], "peak_ratio": f["ratio"],
                    "blocks_ratio": f["blocks_ratio"], "rows_ratio": f["rows_ratio"],
                    "calls_ratio": f["calls_ratio"], "added_db_s_per_hour": f["added_db_s_per_hour"],
                })
        t += HOUR * a.step_hours
    all_runs = sorted((r for runs in findings.values() for r in runs), key=lambda r: r["first"])
    # A finding is reported only when two runs in a row see it (the first run marks it
    # "watching"), and never one that is not a query to fix.
    for r in all_runs:
        r["reported"] = r["ticks"] >= a.confirm_ticks and r["class"] != "waits_io_cpu_not_mine"
    reported = [r for r in all_runs if r["reported"]]
    weeks = max(1.0, (end - start).total_seconds() / (7 * 86400))
    summary = {
        "instance": data["instance"], "from": fmt_time(start), "to": fmt_time(end),
        "ticks": ticks, "instance_wide_ticks": instance_ticks,
        "flag_runs": len(all_runs), "reported_findings": len(reported),
        "reported_per_week": round(len(reported) / weeks, 2),
        "statements_flagged": len(findings), "shared_cause_episodes": len(shared),
    }
    json.dump({"summary": summary, "shared_cause": shared, "findings": all_runs}, sys.stdout, indent=2)
    print()


def number(default):
    """argparse type: a float, or the default when the value is empty (an unset template input)."""
    return lambda v: float(v) if str(v).strip() else default


def add_rule_args(p):
    p.add_argument("--sustain", type=int, default=12, help="hours in the window that must be slow")
    p.add_argument("--baseline-days", type=int, default=7)
    p.add_argument("--min-calls", type=int, default=20, help="average calls per hour over the window")
    p.add_argument("--min-baseline-calls", type=int, default=5)
    p.add_argument("--min-baseline-points", type=int, default=8)
    p.add_argument("--min-ms-added", type=number(20.0), default=20.0)
    p.add_argument("--min-db-seconds-per-hour", type=number(60.0), default=60.0)
    p.add_argument("--top-n", type=int, default=20, help="busiest statements for the instance-wide check")
    p.add_argument("--shared-min-tables", type=int, default=3,
                   help="plan regressions on this many tables at once = a shared cause, not a query")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--project", required=True)
    f.add_argument("--instance", required=True)
    f.add_argument("--days", type=int, default=8)
    f.add_argument("--end", help="UTC hour, default now")
    f.add_argument("--out", required=True)
    f.set_defaults(fn=cmd_fetch)
    d = sub.add_parser("detect")
    d.add_argument("--data", required=True)
    d.add_argument("--now", help="UTC hour, default the end of the data")
    d.add_argument("--plans", action="store_true", help="compare sampled plans from Cloud Trace (live data only)")
    add_rule_args(d)
    d.set_defaults(fn=cmd_detect)
    r = sub.add_parser("replay")
    r.add_argument("--data", required=True)
    r.add_argument("--step-hours", type=int, default=3)
    r.add_argument("--merge-gap-hours", type=int, default=24)
    r.add_argument("--confirm-ticks", type=int, default=2)
    add_rule_args(r)
    r.set_defaults(fn=cmd_replay)
    c = sub.add_parser("run", help="fetch the last days and check now: what a scheduled run calls")
    c.add_argument("--project", required=True)
    c.add_argument("--instance", required=True)
    c.add_argument("--database", help="report findings for this database only")
    c.add_argument("--at", help="check as of this UTC hour (YYYY-MM-DDTHH:00:00Z) instead of now")
    c.add_argument("--step-hours", type=int, default=3, help="hours between scheduled runs")
    c.add_argument("--quiet-hours", type=int, default=24,
                   help="a finding reported within this many hours before is not new")
    c.add_argument("--no-plans", dest="plans", action="store_false", help="skip the Cloud Trace plan check")
    add_rule_args(c)
    c.set_defaults(fn=cmd_run)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
