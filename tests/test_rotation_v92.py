import copy
from unittest import TestCase
from unittest.mock import AsyncMock
import httpx
import predictor as p
from rotation import attach_rotation, similarity, normalize_institutions, institution_summary
from update_all import fetch_aux


def rows():
    return [{"stock_id": str(i), "features": [2,0,3]+[0]*12,
             "entry_metrics": {"average_turnover_5_twd": 200 if i<3 else 100,
                               "average_turnover_20_twd": 100,
                               "capital_flow_5d_pct": 10, "capital_flow_20d_pct": 5,
                               "turnover_acceleration_5v20": 2 if i<3 else 1}} for i in range(6)]


class RotationTests(TestCase):
    def test_share_increase_and_unknown_sector_not_fabricated(self):
        source = rows()
        mapping = {str(i): {"industry_category": "A" if i<3 else "B"} for i in range(6)}
        summary = attach_rotation(source, mapping)
        self.assertGreater(summary[0]["share_change_pp"], 0)
        self.assertEqual(summary[0]["industry"], "A")
        self.assertEqual(source[0]["rotation"]["members"], 3)
        unknown = rows()
        self.assertEqual(attach_rotation(unknown, {}), [])
        self.assertEqual(unknown[0]["rotation"]["industry"], "分類未知")
        self.assertIsNone(unknown[0]["rotation_context"][3])

    def test_uniform_money_growth_is_not_sector_rotation(self):
        source = rows()
        for r in source:
            r["entry_metrics"]["average_turnover_5_twd"] = 200
        summary = attach_rotation(source, {str(i): {"industry_category": "A" if i<3 else "B"} for i in range(6)})
        self.assertTrue(all(abs(s["share_change_pp"])<1e-9 for s in summary))

    def test_rotation_changes_probability_not_only_tie_break(self):
        cohort = [{"base_date": "2020-01-01", "factor_percentile": 50, "features": [0]*7+[1],
                   "actual_return_20d": value, "actual_net_return_20d": value-.6,
                   "actual_alpha_20d": value, "rotation_context": [0,0,1, sector, 0,50]}
                  for value,sector in [(10,10),(-10,-10)] for _ in range(10)]
        a = p._cohort_prediction(cohort, 100, 50, rotation_context=[0,0,1,10,0,50])
        b = p._cohort_prediction(cohort, 100, 50, rotation_context=[0,0,1,-10,0,50])
        self.assertGreater(a["net_profit_probability"], b["net_profit_probability"])
        self.assertEqual(similarity(None, None), 1)

    def test_institutions_deduplicate_and_do_not_double_count_dealers(self):
        data = [{"date": "2026-01-01", "name": n, "buy": b, "sell": 1}
                for n,b in [("Foreign_Investor",10),("Investment_Trust",5),
                            ("Foreign_Investor",20),("Dealer",999)]]
        normalized = normalize_institutions(data)
        self.assertEqual(normalized[0]["foreign_net_shares"], 19)
        self.assertEqual(normalized[0]["trust_net_shares"], 4)
        self.assertEqual(normalize_institutions(data[:1]), [])

    def test_stale_or_incomplete_institution_window_is_unavailable(self):
        dates = ["2026-01-0"+str(i) for i in range(1,6)]
        data = [{"date": d, "foreign_net_shares": 100, "trust_net_shares": -50} for d in dates]
        self.assertEqual(institution_summary(data, dates, 1000)["net_volume_pct"], 25)
        self.assertIsNone(institution_summary(data[:-1], dates, 1000)["net_volume_pct"])

    def test_rotation_replay_uses_same_live_features(self):
        from test_single_20d_contract_v92 import fixture
        prices, bench, universe, dates = fixture(n=6, days=700)
        mapping = {sid: {"industry_category": "A" if i<3 else "B"} for i,sid in enumerate(universe)}
        result = p.build_predictions(prices, universe, bench, "2030-01-01", sector_data=mapping)
        self.assertEqual(result["model"]["sector_coverage"], 6)
        self.assertEqual(len(result["model"]["sector_rotation"]), 2)
        frozen = copy.deepcopy(result["_frozen_reference_candidate"])
        shadow = p.build_predictions(prices, universe, bench, "2030-01-01",
                                     sector_data=mapping, shadow_reference=frozen)
        self.assertEqual(frozen, result["_frozen_reference_candidate"])
        self.assertIn("_shadow_predictions", shadow)
        self.assertEqual(shadow["_shadow_predictions"]["model"]["reference_mode"], "frozen_prospective")


class AuxiliaryAPITests(TestCase):
    def test_official_dataset_and_bearer_token(self):
        import asyncio
        client = AsyncMock()
        client.get.return_value = httpx.Response(200, json={"status":200,"data":[]},
                                                 request=httpx.Request("GET", "https://example.test"))
        asyncio.run(fetch_aux(client, "test-key", "TaiwanStockInfo"))
        self.assertEqual(client.get.call_args.kwargs["params"]["dataset"], "TaiwanStockInfo")
        self.assertEqual(client.get.call_args.kwargs["headers"], {"Authorization":"Bearer test-key"})
