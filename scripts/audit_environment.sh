#!/usr/bin/env bash

set +e

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

section() {
    echo
    echo "============================================================"
    echo "$1"
    echo "============================================================"
}

show_binary() {
    local name="$1"
    local -a paths

    echo
    echo "--- $name ---"

    mapfile -t paths < <(type -aP "$name" 2>/dev/null | awk '!seen[$0]++')

    if [ "${#paths[@]}" -eq 0 ]; then
        echo "STATUS: TIDAK DITEMUKAN DI PATH"
        return
    fi

    for path in "${paths[@]}"; do
        echo "Lokasi       : $path"
        echo "Lokasi nyata : $(readlink -f "$path")"
        file "$path" 2>/dev/null

        if command -v ldd >/dev/null 2>&1; then
            missing="$(ldd "$(readlink -f "$path")" 2>/dev/null | grep 'not found' || true)"
            if [ -n "$missing" ]; then
                echo "DEPENDENSI HILANG:"
                echo "$missing"
            else
                echo "Dependensi dinamis: tidak ditemukan yang hilang"
            fi
        fi
    done
}

section "1. INFORMASI SISTEM"
echo "Tanggal       : $(date)"
echo "User          : $(id -un)"
echo "Home          : $HOME"
echo "WSL           : $(uname -a)"
echo "Arsitektur    : $(uname -m)"
echo "Distribusi    :"
cat /etc/os-release 2>/dev/null | grep -E '^(PRETTY_NAME|VERSION_ID)='
echo "Python        : $(python3 --version 2>&1)"
echo "Disk D:       :"
df -h /mnt/d 2>/dev/null | tail -n 1
echo "Mount options : $(findmnt -no OPTIONS /mnt/d 2>/dev/null)"

section "2. LOKASI DAN VERSI BINARY"
show_binary plink2
show_binary gemma
show_binary ollama

section "3. VERSI PLINK2"
if command -v plink2 >/dev/null 2>&1; then
    plink2 --version 2>&1 | head -n 5
else
    echo "PLINK2 tidak ditemukan melalui PATH."
fi

section "4. VERSI DAN INFORMASI GEMMA"
if command -v gemma >/dev/null 2>&1; then
    timeout 10 gemma -h 2>&1 | head -n 10
else
    echo "GEMMA tidak ditemukan melalui PATH."
fi

section "5. STATUS OLLAMA"
if command -v ollama >/dev/null 2>&1; then
    ollama --version 2>&1
    echo
    echo "Daftar model:"
    ollama list 2>&1
else
    echo "Ollama tidak ditemukan melalui PATH."
fi

echo
echo "Status service:"
systemctl show ollama \
    -p ActiveState \
    -p SubState \
    -p User \
    -p ExecStart 2>&1

echo
echo "Konfigurasi OLLAMA_MODELS pada service:"
systemctl show ollama -p Environment --value 2>/dev/null \
    | tr ' ' '\n' \
    | grep '^OLLAMA_MODELS=' || echo "Tidak ditemukan."

echo
echo "Proses download model:"
pgrep -af '[o]llama pull' || echo "Tidak ditemukan proses ollama pull."

echo
echo "Lokasi runtime Ollama:"
for path in \
    /usr/local/lib/ollama \
    /usr/lib/ollama \
    /lib/ollama \
    /usr/local/share/ollama \
    /usr/share/ollama
do
    if [ -e "$path" ]; then
        du -sh "$path" 2>/dev/null
        ls -ld "$path"
    fi
done

echo
echo "Lokasi model yang umum:"
for path in \
    "$HOME/.ollama/models" \
    /home/ollama/.ollama/models \
    /usr/share/ollama/.ollama/models \
    "$ROOT/tools/ollama/models"
do
    if [ -e "$path" ]; then
        du -sh "$path" 2>/dev/null
        echo "$path"
    fi
done

section "6. PENCARIAN BINARY TAMBAHAN"
find /usr/local/bin /usr/bin /opt \
    -type f \
    \( -iname '*plink*' -o -iname '*gemma*' -o -name 'ollama' \) \
    -print 2>/dev/null | head -n 80

section "7. POSTGRESQL"
echo "pg_isready:"
if command -v pg_isready >/dev/null 2>&1; then
    pg_isready -h localhost -p 5432 2>&1
else
    echo "pg_isready tidak ditemukan."
fi

echo
echo "Service PostgreSQL:"
systemctl is-active postgresql 2>&1

echo
echo "Versi psql:"
psql --version 2>&1

section "8. PYTHON VIRTUAL ENVIRONMENT"
if [ -x "$ROOT/.venv/bin/python" ]; then
    echo "Venv ditemukan: $ROOT/.venv"
    "$ROOT/.venv/bin/python" --version

    "$ROOT/.venv/bin/python" - <<'PY'
import importlib.util

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

for module in modules:
    status = "TERPASANG" if importlib.util.find_spec(module) else "BELUM TERPASANG"
    print(f"{module:22} {status}")
PY
else
    echo "Venv belum ditemukan: $ROOT/.venv"
fi

section "9. FILE DAN FOLDER PROYEK"
for path in \
    "$ROOT/requirements.txt" \
    "$ROOT/requirement.txt" \
    "$ROOT/.env" \
    "$ROOT/.env.example" \
    "$ROOT/tools" \
    "$ROOT/tools/bin" \
    "$ROOT/tools/lib" \
    "$ROOT/tools/downloads" \
    "$ROOT/tools/ollama/models"
do
    if [ -e "$path" ]; then
        ls -ld "$path"
        [ -d "$path" ] && du -sh "$path" 2>/dev/null
    else
        echo "TIDAK ADA: $path"
    fi
done

echo
echo "Isi tools (maksimal 3 tingkat):"
if [ -d "$ROOT/tools" ]; then
    find "$ROOT/tools" -maxdepth 3 -printf '%y %p\n' 2>/dev/null | head -n 100
fi

section "10. KONFIGURASI TANPA MEMBUKA SECRET"
for file in "$ROOT/.env" "$ROOT/.env.example"; do
    echo
    echo "File: $file"

    if [ -f "$file" ]; then
        echo "Status: ADA"
        grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$file" \
            | sed 's/=.*$/=<nilai disembunyikan>/' \
            | sort -u
    else
        echo "Status: TIDAK ADA"
    fi
done

section "11. HASIL AUDIT"
echo "Audit selesai."
echo "Folder proyek: $ROOT"
echo "Tidak ada binary, model, konfigurasi, atau database yang dipindahkan."
