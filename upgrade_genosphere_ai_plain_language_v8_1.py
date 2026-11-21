#!/usr/bin/env python3
"""GenoSphere AI Interpretation v8: factual, lay-friendly report improvements.

Run from /mnt/d/GenoSphere/backend. Creates a timestamped backup and only
modifies the AI interpretation service plus a focused regression test.
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
TEST_FILE = ROOT / "tests/test_ai_interpretation_plain_language_v8.py"
ENV_FILE = ROOT / ".env"
BACKUP = ROOT / "tools/reports" / f"ai_plain_language_v8_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def fail(message: str) -> None:
    print(f"[ERROR] {message}", file=sys.stderr)
    raise SystemExit(1)


def replace_between(text: str, start_marker: str, end_marker: str, replacement: str, label: str) -> str:
    start = text.find(start_marker)
    if start < 0:
        fail(f"Marker awal tidak ditemukan ({label}): {start_marker!r}")
    end = text.find(end_marker, start + len(start_marker))
    if end < 0:
        fail(f"Marker akhir tidak ditemukan ({label}): {end_marker!r}")
    return text[:start] + replacement.rstrip() + "\n\n" + text[end:]


NARRATIVE_CONTEXT = r'''def _narrative_context(payload: dict[str, Any]) -> dict[str, Any]:
    """Compact, de-identified context; include the actual trait label when available."""
    source_analysis = payload.get("analysis") or {}
    analysis_keys = (
        "model", "trait_name", "trait_type", "trait_scale", "n_samples_analyzed",
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
        "skipped", "parameters", "variants_before", "variants_after", "variants_removed",
        "variants_removed_percent", "samples_before", "samples_after", "samples_removed",
        "removed_by_filter",
    ) if source_qc.get(key) is not None}
    for key in ("metrics_before", "metrics_after"):
        compact = _compact_metric_map(source_qc.get(key), limit=4)
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
            "Jelaskan bahwa SNP adalah posisi/varian DNA, bukan otomatis sebuah gen.",
            "Gunakan nama trait jika tersedia; jangan menebak nama atau satuan trait.",
            "Jelaskan asosiasi statistik, bukan sebab-akibat atau fungsi biologis.",
            "Jangan menyatakan QC/PCA baik tanpa bukti; lambda GC tinggi adalah sinyal kehati-hatian.",
            "Jangan menyebut angka, ID SNP atau nilai statistik; backend menambahkan fakta sumber.",
        ],
    }
'''

QC_EXPLANATION = r'''def _backend_qc_explanation(payload: dict[str, Any]) -> str:
    """Explain QC in plain Indonesian using only recorded source metrics."""
    qc = payload.get("qc") or {}
    if qc.get("skipped"):
        return (
            "Quality control (QC) adalah penyaringan untuk menyingkirkan varian atau sampel yang tidak memenuhi ambang kualitas. "
            "Pada analisis ini QC tercatat dilewati, sehingga hasil tidak boleh dianggap telah melewati penyaringan tersebut. "
            "Periksa laporan dan parameter sebelum menafsirkan temuan."
        )

    parts = [
        "Quality control (QC) adalah tahap pemeriksaan dan penyaringan sebelum uji asosiasi. "
        "Tujuannya mengurangi data genotipe atau sampel yang tidak memenuhi ambang kelengkapan dan kualitas; banyaknya data yang tersaring tidak otomatis berarti seluruh dataset buruk."
    ]
    before = _number_or_none(qc.get("variants_before"))
    after = _number_or_none(qc.get("variants_after"))
    removed = _number_or_none(qc.get("variants_removed"))
    pct = _number_or_none(qc.get("variants_removed_percent"))
    if before is not None and after is not None:
        removed = removed if removed is not None else max(0.0, before - after)
        if pct is None and before > 0:
            pct = removed / before * 100.0
        pct_text = f" ({_id_decimal(pct)}%)" if pct is not None else ""
        parts.append(
            f"Jumlah varian berubah dari {_id_int(before)} menjadi {_id_int(after)} setelah QC; {_id_int(removed)} varian dikeluarkan{pct_text} sesuai filter yang diterapkan."
        )

    sample_before = _number_or_none(qc.get("samples_before"))
    sample_after = _number_or_none(qc.get("samples_after"))
    if sample_before is not None and sample_after is not None:
        sample_removed = _number_or_none(qc.get("samples_removed"))
        if sample_removed is None:
            sample_removed = max(0.0, sample_before - sample_after)
        sample_pct = (sample_removed / sample_before * 100.0) if sample_before > 0 else None
        sample_pct_text = f" ({_id_decimal(sample_pct)}% dari sampel awal)" if sample_pct is not None else ""
        parts.append(
            f"Jumlah sampel berubah dari {_id_int(sample_before)} menjadi {_id_int(sample_after)}; {_id_int(sample_removed)} sampel dikeluarkan{sample_pct_text}. Pengurangan sampel dapat mengurangi kekuatan analisis dan perlu diperiksa berdasarkan alasan filter serta kemungkinan perbedaan karakteristik sampel yang tersisa."
        )

    removed_by_filter = qc.get("removed_by_filter")
    if isinstance(removed_by_filter, dict) and removed_by_filter:
        filter_items = []
        for key, value in list(removed_by_filter.items())[:5]:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                filter_items.append(f"{key}: {_id_int(value)}")
            elif isinstance(value, dict):
                sub = ", ".join(f"{k}={_id_int(v)}" for k, v in list(value.items())[:3]
                                if isinstance(v, (int, float)) and not isinstance(v, bool))
                if sub:
                    filter_items.append(f"{key} ({sub})")
        if filter_items:
            parts.append("Catatan jumlah yang tercatat menurut filter: " + "; ".join(filter_items) + ".")

    metrics_after = qc.get("metrics_after") or {}
    missingness = metrics_after.get("variant_missingness") or {}
    if isinstance(missingness, dict) and missingness.get("available"):
        mean_missing = _number_or_none(missingness.get("mean"))
        max_missing = _number_or_none(missingness.get("max"))
        if mean_missing is not None:
            mean_pct = mean_missing * 100.0 if abs(mean_missing) <= 1 else mean_missing
            phrase = f"Rata-rata genotipe yang hilang per varian setelah QC sekitar {_id_decimal(mean_pct, 3)}%"
            if max_missing is not None:
                max_pct = max_missing * 100.0 if abs(max_missing) <= 1 else max_missing
                phrase += f", dengan nilai maksimum sekitar {_id_decimal(max_pct, 3)}%"
            parts.append(phrase + ". Angka yang ditampilkan dibulatkan; missingness yang lebih kecil berarti lebih sedikit data genotipe yang kosong pada ukuran ini.")

    maf = metrics_after.get("minor_allele_frequency") or {}
    if isinstance(maf, dict) and maf.get("available"):
        maf_mean = _number_or_none(maf.get("mean"))
        if maf_mean is not None:
            parts.append(
                f"Rata-rata minor allele frequency (MAF) setelah QC sekitar {_id_decimal(maf_mean, 3)}. MAF menunjukkan frekuensi alel yang lebih jarang; nilai rata-rata tidak menggantikan pemeriksaan distribusi tiap varian."
            )

    hwe = metrics_after.get("hardy_weinberg") or {}
    if isinstance(hwe, dict) and hwe.get("available"):
        reports = [hwe.get(key) or {} for key in ("autosomal", "chromosome_x")]
        n_hwe = sum(int(_number_or_none(report.get("n_records")) or 0) for report in reports)
        below = sum(int(_number_or_none(report.get("below_filter_threshold")) or 0) for report in reports)
        threshold = _number_or_none(hwe.get("filter_threshold"))
        if n_hwe:
            threshold_text = f" {_format_p(threshold)}" if threshold is not None else ""
            parts.append(
                f"Uji Hardy-Weinberg diringkas untuk {_id_int(n_hwe)} catatan; {_id_int(below)} berada di bawah ambang filter HWE{threshold_text}. Ini adalah pemeriksaan keseimbangan genotipe dan perlu ditafsirkan sesuai jenis sampel serta parameter QC."
            )

    warnings = qc.get("warnings")
    if isinstance(warnings, list) and warnings:
        warning_text = [str(item.get("message") or item.get("code") or item) if isinstance(item, dict) else str(item)
                        for item in warnings[:2]]
        parts.append("Peringatan QC: " + "; ".join(warning_text) + ".")
    else:
        parts.append("Untuk menilai QC secara menyeluruh, periksa ambang tiap filter serta alasan setiap varian atau sampel dikeluarkan.")
    return " ".join(parts)
'''

PCA_EXPLANATION = r'''def _backend_pca_explanation(payload: dict[str, Any]) -> str:
    """Explain PCA status and variance correctly; 'complete' is a successful state."""
    pca = payload.get("pca") or {}
    status = str(pca.get("status") or "tidak tersedia").strip()
    normalized_status = status.lower().replace(" ", "_")
    successful = normalized_status in {
        "done", "complete", "completed", "success", "succeeded", "ok", "finished"
    }
    actual = _number_or_none(pca.get("actual_components"))
    raw = pca.get("variance_explained") or []
    percentages = []
    if isinstance(raw, list):
        for value in raw[:10]:
            pct = _as_percent(value)
            if pct is not None:
                percentages.append(pct)

    if not successful:
        if percentages:
            return (
                f"PCA berstatus {status}. Sebagian persentase komponen tersedia, tetapi status belum menunjukkan proses selesai; "
                "anggap hasil ini sementara dan periksa log serta grafik PCA sebelum menafsirkan struktur populasi."
            )
        return (
            f"PCA berstatus {status}. PCA merangkum kemiripan dan perbedaan genotipe antar sampel; "
            "karena proses belum tercatat selesai, pola struktur populasi belum dapat disimpulkan."
        )

    if not percentages:
        return (
            f"PCA tercatat selesai dengan { _id_int(actual) if actual is not None else 'jumlah' } komponen yang dihitung, "
            "tetapi persentase variasi yang dijelaskan tidak tersedia. Grafik PC1–PC2 tetap diperlukan untuk melihat pengelompokan sampel."
        )

    pc1 = percentages[0]
    total = sum(percentages)
    component_count = len(percentages)
    component_text = _id_int(actual) if actual is not None else str(component_count)
    return (
        f"Principal Component Analysis (PCA) berhasil diselesaikan dengan {component_text} komponen yang tercatat. "
        f"PCA merangkum pola perbedaan genotipe antar sampel menjadi sumbu utama: PC1 menjelaskan sekitar {_id_decimal(pc1)}% variasi, "
        f"sedangkan {component_count} komponen yang tercantum bersama-sama menjelaskan sekitar {_id_decimal(total)}% variasi. "
        "Persentase ini menunjukkan banyaknya variasi genotipe yang diringkas oleh komponen, bukan nilai kelulusan atau skor kualitas data. "
        "Untuk mengetahui apakah sampel membentuk kelompok atau menunjukkan kemungkinan struktur populasi, periksa plot PC1 terhadap PC2 dan komponen berikutnya."
    )
'''

SAFE_SUMMARY_HELPER = r'''def _safe_ai_executive_summary(text: str, payload: dict[str, Any]) -> str:
    """Reject unsupported gene/causal claims from a small model and use a safe explanation."""
    candidate = str(text or "").strip()
    unsafe_patterns = (
        r"\bgen(?:-gen)?\b",
        r"\bgene(?:s)?\b",
        r"\bberkontribusi\b",
        r"\bmenyebabkan\b",
        r"\bpenyebab\b",
        r"\bfungsi biologis\b",
        r"\bjalur biologis\b",
        r"\brisiko penyakit\b",
    )
    if candidate and not any(re.search(pattern, candidate, flags=re.IGNORECASE) for pattern in unsafe_patterns):
        return candidate

    analysis = payload.get("analysis") or {}
    trait_name = str(analysis.get("trait_name") or "sifat (trait) yang diukur")
    model_name = str(analysis.get("model") or "model statistik yang digunakan")
    qq = payload.get("qq_diagnostics") or {}
    lambda_gc = _number_or_none(qq.get("lambda_gc"))
    if "Logistic" in model_name:
        model_explanation = (
            "Untuk model logistik, arah koefisien atau odds ratio menjelaskan perubahan odds pada kategori genotipe yang dikodekan; "
            "odds bukan probabilitas langsung."
        )
    else:
        model_explanation = (
            "Untuk regresi linear, tanda beta menjelaskan arah hubungan antara kode genotipe dan nilai trait yang diprediksi; "
            "beta negatif berarti nilai prediksi cenderung lebih rendah ketika kode genotipe meningkat, sesuai pengodean model."
        )
    diagnostic_caveat = (
        "Diagnostik juga menunjukkan lambda GC yang meningkat, sehingga p-value dan jumlah temuan perlu dibaca hati-hati sambil memeriksa kemungkinan struktur populasi, kekerabatan, faktor perancu, atau sifat poligenik."
        if lambda_gc is not None and lambda_gc >= 2.0 else
        "Hasil tetap perlu diperiksa bersama QC, PCA, dan QQ plot sebelum menyimpulkan bahwa asosiasi dapat direplikasi."
    )
    return (
        f"GWAS (Genome-Wide Association Study) adalah analisis yang mencari apakah perbedaan pada banyak posisi DNA, yang disebut varian atau SNP, "
        f"berkaitan secara statistik dengan perbedaan pada {trait_name}. Hasil ini menunjukkan pola asosiasi dalam sampel dan {model_name} yang digunakan; "
        "asosiasi berarti dua hal terlihat berkaitan dalam data; hasil ini sendiri belum membuktikan hubungan sebab-akibat. "
        f"{model_explanation} Satuan trait dan alel efek perlu diketahui sebelum besar atau arah efek diterjemahkan ke makna praktis. {diagnostic_caveat}"
    )
'''

COMPOSER = r'''def compose_interpretation(narrative_raw: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """Compose a beginner-friendly report while retaining source-derived numeric facts."""
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
            f"Dari {_id_int(n_tested)} varian DNA yang diuji, {_id_int(n_sig)} memenuhi "
            f"aturan signifikansi ({method})."
        )
    else:
        count_text = (
            f"Dari {_id_int(n_tested)} varian DNA yang diuji, tidak ada yang memenuhi "
            f"aturan signifikansi ({method})."
        )
    if n_samples is not None:
        count_text += f" Analisis menggunakan {_id_int(n_samples)} sampel setelah tahapan pemrosesan yang tercatat."
    if len(findings) == 5:
        count_text += " Lima SNP yang dibahas merupakan lima varian teratas, bukan jumlah seluruh varian signifikan."
    elif findings:
        count_text += f" Sebanyak {len(findings)} varian teratas ditampilkan untuk penjelasan individual."
    if threshold is not None:
        count_text += f" Ambang p-value yang diterapkan adalah {_format_p(threshold)}."

    safe_summary = _safe_ai_executive_summary(narrative.executive_summary, payload)
    executive = count_text + "\n\n" + safe_summary

    lambda_gc = _number_or_none((payload.get("qq_diagnostics") or {}).get("lambda_gc"))
    limitations = [
        "Asosiasi statistik bukan bukti sebab-akibat. Lima SNP teratas juga belum tentu lima sinyal yang saling independen; varian yang berdekatan dapat berkorelasi karena linkage disequilibrium (LD), dan analisis LD/conditional belum tercantum dalam ringkasan ini.",
        "Arah dan besar beta bergantung pada pengodean genotipe, alel efek, serta skala trait. Nama trait tersedia bila tercatat, tetapi satuan atau standardisasi trait dan alel efek tetap perlu dipastikan sebelum menjelaskan besar efek secara praktis.",
    ]
    if lambda_gc is not None and lambda_gc > 1.1:
        limitations.append(
            "Lambda GC yang meningkat dapat memengaruhi kalibrasi p-value. Karena itu, jumlah varian yang melewati ambang harus dipandang sebagai temuan statistik awal sampai inflasi dan kemungkinan perancu dievaluasi."
        )
    else:
        limitations.append(
            "Penilaian dibatasi pada ringkasan statistik dan diagnostik yang tersedia; plot, asumsi model, dan replikasi independen tetap perlu diperiksa."
        )

    recommendations = [
        "Periksa QQ plot dan lambda GC, plot PC1–PC2, kekerabatan sampel, serta kovariat model. Jika struktur populasi atau kekerabatan terdeteksi, evaluasi apakah penyesuaian model sudah memadai.",
        "Periksa alasan sampel dan varian dikeluarkan pada setiap filter QC. Pastikan pengurangan sampel tidak menimbulkan bias seleksi atau ketidakseimbangan kelompok.",
        "Lengkapi metadata nama trait, satuan/standardisasi, dan alel efek; periksa LD atau lakukan analisis kondisional sebelum menyebut lima SNP sebagai sinyal independen, lalu validasi pada dataset lain bila tersedia.",
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
    """Compact Ollama provider; backend validates and composes source-linked report facts."""

    def generate(self, payload: dict, timeout_seconds: float | None = None) -> tuple[dict, dict | None]:
        settings = get_settings()
        if not settings.ai_api_key:
            raise RuntimeError("AI_NOT_CONFIGURED")
        base = settings.ai_api_base_url.rstrip("/")
        url = base if base.endswith("/chat/completions") else base + "/chat/completions"
        context = _narrative_context(payload)
        schema = _narrative_json_schema()
        system_prompt = (
            "Anda menjelaskan hasil GWAS kepada pembaca awam dalam Bahasa Indonesia. "
            "SNP adalah posisi/varian DNA; jangan menyebutnya sebagai gen. "
            "Jelaskan asosiasi statistik, bukan sebab-akibat, fungsi biologis, atau risiko penyakit. "
            "Jangan mengarang nama trait, satuan, angka, alel, atau temuan. "
            "Gunakan nama trait hanya jika tersedia dalam DATA. Kembalikan satu objek JSON sesuai schema."
        )
        user_prompt = (
            "Tuliskan satu paragraf 4-5 kalimat untuk menjelaskan secara sederhana apa yang dicari GWAS, "
            "apa arti asosiasi antara varian DNA dan trait, serta bagaimana arah pola efek harus dipahami. "
            "Terangkan bahwa asosiasi bukan sebab-akibat. Jangan menulis angka, ID SNP, p-value, beta, "
            "klaim gen, fungsi biologis, atau akibat klinis; backend akan menambahkan statistik dan penjelasan tiap bagian. "
            "Hindari kalimat umum yang menyatakan data baik/stabil tanpa bukti. DATA:\n" +
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
                    "name": "genosphere_v8_plain_language_summary",
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
    _backend_pca_explanation,
    _safe_ai_executive_summary,
    compose_interpretation,
    validate_interpretation,
)


def _payload():
    return {
        "analysis": {
            "model": "Linear Regression",
            "trait_name": "Trait_1",
            "trait_type": "quantitative",
            "trait_scale": "quantitative",
            "n_samples_analyzed": 1410,
            "n_snps_tested": 9282,
            "n_significant_snps": 163,
            "significance_method": "genome_wide",
            "significance_threshold_p": 5e-8,
        },
        "top_snps": [
            {"evidence_ref": f"top_snps[{i}]", "snp_ref": f"rs{i+1}",
             "p_value": (i + 1) * 1e-35, "effect_size": -0.45 + i * 0.0001,
             "odds_ratio": None, "q_value": None, "is_significant": True}
            for i in range(5)
        ],
        "qc": {
            "variants_before": 12226, "variants_after": 10300,
            "variants_removed": 1926, "variants_removed_percent": 15.75,
            "samples_before": 1940, "samples_after": 1410, "samples_removed": 530,
            "metrics_after": {
                "variant_missingness": {"available": True, "mean": 0.0, "max": 0.0},
                "hardy_weinberg": {"available": True, "autosomal": {"n_records": 10300, "below_filter_threshold": 0}, "filter_threshold": 1e-6},
            },
        },
        "pca": {
            "status": "complete", "actual_components": 5,
            "variance_explained": [0.048759688, 0.040189863, 0.036253296, 0.022716985, 0.019790319],
        },
        "qq_diagnostics": {"lambda_gc": 4.3859, "n": 9282, "displayed_variants": 4428, "downsampled": True},
        "source_refs": ["analysis", "qc", "pca", "qq_diagnostics", "top_snps"],
    }


def test_pca_complete_status_is_treated_as_success_and_reports_cumulative_variance():
    text = _backend_pca_explanation(_payload())
    assert "berhasil diselesaikan" in text
    assert "4,88%" in text
    assert "16,77%" in text
    assert "belum tercatat selesai" not in text


def test_unsafe_gene_or_causal_ai_summary_uses_safe_fallback():
    payload = _payload()
    unsafe = "Gen-gen tertentu berkontribusi pada penurunan trait yang diukur. " * 3
    result = _safe_ai_executive_summary(unsafe, payload)
    assert "gen-gen tertentu" not in result.lower()
    assert "menyebabkan" not in result.lower()
    assert "Trait_1" in result
    assert "SNP" in result


def test_composer_explains_sample_removal_and_source_statistics():
    payload = _payload()
    narrative = {
        "executive_summary": (
            "GWAS memeriksa apakah variasi pada posisi DNA berkaitan dengan perbedaan sifat. "
            "Temuan merupakan asosiasi statistik dalam model yang digunakan, bukan bukti sebab-akibat. "
            "Arah efek mengikuti pengodean genotipe pada model. "
            "Hasil perlu diperiksa bersama QC, PCA, dan QQ plot sebelum ditafsirkan lebih jauh."
        )
    }
    result = compose_interpretation(narrative, payload)
    assert "163" in result["executive_summary"]
    assert "530 sampel dikeluarkan" in result["qc_assessment"]
    assert "27,32%" in result["qc_assessment"]
    assert "16,77%" in result["pca_assessment"]
    assert "4,386" in result["diagnostics_assessment"]
    validated = validate_interpretation(result, payload)
    assert validated["statistical_conclusion"]["n_significant_snps"] == 163
'''


def main() -> None:
    required = (AI_FILE, ROOT / "app/services/ai_jobs.py", ENV_FILE)
    if not all(path.is_file() for path in required):
        fail("Jalankan dari root /mnt/d/GenoSphere/backend; source AI, AI jobs, dan .env harus tersedia.")

    original_ai = AI_FILE.read_text(encoding="utf-8")
    jobs_text = (ROOT / "app/services/ai_jobs.py").read_text(encoding="utf-8")
    env_text = (ROOT / ".env").read_text(encoding="utf-8")

    if 'PROMPT_VERSION = "statistical-narrative-v7-compact-hybrid"' not in original_ai:
        fail("Source bukan AI v7 compact-hybrid. Tidak ada file yang diubah.")
    if "def _narrative_context(payload: dict[str, Any]) -> dict[str, Any]:" not in original_ai:
        fail("Fungsi _narrative_context tidak ditemukan.")
    if "def _backend_pca_explanation(payload: dict[str, Any]) -> str:" not in original_ai:
        fail("Fungsi PCA v7 tidak ditemukan.")
    if "def compose_interpretation(narrative_raw: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:" not in original_ai:
        fail("Composer v7 tidak ditemukan.")

    updated = original_ai.replace(
        'PROMPT_VERSION = "statistical-narrative-v7-compact-hybrid"',
        'PROMPT_VERSION = "statistical-narrative-v8-plain-language-verified"',
        1,
    )

    # The run metadata already contains cfg['trait_name']; expose it in the de-identified AI payload.
    # Match within build_ai_payload and allow normal formatting across multiple lines.
    builder_start = updated.find("def build_ai_payload(")
    builder_end = updated.find("\ndef payload_hash(", builder_start)
    if builder_start < 0 or builder_end < 0:
        fail("Batas fungsi build_ai_payload tidak ditemukan; tidak ada file yang diubah.")
    builder = updated[builder_start:builder_end]
    trait_name_field = '"trait_name": cfg.get("trait_name")'
    if trait_name_field not in builder:
        payload_pattern = re.compile(
            r'("analysis"\s*:\s*\{\s*"model"\s*:\s*analysis\.model_used\s*,\s*)'
            r'("trait_type"\s*:\s*cfg\.get\("trait_type"\))',
            re.DOTALL,
        )
        builder_updated, replacements = payload_pattern.subn(
            r'\1"trait_name": cfg.get("trait_name"), \2', builder, count=1
        )
        if replacements != 1:
            fail(
                "Pola field model/trait_type pada build_ai_payload tidak ditemukan. "
                "Tidak ada file yang diubah. Jalankan sed -n '85,115p' app/services/ai_interpretation.py untuk pemeriksaan."
            )
        updated = updated[:builder_start] + builder_updated + updated[builder_end:]

    updated = replace_between(
        updated,
        "def _narrative_context(payload: dict[str, Any]) -> dict[str, Any]:",
        "def _narrative_json_schema(",
        NARRATIVE_CONTEXT,
        "narrative context with trait name",
    )
    updated = replace_between(
        updated,
        "def _backend_qc_explanation(payload: dict[str, Any]) -> str:",
        "def _backend_pca_explanation(payload: dict[str, Any]) -> str:",
        QC_EXPLANATION,
        "plain-language QC explanation",
    )
    updated = replace_between(
        updated,
        "def _backend_pca_explanation(payload: dict[str, Any]) -> str:",
        "def _backend_diagnostics_explanation(payload: dict[str, Any]) -> str:",
        PCA_EXPLANATION,
        "PCA complete status and variance explanation",
    )
    updated = replace_between(
        updated,
        "def compose_interpretation(narrative_raw: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:",
        "def validate_interpretation(raw: dict, payload: dict) -> dict:",
        SAFE_SUMMARY_HELPER + "\n\n" + COMPOSER,
        "safe AI summary and report composer",
    )

    provider_start = updated.find("class OpenAICompatibleProvider:")
    if provider_start < 0:
        fail("Provider OpenAI-compatible tidak ditemukan.")
    updated = updated[:provider_start] + PROVIDER.rstrip() + "\n"
    if "def get_provider(name: str):" not in updated:
        fail("Factory get_provider hilang setelah provider replacement.")

    try:
        ast.parse(updated)
        ast.parse(jobs_text)
    except SyntaxError as exc:
        fail(f"Syntax check gagal: {exc}")

    BACKUP.mkdir(parents=True, exist_ok=False)
    shutil.copy2(AI_FILE, BACKUP / "ai_interpretation.py")
    shutil.copy2(ROOT / ".env", BACKUP / ".env")
    if TEST_FILE.exists():
        shutil.copy2(TEST_FILE, BACKUP / TEST_FILE.name)

    try:
        AI_FILE.write_text(updated, encoding="utf-8", newline="\n")
        TEST_FILE.write_text(TESTS, encoding="utf-8", newline="\n")
    except Exception as exc:
        shutil.copy2(BACKUP / "ai_interpretation.py", AI_FILE)
        fail(f"Penerapan gagal dan ai_interpretation.py dipulihkan: {type(exc).__name__}: {exc}")

    print("[OK] AI prompt version v8 diterapkan.")
    print("[OK] Nama trait dari run_meta.config kini tersedia pada context AI.")
    print("[OK] Status PCA 'complete' dikenali; PC1 dan kumulatif variasi dijelaskan.")
    print("[OK] QC menjelaskan jumlah serta persentase sampel yang dikeluarkan dan alasan filter bila tersedia.")
    print("[OK] Klaim gen/kausal yang tidak didukung pada paragraf AI diganti dengan ringkasan aman.")
    print("[OK] Statistik SNP, jumlah signifikan, dan evidence tetap dibentuk dari sumber backend.")
    print(f"[OK] Backup: {BACKUP}")
    print("[NEXT] python -m py_compile app/services/ai_interpretation.py")
    print("[NEXT] python -m pytest -q tests/test_ai_interpretation_plain_language_v8.py tests/test_ai_interpretation_composer.py tests/test_revision_features.py tests/test_config.py")


if __name__ == "__main__":
    main()
