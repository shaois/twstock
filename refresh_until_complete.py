"""Bounded batch continuation. A successful batch is not a completed refresh."""
import json
import subprocess
import sys
import time
from pathlib import Path

MAX_SECONDS = 3 * 3600
MAX_BATCHES = 30
QUOTA_WAIT = 65 * 60
RETRY_WAIT = 60
MAX_STALLS = 3
ROOT = Path(__file__).resolve().parent

def read(path):
    try: return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError): return {}

def complete(progress, model):
    return (progress.get("total", 0) > 0 and progress.get("current_count") == progress["total"]
            and not progress.get("pending") and not progress.get("institution_pending")
            and progress.get("benchmark_ready") is True
            and model.get("model", {}).get("latest_date") == progress.get("target_date"))

# Confirmed cash-reduction suspension; do not infer a suspension from absent data.
# https://www.moneydj.com/KMDJ/news/newsviewer.aspx?a=80e8b6ea-1b62-4870-93f1-6cce1d660ed1
SUSPENSIONS = {"3714": ("2026-11-05", "2026-11-13")}

def suspended_only(progress, model, benchmark, institutions):
    day = progress.get('target_date')
    if not day or progress.get('errors') or not progress.get('benchmark_ready') or model.get('model',{}).get('latest_date') != day:
        return False
    prices = set(progress.get('pending', []))
    inst = set(progress.get('institution_pending', []))
    if not (prices or inst) or progress.get('total',0)<=0 or progress.get('current_count') != progress['total']-len(prices):
        return False
    def halted(sid,d):
        span = SUSPENSIONS.get(sid)
        return bool(span and span[0] <= d <= span[1])
    if any(not halted(s,day) for s in prices): return False
    days = sorted({r['date'] for r in benchmark.get('data',[]) if r['date']<=day})[-5:]
    if len(days)!=5 or days[-1]!=day: return False
    for sid in inst:
        if not any(halted(sid,d) for d in days): return False
        bars={r['date']:r for r in institutions.get('data',{}).get(sid,[])}
        for d in days:
            if not halted(sid,d) and any(not isinstance(bars.get(d,{}).get(k),(int,float)) for k in ('foreign_net_shares','trust_net_shares')):
                return False
    return True

def run(root=ROOT, execute=None, sleep=time.sleep, clock=time.monotonic):
    execute = execute or (lambda: subprocess.run([sys.executable, "update_all.py"], cwd=root).returncode)
    started = clock(); previous = None; stalls = 0; result = {}
    for batch in range(1, MAX_BATCHES+1):
        if clock()-started >= MAX_SECONDS: break
        print(f"Refresh continuation batch {batch}/{MAX_BATCHES}", flush=True)
        code = execute()
        progress = read(root/"cache/progress.json")
        model = read(root/"cache/predictions.json")
        result = {"batch":batch,"progress":progress,"exit_code":code}
        if code == 0 and complete(progress, model):
            print("COMPLETE: all prices, institutions, benchmark and model current", flush=True)
            return 0
        if code == 0 and suspended_only(progress, model, read(root/"cache/benchmark.json"), read(root/"cache/institutions.json")):
            print("::notice::Available data ready; confirmed suspension affects " + ", ".join(sorted(set(progress["pending"]+progress["institution_pending"]))) + ". Missing observations remain missing.", flush=True)
            return 0
        signature = (progress.get("target_date"), progress.get("current_count"),
                     tuple(progress.get("institution_pending", [])), model.get("model", {}).get("latest_date"))
        stalls = stalls+1 if signature == previous else 0
        previous = signature
        quota = any("quota" in str(e).lower() for e in progress.get("errors", []))
        if code != 0 or (stalls >= MAX_STALLS and not quota): break
        wait = QUOTA_WAIT if quota else RETRY_WAIT if stalls else 2
        if clock()-started+wait >= MAX_SECONDS: break
        print(f"Pending {len(progress.get('pending', []))} prices / {len(progress.get('institution_pending', []))} institutions; retry in {wait}s", flush=True)
        sleep(wait)
    print("INCOMPLETE: saved progress retained; next run resumes. " + json.dumps(result, ensure_ascii=False), flush=True)
    return 1

if __name__ == "__main__":
    raise SystemExit(run())
