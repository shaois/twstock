"""Local-only browser integration fixture. No external calls and no real key."""
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
import uvicorn
import main
from fastapi.responses import HTMLResponse

class FakeProvider:
    def __init__(self, **kwargs): pass
    async def __aenter__(self): return self
    async def __aexit__(self,*args): pass
    async def post(self,*args,**kwargs):
        result={"decision":"等待", **{name:{"text":"模擬測試文字，不是實際 AI 分析或投資建議。", "refs":["M1","F4"]}
                for name in ("support","against","conclusion","risk")},
                "action":"模擬觀察情境", "invalidation":"模擬失效情境"}
        return httpx.Response(200,json={"choices":[{"message":{"content":json.dumps(result,ensure_ascii=False)},"finish_reason":"stop"}]})

if __name__=='__main__':
    main.httpx.AsyncClient=FakeProvider
    for route in main.app.routes:
        if getattr(route,'path',None)=='/':
            main.app.routes.remove(route)
            break
    @main.app.get('/')
    async def fixture_index():
        return HTMLResponse((main.ROOT/'index.html').read_text(encoding='utf-8').replace('台股選股儀表板','離線模擬測試（不呼叫 AI）'))
    uvicorn.run(main.app,host='127.0.0.1',port=0)
