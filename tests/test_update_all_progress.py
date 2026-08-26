import unittest

from update_all import resolve_start_index


class ResolveStartIndexTests(unittest.TestCase):
    def test_unfinished_cycle_continues_across_taipei_midnight(self):
        progress = {"date": "2026-08-25", "index": 40}

        self.assertEqual(resolve_start_index(progress, "2026-08-26", 200), 40)

    def test_completed_cycle_starts_from_first_stock(self):
        progress = {"date": "2026-08-25", "index": 0}

        self.assertEqual(resolve_start_index(progress, "2026-08-26", 200), 0)

    def test_invalid_progress_starts_from_first_stock(self):
        self.assertEqual(resolve_start_index({}, "2026-08-26", 200), 0)
        self.assertEqual(resolve_start_index({"index": 200}, "2026-08-26", 200), 0)


if __name__ == "__main__":
    unittest.main()
