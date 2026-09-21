import unittest
from unittest.mock import patch
import predictor as p
import update_all as updater
from test_single_20d_contract_v92 import fixture


class StableCalendarTests(unittest.TestCase):
    def test_legacy_upgrade_stamps_before_first_trim_and_refresh(self):
        _, rows, _, _ = fixture(n=1, days=710)
        with patch.object(updater, "PRICE_HISTORY_LIMIT", 700):
            first = updater.merge_benchmark_rows(rows[:700], [])
            updated = updater.merge_benchmark_rows(first, rows[690:])
        self.assertEqual(updated[0]["_session_index"], 10)
        self.assertEqual(updated[-1]["_session_index"], 709)
        self.assertEqual(updater.merge_benchmark_rows(updated, rows[690:]), updated)
        self.assertNotIn("_session_index", rows[0])

    def test_future_append_and_leading_trim_keep_same_mature_samples(self):
        prices, rows, universe, dates = fixture(n=2, days=710)
        with patch.object(updater, "PRICE_HISTORY_LIMIT", 700):
            old = updater.merge_benchmark_rows(rows[:700], [])
            new = updater.merge_benchmark_rows(old, rows[695:])
        # Same snapshot and retained stock lookback: future prices cannot
        # rephase the calendar. Compare records, labels and features, not count.
        before, current_before = p._prepare_samples(prices, dates[698], old)
        after, current_after = p._prepare_samples(prices, dates[698], new)
        mature = lambda samples: [s for s in samples
            if s.get("label_end_date", "9999") < dates[698]]
        self.assertEqual(mature(before), mature(after))
        self.assertEqual(current_before, current_after)
        self.assertGreater(len(mature(before)), 20)
        before_dates = sorted({r["base_date"] for r in before})
        self.assertTrue(all(dates.index(b)-dates.index(a) == 20
                            for a, b in zip(before_dates, before_dates[1:])))
        old_model = p.build_predictions(prices, universe, old, dates[699])
        new_model = p.build_predictions(prices, universe, new, dates[699])
        self.assertEqual(old_model["model"]["ranked_20d"], new_model["model"]["ranked_20d"])
        for sid in prices:
            self.assertEqual(old_model["data"][sid]["prediction_20d"],
                             new_model["data"][sid]["prediction_20d"])

    def test_legacy_phase_unchanged(self):
        prices, rows, _, dates = fixture(n=1)
        anchored = updater.merge_benchmark_rows(rows, [])
        self.assertEqual(p._prepare_samples(prices, dates[-1], rows),
                         p._prepare_samples(prices, dates[-1], anchored))

    def test_historical_calendar_insert_fails_instead_of_silent_rephase(self):
        _, rows, _, _ = fixture(n=1, days=5)
        old = updater.merge_benchmark_rows([rows[0], rows[2], rows[3]], [])
        with self.assertRaisesRegex(ValueError, "explicit rebuild"):
            updater.merge_benchmark_rows(old, [rows[1]])
        old[1]["_session_index"] = 100
        with self.assertRaises(ValueError):
            updater.merge_benchmark_rows(old, [])


if __name__ == "__main__":
    unittest.main()
