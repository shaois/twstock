"""Read-only same-cache historical comparison; never publishes predictions.

Usage: python audit_model_revision.py PATH_TO_PREVIOUS_RELEASE_ZIP
Historical replay has known pool-selection biases; not an untouched holdout.
"""
import json
from pathlib import Path
import sys
import types
import zipfile
from datetime import date, timedelta
import predictor


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    cache = Path(__file__).parent/'cache'
    load = lambda name: json.loads((cache/name).read_text(encoding='utf-8'))['data']
    prices, universe, benchmark = load('price.json'), load('universe.json'), load('benchmark.json')
    last = max(r['date'] for rows in prices.values() for r in rows)
    run_date = (date.fromisoformat(last)+timedelta(days=1)).isoformat()
    with zipfile.ZipFile(sys.argv[1]) as archive:
        old_protocol = types.ModuleType('legacy_protocol')
        exec(compile(archive.read('research_protocol.py'), 'legacy_protocol.py', 'exec'), old_protocol.__dict__)
        old_predictor = types.ModuleType('legacy_predictor')
        saved = sys.modules['research_protocol']
        try:
            sys.modules['research_protocol'] = old_protocol
            exec(compile(archive.read('predictor.py'), 'legacy_predictor.py', 'exec'), old_predictor.__dict__)
        finally:
            sys.modules['research_protocol'] = saved
    for name, module in [('previous', old_predictor), ('revised', predictor)]:
        result = module.build_predictions(prices, universe, benchmark, run_date)
        model = result['model']
        validation = model['validation']['20d']
        values = [r['prediction_20d']['expected_net_return'] for r in result['data'].values() if r.get('available')]
        print(json.dumps(dict(version=name, date=model['latest_date'], count=len(values),
                              positive=sum(v>0 for v in values), min=min(values), max=max(values),
                              shrinkage=model['adaptation']['return_shrinkage'],
                              brier=validation['profit_calibration']['brier_score'],
                              baseline_brier=validation['profit_calibration'].get('training_base_rate_brier'),
                              periods=validation['periods'], quintiles=validation['quintile_results'],
                              return_errors=validation.get('return_error_diagnostic'),
                              limitations=validation['limitations']), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
