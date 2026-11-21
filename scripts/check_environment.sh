#!/usr/bin/env bash
# Read-only validation of the GenoSphere portable tool bundle.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS="$ROOT/tools"
ERRORS=0
WARNINGS=0

ok()   { printf '[OK]   %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*"; WARNINGS=$((WARNINGS + 1)); }
fail() { printf '[FAIL] %s\n' "$*"; ERRORS=$((ERRORS + 1)); }
section() { printf '\n========== %s ==========\n' "$*"; }

section "HOST"
printf 'OS: '; . /etc/os-release 2>/dev/null && echo "${PRETTY_NAME:-unknown}" || echo unknown
printf 'Kernel: '; uname -sr
printf 'Arsitektur: '; uname -m
if [[ "$(uname -m)" == "x86_64" ]]; then ok "Arsitektur x86_64 cocok dengan binary sumber"; else fail "Arsitektur bukan x86_64; binary bundle sumber mungkin tidak kompatibel"; fi
if grep -qi microsoft /proc/version 2>/dev/null; then ok "WSL terdeteksi"; else warn "WSL tidak terdeteksi. Bundle ini dibuat dan diaudit pada WSL2 Ubuntu."; fi

section "PYTHON"
if command -v python3 >/dev/null 2>&1; then
    python3 --version
else
    fail "python3 tidak ditemukan"
fi
if [[ -x "$ROOT/.venv/bin/python" ]]; then
    ok ".venv ditemukan"
    "$ROOT/.venv/bin/python" - <<'PY'
import importlib.util
mods = ["fastapi", "uvicorn"]
missing = [m for m in mods if importlib.util.find_spec(m) is None]
if missing:
    print("[FAIL] Modul Python inti belum tersedia: " + ", ".join(missing))
    raise SystemExit(1)
print("[OK] Modul Python inti FastAPI dan Uvicorn tersedia")
PY
    [[ $? -eq 0 ]] || ERRORS=$((ERRORS + 1))
else
    warn ".venv belum dibuat. Jalankan: bash scripts/bootstrap.sh"
fi
[[ -f "$ROOT/requirements.txt" ]] && ok "requirements.txt tersedia" || fail "requirements.txt tidak ditemukan"
[[ -f "$ROOT/.env.example" ]] && ok ".env.example tersedia" || warn ".env.example tidak ditemukan"
[[ -f "$ROOT/.env" ]] && ok ".env tersedia pada mesin ini" || warn ".env belum dibuat; salin .env.example menjadi .env dan isi konfigurasi lokal"

section "PLINK2"
PLINK="$TOOLS/bin/plink2"
if [[ -x "$PLINK" ]]; then
    "$PLINK" --version 2>&1 | head -n 2
    ok "PLINK2 tersedia di dalam backend/tools"
else
    fail "tools/bin/plink2 tidak ditemukan. Jalankan scripts/package_tools.sh di laptop sumber."
fi

section "GEMMA"
GEMMA="$TOOLS/bin/gemma"
if [[ -x "$GEMMA" ]]; then
    "$GEMMA" -h 1 2>&1 | head -n 3
    ok "GEMMA tersedia di dalam backend/tools"
else
    fail "tools/bin/gemma tidak ditemukan. Jalankan scripts/package_tools.sh di laptop sumber."
fi

section "OLLAMA BUNDLE"
OLLAMA="$TOOLS/ollama/bin/ollama"
if [[ -x "$OLLAMA" ]]; then
    "$OLLAMA" -v 2>&1 || true
    ok "Binary Ollama ditemukan di bundle"
else
    fail "tools/ollama/bin/ollama tidak ditemukan"
fi
if [[ -d "$TOOLS/ollama/lib/ollama" ]] && find "$TOOLS/ollama/lib/ollama" -type f -print -quit | grep -q .; then
    ok "Runtime tools/ollama/lib/ollama tersedia"
else
    fail "Runtime lib/ollama kosong/tidak ada. Menyalin binary Ollama saja tidak cukup untuk bundle portabel."
fi
MODEL_DIR="$TOOLS/ollama/models"
if find "$MODEL_DIR/manifests" -type f -path '*/qwen3/1.7b' -print -quit 2>/dev/null | grep -q .; then
    ok "Manifest model qwen3:1.7b ditemukan"
else
    fail "Manifest qwen3:1.7b belum ditemukan pada tools/ollama/models"
fi
if command -v curl >/dev/null 2>&1 && curl -fsS --max-time 2 http://127.0.0.1:11434/api/tags >/tmp/genosphere_ollama_tags.json 2>/dev/null; then
    ok "Ollama API merespons di 127.0.0.1:11434"
    if grep -q 'qwen3:1.7b' /tmp/genosphere_ollama_tags.json; then ok "Model qwen3:1.7b terlihat oleh server aktif"; else warn "Server aktif tidak melaporkan qwen3:1.7b. Pastikan server berjalan dengan OLLAMA_MODELS dari bundle."; fi
else
    warn "Ollama API belum berjalan. Jalankan: bash scripts/start_ollama.sh"
fi
rm -f /tmp/genosphere_ollama_tags.json 2>/dev/null || true

section "DATABASE / KONFIGURASI"
if command -v pg_isready >/dev/null 2>&1; then
    if pg_isready -q; then ok "PostgreSQL merespons melalui pg_isready"; else warn "pg_isready tersedia, tetapi server PostgreSQL lokal belum merespons"; fi
else
    warn "pg_isready tidak ditemukan. Audit ini belum memastikan apakah proyek memakai PostgreSQL lokal, remote, atau container."
fi

section "CHECKSUM"
if [[ -f "$TOOLS/SHA256SUMS" ]]; then
    (cd "$ROOT" && sha256sum -c tools/SHA256SUMS >/dev/null 2>&1) && ok "Checksum bundle valid" || fail "Checksum gagal. File mungkin berubah/rusak saat pemindahan."
else
    warn "tools/SHA256SUMS belum ada. Jalankan scripts/package_tools.sh di laptop sumber."
fi

section "RINGKASAN"
printf 'Error: %s | Warning: %s\n' "$ERRORS" "$WARNINGS"
if (( ERRORS > 0 )); then
    echo "STATUS: BELUM SIAP DIPINDAHKAN / DIJALANKAN"
    exit 1
fi
echo "STATUS: LULUS PEMERIKSAAN DASAR (warning tetap perlu ditinjau)"
