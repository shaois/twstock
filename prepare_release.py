"""Offline display migration. No retraining, provider calls or invented history."""
import json
from pathlib import Path
from predictor import apply_observation_ranking, update_prediction_log, MODEL_NAME
from build_ai_context import publish

def prepare(root):
    from universe300 import install, seed_history
    if (root/"bootstrap/universe300.json").exists(): install(root); seed_history(root)
    from bootstrap_fundamentals import seed_missing
    seed_missing(root)
    cache = root / "cache"
    def read(name):
        return json.loads((cache / name).read_text(encoding="utf-8"))
    predictions = read("predictions.json")
    if not predictions.get('model', {}).get('latest_date') or predictions.get('model', {}).get('name') != MODEL_NAME or set(predictions.get('data', {})) != set(read('universe.json')['data']):
        # Rebuild from the user's existing date, never copy an older bundle
        # over online cache. No external data or AI request is made here.
        from datetime import date, timedelta
        from update_all import build_model_outputs
        data_date = predictions['model'].get('latest_date') or max(r['date'] for r in read('benchmark.json')['data'])
        cutoff = (date.fromisoformat(data_date) + timedelta(days=1)).isoformat()
        prices = {s: [r for r in rows if r['date'] <= data_date]
                  for s, rows in read('price.json')['data'].items()}
        benchmark = [r for r in read('benchmark.json')['data'] if r['date'] <= data_date]
        build_model_outputs(prices, read('universe.json')['data'], benchmark, cutoff,
                            completion_check={'mode': 'v94_offline_migration', 'data_date': data_date})
        predictions = read('predictions.json')
    log_payload = read("prediction_log.json")
    wrapped = "data" in log_payload
    log = log_payload["data"] if wrapped else log_payload
    prior = next((s for d,s in reversed(sorted(log.items()))
                  if d < predictions['model']['latest_date']), None)
    comparable = bool(prior and all(prior.get(k) == predictions['model'].get(k)
                                   for k in ('experiment_id','universe_fingerprint'))
                      and prior.get('model_name') == predictions['model'].get('name'))
    predictions['model']['rank_comparison_status'] = ('comparable' if comparable else
                                                   'model_or_universe_changed' if prior else 'no_history')
    apply_observation_ranking(predictions, log)
    updated = update_prediction_log(log, predictions, read("price.json")["data"],
                                    benchmark_rows=read("benchmark.json")["data"],
                                    run_date=predictions["model"]["latest_date"])
    if wrapped:
        log_payload["data"] = updated
    else:
        log_payload = updated
    for name, value in [("predictions.json", predictions), ("prediction_log.json", log_payload)]:
        target = cache / name
        temp = target.with_suffix(".tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False), encoding="utf-8")
        temp.replace(target)
    publish(root, cache / "ai-context")
    from three_gate import publish as publish_three_gate
    publish_three_gate(root)
    from short_term import publish as publish_short_term
    publish_short_term(root)
    print("V94 display migration:", predictions["model"]["latest_date"],
          "history:", predictions["model"].get("observation_ranking", {}).get("history_dates", []))

if __name__ == "__main__":
    prepare(Path(__file__).resolve().parent)

