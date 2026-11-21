#!/usr/bin/env bash
# Prepare a fresh Python virtual environment inside backend/.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
OFFLINE=0
if [[ "${1:-}" == "--offline" ]]; then OFFLINE=1; fi

[[ -f requirements.txt ]] || { echo "[ERROR] requirements.txt tidak ditemukan." >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || {
    echo "[ERROR] Python 3 tidak tersedia. Ubuntu/WSL: sudo apt update && sudo apt install -y python3 python3-venv python3-pip" >&2
    exit 1
}

PYVER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "[INFO] Python terdeteksi: $PYVER"
if [[ "$PYVER" != "3.12" ]]; then
    echo "[WARN] Bundle sumber diaudit dengan Python 3.12. Python $PYVER mungkin kompatibel, tetapi belum tervalidasi." >&2
fi

if ! python3 -m venv --help >/dev/null 2>&1; then
    echo "[ERROR] Modul venv tidak tersedia. Ubuntu: sudo apt install -y python3-venv" >&2
    exit 1
fi

if [[ -d .venv ]]; then
    echo "[INFO] .venv sudah ada. Memperbarui dependensi di dalamnya."
else
    python3 -m venv .venv
fi

WHEELHOUSE="$ROOT/tools/python/wheelhouse"
if [[ "$OFFLINE" -eq 1 ]]; then
    echo "[INFO] Mode offline: tidak menghubungi package index untuk upgrade pip."
    [[ -d "$WHEELHOUSE" ]] || { echo "[ERROR] Wheelhouse tidak ditemukan: $WHEELHOUSE" >&2; exit 1; }
    compgen -G "$WHEELHOUSE/*" >/dev/null || { echo "[ERROR] Wheelhouse kosong." >&2; exit 1; }
    .venv/bin/python -m pip install --no-index --find-links "$WHEELHOUSE" -r requirements.txt
else
    if compgen -G "$WHEELHOUSE/*" >/dev/null; then
        .venv/bin/python -m pip install --find-links "$WHEELHOUSE" -r requirements.txt
    else
        .venv/bin/python -m pip install -r requirements.txt
    fi
fi

echo "[OK] Virtual environment siap: $ROOT/.venv"
echo "[INFO] Aktifkan dengan: source .venv/bin/activate"
