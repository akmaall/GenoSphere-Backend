#!/usr/bin/env bash

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS="$ROOT/tools"
REPORT="$TOOLS/reports/deep_portability_audit.txt"
TMP="$(mktemp -d /tmp/genosphere-audit.XXXXXX)"

ERRORS=0
WARNINGS=0
BUNDLE_PID=""

ok() {
    echo "[OK] $*"
}

warn() {
    echo "[WARN] $*"
    WARNINGS=$((WARNINGS + 1))
}

fail() {
    echo "[FAIL] $*"
    ERRORS=$((ERRORS + 1))
}

section() {
    echo
    echo "============================================================"
    echo "$1"
    echo "============================================================"
}

cleanup() {
    if [[ -n "$BUNDLE_PID" ]] && kill -0 "$BUNDLE_PID" 2>/dev/null; then
        kill "$BUNDLE_PID" 2>/dev/null || true
        wait "$BUNDLE_PID" 2>/dev/null || true
    fi
    rm -rf "$TMP"
}

trap cleanup EXIT INT TERM

mkdir -p "$TOOLS/reports"

exec > >(tee "$REPORT") 2>&1

echo "GENOSPHERE DEEP PORTABILITY AUDIT"
echo "Tanggal: $(date -Is)"
echo "Root: $ROOT"

# ============================================================
section "1. SISTEM DAN ARSITEKTUR"
# ============================================================

cat /etc/os-release | grep -E '^(PRETTY_NAME|VERSION_ID)='

echo "Kernel: $(uname -r)"
echo "Architecture: $(uname -m)"

if [[ "$(uname -m)" == "x86_64" ]]; then
    ok "Arsitektur sesuai dengan binary bundle"
else
    fail "Arsitektur tidak sesuai"
fi

if grep -qi microsoft /proc/version; then
    ok "WSL terdeteksi"
else
    warn "WSL tidak terdeteksi"
fi

echo
echo "Python:"
python3 --version 2>&1 || fail "Python3 tidak ditemukan"

echo
echo "Disk:"
df -h "$ROOT"

# ============================================================
section "2. STRUKTUR FOLDER TOOLS"
# ============================================================

for DIR in \
    "$TOOLS/bin" \
    "$TOOLS/ollama/bin" \
    "$TOOLS/ollama/lib/ollama" \
    "$TOOLS/ollama/models" \
    "$TOOLS/python/wheelhouse" \
    "$TOOLS/reports"
do
    if [[ -d "$DIR" ]]; then
        ok "$DIR"
    else
        fail "Folder tidak ditemukan: $DIR"
    fi
done

echo
echo "Ukuran komponen:"

du -sh "$TOOLS"/* 2>/dev/null || true

echo
echo "Jumlah file tools:"
find "$TOOLS" -type f | wc -l

# ============================================================
section "3. DUPLIKASI MODEL OLLAMA"
# ============================================================

SOURCE_MODEL="$TOOLS/ollama-models"
BUNDLED_MODEL="$TOOLS/ollama/models"

if [[ -d "$SOURCE_MODEL" ]]; then
    echo "Model store sumber:"
    du -sh "$SOURCE_MODEL"

    echo "Model store bundle:"
    du -sh "$BUNDLED_MODEL"

    echo "Jumlah file sumber:"
    find "$SOURCE_MODEL" -type f | wc -l

    echo "Jumlah file bundle:"
    find "$BUNDLED_MODEL" -type f | wc -l

    if diff -qr "$SOURCE_MODEL" "$BUNDLED_MODEL" > "$TMP/model_diff.txt" 2>&1; then
        ok "Kedua model store identik. Ditemukan kemungkinan duplikasi."
    else
        warn "Model store berbeda. Jangan hapus salah satunya sebelum diperiksa."
        head -n 30 "$TMP/model_diff.txt"
    fi
else
    ok "Tidak ditemukan direktori model sumber duplikat bernama ollama-models"
fi

if find "$BUNDLED_MODEL/manifests" -type f \
    -path '*/qwen3/1.7b' -print -quit 2>/dev/null | grep -q .
then
    ok "Manifest Qwen3 1.7B ditemukan"
else
    fail "Manifest Qwen3 1.7B tidak ditemukan"
fi

if [[ -d "$BUNDLED_MODEL/blobs" ]]; then
    echo "Jumlah blob:"
    find "$BUNDLED_MODEL/blobs" -type f | wc -l
else
    fail "Direktori blobs tidak ditemukan"
fi

# ============================================================
section "4. PLINK2 DAN GEMMA"
# ============================================================

for NAME in plink2 gemma; do

    BIN="$TOOLS/bin/$NAME"

    echo
    echo "--- $NAME ---"

    if [[ -x "$BIN" ]]; then
        ok "$NAME tersedia dalam tools/bin"

        if [[ "$NAME" == "plink2" ]]; then
            "$BIN" --version 2>&1 | head -n 3
        else
            "$BIN" -h 1 2>&1 | head -n 5
        fi

        echo "Dynamic dependencies:"
        LDD_OUTPUT="$(ldd "$BIN" 2>&1 || true)"
        echo "$LDD_OUTPUT" | head -n 20

        if grep -q "not found" <<< "$LDD_OUTPUT"; then
            fail "$NAME memiliki dependensi sistem yang hilang"
        else
            ok "Tidak ditemukan dependensi dinamis yang hilang"
        fi
    else
        fail "$NAME tidak ditemukan atau tidak executable"
    fi
done

# ============================================================
section "5. OLLAMA BUNDLE"
# ============================================================

OLLAMA="$TOOLS/ollama/bin/ollama"
OLLAMA_LIB="$TOOLS/ollama/lib"
MODEL_DIR="$TOOLS/ollama/models"

if [[ -x "$OLLAMA" ]]; then
    ok "Binary Ollama ditemukan"
    "$OLLAMA" --version 2>&1
else
    fail "Binary Ollama tidak ditemukan"
fi

if [[ -d "$OLLAMA_LIB/ollama" ]] &&
   find "$OLLAMA_LIB/ollama" -type f -print -quit | grep -q .
then
    ok "Runtime Ollama ditemukan"
else
    fail "Runtime Ollama tidak lengkap"
fi

if [[ -x "$OLLAMA" ]]; then
    echo
    echo "Dynamic dependencies Ollama:"
    LDD_OUTPUT="$(ldd "$OLLAMA" 2>&1 || true)"
    echo "$LDD_OUTPUT" | head -n 30

    if grep -q "not found" <<< "$LDD_OUTPUT"; then
        fail "Ollama memiliki dependensi sistem yang hilang"
    else
        ok "Tidak ditemukan dependensi dinamis yang hilang"
    fi
fi

echo
echo "Ollama yang sedang aktif di sistem:"

PIDS="$(pgrep -f '[o]llama serve' || true)"

if [[ -n "$PIDS" ]]; then
    while read -r PID; do
        [[ -n "$PID" ]] || continue
        echo "PID: $PID"
        echo "Executable: $(readlink -f "/proc/$PID/exe" 2>/dev/null || true)"
        echo "Model directory:"
        tr '\0' '\n' < "/proc/$PID/environ" 2>/dev/null |
            grep '^OLLAMA_MODELS=' || echo "OLLAMA_MODELS tidak ditetapkan secara eksplisit"
    done <<< "$PIDS"
else
    warn "Tidak ditemukan proses ollama serve aktif"
fi

# ============================================================
section "6. UJI OLLAMA DARI DALAM FOLDER PROYEK"
# ============================================================

OLLAMA_TEST_HOST="127.0.0.1:11435"
OLLAMA_TEST_URL="http://$OLLAMA_TEST_HOST"

if [[ ! -x "$OLLAMA" ]]; then
    fail "Tidak dapat menguji Ollama karena binary tidak ditemukan"
elif [[ ! -d "$OLLAMA_LIB/ollama" ]]; then
    fail "Tidak dapat menguji Ollama karena runtime tidak ditemukan"
elif curl -fsS --max-time 2 "$OLLAMA_TEST_URL/api/tags" >/dev/null 2>&1; then
    warn "Port 11435 sudah digunakan. Pengujian Ollama bundle dilewati."
else
    echo "Menjalankan binary Ollama dari tools/..."

    OLLAMA_MODELS="$MODEL_DIR" \
    OLLAMA_HOST="$OLLAMA_TEST_HOST" \
    OLLAMA_NOHISTORY=1 \
    OLLAMA_MAX_LOADED_MODELS=1 \
    LD_LIBRARY_PATH="$OLLAMA_LIB${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    "$OLLAMA" serve > "$TMP/ollama.log" 2>&1 &

    BUNDLE_PID=$!

    READY=0

    for i in $(seq 1 60); do
        if ! kill -0 "$BUNDLE_PID" 2>/dev/null; then
            break
        fi

        if curl -fsS --max-time 2 "$OLLAMA_TEST_URL/api/tags" \
            -o "$TMP/tags.json" 2>/dev/null; then
            READY=1
            break
        fi

        sleep 1
    done

    if [[ "$READY" == "1" ]]; then
        ok "Ollama bundle berhasil menjalankan server"

        echo "Executable aktif:"
        readlink -f "/proc/$BUNDLE_PID/exe" || true

        echo "OLLAMA_MODELS:"
        tr '\0' '\n' < "/proc/$BUNDLE_PID/environ" |
            grep '^OLLAMA_MODELS=' || true

        echo
        echo "Daftar model:"

        python3 - "$TMP/tags.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    data = json.load(f)

models = data.get("models", [])

for model in models:
    print(model.get("name"))

if any(m.get("name", "").startswith("qwen3:1.7b") for m in models):
    print("[OK] Qwen3 1.7B terlihat dari server bundle")
else:
    print("[FAIL] Qwen3 1.7B tidak terlihat dari server bundle")
    sys.exit(1)
PY

        if [[ $? -eq 0 ]]; then
            ok "Model terlihat pada Ollama bundle"
        else
            fail "Model tidak terdeteksi pada server bundle"
        fi

        echo
        echo "Menguji metadata model melalui API..."

        if curl -fsS --max-time 30 \
            -X POST "$OLLAMA_TEST_URL/api/show" \
            -H "Content-Type: application/json" \
            -d '{"model":"qwen3:1.7b"}' \
            -o "$TMP/show.json"
        then
            ok "Ollama API /api/show berhasil"
        else
            fail "Ollama API /api/show gagal"
        fi

    else
        fail "Ollama bundle gagal melakukan startup"
        echo "Log startup:"
        tail -n 80 "$TMP/ollama.log"
    fi

    if [[ -n "$BUNDLE_PID" ]] && kill -0 "$BUNDLE_PID" 2>/dev/null; then
        kill "$BUNDLE_PID" 2>/dev/null || true
        wait "$BUNDLE_PID" 2>/dev/null || true
        BUNDLE_PID=""
        ok "Server pengujian sementara dihentikan"
    fi
fi

# ============================================================
section "7. PYTHON WHEELHOUSE OFFLINE"
# ============================================================

REQ="$ROOT/requirements.txt"
WHEELHOUSE="$TOOLS/python/wheelhouse"

if [[ ! -f "$REQ" ]]; then
    fail "requirements.txt tidak ditemukan"
elif [[ ! -d "$WHEELHOUSE" ]]; then
    fail "Wheelhouse tidak ditemukan"
else
    echo "Python:"
    python3 --version

    echo
    echo "Wheelhouse:"
    du -sh "$WHEELHOUSE"

    echo "Jumlah wheel:"
    find "$WHEELHOUSE" -maxdepth 1 -name '*.whl' | wc -l

    echo
    echo "Membuat virtual environment sementara..."

    if python3 -m venv "$TMP/venv"; then
        ok "Virtual environment sementara berhasil dibuat"

        echo
        echo "Menginstal requirements dalam kondisi offline..."

        if "$TMP/venv/bin/python" -m pip install \
            --disable-pip-version-check \
            --no-index \
            --find-links "$WHEELHOUSE" \
            -r "$REQ"
        then
            ok "Instalasi offline berhasil"

            echo
            echo "Memeriksa pip dependency consistency..."

            if "$TMP/venv/bin/python" -m pip check; then
                ok "pip check bersih"
            else
                fail "pip check menemukan konflik dependensi"
            fi

            echo
            echo "Memeriksa import modul..."

            "$TMP/venv/bin/python" - <<'PY'
import importlib
import sys

modules = [
    "fastapi",
    "uvicorn",
    "sqlalchemy",
    "psycopg2",
    "pydantic_settings",
    "email_validator",
    "jwt",
    "pwdlib",
    "multipart",
    "pytest",
    "httpx",
    "openpyxl",
]

failed = []

for name in modules:
    try:
        importlib.import_module(name)
        print("[OK]", name)
    except Exception as exc:
        print("[FAIL]", name, "-", repr(exc))
        failed.append(name)

if failed:
    sys.exit(1)

print("SELURUH IMPORT BERHASIL")
PY

            if [[ $? -eq 0 ]]; then
                ok "Seluruh modul utama berhasil di-import"
            else
                fail "Ada modul Python yang gagal di-import"
            fi
        else
            fail "Instalasi offline gagal. Periksa wheelhouse dan requirements.txt"
        fi
    else
        fail "Gagal membuat virtual environment sementara"
    fi
fi

# ============================================================
section "8. POSTGRESQL DAN DATABASE"
# ============================================================

for CMD in psql pg_isready pg_dump pg_restore; do
    if command -v "$CMD" >/dev/null 2>&1; then
        echo "$CMD: $(command -v "$CMD")"
        "$CMD" --version 2>/dev/null | head -n 1
    else
        warn "$CMD tidak tersedia pada PATH"
    fi
done

echo

if command -v pg_isready >/dev/null 2>&1; then
    if pg_isready; then
        ok "PostgreSQL menerima koneksi"
    else
        warn "PostgreSQL belum menerima koneksi"
    fi
fi

if command -v psql >/dev/null 2>&1; then
    echo
    echo "Menguji koneksi PostgreSQL dengan konfigurasi shell saat ini..."

    if psql -X -w -Atqc \
        "SELECT current_database(), current_user, version();" \
        > "$TMP/postgres.txt" 2>&1
    then
        ok "Koneksi SQL berhasil"
        cat "$TMP/postgres.txt"
    else
        warn "Koneksi SQL default gagal. Belum membuktikan database aplikasi bermasalah."
        sed -E 's/(password|Password)=[^ ]+/\1=REDACTED/g' \
            "$TMP/postgres.txt" | head -n 10
    fi
fi

echo
echo "Nama variabel konfigurasi database dalam .env (tanpa nilai rahasia):"

if [[ -f "$ROOT/.env" ]]; then
    grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$ROOT/.env" |
        cut -d= -f1 |
        grep -Ei 'DATABASE|POSTGRES|(^DB_)' || true
else
    warn ".env tidak ditemukan"
fi

if command -v docker >/dev/null 2>&1; then
    echo
    docker --version
    docker compose version 2>/dev/null || true
else
    echo "Docker tidak ditemukan."
fi

# ============================================================
section "9. KONFIGURASI DAN PATH SOURCE CODE"
# ============================================================

for FILE in requirements.txt .env.example; do
    if [[ -f "$ROOT/$FILE" ]]; then
        ok "$FILE ditemukan"
    else
        fail "$FILE tidak ditemukan"
    fi
done

if [[ -f "$ROOT/.env" ]]; then
    ok ".env tersedia pada laptop sumber"
else
    warn ".env belum tersedia"
fi

echo
echo "Pencarian hardcoded global executable path dalam source code:"

if [[ -d "$ROOT/app" ]]; then
    if grep -RInE \
        '/usr/local/(bin|lib)/(plink2|gemma|ollama)|/usr/bin/(plink2|gemma|ollama)|/home/[^ ]+/.ollama/models' \
        "$ROOT/app" \
        --include='*.py' --include='*.sh' \
        2>/dev/null > "$TMP/hardcoded.txt"
    then
        warn "Ditemukan referensi path global:"
        cat "$TMP/hardcoded.txt"
    else
        ok "Tidak ditemukan hardcoded path global yang cocok"
    fi
else
    warn "Folder app tidak ditemukan"
fi

echo
echo "Panggilan tool dalam source code:"

if [[ -d "$ROOT/app" ]]; then
    grep -RInE \
        'subprocess|Popen|shutil\.which|OLLAMA|plink2|gemma' \
        "$ROOT/app" \
        --include='*.py' \
        2>/dev/null | head -n 100 || true
fi

# ============================================================
section "10. CHECKSUM SELURUH TOOLS"
# ============================================================

echo "Membuat ulang SHA256SUMS."

(
    cd "$ROOT"

    find tools -type f \
        ! -name SHA256SUMS \
        ! -name '*.log' \
        -print0 |
        sort -z |
        xargs -0 -r sha256sum
) > "$TOOLS/SHA256SUMS"

if (
    cd "$ROOT"
    sha256sum -c tools/SHA256SUMS
); then
    ok "Seluruh file bundle yang tercakup berhasil diverifikasi"
else
    fail "Pemeriksaan checksum gagal"
fi

echo
echo "Jumlah file dalam SHA256SUMS:"
wc -l < "$TOOLS/SHA256SUMS"

# ============================================================
section "11. LAPORAN UKURAN AKHIR"
# ============================================================

echo "Tools:"
du -sh "$TOOLS"

echo
echo "Komponen terbesar:"
du -h -d 3 "$TOOLS" 2>/dev/null | sort -h | tail -n 30

echo
echo "Wheelhouse:"
du -sh "$WHEELHOUSE" 2>/dev/null || true

echo
echo "Model:"
du -sh "$MODEL_DIR" 2>/dev/null || true

# ============================================================
section "12. HASIL AKHIR"
# ============================================================

echo "Error   : $ERRORS"
echo "Warning : $WARNINGS"

if (( ERRORS == 0 && WARNINGS == 0 )); then
    echo
    echo "STATUS: SELURUH AUDIT TEKNIS YANG DILAKUKAN LULUS"
elif (( ERRORS == 0 )); then
    echo
    echo "STATUS: LULUS DENGAN WARNING YANG HARUS DITINJAU"
else
    echo
    echo "STATUS: BELUM LULUS AUDIT"
fi

echo
echo "Laporan tersimpan di:"
echo "$REPORT"

