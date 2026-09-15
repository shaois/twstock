import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import main


class PriceContext(unittest.TestCase):
    def test_same_day_window_and_no_future(self):
        rows = [{"date":f"2026-09-{i:02}","close":100,"max":101,"min":99} for i in range(1,26)]
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            (folder/"price.json").write_text(json.dumps({"data":{"2454":rows}}),encoding="utf-8")
            with patch.object(main,"CACHE_DIR",folder):
                data=main.price_context("2454","2026-09-21")
                self.assertEqual(len(data["rows"]),21)
                self.assertEqual(data["rows"][-1]["date"],"2026-09-21")
                self.assertEqual(data["rows"][-1]["high"],101)
                with self.assertRaises(main.HTTPException) as error:
                    main.price_context("2454","2026-09-26")
                self.assertEqual(error.exception.status_code,409)
                with self.assertRaises(main.HTTPException): main.price_context("../bad","2026-09-21")

    def test_missing_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(main,"CACHE_DIR",Path(directory)):
                with self.assertRaises(main.HTTPException) as error:
                    main.price_context("2454","2026-09-21")
                self.assertEqual(error.exception.status_code,503)


if __name__=="__main__": unittest.main()
