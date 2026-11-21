#!/usr/bin/env bash

set +e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=================================================="
echo "1. DIAGNOSTIK KECEPATAN DAN SUMBER DAYA"
echo "=================================================="

free -h
echo
nproc
echo
df -h "$ROOT"
echo
df -T "$ROOT"

echo
echo "GPU:"
if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi
else
    echo "nvidia-smi tidak tersedia"
fi

echo
echo "=================================================="
echo "2. LOG KEGAGALAN INFERENSI OLLAMA"
echo "=================================================="

if [[ -f /tmp/genosphere_ollama_test.log ]]; then
    tail -n 120 /tmp/genosphere_ollama_test.log
else
    echo "Log tidak ditemukan"
fi

echo
echo "=================================================="
echo "3. STATUS OLLAMA GLOBAL"
echo "=================================================="

systemctl status ollama --no-pager -l 2>&1 | head -n 45

echo
echo "Proses Ollama:"

pgrep -af ollama

echo
echo "=================================================="
echo "4. KONFIGURASI BINARY BACKEND"
echo "=================================================="

.venv/bin/python - <<'PY'
from app.config import Settings

s = Settings(_env_file=".env")

print("PLINK2_BIN:", s.plink2_bin)
print("GEMMA_BIN:", s.gemma_bin)
print("AI_PROVIDER:", s.ai_provider)
print("AI_API_BASE_URL:", s.ai_api_base_url)
print("AI_MODEL:", s.ai_model)
print("OLLAMA_BASE_URL: tidak dikonfigurasi secara eksplisit")
print("OLLAMA_MODEL: tidak dikonfigurasi secara eksplisit")
PY

echo
echo "=================================================="
echo "5. UJI KONEKSI DATABASE MENGGUNAKAN .env"
echo "=================================================="

.venv/bin/python - <<'PY'
from app.config import Settings
from sqlalchemy import create_engine, inspect, text

try:
    settings = Settings(_env_file=".env")
    engine = create_engine(settings.sqlalchemy_url, pool_pre_ping=True)

    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT
                current_database(),
                current_user,
                current_setting('server_version')
        """)).fetchone()

        print("[OK] PostgreSQL berhasil diakses")
        print("Database:", result[0])
        print("User:", result[1])
        print("Version:", result[2])

        tables = inspect(engine).get_table_names()
        print("\nTabel public:")
        for table in tables:
            print("-", table)

        if tables:
            print("[OK] Tabel database ditemukan")
        else:
            print("[WARN] Database belum memiliki tabel public")

except Exception as e:
    print("[FAIL] Koneksi database gagal")
    print("Jenis error:", type(e).__name__)
    print("Pesan:", str(e).splitlines()[0])
PY

echo
echo "=================================================="
echo "6. AUDIT INTEGRASI AI DALAM SOURCE CODE"
echo "=================================================="

grep -RInE \
'ai_provider|ai_api_base_url|ai_api_key|ai_model|openai|AsyncOpenAI|ChatCompletion|OLLAMA|ollama|api/generate|api/chat' \
app --include='*.py' 2>/dev/null | head -n 180

echo
echo "=================================================="
echo "7. FILE-FILE YANG BERKAITAN DENGAN AI"
echo "=================================================="

find app -type f \
    \( -iname '*ai*' -o -iname '*interpret*' -o -iname '*analysis*' \) \
    -not -path '*/__pycache__/*' \
    | sort

echo
echo "=================================================="
echo "8. KONFIGURASI POSTGRESQL"
echo "=================================================="

sudo -v

if [[ $? -eq 0 ]]; then
    echo
    echo "--- Roles ---"
    sudo -u postgres psql -X -P pager=off -c '\du'

    echo
    echo "--- Databases ---"
    sudo -u postgres psql -X -P pager=off -c '\l'
else
    echo "[WARN] Tidak dapat memperoleh akses sudo"
fi

echo
echo "=================================================="
echo "9. PEMERIKSAAN SCRIPT PORTABILITAS"
echo "=================================================="

for FILE in \
    scripts/bootstrap.sh \
    scripts/package_tools.sh \
    scripts/build_wheelhouse.sh \
    scripts/start_ollama.sh \
    scripts/stop_ollama.sh \
    scripts/check_environment.sh
do
    if [[ -f "$FILE" ]]; then
        echo "[OK] $FILE"
    else
        echo "[MISSING] $FILE"
    fi
done

echo
echo "=================================================="
echo "10. SELESAI"
echo "=================================================="
