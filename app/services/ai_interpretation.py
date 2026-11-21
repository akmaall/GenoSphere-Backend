"""Fast, source-grounded statistical interpretation for GenoSphere GWAS."""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Accession, GwasAnalysis
from app.services import results, storage

PROMPT_VERSION = "statistical-narrative-v8-plain-language-verified"
SCHEMA_VERSION = "1.0"


class FindingStatistics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    p_value: float | None = None
    effect_size: float | None = None
    odds_ratio: float | None = None
    q_value: float | None = None


class InterpretationFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=3, max_length=180)
    interpretation: str = Field(min_length=5, max_length=1200)
    snp_ref: str | None = None
    evidence_refs: list[str] = Field(min_length=1, max_length=20)
    statistics: FindingStatistics = Field(default_factory=FindingStatistics)


class StatisticalConclusion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    significant_snps_detected: bool
    n_significant_snps: int = Field(ge=0)
    statement: str = Field(min_length=5, max_length=1000)


class InterpretationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    executive_summary: str = Field(min_length=10, max_length=2000)
    statistical_conclusion: StatisticalConclusion
    findings: list[InterpretationFinding] = Field(default_factory=list, max_length=20)
    qc_assessment: str = Field(max_length=1600)
    pca_assessment: str = Field(max_length=1200)
    diagnostics_assessment: str = Field(max_length=1600)
    limitations: list[str] = Field(default_factory=list, max_length=20)
    recommendations: list[str] = Field(default_factory=list, max_length=15)
    evidence_refs: list[str] = Field(default_factory=list, max_length=50)


class AIStatisticalNarrative(BaseModel):
    """One concise AI synthesis; numeric evidence is composed by the backend."""
    model_config = ConfigDict(extra="forbid")
    executive_summary: str = Field(min_length=100, max_length=900)


class AIResponseValidationError(ValueError):
    pass

def _run_dir(db: Session, analysis: GwasAnalysis):
    acc = db.get(Accession, analysis.accession_id)
    if acc is None:
        raise ValueError("Accession analisis GWAS tidak ditemukan.")
    ddir = storage.dataset_dir(storage.dataset_folder(acc.genotype_file_path))
    return storage.run_dir(ddir, analysis.analysis_id)


def build_ai_payload(db: Session, analysis: GwasAnalysis) -> dict[str, Any]:
    """Build de-identified, source-grounded statistics; never include per-sample data."""
    if analysis.status != "done":
        raise ValueError("AI payload hanya boleh dibuat setelah status GWAS done.")

    rdir = _run_dir(db, analysis)
    meta = json.loads((rdir / "run_meta.json").read_text(encoding="utf-8"))
    cfg = meta.get("config", {})
    settings = get_settings()
    top_limit = max(1, min(10, settings.ai_top_snp_count))
    rule = results.load_rule(rdir) or meta.get("significance")
    is_logistic = analysis.model_used == "Logistic Regression"
    top = results.top(db, analysis.analysis_id, top_limit, rule, is_logistic)
    summary = results.summary(db, analysis.analysis_id, rule, is_logistic)
    qq = results.qq(db, analysis.analysis_id, max_points=5000)
    qc = meta.get("qc_summary") or {}
    pca = meta.get("pca_summary") or {}

    snps: list[dict[str, Any]] = []
    for item in top.get("items", []):
        chrom = str(item.get("chromosome") or "")
        pos = item.get("position")
        rsid = item.get("rsid")
        snp_ref = str(rsid) if rsid else f"{chrom}:{pos}:{item.get('ref_allele')}>{item.get('alt_allele')}"
        snps.append({
            "evidence_ref": f"top_snps[{len(snps)}]",
            "snp_ref": snp_ref,
            "chromosome": chrom,
            "position": pos,
            "p_value": item.get("p_value"),
            "effect_size": item.get("effect_size"),
            "odds_ratio": item.get("odds_ratio"),
            "q_value": item.get("q_value"),
            "is_significant": bool(item.get("is_significant")),
        })

    # Software versions and run timestamps stay in run_meta; they do not help the model interpret GWAS.
    return {
        "schema_version": SCHEMA_VERSION,
        "analysis": {
            "model": analysis.model_used,
            "trait_name": cfg.get("trait_name"), "trait_type": cfg.get("trait_type"),
            "trait_scale": "binary" if cfg.get("trait_type") == "binary" else "quantitative",
            "n_samples_analyzed": meta.get("n_samples"),
            "n_snps_tested": summary.get("n_variants_tested"),
            "n_significant_snps": summary.get("n_significant"),
            "significance_method": (rule or {}).get("method"),
            "significance_threshold_p": (rule or {}).get("threshold_p"),
            "significance_threshold_q": (rule or {}).get("q"),
        },
        "top_snps": snps,
        "qc": {
            key: qc.get(key) for key in (
                "skipped", "parameters", "variants_before", "variants_after",
                "variants_removed", "variants_removed_percent", "samples_before",
                "samples_after", "samples_removed", "removed_by_filter",
                "metrics_before", "metrics_after", "warnings",
            )
        },
        "pca": {
            key: pca.get(key) for key in (
                "requested_components", "actual_components", "status", "warnings",
                "variance_explained", "cumulative_variance_explained",
            )
        },
        "qq_diagnostics": {
            "lambda_gc": qq.get("lambda_gc"),
            "n": qq.get("n"),
            "displayed_variants": qq.get("displayed_variants"),
            "downsampled": qq.get("filtered"),
        },
        "source_refs": ["analysis", "qc", "pca", "qq_diagnostics", "top_snps"],
    }


def payload_hash(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _number_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _number_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _flatten_numeric_metrics(value: Any, prefix: str = "", depth: int = 0) -> list[tuple[str, float]]:
    found: list[tuple[str, float]] = []
    if depth > 3:
        return found
    if isinstance(value, dict):
        for key, child in value.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            found.extend(_flatten_numeric_metrics(child, name, depth + 1))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        number = _number_or_none(value)
        if number is not None:
            found.append((prefix[:100], number))
    return found


def _compact_metric_map(value: Any, limit: int = 6) -> dict[str, float]:
    leaves = _flatten_numeric_metrics(value)
    priority = ("missing", "maf", "hwe", "call", "geno", "mind", "sample", "variant", "rate")
    leaves.sort(key=lambda pair: (
        not any(term in pair[0].lower() for term in priority),
        pair[0].lower(),
    ))
    return {key: val for key, val in leaves[:limit]}


def _as_percent(value: Any) -> float | None:
    number = _number_or_none(value)
    if number is None:
        return None
    return number * 100.0 if abs(number) <= 1.0 else number


def _narrative_context(payload: dict[str, Any]) -> dict[str, Any]:
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

def _pydantic_error_summary(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors(include_url=False, include_input=False, include_context=False)[:8]:
        location = ".".join(str(piece) for piece in error.get("loc", ())) or "root"
        parts.append(f"{location}:{error.get('type', 'invalid')}")
    return "; ".join(parts) or "schema_validation_failed"


def _format_p(value: Any) -> str:
    number = _number_or_none(value)
    return f"{number:.3g}".replace(".", ",") if number is not None else "tidak tersedia"


def _id_int(value: Any) -> str:
    number = _number_or_none(value)
    return f"{int(number):,}".replace(",", ".") if number is not None else "tidak tersedia"


def _id_decimal(value: Any, digits: int = 2) -> str:
    number = _number_or_none(value)
    return f"{number:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".") if number is not None else "tidak tersedia"


def _snp_evidence_explainer(snp: dict[str, Any], model_name: str, threshold_p: Any) -> str:
    p_value = _number_or_none(snp.get("p_value"))
    beta = _number_or_none(snp.get("effect_size"))
    odds = _number_or_none(snp.get("odds_ratio"))
    significant = bool(snp.get("is_significant"))
    p_text = _format_p(p_value)
    if "Logistic" in model_name:
        if odds is not None and odds > 1:
            direction = "Odds ratio di atas 1 berarti odds outcome lebih tinggi pada kategori genotipe yang dikodekan sebagai efek."
        elif odds is not None and odds < 1:
            direction = "Odds ratio di bawah 1 berarti odds outcome lebih rendah pada kategori genotipe yang dikodekan sebagai efek."
        else:
            direction = "Arah odds tidak dapat ditentukan dari nilai yang tersedia."
        effect = f"Odds ratio = {odds:.4g}. " if odds is not None else (f"Koefisien log-odds = {beta:.4g}. " if beta is not None else "")
        unit_caveat = "Odds bukan probabilitas langsung; besar perubahan probabilitas bergantung pada risiko dasar dan kovariat."
    else:
        if beta is not None and beta < 0:
            direction = "Beta negatif menunjukkan arah asosiasi menuju nilai trait yang lebih rendah menurut pengodean genotipe pada model."
        elif beta is not None and beta > 0:
            direction = "Beta positif menunjukkan arah asosiasi menuju nilai trait yang lebih tinggi menurut pengodean genotipe pada model."
        else:
            direction = "Arah asosiasi tidak dapat ditentukan karena estimasi beta tidak tersedia atau bernilai nol."
        effect = f"Estimasi beta = {beta:.4g}. " if beta is not None else ""
        unit_caveat = "Satuan asli dan standardisasi trait tidak tercantum di ringkasan, sehingga besar efek praktis belum dapat diterjemahkan."
    significance = (
        f"P-value = {p_text}; varian ini ditandai signifikan oleh aturan analisis yang digunakan."
        if significant else
        f"P-value = {p_text}; varian ini tidak ditandai signifikan oleh aturan analisis yang digunakan."
    )
    if p_value is not None and p_value < 1e-10:
        evidence = "Nilai p yang sangat kecil merupakan bukti statistik kuat terhadap hipotesis tidak ada asosiasi, dengan asumsi model terpenuhi."
    elif p_value is not None:
        evidence = "P-value mengukur kesesuaian data dengan hipotesis tidak ada asosiasi; nilai ini tidak membuktikan hubungan sebab-akibat antara varian dan trait."
    else:
        evidence = "Kekuatan bukti statistik tidak dapat dijelaskan karena p-value tidak tersedia."
    return " ".join(x for x in (direction, effect.replace(".", ",", 1) + significance, evidence, unit_caveat) if x)


def _backend_qc_explanation(payload: dict[str, Any]) -> str:
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

def _backend_pca_explanation(payload: dict[str, Any]) -> str:
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

def _backend_diagnostics_explanation(payload: dict[str, Any]) -> str:
    qq = payload.get("qq_diagnostics") or {}
    lam = _number_or_none(qq.get("lambda_gc"))
    if lam is None:
        return "QQ plot membandingkan p-value yang diamati dengan p-value yang diharapkan jika tidak ada asosiasi. Lambda GC tidak tersedia, sehingga tingkat inflasi statistik belum dapat diringkas dengan angka; periksa QQ plot secara langsung."
    if lam >= 2.0:
        return (
            f"Lambda GC sekitar {_id_decimal(lam, 3)}, jauh di atas nilai acuan sekitar 1. Ini menunjukkan statistik uji tampak mengalami inflasi yang kuat dibandingkan ekspektasi nol; p-value perlu ditafsirkan dengan hati-hati. "
            "Inflasi dapat berkaitan dengan stratifikasi populasi, kekerabatan sampel, faktor perancu yang belum dimodelkan, sinyal poligenik, atau artefak teknis. Lambda GC saja tidak dapat menentukan penyebabnya; periksa QQ plot, PCA, kovariat, dan struktur kekerabatan."
        )
    if lam > 1.1:
        return (
            f"Lambda GC sekitar {_id_decimal(lam, 3)}, lebih tinggi dari nilai acuan sekitar 1 dan mengindikasikan kemungkinan inflasi statistik. "
            "Hal ini dapat dipengaruhi stratifikasi populasi, kekerabatan, faktor perancu, atau sifat poligenik trait. Nilai lambda tidak cukup untuk menentukan penyebab; periksa QQ plot dan kovariat model."
        )
    if lam < 0.9:
        return f"Lambda GC sekitar {_id_decimal(lam, 3)}, di bawah nilai acuan sekitar 1 dan dapat menunjukkan statistik yang konservatif. Periksa QQ plot serta kekuatan uji sebelum menarik kesimpulan."
    return f"Lambda GC sekitar {_id_decimal(lam, 3)}, relatif dekat dengan nilai acuan sekitar 1. Hal ini tidak otomatis menjamin bebas perancu; QQ plot, PCA, kekerabatan, dan kovariat tetap perlu ditinjau."



def _backend_qc_facts(payload: dict[str, Any]) -> str:
    qc = payload.get("qc") or {}
    before = _number_or_none(qc.get("variants_before"))
    after = _number_or_none(qc.get("variants_after"))
    removed = _number_or_none(qc.get("variants_removed"))
    pct = _number_or_none(qc.get("variants_removed_percent"))
    sample_before = _number_or_none(qc.get("samples_before"))
    sample_after = _number_or_none(qc.get("samples_after"))
    if qc.get("skipped"):
        return "Catatan sumber: tahap QC tercatat dilewati."
    pieces = []
    if before is not None and after is not None:
        removed = removed if removed is not None else max(0.0, before - after)
        if pct is None and before > 0:
            pct = removed / before * 100.0
        pct_text = f" ({_id_decimal(pct)}%)" if pct is not None else ""
        pieces.append(f"varian: {_id_int(before)} sebelum QC, {_id_int(after)} sesudah QC, {_id_int(removed)} dikeluarkan{pct_text}")
    if sample_before is not None and sample_after is not None:
        sample_removed = _number_or_none(qc.get("samples_removed"))
        pieces.append(f"sampel: {_id_int(sample_before)} sebelum dan {_id_int(sample_after)} sesudah QC; {_id_int(sample_removed or 0)} dikeluarkan")
    return "Catatan sumber: " + "; ".join(pieces) + "." if pieces else "Catatan sumber: jumlah varian/sampel sebelum dan sesudah QC tidak lengkap."


def _backend_pca_facts(payload: dict[str, Any]) -> str:
    pca = payload.get("pca") or {}
    raw = pca.get("variance_explained") or []
    percentages = []
    if isinstance(raw, list):
        for value in raw[:10]:
            pct = _as_percent(value)
            if pct is not None:
                percentages.append(pct)
    if not percentages:
        return "Angka sumber PCA: persentase variasi yang dijelaskan tidak tersedia."
    return (f"Angka sumber PCA: PC1 menjelaskan {_id_decimal(percentages[0])}% variasi; "
            f"{len(percentages)} komponen yang tercantum menjelaskan {_id_decimal(sum(percentages))}% secara kumulatif.")

def _fallback_snp_interpretation(snp: dict[str, Any], model_name: str, threshold_p: Any) -> str:
    return _snp_evidence_explainer(snp, model_name, threshold_p)


def _default_narrative_text(payload: dict[str, Any], field: str) -> str:
    if field == "executive_summary":
        analysis = payload.get("analysis") or {}
        n_sig = int(analysis.get("n_significant_snps") or 0)
        n_tested = int(analysis.get("n_snps_tested") or 0)
        top_count = len(payload.get("top_snps") or [])
        return (f"Analisis menguji {n_tested:,} varian dan menemukan {n_sig:,} varian yang memenuhi aturan signifikansi. "
                f"Sistem menampilkan {top_count} varian teratas untuk dibahas satu per satu; jumlah yang ditampilkan bukan jumlah total temuan signifikan. "
                "Temuan ini menunjukkan asosiasi statistik, bukan bukti bahwa varian tersebut menyebabkan perubahan trait.")
    if field == "qc_assessment":
        return _backend_qc_explanation(payload)
    if field == "pca_assessment":
        return _backend_pca_explanation(payload)
    if field == "diagnostics_assessment":
        return _backend_diagnostics_explanation(payload)
    raise ValueError(f"Unsupported narrative field: {field}")


def _safe_ai_executive_summary(text: str, payload: dict[str, Any]) -> str:
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


def compose_interpretation(narrative_raw: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
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

def validate_interpretation(raw: dict, payload: dict) -> dict:
    try:
        parsed = InterpretationResult.model_validate(raw)
    except ValidationError as exc:
        raise AIResponseValidationError("Struktur JSON interpretasi AI tidak sesuai schema.") from exc

    data = parsed.model_dump()
    expected_n = int(payload["analysis"].get("n_significant_snps") or 0)
    conclusion = data["statistical_conclusion"]
    if conclusion["n_significant_snps"] != expected_n or conclusion["significant_snps_detected"] != (expected_n > 0):
        raise AIResponseValidationError("Kesimpulan jumlah SNP signifikan bertentangan dengan hasil GWAS sumber.")

    top = {x["snp_ref"]: x for x in payload["top_snps"]}
    allowed_refs = {x["evidence_ref"] for x in payload["top_snps"]} | set(payload["source_refs"])
    seen_snp_refs: set[str] = set()
    for finding in data["findings"]:
        snp_ref = finding.get("snp_ref")
        if snp_ref and snp_ref not in top:
            raise AIResponseValidationError("Interpretasi menyebut SNP yang tidak ada pada Top SNP payload.")
        if snp_ref and snp_ref in seen_snp_refs:
            raise AIResponseValidationError("Interpretasi memuat SNP duplikat.")
        if snp_ref:
            seen_snp_refs.add(snp_ref)
        refs = finding.get("evidence_refs", [])
        if any(ref not in allowed_refs for ref in refs):
            raise AIResponseValidationError("Interpretasi menggunakan evidence_refs yang tidak tersedia pada payload.")
        snp = top.get(snp_ref)
        claimed_values = [finding["statistics"].get(field) for field in ("p_value", "effect_size", "odds_ratio", "q_value")]
        if any(value is not None for value in claimed_values) and snp is None:
            raise AIResponseValidationError("Temuan dengan statistik SNP wajib merujuk SNP yang tersedia pada Top SNP payload.")
        if snp:
            for field in ("p_value", "effect_size", "odds_ratio", "q_value"):
                claimed = finding["statistics"].get(field)
                source = _number_or_none(snp.get(field))
                if claimed is not None and (source is None or abs(float(claimed) - source) > max(1e-12, abs(source) * 1e-6)):
                    raise AIResponseValidationError(f"Nilai {field} pada interpretasi tidak cocok dengan sumber untuk {snp_ref}.")

    if any(ref not in allowed_refs for ref in data["evidence_refs"]):
        raise AIResponseValidationError("Daftar evidence_refs utama memuat referensi yang tidak tersedia.")

    forbidden = re.compile(
        r"\b(caus(e|es|al)|causal|menyebabkan|penyebab|risiko penyakit|disease risk|gene function|fungsi gen|pathway|jalur biologis|pathogenic|patogenik)\b",
        re.I,
    )
    if forbidden.search(json.dumps(data, ensure_ascii=False)):
        raise AIResponseValidationError(
            "Interpretasi memuat klaim kausal, risiko penyakit, atau fungsi biologis di luar ruang lingkup statistik."
        )
    return data


class OpenAICompatibleProvider:
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
