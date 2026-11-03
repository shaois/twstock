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
