"""Offline display migration. No retraining, provider calls or invented history."""
import json
from pathlib import Path
from predictor import apply_observation_ranking, update_prediction_log
from build_ai_context import publish

def prepare(root):
    cache = root / "cache"
    def read(name):
        return json.loads((cache / name).read_text(encoding="utf-8"))
    predictions = read("predictions.json")
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
    print("V93 display migration:", predictions["model"]["latest_date"],
          "history:", predictions["model"]["observation_ranking"]["history_dates"])

if __name__ == "__main__":
    prepare(Path(__file__).resolve().parent)
