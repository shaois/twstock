"""Rebuild the complete model from the bundled cache without any API calls."""
from datetime import date, timedelta
from pathlib import Path
from update_all import build_model_outputs, load_json, PRICE_PATH, UNIVERSE_PATH, BENCHMARK_PATH, PREDICTIONS_PATH
from build_ai_context import publish

if __name__ == '__main__':
    current=load_json(PREDICTIONS_PATH,{})
    data_date=current['model']['latest_date']
    cutoff=(date.fromisoformat(data_date)+timedelta(days=1)).isoformat()
    prices=load_json(PRICE_PATH,{})['data']
    prices={sid:[r for r in rows if r['date']<=data_date] for sid,rows in prices.items()}
    benchmark=[r for r in load_json(BENCHMARK_PATH,{})['data'] if r['date']<=data_date]
    build_model_outputs(prices,load_json(UNIVERSE_PATH,{})['data'],benchmark,cutoff,
                        completion_check={'mode':'offline_rebuild','data_date':data_date,'new_market_data_fetched':False})
    root=Path(__file__).resolve().parent
    publish(root,root/'cache/ai-context')
