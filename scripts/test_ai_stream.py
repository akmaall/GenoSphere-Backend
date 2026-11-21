from __future__ import annotations

import json
import re
import time
from urllib.parse import urlsplit, urlunsplit

import httpx

# Ensure the backend project root is importable when this
# diagnostic script is executed directly from scripts/.
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import database
from app.config import get_settings
from app.models import GwasAnalysis
from app.services.ai_interpretation import (
    _narrative_context,
    _narrative_json_schema,
    build_ai_payload,
    compose_interpretation,
    validate_interpretation,
)

ANALYSIS_ID = 44


def extract_text(value):
    if isinstance(value, str):
        return value

    if isinstance(value, list):
        return "".join(
            str(item.get("text", ""))
            for item in value
            if isinstance(item, dict)
        )

    return ""


def make_urls(base_url):
    base = base_url.rstrip("/")
    parsed = urlsplit(base)
    path = parsed.path.rstrip("/")

    for suffix in ("/chat/completions", "/v1"):
        if path.endswith(suffix):
            path = path[:-len(suffix)].rstrip("/")

    root = urlunsplit((
        parsed.scheme,
        parsed.netloc,
        path,
        "",
        "",
    )).rstrip("/")

    endpoint = (
        base if base.endswith("/chat/completions")
        else base + "/chat/completions"
    )

    return root, endpoint


def main():
    settings = get_settings()

    print("=" * 65)
    print("GENOSPHERE AI STREAMING DIAGNOSTIC")
    print("=" * 65)

    print(f"Analysis ID : {ANALYSIS_ID}")
    print(f"Provider    : {settings.ai_provider}")
    print(f"Model       : {settings.ai_model}")
    print(f"Base URL    : {settings.ai_api_base_url}")
    print(f"Timeout     : {settings.ai_timeout_seconds} seconds")
    print(f"Max tokens  : {settings.ai_max_output_tokens}")

    if settings.ai_provider != "openai_compatible":
        raise RuntimeError("Provider bukan openai_compatible.")

    if not settings.ai_api_key:
        raise RuntimeError("AI_API_KEY belum dikonfigurasi.")

    if settings.ai_model != "qwen3:1.7b":
        raise RuntimeError(
            f"Model tidak sesuai target: {settings.ai_model}"
        )

    root_url, endpoint = make_urls(settings.ai_api_base_url)

    # Periksa apakah model sudah dimuat sebelum inferensi.
    print("\n========== MODEL PRELOAD STATUS ==========")

    try:
        ps_response = httpx.get(
            root_url + "/api/ps",
            timeout=5.0,
        )
        ps_response.raise_for_status()

        loaded_models = [
            item.get("name")
            for item in ps_response.json().get("models", [])
        ]

        print("Loaded models:", loaded_models or "(none)")

        model_was_loaded = any(
            name == settings.ai_model
            for name in loaded_models
        )

        print(
            "Initial state:",
            "WARM / ALREADY LOADED"
            if model_was_loaded
            else "NOT LOADED / COLD-START CANDIDATE"
        )

    except Exception as exc:
        model_was_loaded = None
        print(f"[WARNING] Tidak dapat memeriksa /api/ps: {exc}")

    # Ambil payload asli dari database.
    print("\n========== BUILD ACTUAL PAYLOAD ==========")

    db = database.SessionLocal()

    try:
        analysis = db.get(GwasAnalysis, ANALYSIS_ID)

        if analysis is None:
            raise RuntimeError(
                f"Analysis ID {ANALYSIS_ID} tidak ditemukan."
            )

        print("GWAS status:", analysis.status)

        if analysis.status != "done":
            raise RuntimeError(
                "Analisis GWAS belum berstatus done. "
                "Pengujian AI dibatalkan."
            )

        payload = build_ai_payload(db, analysis)

        context = _narrative_context(payload)
        schema = _narrative_json_schema()

        top_snps = payload.get("top_snps") or []

        print("Payload built successfully.")
        print("Top SNP count:", len(top_snps))
        print(
            "Analysis context characters:",
            len(json.dumps(context, ensure_ascii=False))
        )
        print("Schema fields:", len(schema.get("properties", {})))

        # Samakan prompt dengan implementasi provider aktif.
        system_prompt = (
            "Anda menjelaskan hasil GWAS kepada pembaca awam dalam Bahasa Indonesia. "
            "SNP adalah posisi/varian DNA; jangan menyebutnya sebagai gen. "
            "Jelaskan asosiasi statistik, bukan sebab-akibat, fungsi biologis, atau risiko penyakit. "
            "Jangan mengarang nama trait, satuan, angka, alel, atau temuan. "
            "Gunakan nama trait hanya jika tersedia dalam DATA. Kembalikan satu objek JSON sesuai schema."
        )

        user_prompt = (
            "Tuliskan satu paragraf 4-5 kalimat untuk menjelaskan secara sederhana apa yang dicari GWAS, "
            "apa arti asosiasi antara varian DNA dan trait, serta bagaimana arah polaefek harus dipahami. "
            "Terangkan bahwa asosiasi bukan sebab-akibat. Jangan menulis angka, ID SNP, p-value, beta, "
            "klaim gen, fungsi biologis, atau akibat klinis; backend akan menambahkanstatistik dan penjelasan tiap bagian. "
            "Hindari kalimat umum yang menyatakan data baik/stabil tanpa bukti. DATA:\n"
            + json.dumps(
                context,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )

        print("System prompt characters:", len(system_prompt))
        print("User prompt characters:", len(user_prompt))

        body = {
            "model": settings.ai_model,
            "temperature": 0.1,
            "seed": 42,
            "max_tokens": settings.ai_max_output_tokens,
            "reasoning_effort": "none",
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "genosphere_v8_plain_language_summary",
                    "strict": True,
                    "schema": schema,
                },
            },
            "stream": True,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        timeout_value = max(
            1.0,
            float(settings.ai_timeout_seconds),
        )

        timeout = httpx.Timeout(
            timeout_value,
            connect=min(10.0, timeout_value),
            read=timeout_value,
            write=min(30.0, timeout_value),
            pool=min(10.0, timeout_value),
        )

        headers = {
            "Authorization": f"Bearer {settings.ai_api_key}",
            "Content-Type": "application/json",
        }

        print("\n========== STREAMING REQUEST ==========")
        print("Endpoint:", endpoint)
        print("Stream:", body["stream"])
        print("Request read timeout:", timeout.read)

        content_parts = []
        first_event = None
        first_content = None
        first_reasoning = None
        finish_reason = None
        usage = None

        chunk_count = 0
        content_chunk_count = 0
        reasoning_chunk_count = 0
        content_chars = 0
        reasoning_chars = 0
        max_interchunk_gap = 0.0
        previous_event_time = None
        done_received = False

        request_start = time.perf_counter()

        try:
            with httpx.Client(timeout=timeout) as client:
                with client.stream(
                    "POST",
                    endpoint,
                    headers=headers,
                    json=body,
                ) as response:

                    headers_time = time.perf_counter() - request_start

                    print("HTTP status:", response.status_code)
                    print(
                        "Response headers received:",
                        f"{headers_time:.3f} seconds",
                    )

                    if response.status_code >= 400:
                        error_body = response.read().decode(
                            "utf-8",
                            errors="replace",
                        )
                        print("[ERROR] HTTP response:")
                        print(error_body[:2000])
                        return

                    response.raise_for_status()

                    for line in response.iter_lines():
                        if not line:
                            continue

                        if not line.startswith("data:"):
                            continue

                        raw = line[5:].strip()

                        if raw == "[DONE]":
                            done_received = True
                            break

                        try:
                            event = json.loads(raw)
                        except json.JSONDecodeError:
                            continue

                        now = time.perf_counter()
                        elapsed = now - request_start

                        chunk_count += 1

                        if first_event is None:
                            first_event = elapsed

                        if previous_event_time is not None:
                            gap = now - previous_event_time
                            max_interchunk_gap = max(
                                max_interchunk_gap,
                                gap,
                            )

                        previous_event_time = now

                        if event.get("usage"):
                            usage = event["usage"]

                        choices = event.get("choices") or []

                        for choice in choices:
                            delta = choice.get("delta") or {}

                            text = extract_text(delta.get("content"))

                            reasoning = extract_text(
                                delta.get("reasoning")
                                or delta.get("reasoning_content")
                            )

                            if reasoning:
                                if first_reasoning is None:
                                    first_reasoning = elapsed

                                reasoning_chunk_count += 1
                                reasoning_chars += len(reasoning)

                            if text:
                                if first_content is None:
                                    first_content = elapsed

                                content_chunk_count += 1
                                content_chars += len(text)
                                content_parts.append(text)

                            if choice.get("finish_reason") is not None:
                                finish_reason = choice["finish_reason"]

        except httpx.ReadTimeout as exc:
            elapsed = time.perf_counter() - request_start
            print("\n[ERROR] HTTP READ TIMEOUT")
            print("Elapsed:", f"{elapsed:.3f} seconds")
            print("Error:", repr(exc))
            print("Konten diterima:", content_chars)
            return

        except httpx.TimeoutException as exc:
            elapsed = time.perf_counter() - request_start
            print("\n[ERROR] HTTP TIMEOUT")
            print("Elapsed:", f"{elapsed:.3f} seconds")
            print("Error:", repr(exc))
            return

        except httpx.RequestError as exc:
            print("\n[ERROR] NETWORK REQUEST")
            print(type(exc).__name__, str(exc))
            return

        total = time.perf_counter() - request_start

        print("\n========== STREAM METRICS ==========")
        print("Model loaded before request:", model_was_loaded)
        print("HTTP headers latency:", f"{headers_time:.3f} s")
        print(
            "TTFT (first stream event):",
            f"{first_event:.3f} s" if first_event is not None else "N/A",
        )
        print(
            "First reasoning output:",
            f"{first_reasoning:.3f} s" if first_reasoning is not None else "N/A",
        )
        print(
            "First content token:",
            f"{first_content:.3f} s" if first_content is not None else "N/A",
        )
        print("Total duration:", f"{total:.3f} s")
        print("Total stream chunks:", chunk_count)
        print("Content chunks:", content_chunk_count)
        print("Reasoning chunks:", reasoning_chunk_count)
        print("Content characters:", content_chars)
        print("Reasoning characters:", reasoning_chars)
        print(
            "Maximum inter-chunk gap:",
            f"{max_interchunk_gap:.3f} s",
        )
        print("Finish reason:", finish_reason)
        print("Received [DONE]:", done_received)

        if usage:
            print("\n========== TOKEN USAGE ==========")
            for key, value in usage.items():
                print(f"{key}: {value}")

            completion_tokens = usage.get("completion_tokens")

            if (
                completion_tokens is not None
                and first_content is not None
                and total > first_content
            ):
                generation_duration = total - first_content
                print(
                    "Generation speed:",
                    f"{completion_tokens / generation_duration:.3f} tokens/s",
                )
        else:
            print(
                "\n[INFO] Stream tidak memberikan token usage. "
                "Token/s belum dapat dihitung secara akurat dari data ini."
            )

        # Validasi JSON akhir dan validasi aplikasi.
        print("\n========== OUTPUT VALIDATION ==========")

        output_text = "".join(content_parts).strip()

        if output_text.startswith("```"):
            output_text = re.sub(
                r"^```(?:json)?\s*|\s*```$",
                "",
                output_text,
                flags=re.I,
            ).strip()

        if finish_reason == "length":
            print("[FAIL] AI_OUTPUT_TRUNCATED")
            return

        if not output_text:
            print("[FAIL] AI_EMPTY_CONTENT")
            return

        try:
            narrative = json.loads(output_text)

            if not isinstance(narrative, dict):
                raise ValueError("Output bukan objek JSON.")

            print("[OK] JSON valid.")
            print("JSON keys:", list(narrative.keys()))

            composed = compose_interpretation(narrative, payload)
            validate_interpretation(composed, payload)

            print("[OK] Interpretation composition berhasil.")
            print("[OK] Backend validation berhasil.")

        except Exception as exc:
            print("[FAIL] Output validation gagal.")
            print(type(exc).__name__, str(exc))

    finally:
        db.close()


if __name__ == "__main__":
    main()
