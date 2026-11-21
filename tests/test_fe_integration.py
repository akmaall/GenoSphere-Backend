"""Tes tambahan dari revisi "genosphere-backend-revisi" yang diadopsi (login JSON, opsi dropdown) + tes FDR/ekspor
berbasis data sintetis di database (tanpa PLINK), disesuaikan ke kode gabungan."""
import json as _json

import pytest
from sqlalchemy import insert

from app.database import get_db
from app.main import app
from app.models import GwasAnalysis, GwasResult, Snp
from app.services import jobs
from tests.test_gwas import cfg, code, gclient, login, make_dataset  # noqa: F401  (gclient = fixture)

FE_REGISTER = {"fullName": "Dr. John Doe", "email": "peneliti@institusi.ac.id", "password": "rahasia123", "confirmPassword": "rahasia123"}


# ---------------------------------------------------------------- auth & profil (halaman register/login/profile)




def test_login_json_accepts_email_or_username(gclient):
    gclient.post("/api/register", json={**FE_REGISTER, "username": "johndoe"})
    for ident in ("peneliti@institusi.ac.id", "JohnDoe"):
        r = gclient.post("/api/login/json", json={"identifier": ident, "password": "rahasia123"})
        assert r.status_code == 200 and r.json()["user"]["email"] == "peneliti@institusi.ac.id"
    assert gclient.post("/api/login/json", json={"email": "peneliti@institusi.ac.id", "password": "rahasia123"}).status_code == 200
    bad = gclient.post("/api/login/json", json={"identifier": "johndoe", "password": "salahsalah"})
    assert bad.status_code == 401 and code(bad) == "INVALID_CREDENTIALS"






# ---------------------------------------------------------------- Step 1-4


def test_config_options_match_frontend_dropdowns(gclient):
    h = login(gclient)
    o = gclient.get("/api/gwas/config/options", headers=h).json()["options"]
    assert [x["label"] for x in o["trait_type"]] == ["Biner (Case / Control)", "Kuantitatif (Kontinu)"]
    assert [x["label"] for x in o["model"]] == ["Logistic Regression", "Linear Regression", "Linear Mixed Model (LMM)"]
    assert [x["method"] for x in o["significance"]] == ["genome_wide", "suggestive", "bonferroni", "fdr", "custom"]
    assert [x["label"] for x in o["pca_components"]] == ["5 PC", "10 PC", "20 PC"]
    assert [x["label"] for x in o["export_formats"]] == ["CSV (.csv)", "TSV (.tsv)", "JSON (.json)", "Excel (.xlsx)"]




# ---------------------------------------------------------------- Step 8-10 (tanpa PLINK)
def _done_analysis(client, h, pvals):
    ds = make_dataset(client, h)
    client.post(f"/api/gwas/datasets/{ds}/validate", headers=h)
    db = next(app.dependency_overrides[get_db]())
    uid = client.get("/api/me", headers=h).json()["user_id"]
    a = GwasAnalysis(user_id=uid, accession_id=ds, status="done", model_used="Logistic Regression")
    db.add(a); db.commit()
    jobs.prepare_run(jobs.run_dir_for(db, a), cfg(ds), uid)
    db.execute(insert(Snp), [{"rsid": f"rs{i}", "chromosome": "1", "position": i + 1, "ref_allele": "A", "alt_allele": "G"} for i in range(len(pvals))])
    db.execute(insert(GwasResult), [{"analysis_id": a.analysis_id, "snp_id": i + 1, "p_value": p, "effect_size": 0.5, "is_significant": False}
                                    for i, p in enumerate(pvals)])
    db.commit()
    return a.analysis_id


def test_postprocess_accepts_dropdown_labels_and_fdr(gclient):
    h = login(gclient)
    # 100 varian: 3 sangat kecil, sisanya seragam di atas 0.2
    pv = [1e-12, 1e-9, 4e-4] + [0.2 + i / 200 for i in range(97)]
    aid = _done_analysis(gclient, h, pv)
    url = f"/api/gwas/analyses/{aid}/postprocess"
    gw = gclient.post(url, headers=h, json={"method": "5e-8 (Bonferroni)"}).json()
    assert gw["significance"]["method"] == "genome_wide" and gw["significance"]["threshold_p"] == 5e-8 and gw["significance"]["n_significant"] == 2
    sg = gclient.post(url, headers=h, json={"method": "1e-5 (Suggestive)"}).json()
    assert sg["significance"]["method"] == "suggestive" and sg["significance"]["n_significant"] == 2
    fdr = gclient.post(url, headers=h, json={"method": "0.05 (FDR)"}).json()
    # BH, q=0.05, m=100: p(3)=4e-4 <= 3/100*0.05=1.5e-3 lolos -> 3 varian.
    assert fdr["significance"]["method"] == "fdr" and fdr["significance"]["n_significant"] == 3 and fdr["significance"]["threshold_p"] == pytest.approx(4e-4)
    bon = gclient.post(url, headers=h, json={"method": "bonferroni"}).json()
    assert bon["significance"]["threshold_p"] == pytest.approx(0.05 / 100)


def test_postprocess_fdr_with_no_hits_gives_zero(gclient):
    h = login(gclient)
    aid = _done_analysis(gclient, h, [0.3 + i / 100 for i in range(20)])
    r = gclient.post(f"/api/gwas/analyses/{aid}/postprocess", headers=h, json={"method": "fdr"}).json()
    assert r["significance"]["threshold_p"] == 0.0 and r["significance"]["n_significant"] == 0


def test_export_formats_csv_tsv_json(gclient):
    import json as _json
    h = login(gclient)
    aid = _done_analysis(gclient, h, [1e-9, 0.01, 0.5])
    base = f"/api/gwas/analyses/{aid}/export"
    csv_r = gclient.get(base, headers=h)
    assert csv_r.headers["content-type"].startswith("text/csv") and csv_r.headers["content-disposition"].endswith('.csv"')
    assert len(csv_r.text.strip().splitlines()) == 4
    tsv_r = gclient.get(base + "?format=tsv", headers=h)
    lines = tsv_r.text.strip().splitlines()
    assert lines[0].split("\t")[:3] == ["chromosome", "position", "rsid"] and len(lines) == 4 and lines[1].split("\t")[7] == "1e-09"
    assert tsv_r.headers["content-disposition"].endswith('.tsv"')
    js = _json.loads(gclient.get(base + "?format=json", headers=h).text)
    assert len(js) == 3 and js[0]["p_value"] == 1e-9 and js[0]["odds_ratio"] is not None
    assert gclient.get(base + "?format=xlsx", headers=h).content[:2] == b"PK"      # Excel kini didukung
    assert gclient.get(base + "?format=pdf", headers=h).status_code == 400
