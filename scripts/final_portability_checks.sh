#!/usr/bin/env bash
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=================================================="
echo "1. PERBAIKAN CHECKSUM"
echo "=================================================="

(
    find tools -type f \
        ! -name SHA256SUMS \
        ! -path 'tools/reports/deep_portability_audit.txt' \
        ! -name '*.log' \
        -print0 |
    sort -z |
    xargs -0 -r sha256sum
) > tools/SHA256SUMS

if sha256sum -c tools/SHA256SUMS; then
    echo "[OK] Seluruh checksum valid"
else
    echo "[FAIL] Checksum tidak valid"
fi

echo
echo "=================================================="
echo "2. PEMERIKSAAN DUPLIKASI MODEL"
echo "=================================================="

MODEL_DIR="tools/ollama/models"

echo "Model store aktif: $MODEL_DIR"

if [[ ! -d "$MODEL_DIR/manifests" ]]; then
    echo "[FAIL] Direktori manifest model tidak ditemukan."
else
    if find "$MODEL_DIR/manifests" -type f \
        -path '*/qwen3/1.7b' -print -quit 2>/dev/null | grep -q .; then
        echo "[OK] Manifest Qwen3 1.7B ditemukan."
    else
        echo "[FAIL] Manifest Qwen3 1.7B tidak ditemukan."
    fi

    if find "$MODEL_DIR/manifests" -type f \
        -path '*/qwen3/4b' -print -quit 2>/dev/null | grep -q .; then
        echo "[WARN] Manifest Qwen3 4B masih ditemukan."
    else
        echo "[OK] Manifest Qwen3 4B tidak ditemukan."
    fi

    echo "Daftar manifest model:"
    find "$MODEL_DIR/manifests" -type f -printf '%P\n' | sort
fi

du -sh "$MODEL_DIR" 2>/dev/null || true

echo
echo "=================================================="
echo "3. UJI INFERENSI OLLAMA BUNDLE"
echo "=================================================="

OLLAMA="$ROOT/tools/ollama/bin/ollama"
OLLAMA_LIB="$ROOT/tools/ollama/lib"
MODEL_DIR="$ROOT/tools/ollama/models"
HOST="127.0.0.1:11435"
URL="http://$HOST"
PID=""

cleanup() {
    if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
        kill "$PID" 2>/dev/null || true
        wait "$PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

if curl -fsS --max-time 2 "$URL/api/tags" >/dev/null 2>&1; then
    echo "[FAIL] Port 11435 sudah digunakan."
elif [[ ! -x "$OLLAMA" ]]; then
    echo "[FAIL] Binary Ollama tidak ditemukan."
else
    OLLAMA_MODELS="$MODEL_DIR" \
    OLLAMA_HOST="$HOST" \
    OLLAMA_NOHISTORY=1 \
    LD_LIBRARY_PATH="$OLLAMA_LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    "$OLLAMA" serve > /tmp/genosphere_ollama_test.log 2>&1 &

    PID=$!

    READY=0

    for i in $(seq 1 60); do
        if curl -fsS --max-time 2 "$URL/api/tags" >/dev/null 2>&1; then
            READY=1
            break
        fi
        sleep 1
    done

    if [[ "$READY" == "1" ]]; then
        echo "[OK] Server Ollama bundle berjalan."

        echo "[INFO] Menguji inferensi Qwen3 1.7B..."

        if curl -fsS --max-time 240 \
            -X POST "$URL/api/chat" \
            -H "Content-Type: application/json" \
            -d '{
                "model": "qwen3:1.7b",
                "messages": [{"role":"user","content":"Reply with exactly GENOSPHERE_QWEN_OK"}],
                "think": false,
                "stream": false,
                "options": {"num_predict": 64, "temperature": 0}
            }' > /tmp/genosphere_inference.json; then

            python3 - <<'PY'
import json

with open("/tmp/genosphere_inference.json") as f:
    data = json.load(f)

message = data.get("message") or {}
response = message.get("content", "")
print("Respons model:", response)
print("Model:", data.get("model"))
print("Total duration:", data.get("total_duration"))

if data.get("done") is True and "GENOSPHERE_QWEN_OK" in response:
    print("[OK] Inferensi berhasil.")
else:
    print("[FAIL] Respons model kosong.")
    raise SystemExit(1)
PY
        else
            echo "[FAIL] Inferensi Qwen3 gagal."
        fi
    else
        echo "[FAIL] Ollama bundle gagal startup."
        tail -n 40 /tmp/genosphere_ollama_test.log
    fi

    cleanup
    PID=""
fi

echo
echo "=================================================="
echo "4. KONFIGURASI DATABASE TANPA MEMBUKA PASSWORD"
echo "=================================================="

python3 - <<'PY'
from pathlib import Path

path = Path(".env")
safe_keys = [
    "POSTGRES_USER",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "PLINK2_BIN",
    "GEMMA_BIN",
    "AI_PROVIDER",
    "AI_API_BASE_URL",
    "AI_MODEL",
]

values = {}

if path.exists():
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")

    for key in safe_keys:
        print(f"{key}={values.get(key, '<TIDAK DIATUR>')}")
else:
    print("[FAIL] File .env tidak ditemukan.")
PY

echo
echo "=================================================="
echo "5. POSTGRESQL ROLE DAN DATABASE"
echo "=================================================="

if command -v psql >/dev/null && sudo -n true 2>/dev/null; then
    sudo -u postgres psql -X -P pager=off -c \
        "SELECT rolname, rolcanlogin FROM pg_roles ORDER BY rolname;"

    sudo -u postgres psql -X -P pager=off -c \
        "SELECT datname, pg_get_userbyid(datdba) AS owner
         FROM pg_database
         WHERE datistemplate = false
         ORDER BY datname;"
else
    echo "[INFO] Jalankan pemeriksaan role secara manual:"
    echo "sudo -u postgres psql -X -P pager=off -c '\\du'"
    echo "sudo -u postgres psql -X -P pager=off -c '\\l'"
fi

echo
echo "=================================================="
echo "6. KONFIGURASI BACKEND DAN RESOLUSI BINARY"
echo "=================================================="

echo "--- app/config.py ---"
sed -n '1,130p' app/config.py

echo
echo "--- app/services/tools.py ---"
sed -n '1,115p' app/services/tools.py

echo
echo "--- Referensi database ---"

grep -RInE \
    'create_engine|sessionmaker|POSTGRES_USER|POSTGRES_HOST|POSTGRES_DB|DATABASE_URL' \
    app --include='*.py' 2>/dev/null | head -n 100 || true

echo
echo "--- Referensi Ollama ---"

grep -RInE \
    'AI_PROVIDER|AI_API_BASE_URL|AI_MODEL|11434|api/chat' \
    app --include='*.py' 2>/dev/null | head -n 100 || true

echo
echo "=================================================="
echo "7. KONFIGURASI SERVICE OLLAMA GLOBAL"
echo "=================================================="

systemctl show ollama \
    --property=ExecStart \
    --no-pager 2>/dev/null || true

PIDS="$(pgrep -f '[o]llama serve' || true)"

if [[ -n "$PIDS" ]]; then
    while read -r P; do
        [[ -n "$P" ]] || continue
        echo "PID: $P"
        readlink -f "/proc/$P/exe" 2>/dev/null || true
        tr '\0' '\n' < "/proc/$P/environ" 2>/dev/null |
            grep '^OLLAMA_MODELS=' || true
    done <<< "$PIDS"
fi

echo
echo "=================================================="
echo "8. AKHIR PEMERIKSAAN"
echo "=================================================="

echo "Checksum:"
sha256sum -c tools/SHA256SUMS >/dev/null &&
    echo "[OK] Valid"

echo "Ukuran tools:"
du -sh tools

echo
echo "Skrip pemeriksaan selesai."
