import json
import time
import httpx

from app.config import get_settings
from app.database import SessionLocal
from app.models import GwasAnalysis
from app.services.ai_interpretation import (
    build_ai_payload,
    validate_interpretation,
)

def main():
    settings = get_settings()
    db = SessionLocal()

    try:
        analysis = db.get(GwasAnalysis, 44)

        if analysis is None or analysis.status != "done":
            raise RuntimeError("Analysis 44 tidak tersedia.")

        payload = build_ai_payload(db, analysis)

        system_prompt = (
            "Anda adalah analis statistik GWAS. "
            "Interpretasikan hanya bukti statistik yang tersedia. "
            "Jangan membuat klaim kausal, fungsi gen, risiko penyakit, "
            "atau saran klinis. Jangan mengarang angka atau SNP. "
            "Kembalikan JSON valid sesuai schema."
        )

        user_prompt = (
            "Buat JSON dengan field executive_summary, "
            "statistical_conclusion, findings, qc_assessment, "
            "pca_assessment, diagnostics_assessment, limitations, "
            "recommendations, evidence_refs. "
            "Executive summary maksimal 2 kalimat. "
            "Findings maksimal 5, masing-masing maksimal 2 kalimat. "
            "QC, PCA, dan diagnostics masing-masing 1-2 kalimat. "
            "Limitations dan recommendations maksimal 3 item. "
            "Jumlah SNP signifikan harus sama persis dengan payload. "
            "Gunakan hanya angka dan SNP dari sumber. "
            "Jangan mengarang evidence_refs. "
            "Jika tidak ada temuan, gunakan findings kosong. "
            "\nPAYLOAD:\n"
            + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )

        url = settings.ai_api_base_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"

        body = {
            "model": settings.ai_model,
            "temperature": 0.1,
            "max_tokens": 700,
            "reasoning_effort": "none",
            "response_format": {"type": "json_object"},
            "stream": True,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        headers = {
            "Authorization": f"Bearer {settings.ai_api_key}",
            "Content-Type": "application/json",
        }

        print("========== COMPACT PROMPT TEST ==========")
        print("Analysis:", analysis.analysis_id)
        print("Model:", settings.ai_model)
        print("Top SNP:", len(payload["top_snps"]))
        print("Max tokens:", body["max_tokens"])
        print("Memulai inferensi...")

        started = time.perf_counter()
        first_content = None
        output = []
        finish_reason = None

        timeout = httpx.Timeout(
            240.0,
            connect=10.0,
            write=30.0,
            pool=10.0,
        )

        with httpx.Client(timeout=timeout) as client:
            with client.stream(
                "POST",
                url,
                headers=headers,
                json=body,
            ) as response:

                print("HTTP:", response.status_code)
                response.raise_for_status()

                for line in response.iter_lines():

                    if time.perf_counter() - started > 240:
                        raise TimeoutError("Batas 240 detik tercapai.")

                    if not line.startswith("data:"):
                        continue

                    data = line[5:].strip()

                    if not data or data == "[DONE]":
                        continue

                    event = json.loads(data)
                    choices = event.get("choices", [])

                    if not choices:
                        continue

                    choice = choices[0]
                    delta = choice.get("delta") or {}
                    content = delta.get("content")

                    if content:
                        if first_content is None:
                            first_content = time.perf_counter() - started
                            print(
                                f"First content: {first_content:.2f} detik"
                            )

                        output.append(content)

                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]

        total = time.perf_counter() - started
        raw = "".join(output)

        with open("/tmp/genosphere_compact_ai.json", "w") as f:
            f.write(raw)

        print("\n========== HASIL GENERATION ==========")
        print(f"Total waktu: {total:.2f} detik")
        print("First content:", first_content)
        print("Output characters:", len(raw))
        print("Finish reason:", finish_reason)
        print("Raw output: /tmp/genosphere_compact_ai.json")

        result = json.loads(raw)

        print("\n========== VALIDASI GENOSPHERE ==========")
        validated = validate_interpretation(result, payload)

        print("JSON: VALID")
        print("Schema: VALID")
        print("Findings:", len(validated["findings"]))
        print(
            "Significant SNP:",
            validated["statistical_conclusion"]["n_significant_snps"]
        )

    except Exception as exc:
        print("\nPENGUJIAN GAGAL")
        print("Error:", type(exc).__name__, str(exc))

    finally:
        db.close()


if __name__ == "__main__":
    main()
