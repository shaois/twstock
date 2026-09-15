"""Static dashboard and AI explanation proxy for the single 20-day model."""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / "cache"
ALLOWED_ORIGINS = [s.strip() for s in os.environ.get(
    "ALLOWED_ORIGINS", "https://shaois.github.io,https://twstock-app.onrender.com"
).split(",") if s.strip() and s.strip() != "*"]

app = FastAPI(title="Taiwan stock 20-day relative-return model", version="91")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)

if CACHE_DIR.exists():
    app.mount("/cache", StaticFiles(directory=CACHE_DIR), name="cache")


def _request_parts(request: dict[str, Any], provider: str) -> tuple[str, dict[str, Any]]:
    # No fallback to server environment secrets. CORS is not authentication.
    api_key = request.get("api_key")
    if not isinstance(api_key, str) or not api_key.strip():
        raise HTTPException(status_code=400, detail="請提供自己的 API Key")
    body = request.get("body")
    allowed_models = {
        "groq": {"openai/gpt-oss-20b", "openai/gpt-oss-120b"},
        "nvidia": {"nvidia/nemotron-3.5-lightning-30b-a3b"},
    }
    if (not isinstance(body, dict) or not isinstance(body.get("model"), str)
            or body["model"] not in allowed_models[provider]):
        raise HTTPException(status_code=400, detail="Unsupported model")
    messages = body.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 8:
        raise HTTPException(status_code=400, detail="Invalid messages")
    if any(not isinstance(m, dict) or m.get("role") not in {"system", "user", "assistant"}
           or not isinstance(m.get("content"), str) for m in messages):
        raise HTTPException(status_code=400, detail="Invalid message content")
    if sum(len(m["content"]) for m in messages) > 16000 or len(api_key) > 1024:
        raise HTTPException(status_code=400, detail="Request too large")
    if provider == "groq" and api_key.strip().startswith("nvapi-") or provider == "nvidia" and api_key.strip().startswith("gsk_"):
        raise HTTPException(status_code=400, detail="金鑰與供應商不符，請使用對應服務的 API Key")
    upstream_body = {
        "model": body["model"],
        "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
        "temperature": 0.05, "stream": False,
    }
    if provider == "nvidia":
        upstream_body.update(max_tokens=1024, chat_template_kwargs={"enable_thinking": False})
    else:
        upstream_body.update(max_completion_tokens=2048, reasoning_effort="low", reasoning_format="hidden")
    return api_key.strip(), upstream_body


def _upstream_error(response: httpx.Response, provider: str, api_key: str,
                    body: dict[str, Any]) -> HTTPException:
    """Expose only selected error fields, never raw headers/body or credentials."""
    def clean(value: Any) -> str:
        if not isinstance(value, str):
            return ""
        value = value.replace(api_key, "[REDACTED]")
        for message in body.get("messages", []):
            content = message.get("content", "")
            if content:
                value = value.replace(content, "[PROMPT REDACTED]")
        value = re.sub(r"(?i)Bearer\s+\S+|(?:gsk_|sk-|nvapi-)[A-Za-z0-9_-]+", "[REDACTED]", value)
        value = re.sub(r"(?i)(api[_ -]?key|authorization|token)\s*[=:]\s*[^\s,;]+", r"\1=[REDACTED]", value)
        return " ".join(value.split())[:500]

    try:
        payload = response.json()
    except ValueError:
        payload = None
    error = payload.get("error") if isinstance(payload, dict) else None
    error = error if isinstance(error, dict) else {}
    hints = {
        400: "請求參數或模型設定不被接受，請依錯誤原因檢查。",
        401: "金鑰驗證失敗，請確認使用此服務的有效 API Key。",
        403: "請求權限不足，請檢查帳號與模型使用權限。",
        404: "上游模型或資源不存在或無法存取；不代表 Render 路由不存在。",
        410: "模型端點已移除或停止服務，請更換模型；重新產生金鑰無法恢復已移除的端點。",
        413: "請求內容過大，需縮短解讀資料。",
        429: "已達請求或 Token 限制，請稍後再試並檢查服務用量限制。",
    }
    detail = {
        "source": "upstream", "provider": provider,
        "status": response.status_code, "model": body.get("model"),
        "code": clean(error.get("code")), "type": clean(error.get("type")),
        "message": clean(error.get("message")) or "上游未提供可安全顯示的 JSON 錯誤原因。",
        "hint": hints.get(response.status_code, "上游服務暫時異常，請稍後再試。" if response.status_code >= 500 else "請檢查服務設定及請求內容。"),
    }
    retry_after = response.headers.get("retry-after", "")
    if retry_after.isdigit() and len(retry_after) <= 8:
        detail["retry_after_seconds"] = int(retry_after)
    return HTTPException(status_code=response.status_code, detail=detail)


@app.post("/api/nvidia")
async def nvidia_proxy(request: dict[str, Any]) -> Any:
    api_key, body = _request_parts(request, "nvidia")
    timeout = httpx.Timeout(120.0, connect=20.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                "https://integrate.api.nvidia.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=body,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="NVIDIA connection failed") from exc
    if response.is_error:
        raise _upstream_error(response, "NVIDIA", api_key, body)
    return response.json()


@app.post("/api/groq")
async def groq_proxy(request: dict[str, Any]) -> Any:
    api_key, body = _request_parts(request, "groq")
    # Do not immediately repeat a rate-limited request; let the user see Retry-After.
    retry_statuses = {408, 409, 500, 502, 503, 504}
    timeout = httpx.Timeout(90.0, connect=20.0)
    last_response: httpx.Response | None = None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            for attempt in range(3):
                last_response = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=body,
                )
                if last_response.status_code not in retry_statuses:
                    break
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Groq connection failed") from exc
    if last_response is None:
        raise HTTPException(status_code=502, detail="Groq did not respond")
    if last_response.is_error:
        raise _upstream_error(last_response, "Groq", api_key, body)
    return last_response.json()


@app.get("/")
async def root() -> FileResponse:
    return FileResponse(ROOT / "index.html")


@app.get("/app.js")
async def app_script() -> FileResponse:
    return FileResponse(ROOT / "app.js", media_type="application/javascript")


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "model": "single_horizon_20d_rotation_v92",
        "ai_error_reporting": "v92-ai-fix-1",
        "ai_model_config": "v92-ai-model-fix-2",
        "ai_analysis": "direct-data-advice-1",
    }
