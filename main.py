"""Static dashboard and AI explanation proxy for the single 20-day model."""

from __future__ import annotations

import asyncio
import os
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
        "groq": {"llama-3.3-70b-versatile", "llama-3.1-8b-instant"},
        "nvidia": {"meta/llama-3.3-70b-instruct"},
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
    return api_key.strip(), {
        "model": body["model"],
        "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
        "temperature": 0.05, "max_tokens": 600,
    }


@app.post("/api/nvidia")
async def nvidia_proxy(request: dict[str, Any]) -> Any:
    api_key, body = _request_parts(request, "nvidia")
    body.setdefault("temperature", 0.05)
    body.setdefault("max_tokens", 420)
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
        raise HTTPException(status_code=response.status_code, detail="NVIDIA rejected the request; check your key and quota")
    return response.json()


@app.post("/api/groq")
async def groq_proxy(request: dict[str, Any]) -> Any:
    api_key, body = _request_parts(request, "groq")
    body.setdefault("temperature", 0.05)
    body.setdefault("max_tokens", 420)
    retry_statuses = {408, 409, 429, 500, 502, 503, 504}
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
        raise HTTPException(status_code=last_response.status_code, detail="Groq rejected the request; check your key and quota")
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
        "model": "single_horizon_20d_probability_audited_v91",
    }
