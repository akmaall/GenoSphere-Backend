from app.services.ai_interpretation import (
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
