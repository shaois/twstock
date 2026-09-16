import unittest
import tempfile
import json
from pathlib import Path
from build_ai_context import build_context, publish


class ContextTests(unittest.TestCase):
    def test_publish_without_institution_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "cache").mkdir()
            (root / "cache/predictions.json").write_text(json.dumps({"data": {"2834": {
                "available": True, "as_of_date": "2026-09-15", "current_price": 18}}}))
            (root / "cache/price.json").write_text(json.dumps({"data": {"2834": [{
                "date": "2026-09-15", "open": 18, "max": 19, "min": 17,
                "close": 18, "Trading_Volume": 1000}]}}))
            publish(root, root / "output")
            result = json.loads((root / "output/2834.json").read_text(encoding="utf-8"))
            self.assertTrue(result["aligned"])
            self.assertEqual(result["institutions"][0][1:], [None, None])

    def test_alignment_and_missing_institutions(self):
        p = {"as_of_date": "2026-09-15", "current_price": 18}
        bars = [{"date": d, "open": 18, "max": 19, "min": 17, "close": 18,
                 "Trading_Volume": 1000} for d in ["2026-09-14", "2026-09-15", "2026-09-16"]]
        c = build_context("2834", p, bars, [{"date": "2026-09-15", "foreign_net_shares": 100, "trust_net_shares": -50}])
        self.assertTrue(c["aligned"])
        self.assertEqual(len(c["bars"]), 2)
        self.assertEqual(c["institutions"][0][1:], [None, None])
        self.assertEqual(c["institutions"][1][1:], [100, -50])
        self.assertFalse(build_context("2834", {**p, "current_price": 19}, bars, [])["aligned"])
        self.assertEqual(build_context("2834", p, bars[:1], [])["bars"], [])

    def test_bad_ohlc_rejected(self):
        c = build_context("1", {"as_of_date": "2026-09-15", "current_price": 18},
                          [{"date": "2026-09-15", "open": 18, "max": 10, "min": 17,
                            "close": 18, "Trading_Volume": 1}], [])
        self.assertFalse(c["aligned"])


if __name__ == "__main__":
    unittest.main()
