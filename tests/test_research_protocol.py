import copy
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import predictor as p
import research_protocol as rp
import update_all as updater


def adaptation_records():
    records = []
    for i in range(16):
        d = date(2020, 1, 1) + timedelta(days=32*i)
        for j in range(10):
            records.append({
                "date": d.isoformat(), "label_end_date": (d+timedelta(days=28)).isoformat(),
                "local_return": 10.0, "prior_return": 1.0, "gross_return": 1.0,
                "local_alpha": 10.0, "prior_alpha": 1.0, "alpha": 10.0,
                "raw_profit": 80.0, "raw_outperform": 80.0,
                "won": int(j<2), "beat": int(j<3),
            })
    return records


def registered():
    now = datetime.fromisoformat("2026-01-01T18:00:00+08:00")
    state = rp.ensure_protocol({}, ["A", "B"], "code1", p.MODEL_NAME, now)
    cohort = [{"label_end_date": "2025-12-01"}]
    adapted = rp.fit_adaptation(adaptation_records(), "2026-01-01")
    rp.freeze_reference(state, cohort, adapted, "2026-01-01", now)
    return state, state["experiments"][state["active_experiment"]]


def snapshot(experiment, d="2026-01-01", end="2026-01-29"):
    entry = (date.fromisoformat(d)+timedelta(days=1)).isoformat()
    return {
        "experiment_id": experiment["id"], "universe_members": ["A", "B"],
        "first_recorded_at": d+"T19:00:00+08:00",
        "reference_mode": "frozen_prospective", "scheduled_exit_date": end,
        "20d": [{
            "stock_id": sid, "probability_rank": i+1, "entry_date": entry,
            "evaluation_status": "completed", "net_profit_probability": 60,
            "raw_net_profit_probability": 70, "outperform_probability": 55,
            "training_base_profit": 50, "actual_net_return": 1 if i == 0 else -1,
            "actual_alpha": 1 if i == 0 else -1,
        } for i, sid in enumerate(["A", "B"])],
    }


class AdaptationTests(unittest.TestCase):
    def test_shrinkage_chosen_by_earlier_losses_not_fixed_35_percent(self):
        fitted = rp.fit_adaptation(adaptation_records(), "2026-01-01")
        self.assertEqual(fitted["return_shrinkage"], 1.0)
        self.assertEqual(fitted["alpha_shrinkage"], 0.0)
        self.assertFalse(set(fitted["tuning_dates"]) & set(fitted["calibration_dates"]))
        self.assertLess(max(fitted["tuning_dates"]), min(fitted["calibration_dates"]))
        self.assertLess(fitted["max_label_end"], "2026-01-01")

    def test_future_and_calibration_outcomes_cannot_select_shrinkage(self):
        records = adaptation_records()
        original = rp.fit_adaptation(records, "2026-01-01")
        future = dict(records[0], date="2026-01-02", label_end_date="2026-02-01",
                      local_return=99999, gross_return=99999, won=1)
        self.assertEqual(original, rp.fit_adaptation(records+[future], "2026-01-01"))
        for r in records:
            if r["date"] in original["calibration_dates"]:
                r["gross_return"], r["alpha"] = 99999, -99999
        changed = rp.fit_adaptation(records, "2026-01-01")
        self.assertEqual(original["return_shrinkage"], changed["return_shrinkage"])
        self.assertEqual(original["alpha_shrinkage"], changed["alpha_shrinkage"])

    def test_calibration_is_monotone_and_corrects_known_overconfidence(self):
        fitted = rp.fit_adaptation(adaptation_records(), "2026-01-01")
        mapping = fitted["profit_map"]
        values = [rp.calibrated_probability(v, mapping) for v in [0, 20, 50, 80, 100]]
        self.assertEqual(values, sorted(values))
        self.assertTrue(all(0 <= v <= 100 for v in values))
        self.assertLess(rp.calibrated_probability(80, mapping), 50)

    def test_warmup_is_raw_and_does_not_freeze_prematurely(self):
        a = rp.fit_adaptation([], "2026-01-01")
        self.assertEqual(a["return_shrinkage"], 0)
        self.assertEqual(rp.calibrated_probability(80, a["profit_map"]), 80)
        state, _ = registered()
        active = state["experiments"][state["active_experiment"]]
        active["frozen_reference"] = None
        rp.freeze_reference(state, [{"label_end_date": "2025-12-01"}], a, "2026-01-01")
        self.assertIsNone(active["frozen_reference"])

    def test_full_replay_and_truncated_live_use_identical_adaptation(self):
        # Enough history to actually fit adaptation, not just the warmup path.
        from test_single_20d_contract_v92 import fixture
        prices, bench, universe, dates = fixture(n=5, days=1000)
        result = p.build_predictions(prices, universe, bench, "2030-01-01")
        period = result["model"]["validation"]["20d"]["period_details"][-1]
        d = period["date"]
        self.assertTrue(period["adaptation"]["calibration_dates"])
        truncated = {sid: [r for r in rows if r["date"] <= d] for sid, rows in prices.items()}
        live = p.build_predictions(truncated, universe, [r for r in bench if r["date"]<=d],
                                   (date.fromisoformat(d)+timedelta(days=1)).isoformat())
        self.assertEqual(period["adaptation"], live["model"]["adaptation"])
        with patch.object(p, "MAX_REPLAY_PERIODS", 2):
            short_report = p.build_predictions(prices, universe, bench, "2030-01-01")
        self.assertEqual(short_report["model"]["adaptation"], result["model"]["adaptation"])
        self.assertEqual(short_report["model"]["validation"]["20d"]["period_details"][-1]["adaptation"],
                         period["adaptation"])
        reference = result["_frozen_reference_candidate"]
        original_reference = copy.deepcopy(reference)
        baseline = p.build_predictions(prices, universe, bench, "2030-01-01", reference)
        # The caller's reference must not be mutated during a build.
        self.assertEqual(reference, original_reference)
        self.assertEqual(baseline["model"]["adaptation"], reference["adaptation"])


class ProtocolTests(unittest.TestCase):
    def test_updater_saves_freeze_once_and_records_same_day_immutably(self):
        from test_single_20d_contract_v92 import fixture
        prices, bench, universe, dates = fixture(n=5, days=1000)
        now = datetime.fromisoformat(dates[-1]+"T19:00:00+08:00")
        cutoff = (date.fromisoformat(dates[-1])+timedelta(days=1)).isoformat()
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with patch.multiple(updater,
                    RESEARCH_PROTOCOL_PATH=root/"research_protocol.json",
                    PREDICTION_LOG_PATH=root/"prediction_log.json",
                    SHADOW_LOG_PATH=root/"shadow_log.json",
                    SECTORS_PATH=root/"sectors.json",
                    INSTITUTIONS_PATH=root/"institutions.json",
                    PREDICTIONS_PATH=root/"predictions.json"), patch.object(updater, "taipei_now", return_value=now):
                updater.build_model_outputs(prices, universe, bench, cutoff)
                original = updater.load_audit_json(root/"research_protocol.json")
                first_log = updater.load_audit_json(root/"prediction_log.json")
                saved = updater.load_audit_json(root/"predictions.json")
                self.assertNotIn("_frozen_reference_candidate", saved)
                self.assertEqual(saved["model"]["reference_mode"], "rolling_live")
                self.assertEqual(saved["model"]["prospective_evaluation"]["completed_nonoverlapping_periods"], 0)
                updater.build_model_outputs(prices, universe, bench, cutoff)
                self.assertEqual(updater.load_audit_json(root/"research_protocol.json"), original)
                self.assertEqual(updater.load_audit_json(root/"prediction_log.json"), first_log)

    def test_membership_is_not_backdated_and_change_starts_new_experiment(self):
        state, first = registered()
        later = datetime.fromisoformat("2026-02-01T18:00:00+08:00")
        same = rp.ensure_protocol(state, ["B", "A"], "code1", p.MODEL_NAME, later)
        self.assertEqual(same, state)
        changed = rp.ensure_protocol(state, ["A", "C"], "code1", p.MODEL_NAME, later)
        self.assertNotEqual(changed["active_experiment"], first["id"])
        self.assertEqual(len(changed["membership_history"]), 2)
        self.assertEqual(changed["membership_history"][-1]["observed_at"], later.isoformat())
        self.assertFalse(changed["membership_history"][-1]["historical_backfill_verified"])
        code = rp.ensure_protocol(state, ["A", "B"], "code2", p.MODEL_NAME, later)
        self.assertNotEqual(code["active_experiment"], first["id"])

    def test_reference_cannot_refit_on_future_labels(self):
        state, experiment = registered()
        original = copy.deepcopy(experiment["frozen_reference"])
        rp.freeze_reference(state, [{"label_end_date": "2099-01-01"}], {"bad": True}, "2099-02-01")
        self.assertEqual(experiment["frozen_reference"], original)

    def test_future_training_is_rejected(self):
        state, experiment = registered()
        experiment["frozen_reference"] = None
        with self.assertRaises(ValueError):
            rp.freeze_reference(state, [{"label_end_date": "2026-01-01"}],
                                rp.fit_adaptation(adaptation_records(), "2026-01-01"), "2026-01-01")

    def test_forward_brier_excludes_overlapping_daily_signals(self):
        _, e = registered()
        log = {"2026-01-01": snapshot(e), "2026-01-02": snapshot(e, "2026-01-02", "2026-01-30")}
        report = rp.prospective_report(log, e)
        self.assertEqual(report["completed_nonoverlapping_periods"], 1)
        self.assertAlmostEqual(report["profit_brier"], .26)
        self.assertAlmostEqual(report["raw_profit_brier"], .29)
        self.assertAlmostEqual(report["training_baseline_brier"], .25)
        self.assertFalse(report["historical_pool_bias_removed"])
        self.assertFalse(report["test_data_used_for_training"])
        self.assertEqual(report["status"], "accumulating_pre_registered_results")

    def test_missing_outcome_excludes_whole_period_and_reserves_interval(self):
        _, e = registered()
        first = snapshot(e)
        first["20d"][1]["evaluation_status"] = "missing_data"
        report = rp.prospective_report({
            "2026-01-01": first, "2026-01-02": snapshot(e, "2026-01-02", "2026-01-30")}, e)
        self.assertEqual(report["completed_nonoverlapping_periods"], 0)
        self.assertIsNone(report["profit_brier"])
        self.assertEqual(len(report["excluded_or_pending"]), 1)

    def test_late_publication_or_unfrozen_reference_is_not_independent(self):
        _, e = registered()
        s = snapshot(e)
        s["first_recorded_at"] = "2026-01-03T19:00:00+08:00"
        report = rp.prospective_report({"2026-01-01": s}, e)
        self.assertEqual(report["completed_nonoverlapping_periods"], 0)
        s = snapshot(e)
        s["reference_mode"] = "initialization"
        self.assertEqual(rp.prospective_report({"2026-01-01": s}, e)["completed_nonoverlapping_periods"], 0)

    def test_corrupt_audit_files_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d)/"audit.json"
            self.assertEqual(updater.load_audit_json(f), {})
            # Fixture writes only; production loading must not replace damage.
            with patch.object(Path, "read_text", return_value="{broken"):
                with self.assertRaises(json.JSONDecodeError):
                    updater.load_audit_json(f)
            with patch.object(Path, "read_text", return_value="[]"):
                with self.assertRaises(ValueError):
                    updater.load_audit_json(f)

    def test_workflow_preserves_protocol_and_old_pool_bars_survive(self):
        workflow = (updater.ROOT/".github/workflows/daily-cache.yml").read_text()
        self.assertIn("cache/research_protocol.json", workflow)
        clean, _, _, _ = updater.completed_model_inputs(
            {"OLD": [{"date": "2025-12-01"}], "A": []}, [], {"A": {}},
            datetime.fromisoformat("2026-01-01T18:00:00+08:00"))
        self.assertIn("OLD", clean)


if __name__ == "__main__":
    unittest.main()
