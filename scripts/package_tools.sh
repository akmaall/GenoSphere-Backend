#!/usr/bin/env bash
# Bundle locally installed bioinformatics tools and the local Ollama model store.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS="$ROOT/tools"
mkdir -p "$TOOLS/bin" "$TOOLS/ollama/bin" "$TOOLS/ollama/lib" "$TOOLS/ollama/models" "$TOOLS/reports"

log()  { printf '[INFO] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
die()  { printf '[ERROR] %s\n' "$*" >&2; exit 1; }

copy_binary() {
    local name="$1" src real
    src="$(command -v "$name" 2>/dev/null || true)"
    [[ -n "$src" ]] || die "$name tidak ditemukan di PATH."
    real="$(readlink -f "$src")"
    [[ -f "$real" && -x "$real" ]] || die "Binary $name tidak valid: $real"
    install -m 0755 "$real" "$TOOLS/bin/$name"
    log "$name: $src -> $TOOLS/bin/$name"
}

log "Root proyek: $ROOT"
log "Sistem sumber: $(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-Linux tidak diketahui}")"
log "Arsitektur: $(uname -m)"
[[ "$(uname -m)" == "x86_64" ]] || warn "Bundle sumber ini dibuat untuk x86_64; binary mungkin tidak berjalan pada arsitektur lain."

copy_binary plink2
copy_binary gemma

# Ollama needs both the executable and its adjacent lib/ollama runtime directory.
OLLAMA_SRC="$(command -v ollama 2>/dev/null || true)"
[[ -n "$OLLAMA_SRC" ]] || die "ollama tidak ditemukan di PATH."
OLLAMA_REAL="$(readlink -f "$OLLAMA_SRC")"
[[ -f "$OLLAMA_REAL" && -x "$OLLAMA_REAL" ]] || die "Binary Ollama tidak valid: $OLLAMA_REAL"
install -m 0755 "$OLLAMA_REAL" "$TOOLS/ollama/bin/ollama"

OLLAMA_PREFIX="$(cd "$(dirname "$OLLAMA_REAL")/.." && pwd)"
LIB_CANDIDATES=(
    "$OLLAMA_PREFIX/lib/ollama"
    "/usr/local/lib/ollama"
    "/usr/lib/ollama"
    "/lib/ollama"
)
OLLAMA_LIB_SRC=""
for candidate in "${LIB_CANDIDATES[@]}"; do
    if [[ -d "$candidate" ]]; then
        OLLAMA_LIB_SRC="$candidate"
        break
    fi
done
if [[ -n "$OLLAMA_LIB_SRC" ]]; then
    mkdir -p "$TOOLS/ollama/lib/ollama"
    cp -a "$OLLAMA_LIB_SRC/." "$TOOLS/ollama/lib/ollama/"
    log "Runtime Ollama disalin dari $OLLAMA_LIB_SRC"
else
    warn "Direktori runtime lib/ollama tidak ditemukan. Binary Ollama saja belum dianggap bundle lengkap."
fi

# Resolve the model directory. Explicit OLLAMA_MODELS takes priority, then common Linux locations.
MODEL_SRC="${OLLAMA_MODELS:-}"
# A systemd-managed Ollama service may define a custom OLLAMA_MODELS path.
if [[ -z "$MODEL_SRC" ]] && command -v systemctl >/dev/null 2>&1; then
    SERVICE_ENV="$(systemctl show ollama.service --property=Environment --value 2>/dev/null || true)"
    MODEL_FROM_SERVICE="$(printf '%s\n' "$SERVICE_ENV" | tr ' ' '\n' | sed -n 's/^OLLAMA_MODELS=//p' | head -n 1 | tr -d '"')"
    [[ -n "$MODEL_FROM_SERVICE" ]] && MODEL_SRC="$MODEL_FROM_SERVICE"
fi

# Prefer a candidate that actually contains the requested qwen3:1.7b manifest.
if [[ -n "$MODEL_SRC" && ! -d "$MODEL_SRC" ]]; then
    warn "OLLAMA_MODELS mengarah ke direktori yang tidak ditemukan: $MODEL_SRC"
    MODEL_SRC=""
fi
if [[ -z "$MODEL_SRC" ]]; then
    for candidate in "$HOME/.ollama/models" "/usr/share/ollama/.ollama/models" "/root/.ollama/models"; do
        if [[ -d "$candidate/manifests" ]] && find "$candidate/manifests" -type f -path '*/qwen3/1.7b' -print -quit 2>/dev/null | grep -q .; then
            MODEL_SRC="$candidate"
            break
        fi
    done
fi
# Fall back to a non-empty model store so the report can expose a missing Qwen manifest.
if [[ -z "$MODEL_SRC" ]]; then
    for candidate in "$HOME/.ollama/models" "/usr/share/ollama/.ollama/models" "/root/.ollama/models"; do
        if [[ -d "$candidate/manifests" ]] && find "$candidate/manifests" -type f -print -quit 2>/dev/null | grep -q .; then
            MODEL_SRC="$candidate"
            break
        fi
    done
fi
if [[ -n "$MODEL_SRC" && -d "$MODEL_SRC" ]]; then
    MODEL_SRC_REAL="$(readlink -f "$MODEL_SRC")"
    MODEL_DEST_REAL="$(readlink -f "$TOOLS/ollama/models")"

    if [[ "$MODEL_SRC_REAL" == "$MODEL_DEST_REAL" ]]; then
        log "Model store sumber sudah merupakan tools/ollama/models."
        log "Penyalinan dilewati karena sumber dan tujuan sama."
    else
        case "$MODEL_DEST_REAL/" in
            "$MODEL_SRC_REAL/"*)
                die "Sumber merupakan direktori induk tujuan. Penyalinan dibatalkan."
                ;;
        esac

        case "$MODEL_SRC_REAL/" in
            "$MODEL_DEST_REAL/"*)
                die "Sumber berada di dalam direktori tujuan. Penyalinan dibatalkan."
                ;;
        esac

        cp -a "$MODEL_SRC/." "$TOOLS/ollama/models/"
        log "Model store disalin dari $MODEL_SRC"
    fi
else
    warn "Direktori model Ollama tidak ditemukan otomatis. Periksa OLLAMA_MODELS atau lokasi model service Ollama."
fi

# Confirm expected Qwen model exists in the copied store.
if find "$TOOLS/ollama/models/manifests" -type f -path '*/qwen3/1.7b' -print -quit 2>/dev/null | grep -q .; then
    log "Manifest qwen3:1.7b ditemukan di bundle."
else
    warn "Manifest qwen3:1.7b belum ditemukan di tools/ollama/models/manifests. Model belum siap dipindahkan."
fi

# Record source versions and platform information.
REPORT="$TOOLS/reports/source_manifest.txt"
{
    echo "GenoSphere local tools bundle"
    echo "Created: $(date -Is)"
    echo "OS: $(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-unknown}")"
    echo "Kernel: $(uname -sr)"
    echo "Architecture: $(uname -m)"
    echo
    echo "[PLINK2]"
    "$TOOLS/bin/plink2" --version 2>&1 | head -n 3 || true
    echo
    echo "[GEMMA]"
    "$TOOLS/bin/gemma" -h 1 2>&1 | head -n 4 || true
    echo
    echo "[OLLAMA]"
    "$TOOLS/ollama/bin/ollama" -v 2>&1 || true
    echo
    echo "[MODEL STORE]"
    du -sh "$TOOLS/ollama/models" 2>/dev/null || true
    find "$TOOLS/ollama/models/manifests" -type f 2>/dev/null | sed "s|$TOOLS/ollama/models/||" | head -n 30 || true
} > "$REPORT"

# Checksums are useful for detecting damaged/incomplete copies after transferring the project.
(
    cd "$ROOT"
    find tools/bin tools/ollama -type f ! -name SHA256SUMS -print0 \
      | sort -z \
      | xargs -0 -r sha256sum
) > "$TOOLS/SHA256SUMS"

log "Selesai. Manifest: $REPORT"
log "Checksum: $TOOLS/SHA256SUMS"
log "Ukuran folder tools: $(du -sh "$TOOLS" | awk '{print $1}')"
warn "Skrip ini tidak menyalin WSL/Ubuntu, driver GPU, PostgreSQL, atau .venv. Komponen tersebut perlu disiapkan/dideteksi terpisah."
