"""Tes KONTRAK frontend <-> backend (revisi integrasi genosphere-sharing).

Setiap tes meniru apa yang dikirim/diharapkan halaman frontend (nilai dropdown, nama field, urutan Step 1-10)
dan membuktikan backend menjawab sesuai. Pipeline dijalankan dengan PLINK 2 / GEMMA asli bila terpasang.
"""
import gzip
import io
import subprocess

import pytest

from app.config import get_settings
from app.services import ui_adapter
from tests.test_gwas import cfg, code, gclient, login, make_dataset, needs_plink, simulate, upload, validated  # noqa: F401


def status(c, h, aid):
    return c.get(f"/api/gwas/analyses/{aid}/status", headers=h).json()


# ======================================================================== akun & profil (Register / Login / Profile)
def test_register_with_frontend_camelcase_fields_and_auto_username(gclient):
    r = gclient.post("/api/register", json={"fullName": "Dr. Akmal", "email": "Akmal.F@ipb.ac.id", "password": "rahasia123",
                                            "confirmPassword": "rahasia123", "institution": "IPB University", "bio": "Bioinformatika"})
    assert r.status_code == 201, r.text
    u = r.json()
    assert u["full_name"] == "Dr. Akmal" and u["username"] == "akmal.f" and u["institution"] == "IPB University" and u["bio"] == "Bioinformatika"
    # username otomatis juga bisa dipakai login (field "Email / Username")
    assert gclient.post("/api/login", data={"username": "akmal.f", "password": "rahasia123"}).status_code == 200
    # email lokal sama dari domain lain -> username diberi nomor
    r2 = gclient.post("/api/register", json={"full_name": "Akmal Dua", "email": "akmal.f@gmail.com", "password": "rahasia123", "confirm_password": "rahasia123"})
    assert r2.json()["username"] == "akmal.f2"


def test_camelcase_password_mismatch_points_to_confirm_field(gclient):
    r = gclient.post("/api/register", json={"fullName": "Dr. Akmal", "email": "a@ipb.ac.id", "password": "rahasia123", "confirmPassword": "beda12345"})
    assert r.status_code == 422 and r.json()["detail"]["errors"][0]["field"] == "confirmPassword"


def test_patch_me_updates_profile_only(gclient):
    h = login(gclient)
    r = gclient.patch("/api/me", headers=h, json={"fullName": "Nama Baru", "institution": "IPB", "bio": ""})
    assert r.status_code == 200 and r.json()["full_name"] == "Nama Baru" and r.json()["institution"] == "IPB" and r.json()["bio"] is None
    assert gclient.get("/api/me", headers=h).json()["email"] == "akmal@ipb.ac.id"   # email tidak berubah


# ======================================================================== Step 1: upload
def test_upload_accepts_pheno_and_bare_gz_and_records_original_names(gclient):
    h = login(gclient)
    v, p = simulate()
    r = gclient.post("/api/gwas/upload-phenotype", headers=h, files={"file": ("Fenotipe Pasien.pheno", p.replace(",", "\t"))},
                     data={"name": "Diabetes_T2D_Cohort_2026", "source_domain": "kesehatan"})
    assert r.status_code == 200, r.text
    ds = r.json()["dataset_id"]
    r = upload(gclient, h, "data_pasien.gz", gzip.compress(v.encode()), "genotype", ds)
    assert r.status_code == 200 and r.json()["files"][0]["slot"] == "genotype.vcf.gz" and r.json()["ready_for_validation"] is True
    info = gclient.get(f"/api/gwas/datasets/{ds}", headers=h).json()
    assert info["name"] == "Diabetes_T2D_Cohort_2026"
    assert {f["filename"] for f in info["files"]} == {"Fenotipe Pasien.pheno", "data_pasien.gz"}   # nama asli untuk ditampilkan di UI
    rep = gclient.post(f"/api/gwas/datasets/{ds}/validate", headers=h).json()
    assert rep["status"] == "valid", rep["errors"]


@needs_plink
def test_upload_plink_files_sequentially_in_separate_requests(gclient, tmp_path):
    v, p = simulate()
    (tmp_path / "s.vcf").write_text(v)
    subprocess.run([get_settings().plink2_bin, "--vcf", str(tmp_path / "s.vcf"), "--double-id", "--allow-extra-chr", "--make-bed", "--out", str(tmp_path / "s")],
                   check=True, capture_output=True)
    h = login(gclient)
    only_bed = gclient.post("/api/gwas/upload-genotype", headers=h, files={"file": ("s.bed", (tmp_path / "s.bed").read_bytes())}).json()
    assert only_bed["ready_for_validation"] is False and any("fenotipe" in m for m in only_bed["missing"]) and any(".bim" in m for m in only_bed["missing"])
    ds = only_bed["dataset_id"]
    for ext in ("bim", "fam"):
        r = gclient.post("/api/gwas/upload-genotype", headers=h, data={"dataset_id": str(ds)},
                         files={"file": (f"s.{ext}", (tmp_path / f"s.{ext}").read_bytes())})
        assert r.status_code == 200
    r = upload(gclient, h, "p.csv", p, "phenotype", ds)
    assert r.json()["ready_for_validation"] is True and r.json()["missing"] == []
    # request yang salah format tidak menimpa berkas sebelumnya
    bad = gclient.post("/api/gwas/upload-genotype", headers=h, data={"dataset_id": str(ds)},
                       files={"file": ("malware.exe", b"2")})
    assert bad.status_code == 400 and code(bad) == "INVALID_GENOTYPE_FORMAT"
    assert gclient.post("/api/gwas/upload-genotype", headers=h).status_code == 422   # field file wajib


def test_replacing_phenotype_removes_old_format(gclient):
    h = login(gclient)
    ds = make_dataset(gclient, h)
    _, p = simulate()
    r = upload(gclient, h, "baru.tsv", p.replace(",", "\t"), "phenotype", ds)
    assert [f["slot"] for f in r.json()["files"] if f["kind"] == "phenotype"] == ["phenotype.tsv"]


def test_delete_file_and_delete_dataset(gclient):
    h = login(gclient)
    ds = validated(gclient, h)
    r = gclient.delete(f"/api/gwas/datasets/{ds}/files/phenotype.csv", headers=h)
    assert r.status_code == 200 and r.json()["dataset"]["ready_for_validation"] is False
    assert gclient.get(f"/api/gwas/datasets/{ds}", headers=h).json()["validation"] is None          # validasi lama dibuang
    assert gclient.delete(f"/api/gwas/datasets/{ds}/files/evil.sh", headers=h).status_code == 400
    assert gclient.delete(f"/api/gwas/datasets/{ds}/files/phenotype.csv", headers=h).status_code == 404
    confirm = gclient.delete(f"/api/gwas/datasets/{ds}", headers=h)
    assert confirm.status_code == 409 and confirm.json()["detail"]["code"] == "CONFIRMATION_REQUIRED"
    deleted = gclient.delete(f"/api/gwas/datasets/{ds}?confirm=true", headers=h)
    assert deleted.status_code == 200 and deleted.json()["deleted"] == ds
    assert gclient.get(f"/api/gwas/datasets/{ds}", headers=h).status_code == 404


def test_other_user_cannot_delete_my_dataset(gclient):
    ha, hb = login(gclient, "andi"), login(gclient, "budi")
    ds = make_dataset(gclient, ha)
    assert gclient.delete(f"/api/gwas/datasets/{ds}", headers=hb).status_code == 404
    assert gclient.delete(f"/api/gwas/datasets/{ds}/files/phenotype.csv", headers=hb).status_code == 404


def test_dataset_id_must_be_integer(gclient):
    h = login(gclient)
    assert gclient.get("/api/gwas/datasets/abc", headers=h).status_code == 422


# ======================================================================== Step 2: laporan validasi untuk kartu UI
def test_validation_report_has_ui_fields(gclient):
    h = login(gclient)
    ds = make_dataset(gclient, h)
    rep = gclient.post(f"/api/gwas/datasets/{ds}/validate", headers=h).json()
    assert rep["summary_message"].startswith("Tidak ditemukan error") and rep["phenotype"]["size_bytes"] > 0
    m = rep["matching"]
    assert m["matched_percent"] == 100.0 and m["unmatched_total"] == 0 and rep["files"][0]["filename"] if "files" in rep else True
    assert "files" in rep and rep["ready_for_validation"] is True     # dari _dataset_info(acc, ddir)


# ======================================================================== Step 3-4: nilai dropdown UI
def test_config_accepts_ui_dropdown_labels_and_estimates_time(gclient):
    h = login(gclient)
    ds = validated(gclient, h)
    ui = {"dataset_id": ds, "trait_name": "PHENO_DISEASE", "trait_type": "Biner (Case / Control)", "model": "Logistic Regression",
          "analysis_goal": "Analisis Asosiasi Genomik", "parameter_qc": "MAF >= 0.05, Call Rate >= 0.95",
          "population_structure": "Sertakan Hasil PCA (5 PC)", "covariates": "Usia, Jenis Kelamin", "significance_threshold": "5e-8 (Bonferroni)"}
    r = gclient.post("/api/gwas/config/check", headers=h, json=ui).json()
    assert r["valid"], r["errors"]
    c = r["config"]
    assert (c["trait_type"], c["model"], c["use_pca"], c["pca_components"]) == ("binary", "logistic", True, 5)
    assert (c["qc"]["maf"], c["qc"]["call_rate"]) == (0.05, 0.95) and c["covariates"] == ["COV_AGE", "COV_SEX"]   # Usia->Age, Jenis Kelamin->Sex
    assert c["significance_method"] == "genome_wide" and c["significance_threshold"] is None and c["analysis_goal"] == "Analisis Asosiasi Genomik"
    assert r["estimate"]["seconds"] >= 5 and r["estimate"]["label"]


def test_config_ui_mismatch_still_rejected(gclient):
    h = login(gclient)
    ds = validated(gclient, h)
    r = gclient.post("/api/gwas/config/check", headers=h, json={"dataset_id": ds, "trait_name": "PHENO_HEIGHT", "trait_type": "Kuantitatif (Kontinu)",
                                                                  "model": "Logistic Regression"}).json()
    assert [e["code"] for e in r["errors"]] == ["MODEL_TRAIT_MISMATCH"]
    r = gclient.post("/api/gwas/config/check", headers=h, json={"dataset_id": ds, "trait_name": "PHENO_HEIGHT", "trait_type": "quantitative",
                                                                  "covariates": ["Tinggi Badan Ayah"]}).json()
    assert r["errors"][0]["code"] == "COVARIATE_NOT_FOUND"


@pytest.mark.parametrize("label,expected", [
    ("5e-8 (Bonferroni)", ("genome_wide", 5e-8)), ("1e-5 (Suggestive)", ("suggestive", 1e-5)), ("0.05 (FDR)", ("fdr", 0.05)),
    ("bonferroni", ("bonferroni", None)), (0.001, ("custom", 0.001)), ("sampah", (None, None))])
def test_parse_threshold_labels(label, expected):
    assert ui_adapter.parse_threshold(label) == expected


@pytest.mark.parametrize("label,expected", [("Linear Mixed Model (LMM)", "lmm"), ("Linear Regression", "linear"), ("Logistic Regression", "logistic"), ("auto", "auto")])
def test_parse_model_labels(label, expected):
    assert ui_adapter.norm_model(label) == expected


# ======================================================================== Step 4-7: alur bertahap (staged)
@needs_plink
def test_staged_flow_mirrors_wizard_steps_5_6_7(gclient):
    h = login(gclient)
    ds = validated(gclient, h)
    # Step 4 -> "Jalankan GWAS": hanya QC yang berjalan
    r = gclient.post("/api/gwas/analyses", headers=h, json={"dataset_id": ds, "trait_name": "PHENO_HEIGHT", "trait_type": "quantitative",
                                                           "parameter_qc": "MAF >= 0.05, Call Rate >= 0.95", "execution_mode": "staged"})
    assert r.status_code == 202 and r.json()["next_step"] == "pca"
    aid = r.json()["analysis_id"]
    st = status(gclient, h, aid)
    assert st["status"] == "awaiting" and st["next_step"] == "pca" and st["can_continue"] and st["percent"] == 35 and st["execution_mode"] == "staged"
    # Step 5: ringkasan QC sudah tersedia
    qc = gclient.get(f"/api/gwas/analyses/{aid}/qc-summary", headers=h).json()
    assert qc["variants_after"] > 0 and qc["samples_after"] == 120
    # urutan salah ditolak
    assert code(gclient.post(f"/api/gwas/analyses/{aid}/run", headers=h, json={"model": "Linear Regression"})) == "WRONG_STAGE"
    assert gclient.get(f"/api/gwas/analyses/{aid}/results/summary", headers=h).status_code == 409
    # Step 6: PCA + kovariat memakai label UI
    assert gclient.put(f"/api/gwas/analyses/{aid}/covariates", headers=h, json={"covariates": "Usia, Jenis Kelamin"}).status_code == 200
    r = gclient.post(f"/api/gwas/analyses/{aid}/pca", headers=h, json={"pca_components": "5 PC"})
    assert r.status_code == 202, r.text
    st = status(gclient, h, aid)
    assert st["status"] == "awaiting" and st["next_step"] == "gwas" and st["percent"] == 50
    pca = gclient.get(f"/api/gwas/analyses/{aid}/pca", headers=h).json()
    assert pca["components"] == ["PC1", "PC2", "PC3", "PC4", "PC5"] and len(pca["points"]) == 120 and len(pca["eigenvalues"]) == 5
    # Step 7: pilih model, jalankan
    r = gclient.post(f"/api/gwas/analyses/{aid}/run", headers=h, json={"model": "Linear Regression"})
    assert r.status_code == 202 and r.json()["config"]["covariates"] == ["COV_AGE", "COV_SEX"]
    st = status(gclient, h, aid)
    assert st["status"] == "done" and st["percent"] == 100 and not st["can_continue"]
    summ = gclient.get(f"/api/gwas/analyses/{aid}/results/summary", headers=h).json()
    assert summ["best_variant"]["rsid"] == "rs101"                 # hasil biologis sama dengan mode auto


@needs_plink
def test_staged_flow_without_pca_and_redo_pca(gclient):
    h = login(gclient)
    ds = validated(gclient, h)
    aid = gclient.post("/api/gwas/analyses", headers=h, json=cfg(ds, execution_mode="staged", use_pca=False, covariates=[])).json()["analysis_id"]
    r = gclient.post(f"/api/gwas/analyses/{aid}/pca", headers=h, json={"use_pca": False})
    assert r.status_code == 200 and r.json()["next_step"] == "gwas"          # tanpa job
    assert gclient.get(f"/api/gwas/analyses/{aid}/pca", headers=h).status_code == 404   # PCA_NOT_USED
    # pengguna kembali ke Step 6 dan menyalakan PCA
    assert gclient.post(f"/api/gwas/analyses/{aid}/pca", headers=h, json={"use_pca": True, "pca_components": 5}).status_code == 202
    assert len(gclient.get(f"/api/gwas/analyses/{aid}/pca", headers=h).json()["components"]) == 5
    assert gclient.post(f"/api/gwas/analyses/{aid}/run", headers=h, json={}).status_code == 202
    assert status(gclient, h, aid)["status"] == "done"
    assert code(gclient.post(f"/api/gwas/analyses/{aid}/pca", headers=h, json={})) == "WRONG_STAGE"   # sudah selesai


@needs_plink
def test_staged_endpoints_rejected_for_auto_analysis_and_validated(gclient):
    h = login(gclient)
    ds = validated(gclient, h)
    aid = gclient.post("/api/gwas/analyses", headers=h, json=cfg(ds)).json()["analysis_id"]
    assert code(gclient.post(f"/api/gwas/analyses/{aid}/pca", headers=h, json={})) == "NOT_STAGED"
    aid2 = gclient.post("/api/gwas/analyses", headers=h, json=cfg(ds, execution_mode="staged")).json()["analysis_id"]
    bad = gclient.put(f"/api/gwas/analyses/{aid2}/covariates", headers=h, json={"covariates": ["TidakAda"]})
    assert bad.status_code == 422 and bad.json()["detail"]["errors"][0]["code"] == "COVARIATE_NOT_FOUND"
    assert status(gclient, h, aid2)["next_step"] == "pca"                      # tetap menunggu setelah input salah
    hb = login(gclient, "lain")
    assert gclient.post(f"/api/gwas/analyses/{aid2}/pca", headers=hb, json={}).status_code == 404


@needs_plink
def test_staged_retry_repeats_only_failed_stage(gclient, monkeypatch):
    from app.services import jobs
    from app.services.gwas_engine import PipelineError
    h = login(gclient)
    ds = validated(gclient, h)
    aid = gclient.post("/api/gwas/analyses", headers=h, json=cfg(ds, execution_mode="staged")).json()["analysis_id"]
    real = jobs.run_pipeline
    monkeypatch.setattr(jobs, "run_pipeline", lambda c, x, stages=("qc", "pca", "gwas"): (_ for _ in ()).throw(PipelineError("BOOM", "gagal", "coba lagi")))
    gclient.post(f"/api/gwas/analyses/{aid}/pca", headers=h, json={"pca_components": 5})
    st = status(gclient, h, aid)
    assert st["status"] == "failed" and st["error"]["code"] == "BOOM"
    monkeypatch.setattr(jobs, "run_pipeline", real)
    r = gclient.post(f"/api/gwas/analyses/{aid}/retry", headers=h)
    assert r.json()["retry_from"] == "pca"                                          # bukan "all": QC tidak diulang
    assert status(gclient, h, aid)["status"] == "awaiting" and status(gclient, h, aid)["next_step"] == "gwas"


# ======================================================================== Step 8-10: hasil, FDR, ekspor
@pytest.fixture()
def done_analysis(gclient):
    h = login(gclient)
    ds = validated(gclient, h)
    aid = gclient.post("/api/gwas/analyses", headers=h, json=cfg(ds)).json()["analysis_id"]
    assert status(gclient, h, aid)["status"] == "done"
    return gclient, h, aid


@needs_plink
def test_postprocess_ui_labels_and_fdr(done_analysis):
    c, h, aid = done_analysis
    base = f"/api/gwas/analyses/{aid}"
    n = c.get(f"{base}/results/summary", headers=h).json()["n_variants_tested"]
    r = c.post(f"{base}/postprocess", headers=h, json={"method": "5e-8 (Bonferroni)"}).json()["significance"]
    assert r["method"] == "genome_wide" and r["threshold_p"] == 5e-8
    r = c.post(f"{base}/postprocess", headers=h, json={"method": "1e-5 (Suggestive)"}).json()["significance"]
    assert r["method"] == "suggestive" and r["n_significant"] >= 1
    fdr = c.post(f"{base}/postprocess", headers=h, json={"method": "0.05 (FDR)"}).json()["significance"]
    assert fdr["method"] == "fdr" and fdr["q"] == 0.05 and fdr["n_significant"] >= 1 and fdr["threshold_p"] > 0
    # BH: setiap varian signifikan harus punya p <= ambang, dan yang tidak signifikan p > ambang
    sig = c.get(f"{base}/results/significant?limit=1000", headers=h).json()
    assert sig["total"] == fdr["n_significant"] and all(i["p_value"] <= fdr["threshold_p"] for i in sig["items"])
    assert sig["items"][0]["rsid"] == "rs101" and sig["items"][0]["snp"] == "rs101"
    # q sangat kecil -> mungkin tidak ada penemuan, tetap respons sah
    none = c.post(f"{base}/postprocess", headers=h, json={"method": "fdr", "threshold": 1e-300}).json()["significance"]
    assert none["n_significant"] == 0 and none["threshold_p"] == 0.0
    assert c.post(f"{base}/postprocess", headers=h, json={"method": "ngawur"}).status_code == 422


@needs_plink
def test_manhattan_extras_rows_format_and_aliases(done_analysis):
    c, h, aid = done_analysis
    base = f"/api/gwas/analyses/{aid}/results"
    m = c.get(f"{base}/manhattan", headers=h).json()
    assert len(m["pvalue"]) == len(m["x"]) == len(m["position"]) and m["thresholds"]["genome_wide"] == pytest.approx(7.3010, abs=1e-3)
    assert m["chromosome_ticks"]["labels"] == ["1", "2"] and m["x"] == sorted(m["x"])
    rows = c.get(f"{base}/manhattan?format=rows", headers=h).json()
    item = rows["items"][0]
    assert {"chr", "pos", "snp", "pvalue", "neg_log10_p", "x", "is_significant"} <= set(item) and isinstance(rows["items"], list)
    top = c.get(f"{base}/results/top".replace("/results/results", "/results"), headers=h).json()["items"][0]
    assert top["snp"] == top["rsid"] == "rs101" and top["pvalue"] == top["p_value"] and top["chr"] == top["chromosome"]


@needs_plink
@pytest.mark.parametrize("fmt", ["csv", "tsv", "json", "xlsx", "Excel (.xlsx)", "JSON"])
def test_export_formats(done_analysis, fmt):
    c, h, aid = done_analysis
    n = c.get(f"/api/gwas/analyses/{aid}/results/summary", headers=h).json()["n_variants_tested"]
    r = c.get(f"/api/gwas/analyses/{aid}/export", params={"format": fmt}, headers=h)
    assert r.status_code == 200
    kind = fmt.lower().split()[0].strip("()")
    if "xlsx" in fmt.lower() or "excel" in fmt.lower():
        from openpyxl import load_workbook
        ws = load_workbook(io.BytesIO(r.content)).active
        rows = list(ws.values)
        assert rows[0][:3] == ("chromosome", "position", "rsid") and len(rows) == n + 1 and rows[1][2] == "rs101"
        assert r.headers["content-disposition"].endswith('.xlsx"')
    elif kind == "json":
        import json
        data = json.loads(r.text)
        assert len(data) == n and data[0]["rsid"] == "rs101"
    else:
        lines = r.text.strip().splitlines()
        sep = "\t" if kind == "tsv" else ","
        assert lines[0].split(sep)[:3] == ["chromosome", "position", "rsid"] and len(lines) == n + 1
        assert r.headers["content-disposition"].endswith(f'.{kind}"')
    assert c.get(f"/api/gwas/analyses/{aid}/export?format=pdf", headers=h).status_code == 400


@needs_plink
def test_analysis_list_exposes_db_timestamps(done_analysis):
    c, h, aid = done_analysis
    item = c.get("/api/gwas/analyses", headers=h).json()["items"][0]
    assert item["created_at"] and item["finished_at"] and item["trait_name"] == "PHENO_HEIGHT" and item["execution_mode"] == "auto"


# ======================================================================== infrastruktur
def test_cors_allows_nextjs_dev_ports_and_blocks_others(gclient):
    ok = gclient.options("/api/gwas/datasets", headers={"Origin": "http://localhost:3001", "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:3001"
    ok2 = gclient.options("/api/gwas/datasets", headers={"Origin": "http://127.0.0.1:3000", "Access-Control-Request-Method": "GET"})
    assert ok2.headers.get("access-control-allow-origin") == "http://127.0.0.1:3000"
    bad = gclient.options("/api/gwas/datasets", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in bad.headers
    pat = gclient.options("/api/me", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "PATCH"})
    assert "PATCH" in pat.headers.get("access-control-allow-methods", "")


def test_health_reports_schema_status(gclient):
    body = gclient.get("/health").json()
    assert body["status"] == "ok" and body["schema"] == "ok" and body["version"] == get_settings().app_version
