import time

from app.config import get_settings
from app.database import SessionLocal
from app.models import GwasAnalysis
from app.services.ai_interpretation import (
    build_ai_payload,
    get_provider,
    validate_interpretation,
)

def main():
    settings = get_settings()
    db = SessionLocal()

    try:
        analysis = db.get(GwasAnalysis, 44)

        if analysis is None or analysis.status != "done":
            raise RuntimeError(
                "Analysis 44 tidak ditemukan atau belum selesai."
            )

        print("========== MEMBANGUN PAYLOAD ==========")

        payload_started = time.perf_counter()
        payload = build_ai_payload(db, analysis)
        payload_time = time.perf_counter() - payload_started

        print(f"Payload selesai: {payload_time:.2f} detik")
        print("Analysis ID:", analysis.analysis_id)
        print("GWAS model:", analysis.model_used)
        print("AI model:", settings.ai_model)
        print("Provider:", settings.ai_provider)
        print("Top SNP:", len(payload["top_snps"]))
        print("Timeout:", settings.ai_timeout_seconds)
        print("Max output tokens:", settings.ai_max_output_tokens)

        print("\n========== INFERENSI AI ==========")
        print("Mengirim payload ke Ollama...")

        started = time.perf_counter()

        raw_result, usage = get_provider(
            settings.ai_provider
        ).generate(payload)

        inference_time = time.perf_counter() - started

        print("\n========== HASIL INFERENSI ==========")
        print("Status: BERHASIL")
        print(f"Waktu inferensi: {inference_time:.2f} detik")
        print("Response fields:", list(raw_result.keys()))
        print("Token usage:", usage)

        print("\n========== VALIDASI HASIL ==========")

        validation_started = time.perf_counter()

        validated = validate_interpretation(
            raw_result,
            payload
        )

        validation_time = time.perf_counter() - validation_started

        print("Status: VALID")
        print(f"Waktu validasi: {validation_time:.3f} detik")
        print("Jumlah findings:", len(validated["findings"]))
        print(
            "Executive summary tersedia:",
            bool(validated["executive_summary"])
        )
        print(
            "Jumlah SNP signifikan:",
            validated["statistical_conclusion"]["n_significant_snps"]
        )

        print("\n========== TOTAL WAKTU ==========")
        print(
            f"Total: {payload_time + inference_time + validation_time:.2f} detik"
        )

    except Exception as exc:
        print("\n========== PENGUJIAN GAGAL ==========")
        print("Error type:", type(exc).__name__)
        print("Error:", str(exc))

    finally:
        db.close()

if __name__ == "__main__":
    main()
