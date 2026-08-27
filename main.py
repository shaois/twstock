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
NVIDIA_API_KEY_ENV = os.environ.get("NVIDIA_API_KEY", "")
GROQ_API_KEY_ENV = os.environ.get("GROQ_API_KEY", "")

app = FastAPI(title="Taiwan stock 20-day relative-return model", version="85")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

if CACHE_DIR.exists():
    app.mount("/cache", StaticFiles(directory=CACHE_DIR), name="cache")


def _request_parts(request: dict[str, Any], environment_key: str) -> tuple[str, dict[str, Any]]:
    api_key = str(request.get("api_key") or environment_key).strip()
    body = request.get("body")
    if not api_key:
        raise HTTPException(status_code=400, detail="API Key is required")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid AI request body")
    return api_key, dict(body)


@app.post("/api/nvidia")
async def nvidia_proxy(request: dict[str, Any]) -> Any:
    api_key, body = _request_parts(request, NVIDIA_API_KEY_ENV)
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
        raise HTTPException(status_code=502, detail=f"NVIDIA connection failed: {exc}") from exc
    if response.is_error:
        raise HTTPException(status_code=response.status_code, detail=response.text[:500])
    return response.json()


@app.post("/api/groq")
async def groq_proxy(request: dict[str, Any]) -> Any:
    api_key, body = _request_parts(request, GROQ_API_KEY_ENV)
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
        raise HTTPException(status_code=502, detail=f"Groq connection failed: {exc}") from exc
    if last_response is None:
        raise HTTPException(status_code=502, detail="Groq did not respond")
    if last_response.is_error:
        raise HTTPException(status_code=last_response.status_code, detail=last_response.text[:500])
    return last_response.json()


@app.get("/")
async def root() -> FileResponse:
    return FileResponse(ROOT / "index.html")


@app.get("/app.js")
async def app_script() -> FileResponse:
    return FileResponse(ROOT / "app.js", media_type="application/javascript")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "model": "single_horizon_20d_relative_strength_v88_2"}
