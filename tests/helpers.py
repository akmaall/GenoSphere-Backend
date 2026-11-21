"""Fungsi bantu untuk tes: membuat pengguna, mengunggah data, menunggu analisis selesai, dan membuat hasil sintetis."""
import shutil
import time
from pathlib import Path

from sqlalchemy import insert

from app.models import Accession, GwasAnalysis, GwasResult, Snp
from app.services import jobs
from app.services import results as R
from tests.conftest import TestingSession

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "sample_data"
HAS_PLINK = shutil.which("plink2") is not None
HAS_GEMMA = shutil.which("gemma") is not None


def err_code(resp) -> str:
    return resp.json()["detail"]["code"]


def make_user(client, email="peneliti@ipb.ac.id", password="rahasia123"):
    """Daftar dan login. Mengembalikan (header Authorization, user_id)."""
    client.post("/api/register", json={"full_name": "Peneliti Uji", "email": email, "password": password, "confirm_password": password})
    token = client.post("/api/login", json={"identifier": email, "password": password}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    return headers, client.get("/api/me", headers=headers).json()["user_id"]


def upload(client, headers, kind, files, dataset_id=None, **form):
    """Upload through the documented single-file endpoint; genotype triplets are sent sequentially."""
    data = {k: v for k, v in form.items() if v is not None}
    current_dataset_id = dataset_id
    response = None
    for filename, content in files:
        request_data = dict(data)
        if current_dataset_id is not None:
            request_data["dataset_id"] = current_dataset_id
        response = client.post(
            f"/api/gwas/upload-{kind}", headers=headers, data=request_data,
            files={"file": (filename, content[0] if isinstance(content, tuple) else content,
                             content[1] if isinstance(content, tuple) and len(content) > 1 else None)},
        )
        if response.status_code >= 400:
            return response
        current_dataset_id = response.json().get("dataset_id", current_dataset_id)
        data = {}  # metadata applies only to the initial dataset creation
    return response


def upload_sample_cohort(client, headers, domain="kesehatan"):
    """Mengunggah data contoh (120 sampel, 300 varian) dan mengembalikan dataset_id."""
    g = upload(client, headers, "genotype", [("cohort.vcf", (SAMPLE_DIR / "cohort_sim.vcf").read_bytes())],
               name="Kohort uji", source_domain=domain)
    ds = g.json()["dataset_id"]
    upload(client, headers, "phenotype", [("pheno.csv", (SAMPLE_DIR / "phenotype_sim.csv").read_bytes())], dataset_id=ds)
    return ds


def valid_sample_dataset(client, headers, domain="kesehatan"):
    ds = upload_sample_cohort(client, headers, domain)
    assert client.post(f"/api/gwas/datasets/{ds}/validate", headers=headers).json()["status"] == "valid"
    return ds


def base_config(ds, **over):
    cfg = {"dataset_id": ds, "trait_name": "PHENO_HEIGHT", "trait_type": "quantitative", "model": "auto", "qc": {"maf": 0.05}}
    cfg.update(over)
    return cfg


def wait_for(client, headers, analysis_id, states=("done", "failed"), timeout=180):
    """Menunggu sampai analisis mencapai salah satu status. Mengembalikan isi /status terakhir."""
    end = time.time() + timeout
    while time.time() < end:
        s = client.get(f"/api/gwas/analyses/{analysis_id}/status", headers=headers).json()
        if s["status"] in states:
            return s
        time.sleep(0.5)
    raise AssertionError(f"Analisis {analysis_id} tidak mencapai {states} dalam {timeout} detik: {s}")


def synthetic_analysis(user_id, pvalues, chroms=None, method="genome_wide", threshold=None, logistic=False, effect=0.5):
    """Membuat analisis berstatus done langsung di database dengan p-value yang ditentukan sendiri, tanpa menjalankan PLINK.
    Dipakai untuk menguji logika hasil dengan angka yang bisa dihitung tangan. Mengembalikan analysis_id."""
    db = TestingSession()
    try:
        acc = Accession(sample_code="sintetis", genotype_file_path=None, source_domain="kesehatan")
        db.add(acc)
        db.flush()
        acc.genotype_file_path = f"u{user_id}/ds_{acc.accession_id}/genotype"
        a = GwasAnalysis(user_id=user_id, accession_id=acc.accession_id, analysis_mode="manual", qc_skipped=False, pca_used=False,
                         model_used="Logistic Regression" if logistic else "Linear Regression", status="done")
        db.add(a)
        db.commit()
        n = len(pvalues)
        chroms = chroms or ["1"] * n
        db.execute(insert(Snp), [{"snp_id": 100000 * a.analysis_id + i, "rsid": f"rs{i}", "chromosome": chroms[i], "position": 1000 + i,
                                   "ref_allele": "A", "alt_allele": "G"} for i in range(n)])
        db.execute(insert(GwasResult), [{"analysis_id": a.analysis_id, "snp_id": 100000 * a.analysis_id + i, "p_value": p,
                                          "effect_size": effect, "is_significant": False} for i, p in enumerate(pvalues)])
        db.commit()
        rdir = jobs.run_dir_for(db, a)
        jobs.prepare_run(rdir, {"trait_name": "T", "trait_type": "quantitative", "model": "linear", "execution_mode": "auto",
                                 "qc": {"skip_qc": False, "maf": 0.05, "call_rate": 0.95, "hwe_p": 1e-6}, "use_pca": False,
                                 "pca_components": None, "covariates": [], "significance_method": method,
                                 "significance_threshold": threshold}, user_id)
        rule = R.apply_significance(db, a.analysis_id, method, threshold)
        R.save_rule(rdir, rule)
        jobs.write_progress(rdir, state="done", stage="done", percent=100)
        return a.analysis_id
    finally:
        db.close()
