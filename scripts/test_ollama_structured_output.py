#!/usr/bin/env python3
"""Small compatibility probe for Ollama JSON Schema output; does not access GWAS data."""
from __future__ import annotations
import json
import time
import httpx
from app.config import get_settings

settings = get_settings()
base = settings.ai_api_base_url.rstrip("/")
url = base if base.endswith("/chat/completions") else base + "/chat/completions"
schema = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}
body = {
    "model": settings.ai_model,
    "messages": [{"role": "user", "content": "Return the JSON object with ok set to true. No other content."}],
    "response_format": {
        "type": "json_schema",
        "json_schema": {"name": "schema_probe", "strict": True, "schema": schema},
    },
    "reasoning_effort": "none",
    "temperature": 0.0,
    "max_tokens": 32,
    "stream": False,
}
start = time.perf_counter()
try:
    response = httpx.post(
        url,
        headers={"Authorization": f"Bearer {settings.ai_api_key or 'ollama'}", "Content-Type": "application/json"},
        json=body,
        timeout=httpx.Timeout(180.0, connect=10.0, read=180.0, write=30.0, pool=10.0),
    )
    response.raise_for_status()
    data = response.json()
    choices = data.get("choices") or []
    content = (choices[0].get("message") or {}).get("content") if choices else None
    parsed = json.loads(content or "{}")
    if parsed != {"ok": True}:
        raise RuntimeError(f"Model tidak mematuhi schema probe: {parsed!r}")
    print("[OK] Ollama menerima JSON Schema dan menghasilkan objek valid.")
    print("Model:", data.get("model"))
    print("Durasi:", f"{time.perf_counter() - start:.2f} detik")
    print("Usage:", data.get("usage"))
except Exception as exc:
    print("[FAIL] Probe JSON Schema gagal:", type(exc).__name__, str(exc))
    raise SystemExit(1)
