#!/usr/bin/env bash
# Download Python dependency wheels so a matching laptop can install offline.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REQ="$ROOT/requirements.txt"
DEST="$ROOT/tools/python/wheelhouse"
[[ -f "$REQ" ]] || { echo "[ERROR] requirements.txt tidak ditemukan: $REQ" >&2; exit 1; }
mkdir -p "$DEST"
PYTHON_BIN="${PYTHON_BIN:-python3}"
"$PYTHON_BIN" -m pip --version >/dev/null 2>&1 || {
    echo "[ERROR] pip untuk $PYTHON_BIN tidak tersedia. Pasang python3-pip terlebih dahulu." >&2
    exit 1
}
echo "[INFO] Mengunduh wheel berdasarkan $REQ ke $DEST"
"$PYTHON_BIN" -m pip download --only-binary=:all: --dest "$DEST" -r "$REQ"
echo "[INFO] Wheelhouse selesai: $(du -sh "$DEST" | awk '{print $1}')"
echo "[WARN] Wheelhouse bersifat spesifik terhadap versi Python, OS, dan arsitektur. Buat ulang bila platform laptop tujuan berbeda."
