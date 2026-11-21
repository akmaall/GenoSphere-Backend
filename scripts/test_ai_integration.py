import os
import sys
import json
import time
from pathlib import Path

# Pastikan root backend masuk Python import path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Pengujian menggunakan satu percobaan agar tidak mengulang
# permintaan AI berkali-kali apabila terjadi kegagalan.
os.environ["AI_MAX_ATTEMPTS"] = "1"
os.environ["AI_MAX_CONCURRENT_JOBS"] = "1"

import httpx
from sqlalchemy import select

from app import database
from app.models import GwasAnalysis, GwasInterpretation
from app.config import get_settings
from app.services.ai_interpretation import (
    build_ai_payload,
    payload_hash,
)
from app.services.ai_jobs import enqueue_interpretation


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "tools" / "reports"


def section(title):
    print("\n" + "=" * 65)
    print(title)
    print("=" * 65)


def main():
    settings = get_settings()

    section("1. KONFIGURASI AI")

    print("Provider:", settings.ai_provider)
    print("Endpoint:", settings.ai_api_base_url)
    print("Model:", settings.ai_model)
    print("API key tersedia:", bool(settings.ai_api_key))
    print("Timeout:", settings.ai_timeout_seconds)

    if settings.ai_provider != "openai_compatible":
        print("[FAIL] Provider tidak sesuai.")
        return 1

    if settings.ai_model != "qwen3:1.7b":
        print("[FAIL] Model bukan qwen3:1.7b.")
        return 1

    if not settings.ai_api_key:
        print("[FAIL] API key lokal Ollama belum tersedia.")
        return 1

    section("2. KONEKSI OLLAMA")

    try:
        response = httpx.get(
            "http://127.0.0.1:11434/api/tags",
            timeout=10
        )
        response.raise_for_status()

        models = response.json().get("models", [])
        model_names = [m.get("name") for m in models]

        print("Model tersedia:", model_names)

        if not any(name and name.startswith("qwen3:1.7b") for name in model_names):
            print("[FAIL] Qwen3 1.7B tidak ditemukan.")
            return 1

        print("[OK] Ollama siap.")

    except Exception as exc:
        print("[FAIL] Ollama tidak dapat dihubungi:", repr(exc))
        return 1

    section("3. ANALISIS GWAS YANG SUDAH SELESAI")

    db = database.SessionLocal()

    try:
        analyses = db.scalars(
            select(GwasAnalysis)
            .where(GwasAnalysis.status == "done")
            .order_by(GwasAnalysis.analysis_id.desc())
            .limit(30)
        ).all()

        if not analyses:
            print("[FAIL] Belum ada analisis GWAS dengan status done.")
            print("Selesaikan satu analisis GWAS terlebih dahulu.")
            return 1

        eligible = []

        for analysis in analyses:
            ai_row = db.scalar(
                select(GwasInterpretation).where(
                    GwasInterpretation.analysis_id == analysis.analysis_id
                )
            )

            ai_status = ai_row.status if ai_row else "belum ada"

            can_test = (
                ai_row is None
                or ai_status in {"failed", "unavailable", "stale"}
            )

            print(
                f"Analysis ID: {analysis.analysis_id} | "
                f"GWAS: {analysis.status} | "
                f"Model: {analysis.model_used} | "
                f"AI: {ai_status}"
            )

            if can_test:
                eligible.append(analysis.analysis_id)

        if not eligible:
            print("\n[FAIL] Tidak ada analisis yang dapat diuji ulang.")
            print("Analisis yang sudah queued, running, atau done tidak diproses ulang.")
            return 1

        print("\nAnalysis ID yang dapat diuji:", eligible)

        try:
            selected = int(input("\nMasukkan Analysis ID untuk pengujian: "))
        except ValueError:
            print("[FAIL] ID harus berupa angka.")
            return 1

        if selected not in eligible:
            print("[FAIL] Analysis ID tidak termasuk pilihan yang tersedia.")
            return 1

        analysis = db.get(GwasAnalysis, selected)

        section("4. PEMBUATAN PAYLOAD STATISTIK")

        payload = build_ai_payload(db, analysis)

        print("Analysis ID:", selected)
        print("Payload hash:", payload_hash(payload))

        summary = payload.get("analysis", {})

        print(
            json.dumps(
                {
                    "model": summary.get("model"),
                    "n_samples_analyzed": summary.get("n_samples_analyzed"),
                    "n_snps_tested": summary.get("n_snps_tested"),
                    "n_significant_snps": summary.get("n_significant_snps"),
                    "top_snps_count": len(payload.get("top_snps", [])),
                    "qc_available": bool(payload.get("qc")),
                    "pca_available": bool(payload.get("pca")),
                },
                indent=2,
                ensure_ascii=False,
            )
        )

    except Exception as exc:
        print("[FAIL] Gagal menyiapkan payload:", repr(exc))
        return 1

    finally:
        db.close()

    section("5. MENJALANKAN AI JOBS")

    result = enqueue_interpretation(selected)

    print("Hasil enqueue:", result)

    if result.get("status") not in {"queued", "running", "done"}:
        print("[FAIL] AI job tidak berhasil dijalankan.")
        print(result)
        return 1

    if result.get("status") == "done":
        print("[INFO] Hasil interpretasi sudah tersedia sebelumnya.")

    section("6. MEMANTAU STATUS AI JOB")

    deadline = time.monotonic() + max(
        60,
        settings.ai_timeout_seconds + 120
    )

    previous_status = None

    while time.monotonic() < deadline:
        db = database.SessionLocal()

        try:
            row = db.scalar(
                select(GwasInterpretation).where(
                    GwasInterpretation.analysis_id == selected
                )
            )

            if row is None:
                print("[FAIL] Record interpretasi tidak ditemukan.")
                return 1

            status = row.status

            if status != previous_status:
                print(
                    "Status:", status,
                    "| Attempts:", row.attempts,
                    "| Error:", row.error_code
                )
                previous_status = status

            if status in {"done", "failed", "unavailable", "stale"}:
                section("7. HASIL AKHIR")

                print("Analysis ID:", selected)
                print("Status:", row.status)
                print("Provider:", row.provider)
                print("Model:", row.model)
                print("Attempts:", row.attempts)
                print("Error code:", row.error_code)
                print("Error message:", row.error_message)

                if status == "done":
                    REPORT_DIR.mkdir(parents=True, exist_ok=True)

                    output = {
                        "analysis_id": selected,
                        "status": row.status,
                        "provider": row.provider,
                        "model": row.model,
                        "prompt_version": row.prompt_version,
                        "schema_version": row.schema_version,
                        "input_hash": row.input_hash,
                        "result": row.result_json,
                        "token_usage": row.token_usage,
                        "finished_at": (
                            row.finished_at.isoformat()
                            if row.finished_at else None
                        ),
                    }

                    output_path = REPORT_DIR / (
                        f"ai_interpretation_{selected}.json"
                    )

                    output_path.write_text(
                        json.dumps(output, indent=2, ensure_ascii=False),
                        encoding="utf-8"
                    )

                    print("\n[OK] Interpretasi tersimpan di PostgreSQL.")
                    print("[OK] File hasil:", output_path)
                    print("\nISI INTERPRETASI:")

                    print(
                        json.dumps(
                            row.result_json,
                            indent=2,
                            ensure_ascii=False
                        )
                    )

                    return 0

                print("[FAIL] AI job belum berhasil.")
                return 1

        finally:
            db.close()

        time.sleep(5)

    print("[WARN] Batas pemantauan tercapai.")
    print("Periksa kembali status melalui database.")
    return 2


if __name__ == "__main__":
    sys.exit(main())
