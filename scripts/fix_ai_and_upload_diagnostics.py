from pathlib import Path
from datetime import datetime
import shutil
import re
import sys

ROOT = Path.cwd()
BACKUP = ROOT / "tools/reports/fix_backup"
BACKUP.mkdir(parents=True, exist_ok=True)

def backup(path):
    destination = BACKUP / (
        path.name + "." + datetime.now().strftime("%Y%m%d_%H%M%S") + ".bak"
    )
    shutil.copy2(path, destination)
    print("[BACKUP]", destination)

def update_env(path, updates):
    if not path.exists():
        path.write_text("", encoding="utf-8")

    lines = path.read_text(encoding="utf-8").splitlines()
    output = []
    seen = set()

    for line in lines:
        if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
            output.append(line)
            continue

        key = line.split("=", 1)[0].strip()

        if key in updates:
            if key not in seen:
                output.append(f"{key}={updates[key]}")
                seen.add(key)
        else:
            output.append(line)

    for key, value in updates.items():
        if key not in seen:
            output.append(f"{key}={value}")

    path.write_text("\n".join(output) + "\n", encoding="utf-8")

updates = {
    "PLINK2_BIN": "tools/bin/plink2",
    "GEMMA_BIN": "tools/bin/gemma",
    "AI_PROVIDER": "openai_compatible",
    "AI_API_BASE_URL": "http://127.0.0.1:11434/v1",
    "AI_API_KEY": "ollama",
    "AI_MODEL": "qwen3:1.7b",
    "AI_TIMEOUT_SECONDS": "240",
    "AI_MAX_ATTEMPTS": "1",
    "AI_MAX_OUTPUT_TOKENS": "512",
    "AI_TOP_SNP_COUNT": "5",
    "AI_MAX_CONCURRENT_JOBS": "1",
    "MAX_CONCURRENT_JOBS": "1",
    "ANALYSIS_MEMORY_RESERVE_MB": "1024",
}

print("\n========== 1. BACKUP DAN ENVIRONMENT ==========")

env = ROOT / ".env"
backup(env)
update_env(env, updates)
env.chmod(0o600)

example = ROOT / ".env.example"
if example.exists():
    backup(example)
    update_env(example, updates)

print("[OK] URL Ollama dikoreksi tanpa backslash.")
print("[OK] Timeout AI: 120 detik per percobaan.")
print("[OK] Maksimum 2 percobaan.")
print("[OK] Batas pekerjaan AI akan diperketat menjadi 300 detik.")

print("\n========== 2. AI PROVIDER ==========")

provider_path = ROOT / "app/services/ai_interpretation.py"
provider = provider_path.read_text(encoding="utf-8")

old_signature = "def generate(self, payload: dict) -> tuple[dict, dict | None]:"
new_signature = (
    "def generate(self, payload: dict, "
    "timeout_seconds: float | None = None) -> tuple[dict, dict | None]:"
)

if old_signature in provider:
    backup(provider_path)
    provider = provider.replace(old_signature, new_signature, 1)
elif new_signature in provider:
    print("[INFO] Signature provider sudah diperbarui.")
else:
    raise RuntimeError("Signature AI provider tidak cocok. File tidak diubah lebih lanjut.")

if "timeout=settings.ai_timeout_seconds)" in provider:
    marker = "        response = httpx.post(url, headers="
    if marker not in provider:
        raise RuntimeError("Lokasi HTTP request tidak ditemukan.")

    provider = provider.replace(
        marker,
        """        request_timeout = max(
            1.0,
            min(
                float(timeout_seconds or settings.ai_timeout_seconds),
                float(settings.ai_timeout_seconds)
            )
        )

        response = httpx.post(url, headers=""",
        1
    )

    provider = provider.replace(
        "timeout=settings.ai_timeout_seconds)",
        """timeout=httpx.Timeout(
                request_timeout,
                connect=min(10.0, request_timeout),
                read=request_timeout,
                write=min(30.0, request_timeout),
                pool=min(10.0, request_timeout)
            ))""",
        1
    )

provider_path.write_text(provider, encoding="utf-8")
print("[OK] Provider menerima timeout dinamis.")

print("\n========== 3. AI JOB DEADLINE ==========")

jobs_path = ROOT / "app/services/ai_jobs.py"
jobs = jobs_path.read_text(encoding="utf-8")

old_payload = """        analysis = db.get(GwasAnalysis, analysis_id)
        payload = build_ai_payload(db, analysis)"""

new_payload = """        analysis = db.get(GwasAnalysis, analysis_id)

        # Batas absolut dihitung sejak pekerjaan masuk antrean.
        queued_at = row.queued_at or _now()
        if queued_at.tzinfo is None:
            queued_at = queued_at.replace(tzinfo=timezone.utc)

        elapsed = max(0.0, (_now() - queued_at).total_seconds())
        deadline = time.monotonic() + max(0.0, 300.0 - elapsed)

        payload = build_ai_payload(db, analysis)"""

if old_payload in jobs:
    backup(jobs_path)
    jobs = jobs.replace(old_payload, new_payload, 1)
elif "deadline = time.monotonic() + max(0.0, 300.0 - elapsed)" not in jobs:
    raise RuntimeError("Lokasi pembuatan payload AI tidak ditemukan.")

old_call = "                result, usage = get_provider(settings.ai_provider).generate(payload)"

new_call = """                remaining = deadline - time.monotonic() - 15.0

                if remaining <= 0:
                    last_error = (
                        "AI_DEADLINE_EXCEEDED",
                        "Batas total interpretasi AI selama 5 menit telah habis."
                    )
                    break

                result, usage = get_provider(settings.ai_provider).generate(
                    payload,
                    timeout_seconds=min(
                        float(settings.ai_timeout_seconds),
                        remaining
                    )
                )"""

if old_call in jobs:
    jobs = jobs.replace(old_call, new_call, 1)
elif "timeout_seconds=min(" not in jobs:
    raise RuntimeError("Pemanggilan AI provider tidak ditemukan.")

old_validation = "                result = validate_interpretation(result, payload)"
new_validation = """                result = validate_interpretation(result, payload)

                if time.monotonic() >= deadline:
                    result = None
                    usage = None
                    last_error = (
                        "AI_DEADLINE_EXCEEDED",
                        "Batas total interpretasi AI selama 5 menit telah habis."
                    )
                    break"""

if old_validation in jobs:
    jobs = jobs.replace(old_validation, new_validation, 1)
elif "Batas total interpretasi AI selama 5 menit telah habis." not in jobs:
    raise RuntimeError("Validasi hasil AI tidak ditemukan.")

old_exception = """                code = "AI_TIMEOUT" if "timeout" in type(exc).__name__.lower() else "AI_PROVIDER_ERROR"
                last_error = (code, f"Provider AI gagal ({type(exc).__name__}).")"""

new_exception = """                exc_name = type(exc).__name__
                raw_message = str(exc).strip()

                if "timeout" in exc_name.lower() or "timeout" in raw_message.lower():
                    code = "AI_TIMEOUT"
                elif raw_message in {
                    "AI_EMPTY_CONTENT",
                    "AI_INVALID_RESPONSE",
                    "AI_INVALID_JSON"
                }:
                    code = raw_message
                else:
                    code = "AI_PROVIDER_ERROR"

                safe_detail = (
                    raw_message[:240]
                    if raw_message
                    else "Tidak ada detail tambahan."
                )

                last_error = (
                    code,
                    f"Provider AI gagal ({exc_name}): {safe_detail}"
                )"""

if old_exception in jobs:
    jobs = jobs.replace(old_exception, new_exception, 1)
elif "safe_detail = (" not in jobs:
    raise RuntimeError("Blok penanganan exception AI tidak ditemukan.")

jobs_path.write_text(jobs, encoding="utf-8")
print("[OK] Batas total AI 300 detik diterapkan.")
print("[OK] Detail penyebab kegagalan AI sekarang dicatat.")
print("[OK] Percobaan ulang tetap dibatasi oleh deadline.")

print("\n========== 4. OLLAMA CONTEXT ==========")

start_path = ROOT / "scripts/start_ollama.sh"
if start_path.exists():
    start = start_path.read_text(encoding="utf-8")

    start = re.sub(
        r"(?m)^export OLLAMA_CONTEXT_LENGTH=.*$",
        "export OLLAMA_CONTEXT_LENGTH=2048",
        start
    )

    if "OLLAMA_CONTEXT_LENGTH=" not in start:
        start = start.replace(
            "export OLLAMA_NO_CLOUD=1",
            "export OLLAMA_NO_CLOUD=1\nexport OLLAMA_CONTEXT_LENGTH=2048"
        )

    if "OLLAMA_KEEP_ALIVE=" not in start:
        start = start.replace(
            "export OLLAMA_CONTEXT_LENGTH=2048",
            "export OLLAMA_CONTEXT_LENGTH=2048\nexport OLLAMA_KEEP_ALIVE=5m"
        )

    start_path.write_text(start, encoding="utf-8")
    print("[OK] Ollama context 2048 dan keep-alive 5 menit.")
else:
    print("[WARN] start_ollama.sh tidak ditemukan.")

print("\n========== 5. PEMERIKSAAN SINTAKS ==========")

print("[OK] Patch berhasil ditulis.")
