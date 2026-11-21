"""Regression tests for the backend revision decisions."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas_gwas import PcaRequest
from app.services.ai_interpretation import AIResponseValidationError, validate_interpretation
from app.services.gwas_engine import _compute_pca_python, _summarize_plink_metrics
from app.services.resource_policy import upload_budget


def test_pca_request_accepts_only_5_10_20():
    for value in (5, 10, 20):
        assert PcaRequest(pca_components=value).pca_components == value
    for value in (2, 3, 7, 21):
        with pytest.raises(ValidationError):
            PcaRequest(pca_components=value)


def test_pca_request_accepts_frontend_label_but_not_two_pc():
    assert PcaRequest.model_validate({"pca_components": "10 PC"}).pca_components == 10
    with pytest.raises((ValidationError, ValueError)):
        PcaRequest.model_validate({"pca_components": "2 PC"})


def _valid_ai_payload():
    return {
        "analysis": {"n_significant_snps": 1},
        "top_snps": [{
            "evidence_ref": "top_snps[0]", "snp_ref": "rs-test-1", "p_value": 1e-9,
            "effect_size": 0.125, "odds_ratio": None, "q_value": None,
        }],
        "source_refs": ["analysis", "qc", "pca", "qq_diagnostics"],
    }


def _valid_ai_result():
    return {
        "executive_summary": "Satu SNP memenuhi ambang signifikansi yang ditetapkan.",
        "statistical_conclusion": {
            "significant_snps_detected": True,
            "n_significant_snps": 1,
            "statement": "Terdapat satu SNP signifikan menurut ambang analisis.",
        },
        "findings": [{
            "title": "Asosiasi statistik rs-test-1",
            "interpretation": "Nilai p menunjukkan bukti asosiasi statistik pada model yang digunakan.",
            "snp_ref": "rs-test-1", "evidence_refs": ["top_snps[0]"],
            "statistics": {"p_value": 1e-9, "effect_size": 0.125},
        }],
        "qc_assessment": "Ringkasan QC tersedia dari pipeline.",
        "pca_assessment": "PCA menghasilkan komponen sesuai metadata.",
        "diagnostics_assessment": "Diagnostik QQ perlu dibaca bersama ukuran sampel.",
        "limitations": ["Interpretasi dibatasi pada bukti statistik yang tersedia."],
        "recommendations": ["Periksa kembali konsistensi model dan ambang statistik."],
        "evidence_refs": ["analysis", "top_snps[0]"],
    }


def test_ai_response_validation_accepts_source_supported_finding():
    result = validate_interpretation(_valid_ai_result(), _valid_ai_payload())
    assert result["findings"][0]["snp_ref"] == "rs-test-1"


def test_ai_response_validation_rejects_fabricated_snp():
    result = _valid_ai_result()
    result["findings"][0]["snp_ref"] = "rs-fabricated"
    with pytest.raises(AIResponseValidationError, match="SNP"):
        validate_interpretation(result, _valid_ai_payload())


def test_ai_response_validation_rejects_mismatched_statistics():
    result = _valid_ai_result()
    result["findings"][0]["statistics"]["p_value"] = 0.25
    with pytest.raises(AIResponseValidationError, match="p_value"):
        validate_interpretation(result, _valid_ai_payload())


def test_ai_response_validation_rejects_biological_or_causal_scope():
    result = _valid_ai_result()
    result["executive_summary"] = "Varian ini menyebabkan penyakit dan meningkatkan risiko penyakit."
    with pytest.raises(AIResponseValidationError, match="kausal"):
        validate_interpretation(result, _valid_ai_payload())


def _write_bed(path: Path, matrix: list[list[int]]) -> None:
    """Write a small variant-major PLINK BED with 0/1/2 dosages and no missing calls."""
    n_samples = len(matrix)
    n_variants = len(matrix[0])
    encoded = bytearray(b"\x6c\x1b\x01")
    for variant_index in range(n_variants):
        for start in range(0, n_samples, 4):
            byte = 0
            for offset in range(4):
                sample_index = start + offset
                if sample_index >= n_samples:
                    break
                dosage = matrix[sample_index][variant_index]
                code = {0: 0b00, 1: 0b10, 2: 0b11}[dosage]
                byte |= code << (offset * 2)
            encoded.append(byte)
    path.write_bytes(encoded)


def test_python_pca_fallback_streams_bed_and_writes_actual_components(tmp_path, monkeypatch):
    import app.services.gwas_engine as engine

    matrix = [
        [0, 0, 2, 0, 1],
        [0, 1, 0, 2, 0],
        [0, 2, 1, 1, 2],
        [1, 0, 2, 1, 0],
        [1, 1, 1, 0, 2],
        [2, 2, 0, 2, 1],
    ]
    (tmp_path / "qc.fam").write_text("".join(f"F{i} I{i} 0 0 0 -9\n" for i in range(6)))
    (tmp_path / "qc.bim").write_text("".join(f"1 rs{i} 0 {i+1} A G\n" for i in range(5)))
    _write_bed(tmp_path / "qc.bed", matrix)
    monkeypatch.setattr(engine, "check_pca_fallback_memory", lambda n, v: (True, 1024, 4096))

    assert _compute_pca_python(tmp_path, 5) is True
    lines = (tmp_path / "pca.eigenvec").read_text().splitlines()
    assert lines[0].startswith("#FID\tIID\tPC1")
    assert len(lines) == 7
    eigenvalues = [float(v) for v in (tmp_path / "pca.eigenval").read_text().split()]
    variance = json.loads((tmp_path / "pca.variance.json").read_text())
    assert len(eigenvalues) == 4  # rank genotipe aktual membatasi hasil menjadi partial
    assert len(variance["variance_explained"]) == 4
    assert variance["cumulative_variance_explained"][-1] == pytest.approx(1.0)


def test_plink_qc_metrics_are_summarized_from_real_output_files(tmp_path):
    prefix = tmp_path / "qc"
    Path(f"{prefix}.vmiss").write_text(
        "#CHROM POS ID MISSING_CT OBS_CT F_MISS\n1 1 rs1 10 100 0.10\n1 2 rs2 20 100 0.20\n")
    Path(f"{prefix}.smiss").write_text(
        "#FID IID MISSING_CT OBS_CT F_MISS\nF1 I1 1 10 0.10\nF2 I2 0 10 0.00\n")
    Path(f"{prefix}.afreq").write_text(
        "#CHROM ID REF ALT ALT_FREQS OBS_CT\n1 rs1 A G 0.10 100\n1 rs2 C T 0.50 100\n")
    Path(f"{prefix}.hardy").write_text(
        "#CHROM POS ID REF ALT1 ALT A1 AX HOM_A1_CT HET_A1_CT TWO_AX_CT GCOUNTS O(HET_A1) E(HET_A1) P\n"
        "1 1 rs1 A G G G A 20 10 70 20,10,70 0.10 0.20 0.01\n"
        "1 2 rs2 C T T T C 30 40 30 30,40,30 0.40 0.40 0.90\n")

    stats = _summarize_plink_metrics(prefix, 0.95, 0.05)
    assert stats["variant_missingness"]["n_records"] == 2
    assert stats["variant_missingness"]["above_threshold"] == 2
    assert stats["sample_missingness"]["mean"] == pytest.approx(0.05)
    assert stats["minor_allele_frequency"]["mean"] == pytest.approx(0.30)
    assert stats["hardy_weinberg"]["autosomal"]["n_records"] == 2
    assert stats["hardy_weinberg"]["autosomal"]["below_filter_threshold"] == 1


def test_upload_budget_respects_dataset_quota_and_disk_reserve(tmp_path, monkeypatch):
    import app.services.resource_policy as policy

    settings = policy.get_settings()
    monkeypatch.setattr(settings, "max_upload_mb", 2048)
    monkeypatch.setattr(settings, "max_dataset_upload_mb", 6144)
    monkeypatch.setattr(settings, "upload_disk_reserve_mb", 1024)
    monkeypatch.setattr(policy, "resource_snapshot", lambda _path=None: {
        "cpu_cores": 4, "memory_total_bytes": 8 * 1024**3,
        "memory_available_bytes": 6 * 1024**3, "disk_total_bytes": 20 * 1024**3,
        "disk_free_bytes": 2 * 1024**3,
    })
    (tmp_path / "genotype.bed").touch()
    with (tmp_path / "genotype.bed").open("wb") as handle:
        handle.truncate(5 * 1024**3)  # sparse file: tests metadata accounting without allocating 5 GiB RAM

    budget = upload_budget(tmp_path)
    assert budget["dataset_quota_remaining_bytes"] == 1024**3
    assert budget["disk_budget_bytes"] == 1024**3
    assert budget["per_file_bytes"] == 1024**3
