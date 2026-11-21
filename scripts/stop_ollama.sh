#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT/.run/ollama.pid"
if [[ ! -f "$PID_FILE" ]]; then
    echo "[INFO] Tidak ada PID Ollama yang dikelola oleh proyek."
    exit 0
fi
PID="$(cat "$PID_FILE")"
if [[ "$PID" =~ ^[0-9]+$ ]] && kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    for _ in $(seq 1 10); do
        kill -0 "$PID" 2>/dev/null || break
        sleep 1
    done
    if kill -0 "$PID" 2>/dev/null; then
        echo "[WARN] Proses belum berhenti; mengirim SIGTERM tambahan tidak dilakukan otomatis. PID=$PID"
        exit 1
    fi
    echo "[OK] Ollama proyek dihentikan."
else
    echo "[INFO] PID lama tidak aktif."
fi
rm -f "$PID_FILE"
