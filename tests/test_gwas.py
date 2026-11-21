"""Tes modul GWAS. Tes yang butuh PLINK 2 / GEMMA otomatis dilewati bila programnya tidak terpasang."""
import random
import shutil

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import database
from app.config import get_settings
from app.database import Base, get_db
from app.main import app

HAS_PLINK = shutil.which(get_settings().plink2_bin) is not None
HAS_GEMMA = shutil.which(get_settings().gemma_bin) is not None
needs_plink = pytest.mark.skipif(not HAS_PLINK, reason="plink2 tidak terpasang")
needs_gemma = pytest.mark.skipif(not (HAS_PLINK and HAS_GEMMA), reason="plink2/gemma tidak terpasang")

CAUSAL = "rs101"


def simulate(n=120, m=300, causal=100, seed=3):
    """Dataset kecil dengan SATU SNP kausal (rs101) untuk trait kuantitatif (Height) dan biner (Disease)."""
    rnd = random.Random(seed)
    ids = [f"S{i:03d}" for i in range(n)]
    maf = [rnd.uniform(0.15, 0.5) for _ in range(m)]
    g = [[(rnd.random() < maf[j]) + (rnd.random() < maf[j]) for _ in range(n)] for j in range(m)]
    age = [rnd.randint(25, 70) for _ in range(n)]
    height = [round(1.5 * g[causal][i] + 0.02 * age[i] + rnd.gauss(0, 1), 4) for i in range(n)]
    disease = [1 if 2.0 * g[causal][i] + rnd.gauss(0, 1) > 2.2 else 0 for i in range(n)]
    gt = {0: "0/0", 1: "0/1", 2: "1/1"}
    vcf = ["##fileformat=VCFv4.2", "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\t" + "\t".join(ids)]
    for j in range(m):
        vcf.append(f"{1 if j < m // 2 else 2}\t{(j + 1) * 1000}\trs{j + 1}\tA\tG\t100\tPASS\t.\tGT\t" + "\t".join(gt[x] for x in g[j]))
    pheno = ["FID,IID,PHENO_HEIGHT,PHENO_DISEASE,COV_AGE,COV_SEX,COV_CODE"] + [f"{s},{s},{height[i]},{disease[i]},{age[i]},{'M' if i % 2 else 'F'},K{i}" for i, s in enumerate(ids)]
    return "\n".join(vcf) + "\n", "\n".join(pheno) + "\n"


@pytest.fixture()
def gclient(tmp_path, monkeypatch):
    """Klien dengan database SQLite sementara + folder penyimpanan sementara. SessionLocal juga diarahkan ke DB tes
    karena job latar belakang membuat sesinya sendiri."""
    monkeypatch.setattr(get_settings(), "storage_dir", str(tmp_path / "store"))
    engine = create_engine("sqlite+pysqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override
    monkeypatch.setattr(database, "SessionLocal", Session)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def login(client, name="akmal"):
    client.post("/api/register", json={"full_name": name.title(), "email": f"{name}@ipb.ac.id", "password": "rahasia123", "confirm_password": "rahasia123"})
    tok = client.post("/api/login", data={"username": f"{name}@ipb.ac.id", "password": "rahasia123"}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def upload(client, h, name, content, kind, dataset_id=None, **form):
    data = {**({"dataset_id": str(dataset_id)} if dataset_id else {}), **form}
    return client.post(f"/api/gwas/upload-{kind}", headers=h, files={"file": (name, content)}, data=data)


def make_dataset(client, h, domain=None, vcf_text=None, pheno_text=None):
    v, p = simulate()
    r_pheno = upload(client, h, "phenotype.csv", pheno_text or p, "phenotype", **({"source_domain": domain} if domain else {}))
    body = r_pheno.json(); ds_id = body.get("dataset_id") or body["dataset"]["dataset_id"]
    upload(client, h, "cohort.vcf", vcf_text or v, "genotype", dataset_id=ds_id)
    return ds_id


def cfg(ds, **over):
    base = {"dataset_id": ds, "trait_name": "PHENO_HEIGHT", "trait_type": "quantitative", "model": "linear",
            "qc": {"maf": 0.05, "call_rate": 0.95, "hwe_p": 1e-6}, "use_pca": True, "pca_components": 5, "covariates": ["Age"]}
    return {**base, **over}


def code(r):
    return r.json()["detail"]["code"]


# ---------------------------------------------------------------- upload (Step 1)
def test_upload_requires_login(gclient):
    assert gclient.post("/api/gwas/upload-genotype", files={"file": ("a.vcf", b"x")}).status_code == 401


def test_upload_rejects_unsupported_formats(gclient):
    h = login(gclient)
    r = upload(gclient, h, "data.ped", b"x", "genotype")
    assert r.status_code == 400 and code(r) == "INVALID_GENOTYPE_FORMAT"
    r = upload(gclient, h, "pheno.xlsx", b"x", "phenotype")
    assert r.status_code == 400 and code(r) == "INVALID_PHENOTYPE_FORMAT"
    assert upload(gclient, h, "x.vcf", b"x", "genotype", source_domain="astrologi").status_code == 400


def test_upload_ignores_client_filename_path_traversal(gclient, tmp_path):
    h = login(gclient)
    r = upload(gclient, h, "../../../evil.vcf", b"##fileformat=VCFv4.2\n", "genotype")
    assert r.status_code == 200
    assert r.json()["files"][0]["slot"] == "genotype.vcf"
    uid = gclient.get("/api/me", headers=h).json()["user_id"]
    saved = tmp_path / "store" / f"u{uid}" / f"ds_{r.json()['dataset_id']}" / "genotype.vcf"
    assert saved.is_file()
    assert not (tmp_path / "evil.vcf").exists()
    assert list((tmp_path / "store").rglob("evil*")) == []


def test_upload_size_limit(gclient, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    h = login(gclient)
    r = upload(gclient, h, "big.vcf", b"x" * (2 * 1024 * 1024), "genotype")
    assert r.status_code == 413 and code(r) == "FILE_TOO_LARGE"
    assert list((get_settings() and __import__("pathlib").Path(get_settings().storage_dir)).rglob(".upload-*")) == []  # tak ada sisa sementara


# ---------------------------------------------------------------- validasi (Step 2)
def test_validate_valid_dataset_and_trait_detection(gclient):
    h = login(gclient)
    ds = make_dataset(gclient, h)
    r = gclient.post(f"/api/gwas/datasets/{ds}/validate", headers=h)
    body = r.json()
    assert r.status_code == 200 and body["status"] == "valid", body["errors"]
    assert body["matching"]["matched"] == 120 and body["genotype"]["format"] == "vcf"
    traits = {t["name"]: t["type"] for t in body["phenotype"]["traits"]}
    assert traits == {"PHENO_HEIGHT": "quantitative", "PHENO_DISEASE": "binary"}   # Sex (teks) bukan trait
    assert "COV_SEX" not in body["phenotype"]["numeric_columns"]
    assert body["phenotype"]["categorical_columns"] == [{"name": "COV_SEX", "levels": ["F", "M"]}]   # Kode (120 level) bukan kovariat


def test_validate_incomplete_plink_set_and_corrupt_bed(gclient):
    h = login(gclient)
    ds = upload(gclient, h, "a.bed", b"\x6c\x1b\x01", "genotype").json()["dataset_id"]
    upload(gclient, h, "a.fam", "S1 S1 0 0 0 -9\n", "genotype", ds)
    r = gclient.post(f"/api/gwas/datasets/{ds}/validate", headers=h).json()
    assert r["status"] == "incomplete" and any(e["code"] == "GENOTYPE_INCOMPLETE" and ".bim" in e["message"] for e in r["errors"])
    upload(gclient, h, "a.bim", "1 rs1 0 100 A G\n", "genotype", ds)
    upload(gclient, h, "a.bed", b"not-a-bed-file", "genotype", ds)
    r = gclient.post(f"/api/gwas/datasets/{ds}/validate", headers=h).json()
    assert any(e["code"] == "BED_CORRUPT" for e in r["errors"])


def test_validate_sample_id_mismatch_and_bad_rows(gclient):
    h = login(gclient)
    v, p = simulate()
    header, *body = p.splitlines()
    # Seluruh ID fenotipe diubah -> tidak ada yang cocok (FR-09)
    none_match = "\n".join([header] + [ln.replace("S", "X", 2) for ln in body]) + "\n"
    ds = make_dataset(gclient, h, pheno_text=none_match)
    r = gclient.post(f"/api/gwas/datasets/{ds}/validate", headers=h).json()
    assert r["status"] == "invalid" and any(e["code"] == "NO_MATCHING_SAMPLES" for e in r["errors"])
    assert r["matching"]["matched"] == 0 and r["matching"]["unmatched_phenotype"] == 120 and r["matching"]["unmatched_genotype_examples"]

    # Sebagian cocok (>= minimum) -> valid, tetapi ada peringatan jumlah sampel yang dikeluarkan
    partial = p.replace("S00", "X00")           # 10 sampel (S000..S009) tidak cocok
    ds1 = make_dataset(gclient, h, pheno_text=partial)
    r = gclient.post(f"/api/gwas/datasets/{ds1}/validate", headers=h).json()
    assert r["status"] == "valid" and r["matching"]["matched"] == 110 and r["warnings"]

    # Baris dengan jumlah kolom salah dilaporkan beserta nomor barisnya (NFR-05)
    ds2 = make_dataset(gclient, h, pheno_text=p.replace("S005,S005,", "S005,", 1))
    r = gclient.post(f"/api/gwas/datasets/{ds2}/validate", headers=h).json()
    e = next(e for e in r["errors"] if e["code"] == "PHENOTYPE_ROW_LENGTH")
    assert "7" in e["message"] and e["hint"]


def test_validate_writes_phenotype_rows(gclient):
    from sqlalchemy import select
    from app.models import Phenotype
    h = login(gclient)
    ds = make_dataset(gclient, h)
    gclient.post(f"/api/gwas/datasets/{ds}/validate", headers=h)
    db = next(app.dependency_overrides[get_db]())
    rows = db.scalars(select(Phenotype).where(Phenotype.accession_id == ds)).all()
    assert {r.trait_name for r in rows} == {"PHENO_HEIGHT", "PHENO_DISEASE"}


# ---------------------------------------------------------------- konfigurasi (Step 3-4)
def validated(client, h, **kw):
    ds = make_dataset(client, h, **kw)
    client.post(f"/api/gwas/datasets/{ds}/validate", headers=h)
    return ds


def test_config_range_errors_are_indonesian(gclient):
    h = login(gclient)
    ds = validated(gclient, h)
    r = gclient.post("/api/gwas/config/check", headers=h, json=cfg(ds, qc={"maf": 0.9}))
    assert r.status_code == 422
    err = {e["field"]: e["message"] for e in r.json()["detail"]["errors"]}
    assert err["qc.maf"] == "Ambang MAF harus lebih kecil atau sama dengan 0.5."


@pytest.mark.parametrize("over,expected", [
    ({"model": "linear", "trait_name": "PHENO_DISEASE", "trait_type": "binary"}, "MODEL_TRAIT_MISMATCH"),
    ({"trait_type": "binary"}, "TRAIT_TYPE_MISMATCH"),
    ({"trait_name": "Nama"}, "TRAIT_NOT_FOUND"),
    ({"covariates": ["COV_CODE"]}, "COVARIATE_NOT_SUPPORTED"),
    ({"covariates": ["Age", "Sex"]}, None),            # kategori sederhana (L/P) diterima
    ({"covariates": ["PHENO_HEIGHT"]}, "COVARIATE_IS_TRAIT"),
    ({"pca_components": 20, "use_pca": True}, None),
])
def test_config_business_rules(gclient, over, expected):
    h = login(gclient)
    ds = validated(gclient, h)
    r = gclient.post("/api/gwas/config/check", headers=h, json=cfg(ds, **over)).json()
    codes = [e["code"] for e in r["errors"]]
    assert (expected in codes) if expected else r["valid"]


def test_auto_model_follows_trait_type(gclient):
    h = login(gclient)
    ds = validated(gclient, h)
    r = gclient.post("/api/gwas/config/check", headers=h, json=cfg(ds, model="auto", trait_name="PHENO_DISEASE", trait_type="binary")).json()
    assert r["valid"] and r["config"]["model"] == "logistic"


def test_guided_mode_requires_matching_domain_and_applies_preset(gclient):
    h = login(gclient)
    ds = validated(gclient, h, domain="kesehatan")
    base = cfg(ds, analysis_mode="guided", covariates=[])
    base.pop("qc"); base.pop("use_pca"); base.pop("pca_components")
    bad = gclient.post("/api/gwas/config/check", headers=h, json={**base, "domain": "agrikultur"}).json()
    assert [e["code"] for e in bad["errors"]] == ["DOMAIN_MISMATCH"]          # BR-04
    ok = gclient.post("/api/gwas/config/check", headers=h, json={**base, "domain": "kesehatan"}).json()
    assert ok["valid"] and ok["config"]["qc"]["maf"] == 0.01 and ok["config"]["pca_components"] == 10 and ok["config"]["use_pca"]
    # revisi integrasi: tanpa `domain`, Guided Mode memakai domain yang tercatat pada dataset (frontend belum punya dropdown domain)
    fb = gclient.post("/api/gwas/config/check", headers=h, json={**base}).json()
    assert fb["valid"] and fb["config"]["domain"] == "kesehatan" and fb["config"]["qc"]["maf"] == 0.01
    # dataset TANPA domain + tanpa `domain` -> tetap DOMAIN_REQUIRED
    ds2 = validated(gclient, h)
    nb = {**cfg(ds2, analysis_mode="guided", covariates=[])}
    assert gclient.post("/api/gwas/config/check", headers=h, json=nb).json()["errors"][0]["code"] == "DOMAIN_REQUIRED"


def test_cannot_run_unvalidated_dataset(gclient):             # BR-02
    h = login(gclient)
    ds = make_dataset(gclient, h)
    r = gclient.post("/api/gwas/analyses", headers=h, json=cfg(ds))
    assert r.status_code == 422 and code(r) == "CONFIG_INVALID"
    assert r.json()["detail"]["errors"][0]["code"] == "DATASET_NOT_VALIDATED"


def test_parameter_docs_and_presets_available(gclient):
    h = login(gclient)
    assert any(p["name"] == "qc.maf" for p in gclient.get("/api/gwas/config/parameters", headers=h).json()["parameters"])
    assert set(gclient.get("/api/gwas/config/presets", headers=h).json()["presets"]) == {"kesehatan", "farmasi", "agrikultur", "peternakan"}


# ---------------------------------------------------------------- BR-05 & BR-06
def test_other_users_cannot_touch_my_data(gclient):
    a, b = login(gclient, "akmal"), login(gclient, "budi")
    ds = validated(gclient, a)
    assert gclient.get(f"/api/gwas/datasets/{ds}", headers=b).status_code == 404
    assert gclient.post(f"/api/gwas/datasets/{ds}/validate", headers=b).status_code == 404
    assert upload(gclient, b, "x.fam", "S1 S1 0 0 0 -9\n", "genotype", ds).status_code == 404
    assert gclient.post("/api/gwas/analyses", headers=b, json=cfg(ds)).status_code == 404
    assert gclient.get("/api/gwas/datasets", headers=b).json()["items"] == []


def test_results_blocked_until_analysis_done(gclient):         # BR-05
    from app.models import GwasAnalysis
    h = login(gclient)
    ds = validated(gclient, h)
    db = next(app.dependency_overrides[get_db]())
    uid = gclient.get("/api/me", headers=h).json()["user_id"]
    a = GwasAnalysis(user_id=uid, accession_id=ds, status="running", model_used="Linear Regression")
    db.add(a); db.commit()
    from app.services import jobs
    jobs.prepare_run(jobs.run_dir_for(db, a), cfg(ds), uid)
    for path in ("results/summary", "results/manhattan", "results/qq", "results/significant", "results/top", "export"):
        r = gclient.get(f"/api/gwas/analyses/{a.analysis_id}/{path}", headers=h)
        assert r.status_code == 409 and code(r) == "ANALYSIS_NOT_FINISHED", path
    assert gclient.post(f"/api/gwas/analyses/{a.analysis_id}/retry", headers=h).status_code == 409   # hanya failed yang bisa di-retry


# ---------------------------------------------------------------- integrasi penuh dengan PLINK 2 / GEMMA
def run_analysis(client, h, ds, **over):
    r = client.post("/api/gwas/analyses", headers=h, json=cfg(ds, **over))
    assert r.status_code == 202, r.text
    return r.json()["analysis_id"]       # BackgroundTasks berjalan sinkron di TestClient -> job sudah selesai


@needs_plink
def test_full_pipeline_linear_with_pca_and_covariate(gclient):
    h = login(gclient)
    ds = validated(gclient, h)
    aid = run_analysis(gclient, h, ds, covariates=["Age", "Sex"])   # Sex (F/M) dikodekan otomatis menjadi dummy 0/1
    base = f"/api/gwas/analyses/{aid}"
    st = gclient.get(f"{base}/status", headers=h).json()
    assert st["status"] == "done" and st["percent"] == 100, st

    summ = gclient.get(f"{base}/results/summary", headers=h).json()
    assert summ["best_variant"]["rsid"] == CAUSAL and summ["best_variant"]["p_value"] < 1e-8
    assert summ["n_variants_tested"] > 250 and summ["n_significant"] >= 1

    qc = gclient.get(f"{base}/qc-summary", headers=h).json()
    assert qc["variants_after"] <= qc["variants_before"] == 300 and qc["samples_after"] == 120
    pca = gclient.get(f"{base}/pca", headers=h).json()
    assert pca["components"] == ["PC1", "PC2", "PC3", "PC4", "PC5"] and len(pca["points"]) == 120

    man = gclient.get(f"{base}/results/manhattan", headers=h).json()
    assert not man["filtered"] and len(man["position"]) == summ["n_variants_tested"] and max(man["neg_log10_p"]) > 8
    assert man["chromosome_order"] == ["1", "2"]
    qq = gclient.get(f"{base}/results/qq", headers=h).json()
    assert qq["n"] == summ["n_variants_tested"] and 0.3 < qq["lambda_gc"] < 3 and len(qq["observed_p"]) == len(qq["expected_p"])

    top = gclient.get(f"{base}/results/top?limit=3", headers=h).json()["items"]
    assert top[0]["rsid"] == CAUSAL and top[0]["odds_ratio"] is None   # linear: tidak ada odds ratio

    # Post-processing (Step 8): ambang yang lebih longgar -> lebih banyak signifikan; ambang bisa diganti ulang
    loose = gclient.post(f"{base}/postprocess", headers=h, json={"method": "custom", "threshold": 0.05}).json()["significance"]
    assert loose["n_significant"] > summ["n_significant"]
    bonf = gclient.post(f"{base}/postprocess", headers=h, json={"method": "bonferroni"}).json()["significance"]
    assert abs(bonf["threshold_p"] - 0.05 / summ["n_variants_tested"]) < 1e-12
    sig = gclient.get(f"{base}/results/significant", headers=h).json()
    assert sig["total"] == bonf["n_significant"] and sig["items"][0]["rsid"] == CAUSAL
    assert gclient.post(f"{base}/postprocess", headers=h, json={"method": "custom"}).status_code == 422

    # Ekspor (Step 10)
    csv_text = gclient.get(f"{base}/export", headers=h)
    lines = csv_text.text.strip().splitlines()
    assert csv_text.headers["content-disposition"].endswith('_1.csv"') or "gwas_analysis_" in csv_text.headers["content-disposition"]
    assert lines[0].startswith("chromosome,position,rsid") and len(lines) == summ["n_variants_tested"] + 1 and CAUSAL in lines[1]
    only = gclient.get(f"{base}/export?only_significant=true", headers=h).text.strip().splitlines()
    assert len(only) == bonf["n_significant"] + 1
    conf = gclient.get(f"{base}/config/export", headers=h).json()
    assert conf["configuration"]["trait_name"] == "PHENO_HEIGHT" and "plink2" in conf["software_versions"]
    from pathlib import Path
    covar = next(Path(get_settings().storage_dir).rglob("covar.txt")).read_text().splitlines()[0]
    assert covar == "FID\tIID\tCOV_AGE\tCOV_SEX_M\tPC1\tPC2\tPC3\tPC4\tPC5"

    # Riwayat (FR-19)
    hist = gclient.get("/api/gwas/analyses", headers=h).json()["items"]
    assert [x["analysis_id"] for x in hist] == [aid] and hist[0]["trait_name"] == "PHENO_HEIGHT"
    log = gclient.get(f"{base}/log", headers=h).json()
    assert log["finished"] and len(log["logs"]) > 0


@needs_plink
def test_full_pipeline_logistic_reports_odds_ratio_and_no_pca(gclient):
    h = login(gclient)
    ds = validated(gclient, h)
    aid = run_analysis(gclient, h, ds, trait_name="PHENO_DISEASE", trait_type="binary", model="auto", use_pca=False, covariates=[])
    base = f"/api/gwas/analyses/{aid}"
    assert gclient.get(f"{base}/status", headers=h).json()["status"] == "done"
    assert gclient.get(f"{base}", headers=h).json()["model_used"] == "Logistic Regression"
    top = gclient.get(f"{base}/results/top?limit=1", headers=h).json()["items"][0]
    assert top["rsid"] == CAUSAL and top["odds_ratio"] > 1
    assert gclient.get(f"{base}/pca", headers=h).status_code == 404          # PCA tidak dipakai


@needs_gemma
def test_full_pipeline_lmm_gemma(gclient):
    h = login(gclient)
    ds = validated(gclient, h)
    aid = run_analysis(gclient, h, ds, model="lmm", use_pca=False)
    base = f"/api/gwas/analyses/{aid}"
    assert gclient.get(f"{base}/status", headers=h).json()["status"] == "done"
    top = gclient.get(f"{base}/results/top?limit=1", headers=h).json()["items"][0]
    assert top["rsid"] == CAUSAL and top["p_value"] < 1e-8
    assert gclient.get(f"{base}", headers=h).json()["model_used"] == "LMM (GEMMA)"


@needs_plink
def test_plink_bed_input_and_skip_qc(gclient, tmp_path):
    """Jalur input PLINK .bed/.bim/.fam (bukan VCF), dengan QC dilewati."""
    import subprocess
    v, p = simulate()
    (tmp_path / "s.vcf").write_text(v)
    subprocess.run([get_settings().plink2_bin, "--vcf", str(tmp_path / "s.vcf"), "--double-id", "--allow-extra-chr", "--make-bed", "--out", str(tmp_path / "s")],
                   check=True, capture_output=True)
    h = login(gclient)
    ds = upload(gclient, h, "s.bed", (tmp_path / "s.bed").read_bytes(), "genotype").json()["dataset_id"]
    for ext in ("bim", "fam"):
        upload(gclient, h, f"s.{ext}", (tmp_path / f"s.{ext}").read_bytes(), "genotype", ds)
    upload(gclient, h, "p.csv", p, "phenotype", ds)
    rep = gclient.post(f"/api/gwas/datasets/{ds}/validate", headers=h).json()
    assert rep["status"] == "valid" and rep["genotype"]["format"] == "plink"
    aid = run_analysis(gclient, h, ds, qc={"skip_qc": True}, use_pca=False, covariates=[])
    assert gclient.get(f"/api/gwas/analyses/{aid}/status", headers=h).json()["status"] == "done"
    assert gclient.get(f"/api/gwas/analyses/{aid}/qc-summary", headers=h).json()["skipped"] is True


@needs_plink
def test_failure_is_isolated_and_retry_works(gclient, monkeypatch):
    """NFR-08: tool hilang -> analisis 'failed' dengan pesan jelas, server tetap hidup; setelah diperbaiki, retry berhasil."""
    h = login(gclient)
    ds = validated(gclient, h)
    good = get_settings().plink2_bin
    monkeypatch.setattr(get_settings(), "plink2_bin", "plink2-tidak-ada")
    aid = run_analysis(gclient, h, ds)
    st = gclient.get(f"/api/gwas/analyses/{aid}/status", headers=h).json()
    assert st["status"] == "failed" and st["error"]["code"] == "TOOL_NOT_FOUND" and st["error"]["hint"]
    assert gclient.get("/health").status_code in (200, 503)               # server tetap merespons
    assert gclient.get(f"/api/gwas/analyses/{aid}/results/summary", headers=h).status_code == 409

    monkeypatch.setattr(get_settings(), "plink2_bin", good)
    assert gclient.post(f"/api/gwas/analyses/{aid}/retry", headers=h).status_code == 202
    assert gclient.get(f"/api/gwas/analyses/{aid}/status", headers=h).json()["status"] == "done"
    assert gclient.get(f"/api/gwas/analyses/{aid}/results/summary", headers=h).json()["n_variants_tested"] > 250


@needs_plink
def test_results_isolated_between_users_and_websocket(gclient):
    a, b = login(gclient, "akmal"), login(gclient, "budi")
    ds = validated(gclient, a)
    aid = run_analysis(gclient, a, ds)
    for path in ("", "/status", "/log", "/results/summary", "/export", "/qc-summary", "/pca"):
        assert gclient.get(f"/api/gwas/analyses/{aid}{path}", headers=b).status_code == 404, path
    assert gclient.post(f"/api/gwas/analyses/{aid}/postprocess", headers=b, json={"method": "genome_wide"}).status_code == 404
    assert gclient.get("/api/gwas/analyses", headers=b).json()["items"] == []

    tok_a = a["Authorization"].split()[1]
    with gclient.websocket_connect(f"/api/gwas/analyses/{aid}/ws?token={tok_a}") as ws:
        msgs = []
        while True:
            try:
                msgs.append(ws.receive_json())
            except Exception:
                break
    assert any(m["type"] == "log" for m in msgs) and msgs[-1]["type"] == "progress" and msgs[-1]["state"] == "done"
    with pytest.raises(Exception):   # token tidak valid / bukan pemilik -> ditolak
        with gclient.websocket_connect(f"/api/gwas/analyses/{aid}/ws?token=salah"):
            pass
    with pytest.raises(Exception):
        with gclient.websocket_connect(f"/api/gwas/analyses/{aid}/ws?token={b['Authorization'].split()[1]}"):
            pass


def test_large_results_are_filtered_for_plots(gclient, monkeypatch):
    """UC-04 Alt-04: hasil besar tidak dikirim utuh; varian paling signifikan tetap ada dan UI diberi pesan."""
    from app.models import GwasAnalysis, GwasResult, Snp
    from sqlalchemy import insert
    h = login(gclient)
    ds = validated(gclient, h)
    db = next(app.dependency_overrides[get_db]())
    uid = gclient.get("/api/me", headers=h).json()["user_id"]
    a = GwasAnalysis(user_id=uid, accession_id=ds, status="done", model_used="Linear Regression")
    db.add(a); db.commit()
    n = 5000
    db.execute(insert(Snp), [{"rsid": f"rs{i}", "chromosome": "1", "position": i + 1, "ref_allele": "A", "alt_allele": "G"} for i in range(n)])
    db.execute(insert(GwasResult), [{"analysis_id": a.analysis_id, "snp_id": i + 1, "p_value": 10.0 ** -(10 + i - (n - 20)) if i >= n - 20 else 0.5 + (i % 100) / 250,
                                     "effect_size": 0.1, "is_significant": False} for i in range(n)])
    db.commit()
    r = gclient.get(f"/api/gwas/analyses/{a.analysis_id}/results/manhattan?max_points=1000", headers=h).json()
    assert r["filtered"] and r["total_variants"] == n and r["displayed_variants"] <= 1100 and "diambil sampelnya" in r["message"]
    assert max(r["neg_log10_p"]) > 9               # varian paling signifikan tetap tampil
    q = gclient.get(f"/api/gwas/analyses/{a.analysis_id}/results/qq?max_points=1000", headers=h).json()
    assert q["filtered"] and len(q["observed_p"]) <= 1100 and q["lambda_gc"] is not None


def test_ai_interpretation_json_export_is_owner_scoped(gclient):
    from app import database
    from app.models import Accession, GwasAnalysis, GwasInterpretation

    headers = login(gclient)
    dataset_id = validated(gclient, headers)
    user_id = gclient.get("/api/me", headers=headers).json()["user_id"]
    payload = {"executive_summary": "Ringkasan statistik hasil uji.", "findings": []}
    db = database.SessionLocal()
    try:
        analysis = GwasAnalysis(user_id=user_id, accession_id=dataset_id, status="done", model_used="Linear Regression")
        db.add(analysis)
        db.flush()
        db.add(GwasInterpretation(analysis_id=analysis.analysis_id, status="done", provider="test",
                                  model="test-model", result_json=payload))
        db.commit()
        analysis_id = analysis.analysis_id
    finally:
        db.close()

    response = gclient.get(f"/api/gwas/analyses/{analysis_id}/interpretation/export", headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert f'gwas_analysis_{analysis_id}_ai_interpretation.json' in response.headers["content-disposition"]
    assert response.json()["result"] == payload
    other_user = login(gclient, "pengguna-lain")
    assert gclient.get(f"/api/gwas/analyses/{analysis_id}/interpretation/export", headers=other_user).status_code == 404
