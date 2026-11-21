#!/usr/bin/env bash

set +e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

REPORT="tools/reports/integration_audit.txt"

mkdir -p tools/reports

exec > >(tee "$REPORT") 2>&1

echo "============================================================"
echo "GENOSPHERE INTEGRATION AUDIT"
echo "============================================================"
date -Is

echo
echo "============================================================"
echo "1. RAM DAN SWAP WSL"
echo "============================================================"

free -h
cat /proc/meminfo | grep -E 'MemTotal|MemAvailable|SwapTotal|SwapFree'

echo
echo "Cgroup memory:"
for FILE in /sys/fs/cgroup/memory.max /sys/fs/cgroup/memory.current /sys/fs/cgroup/memory.events; do
    if [[ -f "$FILE" ]]; then
        echo "--- $FILE ---"
        cat "$FILE"
    fi
done

echo
echo "============================================================"
echo "2. SPESIFIKASI LAPTOP WINDOWS"
echo "============================================================"

if command -v powershell.exe >/dev/null 2>&1; then
    powershell.exe -NoProfile -Command '
        $computer = Get-CimInstance Win32_ComputerSystem
        Write-Output "RAM_FISIK_GB:"
        [math]::Round($computer.TotalPhysicalMemory / 1GB, 2)

        Write-Output "GPU:"
        Get-CimInstance Win32_VideoController |
            Select-Object -ExpandProperty Name

        Write-Output "WSL_CONFIG:"
        $p = Join-Path $env:USERPROFILE ".wslconfig"
        if (Test-Path $p) {
            Get-Content $p
        } else {
            Write-Output "Tidak ada .wslconfig"
        }
    '
else
    echo "PowerShell tidak ditemukan."
fi

echo
echo "============================================================"
echo "3. STATUS OLLAMA GLOBAL"
echo "============================================================"

ollama --version 2>&1
ollama list 2>&1

echo
echo "--- API tags ---"
curl -sS --max-time 10 http://127.0.0.1:11434/api/tags \
    | python3 -m json.tool 2>/dev/null || true

echo
echo "--- API ps ---"
curl -sS --max-time 10 http://127.0.0.1:11434/api/ps \
    | python3 -m json.tool 2>/dev/null || true

echo
echo "--- Metadata Qwen3 1.7B ---"
curl -sS --max-time 30 \
    -X POST http://127.0.0.1:11434/api/show \
    -H "Content-Type: application/json" \
    -d '{"model":"qwen3:1.7b"}' |
python3 -c '
import json,sys
try:
    d=json.load(sys.stdin)
    print("Details:",d.get("details"))
    print("Model info:")
    for k,v in d.get("model_info",{}).items():
        if any(x in k.lower() for x in ("context_length","block_count","embedding_length")):
            print(k,":",v)
    print("Parameters:")
    print(d.get("parameters",""))
except Exception as e:
    print("Gagal membaca metadata:",e)
' 2>/dev/null

echo
echo "--- Log service Ollama ---"
journalctl -u ollama -n 50 --no-pager 2>&1

echo
echo "============================================================"
echo "4. FILE AI INTERPRETATION LENGKAP"
echo "============================================================"

echo "--- app/services/ai_interpretation.py ---"
nl -ba app/services/ai_interpretation.py

echo
echo "--- app/services/ai_jobs.py ---"
nl -ba app/services/ai_jobs.py

echo
echo "============================================================"
echo "5. PEMANGGILAN AI DARI ROUTER DAN SERVICE"
echo "============================================================"

grep -RInE -C 4 \
"enqueue|ai_jobs|generate\\(|get_provider|interpretation" \
app/routers app/services \
--include="*.py" 2>/dev/null | head -n 300

echo
echo "============================================================"
echo "6. FILE KONFIGURASI AMAN"
echo "============================================================"

python3 - <<'PY'
from pathlib import Path

safe_keys = {
    "POSTGRES_USER",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
    "PLINK2_BIN",
    "GEMMA_BIN",
    "AI_PROVIDER",
    "AI_API_BASE_URL",
    "AI_MODEL",
    "AI_TIMEOUT_SECONDS",
    "OLLAMA_BASE_URL",
    "OLLAMA_MODEL",
}

for filename in [".env", ".env.example"]:
    print(f"\n--- {filename} ---")
    path = Path(filename)

    if not path.exists():
        print("Tidak ditemukan")
        continue

    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        if key.strip() in safe_keys:
            print(f"{key.strip()}={value.strip()}")

    print("[Nilai rahasia sengaja tidak ditampilkan]")
PY

echo
echo "============================================================"
echo "7. SCRIPT OLLAMA DAN BOOTSTRAP"
echo "============================================================"

for FILE in \
    scripts/start_ollama.sh \
    scripts/stop_ollama.sh \
    scripts/bootstrap.sh \
    scripts/check_environment.sh
do
    echo
    echo "--- $FILE ---"

    if [[ -f "$FILE" ]]; then
        nl -ba "$FILE"
    else
        echo "FILE TIDAK DITEMUKAN"
    fi
done

echo
echo "============================================================"
echo "8. KONFIGURASI BACKEND"
echo "============================================================"

echo "--- app/main.py ---"
sed -n '1,180p' app/main.py

echo
echo "--- app/database.py ---"
cat app/database.py

echo
echo "============================================================"
echo "SELESAI"
echo "============================================================"

echo "Laporan: $REPORT"

