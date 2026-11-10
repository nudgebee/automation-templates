import argparse
import unittest
from datetime import datetime, timedelta, timezone

import pqf


def cfg(**kw):
    a = argparse.Namespace(sustain=3, baseline_days=7, min_calls=20, min_baseline_calls=5,
                           min_baseline_points=8, min_ms_added=20, min_db_seconds_per_hour=60, top_n=20,
                           shared_min_tables=3)
    for k, v in kw.items():
        setattr(a, k, v)
    return a


NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)


def series(ms_fn, calls=1000, blocks_fn=lambda h: 10, rows_fn=lambda h: 1, days=9):
    cells = {}
    for i in range(days * 24):
        h = NOW - timedelta(hours=i)
        ms = ms_fn(h)
        cells[pqf.fmt_time(h)] = [calls, ms * 1000 * calls, blocks_fn(h) * calls, rows_fn(h) * calls]
    return cells


def data(**named):
    return {"instance": "i", "texts": {}, "series": {"db\x00" + k: v for k, v in named.items()}}


def bad_since(hours):
    return lambda h: h > NOW - timedelta(hours=hours + 1)


class NormaliseTest(unittest.TestCase):
    def test_in_lists_collapse(self):
        self.assertEqual(pqf.normalise("SELECT * FROM t WHERE id IN ($1, $2, $3)"),
                         pqf.normalise("SELECT * FROM t WHERE id IN ($1,$2)"))

    def test_values_rows_collapse(self):
        self.assertEqual(pqf.normalise("INSERT INTO t VALUES ($1,$2),($3,$4),($5,$6)"),
                         pqf.normalise("INSERT INTO t VALUES ($1,$2)"))

    def test_different_statements_stay_apart(self):
        self.assertNotEqual(pqf.normalise("SELECT a FROM t WHERE x = $1"),
                            pqf.normalise("SELECT b FROM t WHERE x = $1"))


class DetectTest(unittest.TestCase):
    def test_sustained_plan_regression_found(self):
        bad = bad_since(3)
        d = data(q=series(lambda h: 200 if bad(h) else 10, blocks_fn=lambda h: 500 if bad(h) else 10))
        r = pqf.detect(d, NOW, cfg())
        self.assertEqual(len(r["findings"]), 1)
        self.assertEqual(r["findings"][0]["class"], "plan_or_access_path")

    def test_two_bad_hours_not_enough(self):
        bad = bad_since(2)
        d = data(q=series(lambda h: 200 if bad(h) else 10))
        self.assertEqual(pqf.detect(d, NOW, cfg())["findings"], [])

    def test_skew_that_alternates_all_week_stays_quiet(self):
        # Two plans take turns every other hour all week: the baseline p90 already covers the slow one.
        d = data(q=series(lambda h: 200 if h.hour % 2 else 10))
        self.assertEqual(pqf.detect(d, NOW, cfg())["findings"], [])

    def test_small_added_time_ignored(self):
        bad = bad_since(3)
        # 10 ms -> 25 ms: 2.5x but only +15 ms per call.
        d = data(q=series(lambda h: 25 if bad(h) else 10))
        self.assertEqual(pqf.detect(d, NOW, cfg())["findings"], [])

    def test_rows_up_is_code_or_data(self):
        bad = bad_since(3)
        d = data(q=series(lambda h: 200 if bad(h) else 10, rows_fn=lambda h: 50 if bad(h) else 1))
        self.assertEqual(pqf.detect(d, NOW, cfg())["findings"][0]["class"], "code_or_data_change")

    def test_instance_wide_gives_no_findings(self):
        bad = bad_since(3)
        d = data(**{f"q{i}": series(lambda h: 200 if bad(h) else 10) for i in range(8)})
        r = pqf.detect(d, NOW, cfg())
        self.assertTrue(r["instance_wide"])
        self.assertEqual(r["findings"], [])


    def test_blocks_up_on_many_tables_is_a_shared_cause(self):
        bad = bad_since(3)
        q = lambda: series(lambda h: 200 if bad(h) else 10, blocks_fn=lambda h: 500 if bad(h) else 10)
        named = {"a": q(), "b": q(), "c": q()}
        d = {"texts": {"db\x00a": "SELECT * FROM alpha WHERE x = $1", "db\x00b": "SELECT * FROM beta",
                       "db\x00c": "UPDATE gamma SET y = $1"},
             "series": {"db\x00" + k: v for k, v in named.items()}}
        r = pqf.detect(d, NOW, cfg(top_n=100))
        self.assertEqual(r["findings"], [])
        self.assertEqual(r["shared_cause"]["tables"], ["alpha", "beta", "gamma"])


class CheckTest(unittest.TestCase):
    def setUp(self):
        bad = bad_since(4)
        self.d = data(q=series(lambda h: 200 if bad(h) else 10, blocks_fn=lambda h: 500 if bad(h) else 10))

    def at(self, hours_later):
        return pqf.check(self.d, NOW + timedelta(hours=hours_later), cfg(step_hours=1, quiet_hours=3, database=None))

    def test_watching_then_new_then_ongoing(self):
        # Slow since NOW-4h with sustain 3: first flagged at NOW-1h.
        first = self.at(-1)
        self.assertEqual((len(first["watching"]), first["findings"], first["new"]), (1, [], False))
        confirmed = self.at(0)
        self.assertTrue(confirmed["new"])
        self.assertEqual(len(confirmed["findings"]), 1)
        self.assertTrue(confirmed["findings"][0]["new"])
        self.assertRegex(confirmed["findings"][0]["id"], "^[0-9a-f]{12}$")
        ongoing = self.at(1)
        self.assertFalse(ongoing["new"])
        self.assertFalse(ongoing["findings"][0]["new"])

    def test_database_filter(self):
        r = pqf.check(self.d, NOW, cfg(step_hours=1, quiet_hours=3, database="other"))
        self.assertEqual(r["findings"] + r["watching"], [])


class InputTest(unittest.TestCase):
    def test_empty_threshold_falls_back_to_default(self):
        ap = argparse.ArgumentParser()
        pqf.add_rule_args(ap)
        a = ap.parse_args(["--min-ms-added", "", "--min-db-seconds-per-hour", "30"])
        self.assertEqual((a.min_ms_added, a.min_db_seconds_per_hour), (20.0, 30.0))


class ScopeTest(unittest.TestCase):
    def test_database_filter_also_scopes_the_shared_cause(self):
        bad = bad_since(6)
        q = lambda: series(lambda h: 200 if bad(h) else 10, blocks_fn=lambda h: 500 if bad(h) else 10)
        d = {"instance": "i",
             "texts": {"other\x00a": "SELECT * FROM alpha", "other\x00b": "SELECT * FROM beta",
                       "other\x00c": "SELECT * FROM gamma"},
             "series": {"other\x00a": q(), "other\x00b": q(), "other\x00c": q()}}
        everything = pqf.check(d, NOW, cfg(step_hours=1, quiet_hours=3, database=None, top_n=100))
        self.assertIsNotNone(everything["shared_cause"])
        mine = pqf.check(d, NOW, cfg(step_hours=1, quiet_hours=3, database="app", top_n=100))
        self.assertIsNone(mine["shared_cause"])
        self.assertFalse(mine["new"])


class PlansUnavailableTest(unittest.TestCase):
    def test_trace_error_keeps_the_finding(self):
        bad = bad_since(4)
        d = data(q=series(lambda h: 200 if bad(h) else 10, blocks_fn=lambda h: 500 if bad(h) else 10))
        d["project"] = "p"
        real = pqf.list_plans
        def fail(*a, **k):
            raise OSError("403 Forbidden")
        pqf.list_plans = fail
        try:
            r = pqf.check(d, NOW, cfg(step_hours=1, quiet_hours=3, database=None), tok="t")
        finally:
            pqf.list_plans = real
        self.assertEqual(len(r["findings"]), 1)
        self.assertEqual(r["findings"][0]["plans"]["verdict"], "unavailable")


def plan(*nodes):
    """Spans of one sampled plan: nodes are (id, parent, labels)."""
    spans = [{"spanId": "r", "name": "Cloud SQL Query", "labels": {}}]
    spans += [{"spanId": i, "parentSpanId": parent, "labels": lab} for i, parent, lab in nodes]
    return pqf.plan_shape(spans, "r")


INDEX = plan(("1", "r", {"Node Type": "Limit"}),
             ("2", "1", {"Node Type": "Index Scan", "Index Name": "t_pkey", "Relation Name": "t", "Total Cost": "4"}))
SEQ = plan(("1", "r", {"Node Type": "Limit"}), ("2", "1", {"Node Type": "Seq Scan", "Relation Name": "t"}))


class PlanTest(unittest.TestCase):
    def test_shape_ignores_costs_and_names_the_index(self):
        again = plan(("9", "r", {"Node Type": "Limit"}),
                     ("8", "9", {"Node Type": "Index Scan", "Index Name": "t_pkey", "Total Cost": "99"}))
        self.assertEqual(INDEX, again)
        self.assertEqual(INDEX, "Limit (Index Scan using t_pkey)")
        self.assertEqual(SEQ, "Limit (Seq Scan on t)")

    def test_new_plan_is_a_plan_change(self):
        r = pqf.compare_plans([INDEX] * 20, [SEQ] * 4)
        self.assertEqual(r["verdict"], "plan_changed")
        self.assertEqual((r["before_plan"], r["after_plan"]), (INDEX, SEQ))

    def test_plans_that_alternated_before_are_a_mix_shift(self):
        self.assertEqual(pqf.compare_plans([INDEX] * 12 + [SEQ] * 8, [SEQ] * 4)["verdict"], "mix_shift")

    def test_same_plan(self):
        self.assertEqual(pqf.compare_plans([INDEX] * 10, [INDEX] * 3)["verdict"], "same_plan")

    def test_too_few_samples(self):
        self.assertEqual(pqf.compare_plans([INDEX] * 10, [SEQ])["verdict"], "too_few_plans")


if __name__ == "__main__":
    unittest.main()
