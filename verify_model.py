"""Rebuild and audit V91.1 using local caches; never overwrite production cache."""
import argparse
import json
from datetime import date, timedelta
from pathlib import Path
import predictor as p

ROOT = Path(__file__).resolve().parent

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path)
    parser.add_argument("--preview", type=Path)
    args = parser.parse_args()
    def load(name):
        return json.loads((ROOT / "cache" / name).read_text(encoding="utf-8"))["data"]
    prices, universe, benchmark = load("price.json"), load("universe.json"), load("benchmark.json")
    latest = max(r["date"] for rows in list(prices.values())+[benchmark] for r in rows if r.get("date"))
    run_date = (date.fromisoformat(latest)+timedelta(days=1)).isoformat()
    sector_path = ROOT / "cache" / "sectors.json"
    sectors = json.loads(sector_path.read_text(encoding="utf-8")).get("data", {}) if sector_path.exists() else {}
    result = p.build_predictions(prices, universe, benchmark, run_date, sector_data=sectors)
    result.pop("_frozen_reference_candidate", None)
    model = result["model"]
    assert result["count"] == len(universe) == 200
    assert model["architecture_contract"]["alpha_basis"] == "stock_net_minus_benchmark_net"
    assert model["architecture_contract"]["entry_basis"] == "next_benchmark_session_open"
    assert model["selected_20d"] == []
    available = [r for r in result["data"].values() if r.get("available")]
    assert sorted(r["probability_rank_20d"] for r in available) == list(range(1,len(available)+1))
    assert all("prediction_20d" in r for r in available)
    for period in model["validation"]["20d"]["period_details"]:
        assert period["max_training_label_end"] < period["date"]
    report = {
        "model": model["name"], "cache_data_date": model["latest_date"],
        "cache_only_not_live_data": True, "count": result["count"],
        "ranked": len(available),
        "sector_coverage": model["sector_coverage"],
        "distinct_downside_estimates": len({r["prediction_20d"]["downside_return"] for r in available}),
        "validation": model["validation"]["20d"],
        "adaptation": model["adaptation"],
        "prospective_status": "not_registered_local_development_replay_only",
    }
    for path, payload in ((args.report, report), (args.preview, result)):
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {k:v for k,v in report.items() if k != "validation"}
    summary["replay_periods"] = report["validation"]["periods"]
    summary["quintiles"] = report["validation"]["quintile_results"]
    summary["profit_brier"] = report["validation"]["profit_calibration"]["brier_score"]
    summary["baseline_brier"] = report["validation"]["profit_calibration"]["training_base_rate_brier"]
    summary["interval_coverage"] = report["validation"]["interval_coverage_pct"]
    print(json.dumps(summary, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
