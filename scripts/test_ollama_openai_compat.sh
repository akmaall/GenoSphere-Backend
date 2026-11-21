#!/usr/bin/env bash
set -Eeuo pipefail

URL="http://127.0.0.1:11434/v1/chat/completions"
OUTPUT="/tmp/genosphere_ollama_api_test.json"

echo "[INFO] Menguji OpenAI-compatible API Qwen3 1.7B."
echo "[INFO] Batas waktu 20 menit."

curl -fSs \
    --connect-timeout 10 \
    --max-time 1200 \
    -X POST "$URL" \
    -H "Authorization: Bearer ollama" \
    -H "Content-Type: application/json" \
    -d '{
        "model": "qwen3:1.7b",
        "messages": [
            {
                "role": "system",
                "content": "Return one valid JSON object only."
            },
            {
                "role": "user",
                "content": "Return a JSON object with status equal to GENOSPHERE_OK."
            }
        ],
        "temperature": 0,
        "max_tokens": 100,
        "stream": false,
        "response_format": {
            "type": "json_object"
        }
    }' -o "$OUTPUT"

python3 - "$OUTPUT" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    response = json.load(f)

content = response["choices"][0]["message"]["content"]
print("Model:", response.get("model"))
print("Response:", content)

result = json.loads(content)

if result.get("status") != "GENOSPHERE_OK":
    raise SystemExit("[FAIL] Respons JSON tidak sesuai.")

print("[OK] OpenAI-compatible API dan inferensi Qwen3 berhasil.")
PY
