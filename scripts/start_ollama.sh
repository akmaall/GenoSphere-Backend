#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="$ROOT/tools/ollama"
OLLAMA="$PREFIX/bin/ollama"
MODEL_DIR="$PREFIX/models"
RUN_DIR="$ROOT/.run"
PID_FILE="$RUN_DIR/ollama.pid"
LOG_FILE="$PREFIX/ollama.log"
HOST="127.0.0.1:11434"

[[ -x "$OLLAMA" ]] || {
    echo "[ERROR] Binary Ollama tidak ditemukan."
    exit 1
}

[[ -d "$PREFIX/lib/ollama" ]] || {
    echo "[ERROR] Runtime Ollama tidak ditemukan."
    exit 1
}

[[ -d "$MODEL_DIR/manifests" ]] || {
    echo "[ERROR] Model store tidak ditemukan."
    exit 1
}

find "$MODEL_DIR/manifests" -type f \
    -path '*/qwen3/1.7b' -print -quit | grep -q . || {
    echo "[ERROR] Model Qwen3 1.7B tidak ditemukan."
    exit 1
}

if curl -fsS --max-time 2 "http://$HOST/api/tags" >/dev/null 2>&1; then
    echo "[ERROR] Port 11434 sudah digunakan."
    echo "Periksa service Ollama yang sedang berjalan."
    exit 2
fi

mkdir -p "$RUN_DIR"

export OLLAMA_MODELS="$MODEL_DIR"
export OLLAMA_HOST="$HOST"
export OLLAMA_NOHISTORY=1
export OLLAMA_NO_CLOUD=1

# Memberikan waktu tambahan untuk laptop dengan RAM terbatas.
export OLLAMA_LOAD_TIMEOUT=15m
export OLLAMA_CONTEXT_LENGTH=2048
export OLLAMA_MAX_LOADED_MODELS=1
export OLLAMA_NUM_PARALLEL=1
export OLLAMA_KEEP_ALIVE=30m

export LD_LIBRARY_PATH="$PREFIX/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

nohup "$OLLAMA" serve > "$LOG_FILE" 2>&1 &
PID=$!

echo "$PID" > "$PID_FILE"

for _ in $(seq 1 60); do
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "[ERROR] Ollama gagal berjalan."
        tail -n 50 "$LOG_FILE"
        rm -f "$PID_FILE"
        exit 1
    fi

    if curl -fsS --max-time 3 \
        "http://$HOST/api/tags" >/dev/null 2>&1; then

        echo "[OK] Ollama bundle berjalan."
        echo "Executable: $OLLAMA"
        echo "Model: $MODEL_DIR"
        echo "PID: $PID"
        echo "Log: $LOG_FILE"
        exit 0
    fi

    sleep 1
done

echo "[ERROR] Ollama tidak siap."
tail -n 50 "$LOG_FILE"
exit 1
