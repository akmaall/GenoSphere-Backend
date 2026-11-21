from app.services.ai_interpretation import (
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
