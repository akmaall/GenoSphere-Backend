"""Catatan analis no. 9, 10, 11: respons konfigurasi, aturan PCA dan kovariat, serta syarat tiap model."""
from app.services.tools import ToolError
from tests import gwas_data as D
from tests.helpers import base_config, err_code, make_user, upload, valid_sample_dataset


def _check(client, headers, cfg):
    r = client.post("/api/gwas/config/check", headers=headers, json=cfg)
    assert r.status_code == 200, r.text
    return r.json()


def _codes(items):
    return [i["code"] for i in items]


def _small_dataset(client, headers, disease):
    """Dataset 12 sampel dengan kolom Disease yang ditentukan sendiri."""
    rows = [f"{i},{i},{150 + n},{disease[n]},{30 + n},{'L' if n % 2 else 'P'}" for n, i in enumerate(D.IDS)]
    ds = upload(client, headers, "genotype", D.plink_files(), source_domain="kesehatan").json()["dataset_id"]
    upload(client, headers, "phenotype", [("p.csv", D.pheno_csv(rows=rows))], dataset_id=ds)
    assert client.post(f"/api/gwas/datasets/{ds}/validate", headers=headers).json()["status"] == "valid"
    return ds


# ---------- no. 9: respons konfigurasi tidak lagi "string" ----------
def test_presets_returns_domain_choices_for_frontend_dropdown(client):
    headers, _ = make_user(client)
    body = client.get("/api/gwas/config/presets", headers=headers).json()
    assert [d["value"] for d in body["domains"]] == ["kesehatan", "farmasi", "agrikultur", "peternakan"]
    assert all(d["label"] and d["description"] for d in body["domains"])
    assert body["presets"]["agrikultur"]["qc"]["hwe_p"] is None and body["presets"]["kesehatan"]["pca_components"] == 10


def test_presets_marks_domain_recorded_on_dataset(client):
    headers, _ = make_user(client)
    ds = valid_sample_dataset(client, headers, domain="farmasi")
    domains = client.get(f"/api/gwas/config/presets?dataset_id={ds}", headers=headers).json()["domains"]
    assert {d["value"]: d["is_recorded_domain"] for d in domains} == {"kesehatan": False, "farmasi": True, "agrikultur": False, "peternakan": False}


def test_models_catalog_lists_rules_per_model(client):
    headers, _ = make_user(client)
    body = client.get("/api/gwas/config/models", headers=headers).json()
    by = {m["value"]: m for m in body["models"]}
    assert set(by) == {"linear", "logistic", "lmm"}
    assert by["logistic"]["trait_types"] == ["binary"] and by["linear"]["trait_types"] == ["quantitative"]
    assert by["lmm"]["engine"] == "GEMMA" and by["linear"]["traits_per_run"] == 1
    assert all(m["requirements"] and m["description"] for m in by.values())
    assert body["auto_choice"] == {"binary": "logistic", "quantitative": "linear"}


def test_options_contains_pca_covariate_and_significance_choices(client):
    headers, _ = make_user(client)
    o = client.get("/api/gwas/config/options", headers=headers).json()["options"]
    assert [x["value"] for x in o["pca_components"]] == [5, 10, 20]
    assert [x["value"] for x in o["covariates"]] == ["none", "age", "sex", "age_sex", "bmi"]
    assert [x["value"] for x in o["significance_methods"]] == ["genome_wide", "suggestive", "bonferroni", "fdr", "custom"]
    assert next(x for x in o["significance_methods"] if x["value"] == "custom")["uses_threshold"] is True


def test_config_check_response_is_fully_structured(client):
    headers, _ = make_user(client)
    ds = valid_sample_dataset(client, headers)
    body = _check(client, headers, base_config(ds))
    assert set(body) == {"valid", "errors", "warnings", "config", "estimate"}
    assert body["valid"] is True and body["estimate"]["seconds"] >= 5
    assert body["config"]["model"] == "linear" and body["config"]["qc"]["maf"] == 0.05


def test_traits_lists_phenotypes_with_matching_models(client):
    headers, _ = make_user(client)
    ds = valid_sample_dataset(client, headers)
    body = client.get(f"/api/gwas/datasets/{ds}/traits", headers=headers).json()
    t = {x["name"]: x for x in body["traits"]}
    assert t["PHENO_HEIGHT"]["recommended_model"] == "linear" and [m["value"] for m in t["PHENO_HEIGHT"]["models"]] == ["linear", "lmm"]
    assert t["PHENO_DISEASE"]["recommended_model"] == "logistic" and [m["value"] for m in t["PHENO_DISEASE"]["models"]] == ["logistic"]
    assert t["PHENO_DISEASE"]["n_cases"] == 52 and t["PHENO_DISEASE"]["n_controls"] == 68
    kinds = {c["name"]: c["kind"] for c in body["covariate_candidates"]}
    assert kinds["COV_AGE"] == "numeric" and kinds["COV_SEX"] == "categorical"


# ---------- no. 10: use_pca dan pca_components ----------
def test_pca_components_ignored_when_pca_off(client):
    headers, _ = make_user(client)
    ds = valid_sample_dataset(client, headers)
    body = _check(client, headers, base_config(ds, use_pca=False, pca_components=10))
    assert body["valid"] and body["config"]["pca_components"] is None
    assert "PCA_COMPONENTS_IGNORED" in _codes(body["warnings"])
    assert body["config"]["covariate_sources"]["principal_components"] == 0


def test_pca_on_without_components_uses_default_5(client):
    headers, _ = make_user(client)
    ds = valid_sample_dataset(client, headers)
    body = _check(client, headers, base_config(ds, use_pca=True))
    assert body["config"]["pca_components"] == 5 and "PCA_COMPONENTS_DEFAULT" in _codes(body["warnings"])


def test_pca_component_choices_are_only_5_10_20(client):
    headers, _ = make_user(client)
    ds = valid_sample_dataset(client, headers)
    for n in (5, 10, 20):
        assert _check(client, headers, base_config(ds, use_pca=True, pca_components=n))["config"]["pca_components"] == n
    r = client.post("/api/gwas/config/check", headers=headers, json=base_config(ds, use_pca=True, pca_components=1))
    assert r.status_code == 422 and "5, 10, 20" in r.json()["detail"]["errors"][0]["message"]
    assert _check(client, headers, base_config(ds, use_pca=True, pca_components="10 PC"))["config"]["pca_components"] == 10   # label dropdown


def test_pca_components_may_be_partial_for_small_cohort(client):
    headers, _ = make_user(client)
    ds = _small_dataset(client, headers, [0, 1] * 6)     # hanya 12 sampel
    body = _check(client, headers, base_config(ds, use_pca=True, pca_components=20))
    assert body["valid"] is True
    assert body["config"]["pca_components"] == 20
    assert "PCA_TOO_MANY_COMPONENTS" not in _codes(body["errors"])


# ---------- no. 10: kovariat dari fenotipe dipisah dari PC ----------
def test_covariates_come_from_phenotype_and_pcs_are_separate(client):
    headers, _ = make_user(client)
    ds = valid_sample_dataset(client, headers)
    body = _check(client, headers, base_config(ds, covariates=["Age", "Sex"], use_pca=True, pca_components=10))
    assert body["config"]["covariate_sources"] == {"phenotype_columns": ["Age", "Sex"], "principal_components": 10}
    assert body["config"]["covariates"] == ["COV_AGE", "COV_SEX"]       # PC tidak tercampur ke daftar kovariat


def test_covariate_choices_from_dropdown_are_understood(client):
    headers, _ = make_user(client)
    ds = valid_sample_dataset(client, headers)
    case = lambda v: _check(client, headers, base_config(ds, covariates=v))["config"]["covariates"]   # noqa: E731
    assert case("Age + Sex") == ["COV_AGE", "COV_SEX"] and case(["age_sex"]) == ["COV_AGE", "COV_SEX"]
    assert case("No additional covariate") == [] and case([]) == []
    assert case(["Usia"]) == ["COV_AGE"] and case(["sex"]) == ["COV_SEX"]


def test_covariate_errors_are_explained(client):
    headers, _ = make_user(client)
    ds = valid_sample_dataset(client, headers)
    assert "COVARIATE_NOT_FOUND" in _codes(_check(client, headers, base_config(ds, covariates=["bmi"]))["errors"])     # sampel tidak punya BMI
    assert "COVARIATE_IS_TRAIT" in _codes(_check(client, headers, base_config(ds, covariates=["PHENO_HEIGHT"]))["errors"])
    assert "COVARIATE_NOT_SUPPORTED" in _codes(_check(client, headers, base_config(ds, covariates=["COV_CODE"]))["errors"])


# ---------- no. 11: tiap model punya aturan sendiri ----------
def test_model_must_match_trait_type(client):
    headers, _ = make_user(client)
    ds = valid_sample_dataset(client, headers)
    assert "MODEL_TRAIT_MISMATCH" in _codes(_check(client, headers, base_config(ds, model="logistic"))["errors"])
    bad = base_config(ds, trait_name="PHENO_DISEASE", trait_type="binary", model="linear")
    assert "MODEL_TRAIT_MISMATCH" in _codes(_check(client, headers, bad)["errors"])
    ok = _check(client, headers, base_config(ds, trait_name="PHENO_DISEASE", trait_type="binary", model="auto"))
    assert ok["valid"] and ok["config"]["model"] == "logistic"


def test_trait_type_must_match_detected_type(client):
    headers, _ = make_user(client)
    ds = valid_sample_dataset(client, headers)
    assert "TRAIT_TYPE_MISMATCH" in _codes(_check(client, headers, base_config(ds, trait_type="binary"))["errors"])
    assert "TRAIT_NOT_FOUND" in _codes(_check(client, headers, base_config(ds, trait_name="Tidak ada"))["errors"])


def test_logistic_needs_enough_cases_and_controls(client):
    headers, _ = make_user(client)
    ds = _small_dataset(client, headers, [1, 1, 1] + [0] * 9)       # hanya 3 kasus
    cfg = base_config(ds, trait_name="PHENO_DISEASE", trait_type="binary")
    body = _check(client, headers, cfg)
    assert "BINARY_CLASS_TOO_SMALL" in _codes(body["errors"]) and "3 kasus" in body["errors"][0]["message"]
    assert client.post("/api/gwas/analyses", headers=headers, json=cfg).status_code == 422


def test_logistic_with_small_groups_gets_warning(client):
    headers, _ = make_user(client)
    ds = _small_dataset(client, headers, [1] * 6 + [0] * 6)
    body = _check(client, headers, base_config(ds, trait_name="PHENO_DISEASE", trait_type="binary"))
    assert body["valid"] and "BINARY_CLASS_SMALL" in _codes(body["warnings"])


def test_lmm_needs_gemma_installed(client, monkeypatch):
    import app.routers.gwas as gw
    headers, _ = make_user(client)
    ds = valid_sample_dataset(client, headers)

    def missing(name):
        raise ToolError("TOOL_NOT_FOUND", "tidak ada", "pasang GEMMA")
    monkeypatch.setattr(gw, "resolve_binary", missing)
    body = _check(client, headers, base_config(ds, model="lmm"))
    assert "TOOL_NOT_AVAILABLE" in _codes(body["errors"])


def test_lmm_with_pca_warns_but_allows(client, monkeypatch):
    import app.routers.gwas as gw
    headers, _ = make_user(client)
    ds = valid_sample_dataset(client, headers)
    monkeypatch.setattr(gw, "resolve_binary", lambda name: "/usr/bin/gemma")
    body = _check(client, headers, base_config(ds, model="lmm", use_pca=True, pca_components=5))
    assert body["valid"] and "PCA_WITH_LMM" in _codes(body["warnings"])


# ---------- guided mode dan ambang awal ----------
def test_guided_mode_uses_preset_of_dataset_domain(client):
    headers, _ = make_user(client)
    ds = valid_sample_dataset(client, headers, domain="kesehatan")
    cfg = base_config(ds, analysis_mode="guided")
    del cfg["qc"]            # Guided Mode mengisi QC dari preset hanya bila pengguna tidak mengisinya sendiri
    body = _check(client, headers, cfg)
    assert body["valid"] and body["config"]["domain"] == "kesehatan"
    assert body["config"]["qc"]["maf"] == 0.01 and body["config"]["use_pca"] is True and body["config"]["pca_components"] == 10
    own = _check(client, headers, base_config(ds, analysis_mode="guided"))          # isian pengguna (maf 0.05) tetap menang
    assert own["config"]["qc"]["maf"] == 0.05


def test_guided_mode_rejects_other_domain(client):
    headers, _ = make_user(client)
    ds = valid_sample_dataset(client, headers, domain="kesehatan")
    assert "DOMAIN_MISMATCH" in _codes(_check(client, headers, base_config(ds, analysis_mode="guided", domain="agrikultur"))["errors"])


def test_initial_threshold_rules(client):
    headers, _ = make_user(client)
    ds = valid_sample_dataset(client, headers)
    fixed = _check(client, headers, base_config(ds, significance_method="genome_wide", significance_threshold=0.5))
    assert fixed["config"]["significance_threshold"] is None and "THRESHOLD_IGNORED" in _codes(fixed["warnings"])
    assert "SIGNIFICANCE_INVALID" in _codes(_check(client, headers, base_config(ds, significance_method="custom"))["errors"])
    fdr = _check(client, headers, base_config(ds, significance_method="fdr"))
    assert fdr["config"]["significance_threshold"] == 0.05
    label = _check(client, headers, base_config(ds, significance_threshold="5e-8 (Bonferroni)"))
    assert label["config"]["significance_method"] == "genome_wide" and label["config"]["significance_threshold"] is None
    label2 = _check(client, headers, base_config(ds, significance_threshold="0.05 (FDR)"))
    assert label2["config"]["significance_method"] == "fdr" and label2["config"]["significance_threshold"] == 0.05
