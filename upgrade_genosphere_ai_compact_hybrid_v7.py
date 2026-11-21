#!/usr/bin/env python3
"""Upgrade GenoSphere AI to a compact hybrid narrative pipeline (v7).

AI generates one short plain-language synthesis. Backend deterministically
explains every SNP, QC, PCA, QQ/lambda-GC, counts, limitations and evidence.
Run from the backend root. A timestamped backup is created before edits.
"""
from __future__ import annotations

import ast
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path.cwd()
AI_FILE = ROOT / "app/services/ai_interpretation.py"
TEST_FILE = ROOT / "tests/test_ai_interpretation_composer.py"
ENV_FILE = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
BACKUP = ROOT / "tools/reports" / f"ai_hybrid_v7_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

MODEL_BLOCK = r'''class AIStatisticalNarrative(BaseModel):
    """One concise AI synthesis; numeric evidence is composed by the backend."""
    model_config = ConfigDict(extra="forbid")
    executive_summary: str = Field(min_length=100, max_length=900)


class AIResponseValidationError(ValueError):
    pass
'''

NARRATIVE_CONTEXT = r'''def _narrative_context(payload: dict[str, Any]) -> dict[str, Any]:
    """Compact, de-identified context for one short AI synthesis."""
    source_analysis = payload.get("analysis") or {}
    analysis_keys = (
        "model", "trait_type", "trait_scale", "n_samples_analyzed",
        "n_snps_tested", "n_significant_snps", "significance_method",
        "significance_threshold_p", "significance_threshold_q",
    )
    analysis = {key: source_analysis.get(key) for key in analysis_keys
                if source_analysis.get(key) is not None}

    top_snps = []
    model_name = str(source_analysis.get("model") or "")
    for index, snp in enumerate((payload.get("top_snps") or [])[:5]):
        beta = _number_or_none(snp.get("effect_size"))
        odds_ratio = _number_or_none(snp.get("odds_ratio"))
        item = {
            "index": index,
            "significant": bool(snp.get("is_significant")),
            "p_value": _number_or_none(snp.get("p_value")),
            "effect_size": beta,
            "effect_direction": "positive" if beta is not None and beta > 0 else "negative" if beta is not None and beta < 0 else "unavailable",
        }
        if "Logistic" in model_name:
            item["odds_ratio"] = odds_ratio
            item["odds_relation"] = "above_1" if odds_ratio is not None and odds_ratio > 1 else "below_1" if odds_ratio is not None and odds_ratio < 1 else "unavailable"
        top_snps.append(item)

    source_qc = payload.get("qc") or {}
    qc = {key: source_qc.get(key) for key in (
        "skipped", "variants_before", "variants_after", "variants_removed",
        "variants_removed_percent", "samples_before", "samples_after", "samples_removed",
    ) if source_qc.get(key) is not None}
    for key in ("metrics_before", "metrics_after"):
        compact = _compact_metric_map(source_qc.get(key), limit=3)
        if compact:
            qc[key] = compact
    if isinstance(source_qc.get("warnings"), list):
        qc["warnings"] = [str(item)[:120] for item in source_qc["warnings"][:2]]

    source_pca = payload.get("pca") or {}
    variance = []
    for item in (source_pca.get("variance_explained") or [])[:5]:
        percent = _as_percent(item)
        if percent is not None:
            variance.append(round(percent, 3))
    pca = {key: source_pca.get(key) for key in (
        "requested_components", "actual_components", "status", "warnings"
    ) if source_pca.get(key) is not None}
    if variance:
        pca["variance_explained_percent_by_pc"] = variance
        pca["cumulative_percent_for_listed_pcs"] = round(sum(variance), 3)

    source_qq = payload.get("qq_diagnostics") or {}
    lambda_gc = _number_or_none(source_qq.get("lambda_gc"))
    if lambda_gc is None:
        lambda_flag = "unavailable"
    elif lambda_gc >= 2:
        lambda_flag = "markedly_elevated_potential_inflation"
    elif lambda_gc > 1.1:
        lambda_flag = "elevated_potential_inflation"
    elif lambda_gc < 0.9:
        lambda_flag = "potentially_conservative"
    else:
        lambda_flag = "near_reference"
    qq = {key: source_qq.get(key) for key in (
        "lambda_gc", "n", "displayed_variants", "downsampled"
    ) if source_qq.get(key) is not None}
    qq["interpretive_flag"] = lambda_flag

    return {
        "analysis": analysis,
        "top_snps_pattern": top_snps,
        "qc": qc,
        "pca": pca,
        "qq_diagnostics": qq,
        "writing_rules": [
            "Tulis Bahasa Indonesia yang mudah dipahami pembaca awam.",
            "Jelaskan makna umum pola asosiasi, bukan mengulang angka atau menyalin payload.",
            "Jangan menulis jumlah, ID SNP, posisi, p-value, beta, atau angka; backend menambahkan fakta terverifikasi.",
            "Jangan menyebut gen, fungsi biologis, jalur, kausalitas, risiko penyakit, atau saran klinis.",
            "Jangan menyebut QC/PCA baik tanpa bukti; lambda GC tinggi harus diperlakukan sebagai sinyal kehati-hatian.",
        ],
    }


def _narrative_json_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "executive_summary": {
                "type": "string", "minLength": 100, "maxLength": 900
            }
        },
        "required": ["executive_summary"],
        "additionalProperties": False,
    }
'''

QC_EXPLANATION = r'''def _backend_qc_explanation(payload: dict[str, Any]) -> str:
    qc = payload.get("qc") or {}
    if qc.get("skipped"):
        return (
            "Quality control (QC) adalah penyaringan untuk mengeluarkan varian atau sampel yang tidak memenuhi ambang kualitas. "
            "Pada analisis ini QC tercatat dilewati, sehingga hasil GWAS tidak boleh diasumsikan telah melalui filter QC. "
            "Periksa parameter dan laporan kualitas sebelum menafsirkan temuan."
        )

    before = _number_or_none(qc.get("variants_before"))
    after = _number_or_none(qc.get("variants_after"))
    removed = _number_or_none(qc.get("variants_removed"))
    pct = _number_or_none(qc.get("variants_removed_percent"))
    sample_before = _number_or_none(qc.get("samples_before"))
    sample_after = _number_or_none(qc.get("samples_after"))
    parts = [
        "Quality control (QC) adalah tahap penyaringan sebelum uji asosiasi. Tujuannya mengurangi varian atau sampel yang tidak memenuhi ambang kelengkapan dan kualitas yang ditetapkan; varian yang tersaring bukan otomatis berarti seluruh dataset buruk."
    ]
    if before is not None and after is not None:
        removed = removed if removed is not None else max(0.0, before - after)
        if pct is None and before > 0:
            pct = removed / before * 100.0
        pct_text = f" ({_id_decimal(pct)}%)" if pct is not None else ""
        parts.append(
            f"Varian berubah dari {_id_int(before)} sebelum QC menjadi {_id_int(after)} sesudah QC; {_id_int(removed)} varian dikeluarkan{pct_text} sesuai filter yang diterapkan."
        )
    if sample_before is not None and sample_after is not None:
        sample_removed = _number_or_none(qc.get("samples_removed"))
        parts.append(
            f"Jumlah sampel tetap {_id_int(sample_after)} dari {_id_int(sample_before)} sampel awal; {_id_int(sample_removed or 0)} sampel dikeluarkan."
            if sample_removed == 0 or (sample_removed is None and sample_before == sample_after)
            else f"Jumlah sampel berubah dari {_id_int(sample_before)} menjadi {_id_int(sample_after)}; {_id_int(sample_removed or (sample_before - sample_after))} sampel dikeluarkan."
        )

    metrics_after = qc.get("metrics_after") or {}
    missingness = metrics_after.get("variant_missingness") or {}
    if isinstance(missingness, dict) and missingness.get("available"):
        mean_missing = _number_or_none(missingness.get("mean"))
        max_missing = _number_or_none(missingness.get("max"))
        if mean_missing is not None:
            mean_pct = mean_missing * 100.0 if abs(mean_missing) <= 1 else mean_missing
            phrase = f"Rata-rata tingkat genotipe yang hilang per varian setelah QC sekitar {_id_decimal(mean_pct)}%"
            if max_missing is not None:
                max_pct = max_missing * 100.0 if abs(max_missing) <= 1 else max_missing
                phrase += f", dengan nilai maksimum sekitar {_id_decimal(max_pct)}%"
            parts.append(phrase + ". Missingness menunjukkan bagian data genotipe yang tidak tercatat; nilai lebih kecil berarti data lebih lengkap pada ukuran ini.")

    maf = metrics_after.get("minor_allele_frequency") or {}
    if isinstance(maf, dict) and maf.get("available"):
        maf_mean = _number_or_none(maf.get("mean"))
        if maf_mean is not None:
            parts.append(
                f"Rata-rata minor allele frequency (MAF) setelah QC sekitar {_id_decimal(maf_mean, 3)}. MAF menggambarkan frekuensi alel yang lebih jarang pada suatu varian; ringkasan rata-rata tidak menggantikan pemeriksaan distribusi MAF tiap varian."
            )

    hwe = metrics_after.get("hardy_weinberg") or {}
    if isinstance(hwe, dict) and hwe.get("available"):
        reports = [hwe.get(key) or {} for key in ("autosomal", "chromosome_x")]
        n_hwe = sum(int(_number_or_none(report.get("n_records")) or 0) for report in reports)
        below = sum(int(_number_or_none(report.get("below_filter_threshold")) or 0) for report in reports)
        threshold = _number_or_none(hwe.get("filter_threshold"))
        if n_hwe:
            parts.append(
                f"Ringkasan uji Hardy-Weinberg tersedia untuk {_id_int(n_hwe)} catatan; {_id_int(below)} berada di bawah ambang filter HWE{f' {_format_p(threshold)}' if threshold is not None else ''}. Hasil HWE merupakan pemeriksaan tambahan dan perlu ditafsirkan sesuai jenis sampel serta parameter QC."
            )

    warnings = qc.get("warnings")
    if isinstance(warnings, list) and warnings:
        warning_text = [str(item.get("message") or item.get("code") or item) if isinstance(item, dict) else str(item) for item in warnings[:2]]
        parts.append("Catatan QC: " + "; ".join(warning_text) + ".")
    else:
        parts.append("Kesimpulan QC harus dibaca bersama jenis filter, ambang yang dipilih, dan metrik sebelum serta sesudah penyaringan.")
    return " ".join(parts)
'''

COMPOSER = r'''def compose_interpretation(narrative_raw: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Build a full beginner-friendly report from one AI synthesis plus source facts."""
    if not isinstance(narrative_raw, dict):
        raise AIResponseValidationError("Respons narasi AI bukan objek JSON.")
    try:
        narrative = AIStatisticalNarrative.model_validate(narrative_raw)
    except ValidationError as exc:
        raise AIResponseValidationError(
            f"Schema narasi tidak valid: {_pydantic_error_summary(exc)}"
        ) from exc

    analysis = payload.get("analysis") or {}
    model_name = str(analysis.get("model") or "GWAS")
    source_snps = payload.get("top_snps") or []

    # SNP identity, effect estimates and evidence references always come from source data.
    findings: list[dict[str, Any]] = []
    for index, snp in enumerate(source_snps[:5]):
        snp_ref = str(snp.get("snp_ref") or f"SNP index {index}")
        interpretation = _snp_evidence_explainer(
            snp, model_name, analysis.get("significance_threshold_p")
        )
        findings.append({
            "title": f"Apa arti hasil untuk {snp_ref}"[:180],
            "interpretation": interpretation[:1200],
            "snp_ref": snp_ref,
            "evidence_refs": [str(snp.get("evidence_ref") or f"top_snps[{index}]")],
            "statistics": {
                "p_value": _number_or_none(snp.get("p_value")),
                "effect_size": _number_or_none(snp.get("effect_size")),
                "odds_ratio": _number_or_none(snp.get("odds_ratio")),
                "q_value": _number_or_none(snp.get("q_value")),
            },
        })

    n_sig = max(0, int(analysis.get("n_significant_snps") or 0))
    n_tested = max(0, int(analysis.get("n_snps_tested") or 0))
    n_samples = analysis.get("n_samples_analyzed")
    method_key = str(analysis.get("significance_method") or "")
    method = {
        "genome_wide": "ambang genome-wide",
        "suggestive": "ambang suggestive",
        "bonferroni": "koreksi Bonferroni",
        "fdr": "koreksi FDR Benjamini-Hochberg",
    }.get(method_key, method_key or "aturan signifikansi yang dipilih")
    threshold = _number_or_none(analysis.get("significance_threshold_p"))
    if n_sig:
        count_text = (
            f"Dari {_id_int(n_tested)} varian yang diuji, {_id_int(n_sig)} memenuhi "
            f"aturan signifikansi ({method})."
        )
    else:
        count_text = (
            f"Dari {_id_int(n_tested)} varian yang diuji, tidak ada yang memenuhi "
            f"aturan signifikansi ({method})."
        )
    if n_samples is not None:
        count_text += f" Analisis menggunakan {_id_int(n_samples)} sampel."
    count_text += (
        f" Lima varian teratas yang dijelaskan di bawah adalah daftar teratas, "
        f"bukan jumlah keseluruhan varian signifikan."
        if len(findings) == 5 else
        f" {len(findings)} varian teratas ditampilkan untuk penjelasan individual."
    )
    if threshold is not None:
        count_text += f" Ambang p-value yang diterapkan adalah {_format_p(threshold)}."

    executive = count_text + "\n\n" + narrative.executive_summary.strip()

    limitations = [
        "Asosiasi statistik tidak dapat ditafsirkan sebagai bukti hubungan sebab-akibat antara varian dan perubahan trait.",
        "Arah dan besar beta mengikuti pengodean genotipe serta skala trait pada model; satuan trait dan alel efek perlu dipastikan sebelum menilai besar dampak praktis.",
    ]
    lambda_gc = _number_or_none((payload.get("qq_diagnostics") or {}).get("lambda_gc"))
    if lambda_gc is not None and lambda_gc > 1.1:
        limitations.append(
            "Lambda GC yang meningkat dapat memengaruhi kalibrasi p-value; nilai ini sendiri tidak dapat menentukan sumber inflasi."
        )
    else:
        limitations.append(
            "Penilaian dibatasi pada ringkasan statistik dan diagnostik yang tersedia; hasil plot serta asumsi model tetap perlu diperiksa."
        )

    recommendations = [
        "Periksa QQ plot, PCA scatter plot, kekerabatan sampel, dan kovariat untuk menilai kemungkinan struktur populasi atau perancu.",
        "Pastikan pengodean alel efek, nama trait, serta satuan atau standardisasi trait sebelum menjelaskan arah dan besar beta secara biologis.",
        "Tinjau parameter dan metrik QC, kemudian validasi temuan pada dataset independen bila tersedia.",
    ]

    evidence_refs = ["analysis", "qc", "pca", "qq_diagnostics"]
    evidence_refs.extend(item["evidence_refs"][0] for item in findings)

    return {
        "executive_summary": executive[:2000],
        "statistical_conclusion": {
            "significant_snps_detected": n_sig > 0,
            "n_significant_snps": n_sig,
            "statement": count_text[:1000],
        },
        "findings": findings,
        "qc_assessment": _backend_qc_explanation(payload),
        "pca_assessment": _backend_pca_explanation(payload),
        "diagnostics_assessment": _backend_diagnostics_explanation(payload),
        "limitations": limitations[:3],
        "recommendations": recommendations[:3],
        "evidence_refs": list(dict.fromkeys(evidence_refs)),
    }
'''

PROVIDER = r'''class OpenAICompatibleProvider:
    """Low-output Ollama adapter; backend composes all source-linked statistics."""

    def generate(self, payload: dict, timeout_seconds: float | None = None) -> tuple[dict, dict | None]:
        settings = get_settings()
        if not settings.ai_api_key:
            raise RuntimeError("AI_NOT_CONFIGURED")
        base = settings.ai_api_base_url.rstrip("/")
        url = base if base.endswith("/chat/completions") else base + "/chat/completions"
        context = _narrative_context(payload)
        schema = _narrative_json_schema()
        system_prompt = (
            "Anda adalah analis statistik GWAS yang menjelaskan hasil kepada orang awam. "
            "Tulis Bahasa Indonesia yang natural, jelas, dan tidak bertele-tele. "
            "Jangan mengarang data, angka, identitas SNP, nama gen, fungsi biologis, "
            "jalur, hubungan sebab-akibat, risiko penyakit, atau saran klinis. "
            "Jangan mengulang payload. Kembalikan satu objek JSON sesuai schema."
        )
        user_prompt = (
            "Tuliskan satu paragraf penjelasan umum sebanyak 4 sampai 5 kalimat. "
            "Jelaskan dengan bahasa sederhana apa tujuan GWAS, apa arti pola asosiasi "
            "yang terlihat pada hasil ini, bagaimana arah pola efek secara umum harus "
            "dibaca, serta mengapa hasil perlu ditafsirkan bersama QC, PCA, dan QQ plot. "
            "Jangan tulis angka, jumlah temuan, p-value, beta, atau ID SNP; backend "
            "akan menyusun angka dan uraian setiap bagian dari sumber secara tepat. "
            "Jangan menyatakan hasil pasti baik atau bebas perancu. Hindari istilah teknis "
            "tanpa penjelasan sederhana. DATA:\n" +
            json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        )
        request_timeout = max(
            1.0,
            min(float(timeout_seconds or settings.ai_timeout_seconds), float(settings.ai_timeout_seconds)),
        )
        body = {
            "model": settings.ai_model,
            "temperature": 0.1,
            "seed": 42,
            "max_tokens": settings.ai_max_output_tokens,
            "reasoning_effort": "none",
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "genosphere_short_plain_language_summary",
                    "strict": True,
                    "schema": schema,
                },
            },
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        timeout = httpx.Timeout(
            request_timeout,
            connect=min(10.0, request_timeout),
            read=request_timeout,
            write=min(30.0, request_timeout),
            pool=min(10.0, request_timeout),
        )
        headers = {
            "Authorization": f"Bearer {settings.ai_api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = httpx.post(url, headers=headers, json=body, timeout=timeout)
            if response.status_code in {400, 422}:
                detail = response.text[:1000].lower()
                if "json_schema" in detail or "response_format" in detail:
                    fallback_body = dict(body)
                    fallback_body["response_format"] = {"type": "json_object"}
                    response = httpx.post(url, headers=headers, json=fallback_body, timeout=timeout)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise RuntimeError("AI_TIMEOUT") from exc
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"AI_HTTP_{exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            raise RuntimeError("AI_CONNECTION_ERROR") from exc

        response_data = response.json()
        choices = response_data.get("choices") or []
        if not choices or not isinstance(choices[0].get("message"), dict):
            raise RuntimeError("AI_INVALID_RESPONSE")
        choice = choices[0]
        if choice.get("finish_reason") == "length":
            raise RuntimeError("AI_OUTPUT_TRUNCATED")
        content = choice["message"].get("content")
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        text = str(content or "").strip()
        if not text:
            raise RuntimeError("AI_EMPTY_CONTENT")
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
        try:
            narrative_raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError("AI_INVALID_JSON") from exc
        if not isinstance(narrative_raw, dict):
            raise RuntimeError("AI_INVALID_JSON")
        return compose_interpretation(narrative_raw, payload), response_data.get("usage")


def get_provider(name: str):
    providers = {"openai_compatible": OpenAICompatibleProvider}
    provider_cls = providers.get(name)
    if provider_cls is None:
        raise RuntimeError("AI_PROVIDER_UNSUPPORTED")
    return provider_cls()
'''

TESTS = r'''from app.services.ai_interpretation import (
    _narrative_context,
    _narrative_json_schema,
    compose_interpretation,
    validate_interpretation,
)


def sample_payload():
    return {
        "analysis": {
            "model": "Linear Regression", "trait_type": "quantitative", "trait_scale": "quantitative",
            "n_samples_analyzed": 1940, "n_snps_tested": 9282, "n_significant_snps": 163,
            "significance_method": "genome_wide", "significance_threshold_p": 5e-8,
        },
        "top_snps": [
            {"evidence_ref": "top_snps[0]", "snp_ref": "rs-test-1", "p_value": 1e-35,
             "effect_size": -0.45, "odds_ratio": None, "q_value": None, "is_significant": True},
            {"evidence_ref": "top_snps[1]", "snp_ref": "rs-test-2", "p_value": 2e-35,
             "effect_size": -0.40, "odds_ratio": None, "q_value": None, "is_significant": True},
        ],
        "qc": {"variants_before": 12226, "variants_after": 10300, "variants_removed": 1926,
               "variants_removed_percent": 15.75, "samples_before": 1940, "samples_after": 1940,
               "samples_removed": 0, "metrics_before": {"variant_missing_rate": 0.02},
               "metrics_after": {"variant_missing_rate": 0.01}},
        "pca": {"status": "done", "actual_components": 5,
                "variance_explained": [0.048759688, 0.040189863, 0.036253296, 0.022716985, 0.019790319]},
        "qq_diagnostics": {"lambda_gc": 4.3859, "n": 9282, "displayed_variants": 4428, "downsampled": True},
        "source_refs": ["analysis", "qc", "pca", "qq_diagnostics", "top_snps"],
    }


def sample_narrative():
    return {
        "executive_summary": (
            "GWAS memeriksa apakah perbedaan pada banyak titik DNA berkaitan dengan "
            "perbedaan sifat yang diukur. Pada hasil ini, beberapa varian menunjukkan "
            "pola asosiasi dengan arah efek yang serupa menurut pengodean model. "
            "Pola tersebut merupakan petunjuk statistik, bukan bukti hubungan sebab-akibat. "
            "Hasil perlu dibaca bersama pemeriksaan kualitas data dan diagnostik model."
        )
    }


def test_schema_is_compact_single_summary():
    schema = _narrative_json_schema()
    assert list(schema["properties"]) == ["executive_summary"]
    assert schema["additionalProperties"] is False


def test_context_keeps_core_evidence_and_is_compact():
    context = _narrative_context(sample_payload())
    assert len(context["top_snps_pattern"]) == 2
    assert context["analysis"]["n_significant_snps"] == 163
    assert context["qq_diagnostics"]["interpretive_flag"] == "markedly_elevated_potential_inflation"
    assert "software_versions" not in context
    assert "timing" not in context


def test_composer_uses_backend_counts_and_builds_full_explanations():
    payload = sample_payload()
    result = compose_interpretation(sample_narrative(), payload)
    assert result["statistical_conclusion"]["n_significant_snps"] == 163
    assert "163" in result["executive_summary"]
    assert "5 varian teratas" not in result["statistical_conclusion"]["statement"]
    assert len(result["findings"]) == 2
    assert "Beta negatif" in result["findings"][0]["interpretation"]
    assert "15,75%" in result["qc_assessment"]
    assert "4,88%" in result["pca_assessment"]
    assert "4,386" in result["diagnostics_assessment"]
    assert len(result["limitations"]) >= 2
    assert len(result["recommendations"]) == 3


def test_final_result_passes_source_consistency_validation():
    payload = sample_payload()
    result = compose_interpretation(sample_narrative(), payload)
    validated = validate_interpretation(result, payload)
    assert validated["statistical_conclusion"]["n_significant_snps"] == 163
'''


def fail(message: str) -> None:
    print(f"[ERROR] {message}", file=sys.stderr)
    raise SystemExit(1)


def replace_between(text: str, start_marker: str, end_marker: str, replacement: str, label: str) -> str:
    start = text.find(start_marker)
    if start < 0:
        fail(f"Marker awal tidak ditemukan untuk {label}: {start_marker!r}")
    end = text.find(end_marker, start + len(start_marker))
    if end < 0:
        fail(f"Marker akhir tidak ditemukan untuk {label}: {end_marker!r}")
    return text[:start] + replacement.rstrip() + "\n\n" + text[end:]


def set_env_value(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"(?m)^{re.escape(key)}=.*$")
    if pattern.search(text):
        return pattern.sub(f"{key}={value}", text, count=1)
    return text.rstrip() + f"\n{key}={value}\n"


def main() -> None:
    required_files = (AI_FILE, TEST_FILE, ENV_FILE)
    if not all(path.is_file() for path in required_files):
        fail("Jalankan dari root /mnt/d/GenoSphere/backend; source AI, tests, dan .env harus tersedia.")
    original_ai = AI_FILE.read_text(encoding="utf-8")
    original_test = TEST_FILE.read_text(encoding="utf-8")
    original_env = ENV_FILE.read_text(encoding="utf-8")
    original_example = ENV_EXAMPLE.read_text(encoding="utf-8") if ENV_EXAMPLE.exists() else None

    if 'PROMPT_VERSION = "statistical-narrative-v6-plain-language"' not in original_ai:
        fail("Source bukan v6-plain-language. Tidak ada file yang diubah.")
    for marker in (
        "class SnpNarrative(BaseModel):",
        "def _narrative_context(payload: dict[str, Any])",
        "def _narrative_json_schema(snp_count: int)",
        "def compose_interpretation(narrative_raw: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:",
        "class OpenAICompatibleProvider:",
    ):
        if marker not in original_ai:
            fail(f"Struktur source tidak cocok: {marker}. Tidak ada file yang diubah.")

    updated = original_ai.replace(
        'PROMPT_VERSION = "statistical-narrative-v6-plain-language"',
        'PROMPT_VERSION = "statistical-narrative-v7-compact-hybrid"',
        1,
    )
    updated = replace_between(
        updated,
        "class SnpNarrative(BaseModel):",
        "def _run_dir(",
        MODEL_BLOCK.rstrip() + "\n\n",
        "narrative model",
    ) if "def _run_dir(" in updated[updated.find("class SnpNarrative(BaseModel):"):] else updated

    # The source's model block ends immediately before the original payload helpers in v6.
    if "class SnpNarrative(BaseModel):" in updated:
        # Re-do with the reliable v6 terminator if prior structure still contains the legacy block.
        start = updated.find("class SnpNarrative(BaseModel):")
        end_candidates = [x for x in (updated.find("class AIResponseValidationError", start),) if x >= 0]
        if not end_candidates:
            fail("Terminator model narasi v6 tidak ditemukan.")
        end = end_candidates[0]
        # Keep one declaration of AIResponseValidationError from the replacement block.
        updated = updated[:start] + MODEL_BLOCK.rstrip() + "\n\n" + updated[end + len("class AIResponseValidationError(ValueError):\n    pass"):]

    updated = replace_between(
        updated,
        "def _narrative_context(payload: dict[str, Any]) -> dict[str, Any]:",
        "def _pydantic_error_summary(exc: ValidationError) -> str:",
        NARRATIVE_CONTEXT,
        "compact narrative context",
    )
    updated = replace_between(
        updated,
        "def _backend_qc_explanation(payload: dict[str, Any]) -> str:",
        "def _backend_pca_explanation(payload: dict[str, Any]) -> str:",
        QC_EXPLANATION,
        "detailed deterministic QC explanation",
    )
    updated = replace_between(
        updated,
        "def compose_interpretation(narrative_raw: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:",
        "def validate_interpretation(raw: dict, payload: dict) -> dict:",
        COMPOSER,
        "hybrid result composer",
    )
    forbidden_phrase = "nilainya bukan peluang bahwa SNP menyebabkan trait."
    if forbidden_phrase not in updated:
        fail("Kalimat legacy SNP tidak ditemukan untuk koreksi validasi. Tidak ada file yang diubah.")
    updated = updated.replace(
        forbidden_phrase,
        "nilai ini tidak membuktikan hubungan sebab-akibat antara varian dan trait.",
        1,
    )

    provider_start = updated.find("class OpenAICompatibleProvider:")
    if provider_start < 0:
        fail("Provider class tidak ditemukan.")
    updated = updated[:provider_start] + PROVIDER.rstrip() + "\n"

    try:
        ast.parse(updated)
    except SyntaxError as exc:
        fail(f"Patch AI menghasilkan syntax error: {exc}")

    updated_env = original_env
    updated_env = set_env_value(updated_env, "AI_MAX_OUTPUT_TOKENS", "256")
    updated_env = set_env_value(updated_env, "AI_TIMEOUT_SECONDS", "240")
    updated_env = set_env_value(updated_env, "AI_JOB_DEADLINE_SECONDS", "360")
    updated_env = set_env_value(updated_env, "AI_MAX_ATTEMPTS", "1")
    if original_example is not None:
        updated_example = original_example
        updated_example = set_env_value(updated_example, "AI_MAX_OUTPUT_TOKENS", "256")
        updated_example = set_env_value(updated_example, "AI_TIMEOUT_SECONDS", "240")
        updated_example = set_env_value(updated_example, "AI_JOB_DEADLINE_SECONDS", "360")
        updated_example = set_env_value(updated_example, "AI_MAX_ATTEMPTS", "1")
    else:
        updated_example = None

    BACKUP.mkdir(parents=True, exist_ok=False)
    shutil.copy2(AI_FILE, BACKUP / "ai_interpretation.py")
    shutil.copy2(TEST_FILE, BACKUP / "test_ai_interpretation_composer.py")
    shutil.copy2(ENV_FILE, BACKUP / ".env")
    if original_example is not None:
        shutil.copy2(ENV_EXAMPLE, BACKUP / ".env.example")

    try:
        AI_FILE.write_text(updated, encoding="utf-8", newline="\n")
        TEST_FILE.write_text(TESTS, encoding="utf-8", newline="\n")
        ENV_FILE.write_text(updated_env, encoding="utf-8", newline="\n")
        if updated_example is not None:
            ENV_EXAMPLE.write_text(updated_example, encoding="utf-8", newline="\n")
        print("[OK] AI v7 memakai satu paragraf sintesis AI ringkas.")
        print("[OK] Penjelasan lima SNP, QC, PCA, QQ/lambda GC, angka, limitations dan recommendations disusun backend dari sumber.")
        print("[OK] Jumlah seluruh SNP signifikan dibedakan dari daftar Top SNP.")
        print("[OK] Context AI dipadatkan; metadata software dan timing tidak dikirim ke model.")
        print("[OK] Output token 256, timeout 240s, job deadline 360s, attempts 1.")
        print(f"[OK] Backup: {BACKUP}")
        print("[NEXT] python -m py_compile app/services/ai_interpretation.py")
        print("[NEXT] python -m pytest -q tests/test_ai_interpretation_composer.py tests/test_revision_features.py tests/test_config.py")
    except Exception as exc:
        shutil.copy2(BACKUP / "ai_interpretation.py", AI_FILE)
        shutil.copy2(BACKUP / "test_ai_interpretation_composer.py", TEST_FILE)
        shutil.copy2(BACKUP / ".env", ENV_FILE)
        if original_example is not None:
            shutil.copy2(BACKUP / ".env.example", ENV_EXAMPLE)
        fail(f"Penerapan gagal; file dipulihkan: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
