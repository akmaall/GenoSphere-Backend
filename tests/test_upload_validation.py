"""Catatan analis no. 4 sampai 8: unggah, isi fenotipe, kelengkapan PLINK, penghapusan berkas, error dan warning."""
from pathlib import Path

from app.main import app
from tests import gwas_data as D
from tests.helpers import err_code, make_user, upload, valid_sample_dataset


def _ds(client, headers, files=None, pheno=None, domain=None):
    g = upload(client, headers, "genotype", files or D.plink_files(), source_domain=domain)
    ds = g.json()["dataset_id"]
    if pheno is not None:
        upload(client, headers, "phenotype", [("p.csv", pheno)], dataset_id=ds)
    return ds


def _validate(client, headers, ds):
    r = client.post(f"/api/gwas/datasets/{ds}/validate", headers=headers)
    assert r.status_code == 200
    return r.json()


def _codes(items):
    return [i["code"] for i in items]


# ---------- upload satu berkas per request ----------
def test_upload_endpoints_document_single_file_field():
    spec = app.openapi()
    for ep in ("/api/gwas/upload-genotype", "/api/gwas/upload-phenotype"):
        body = spec["paths"][ep]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"]
        ref = body["$ref"].split("/")[-1]
        props = spec["components"]["schemas"][ref]["properties"]
        assert "file" in props and "files" not in props


def test_upload_accepts_single_file_field(client):
    headers, _ = make_user(client)
    r = client.post("/api/gwas/upload-phenotype", headers=headers, files={"file": ("p.csv", D.pheno_csv())})
    assert r.status_code == 200 and len(r.json()["files"]) == 1


def test_upload_response_reports_current_file_and_full_dataset(client):
    headers, _ = make_user(client)
    ds = upload(client, headers, "genotype", [("g.bed", D.bed())], name="Kohort A").json()["dataset_id"]
    upload(client, headers, "genotype", [("g.bim", D.bim())], dataset_id=ds)
    r = upload(client, headers, "genotype", [("g.fam", D.fam())], dataset_id=ds)
    body = r.json()
    assert r.status_code == 200 and body["status"] == "success" and isinstance(body["dataset_id"], int)
    assert [f["slot"] for f in body["files"]] == ["genotype.fam"]
    assert {f["slot"] for f in body["dataset_files"]} == {"genotype.bed", "genotype.bim", "genotype.fam"}
    assert body["files"][0]["filename"] == "g.fam"
    assert body["ready_for_validation"] is False and body["missing"] == ["fenotipe belum diunggah"]


def test_phenotype_is_uploaded_one_file_per_request_and_replacement_is_single(client):
    headers, _ = make_user(client)
    first = client.post("/api/gwas/upload-phenotype", headers=headers, files={"file": ("a.csv", D.pheno_csv())})
    ds = first.json()["dataset_id"]
    second = client.post("/api/gwas/upload-phenotype", headers=headers, data={"dataset_id": str(ds)},
                         files={"file": ("b.tsv", D.pheno_csv().replace(b",", b"\t"))})
    assert first.status_code == 200 and second.status_code == 200
    assert [f["slot"] for f in second.json()["dataset_files"] if f["kind"] == "phenotype"] == ["phenotype.tsv"]


def test_invalid_upload_does_not_create_dataset_or_store_file(client):
    headers, _ = make_user(client)
    r = upload(client, headers, "genotype", [("virus.exe", b"x")])
    assert r.status_code == 400 and err_code(r) == "INVALID_GENOTYPE_FORMAT"
    assert client.get("/api/gwas/datasets", headers=headers).json()["items"] == []


# ---------- no. 5: isi fenotipe ----------
def test_phenotype_valid_file_passes(client):
    headers, _ = make_user(client)
    ds = _ds(client, headers, pheno=D.pheno_csv())
    rep = _validate(client, headers, ds)
    assert rep["status"] == "valid" and rep["can_proceed"] is True and rep["errors"] == []
    traits = {t["name"]: t for t in rep["phenotype"]["traits"]}
    assert traits["PHENO_HEIGHT"]["type"] == "quantitative" and traits["PHENO_DISEASE"]["type"] == "binary"
    assert traits["PHENO_DISEASE"]["n_cases"] == 6 and traits["PHENO_DISEASE"]["n_controls"] == 6


def test_phenotype_without_id_column_is_error(client):
    headers, _ = make_user(client)
    rep = _validate(client, headers, _ds(client, headers, pheno=D.pheno_csv(header="A,B,PHENO_HEIGHT", rows=[f"x,y,{n}" for n in range(12)])))
    assert rep["status"] == "invalid" and "PHENOTYPE_HEADER_INVALID" in _codes(rep["errors"])


def test_phenotype_without_trait_column_is_error(client):
    headers, _ = make_user(client)
    rep = _validate(client, headers, _ds(client, headers, pheno=D.pheno_csv(header="FID,IID,COV_CATATAN", rows=[f"{i},{i},baik" for i in D.IDS])))
    assert rep["status"] == "invalid" and "PHENOTYPE_NO_TRAIT" in _codes(rep["errors"])


def test_phenotype_duplicate_and_empty_ids_are_errors(client):
    headers, _ = make_user(client)
    rows = [f"{i},{i},{150 + n}" for n, i in enumerate(D.IDS)]
    rows[1] = "S00,S00,160"          # ID ganda
    rows[2] = ",,170"                # ID kosong
    rep = _validate(client, headers, _ds(client, headers, pheno=D.pheno_csv(header="FID,IID,PHENO_HEIGHT", rows=rows)))
    assert {"DUPLICATE_SAMPLE_ID", "PHENOTYPE_EMPTY_ID"} <= set(_codes(rep["errors"]))


def test_phenotype_mixed_numbers_and_text_is_format_error(client):
    """Nilai fenotipe harus sesuai tipe trait: kolom angka yang disusupi teks ditolak."""
    headers, _ = make_user(client)
    rows = [f"{i},{i},{150 + n}" for n, i in enumerate(D.IDS)]
    rows[3] = "S03,S03,tinggi"
    rep = _validate(client, headers, _ds(client, headers, pheno=D.pheno_csv(header="FID,IID,PHENO_HEIGHT", rows=rows)))
    assert rep["status"] == "invalid" and "PHENOTYPE_MIXED_VALUES" in _codes(rep["errors"])
    assert "PHENO_HEIGHT" in rep["errors"][0]["message"] and rep["errors"][0]["hint"]


def test_phenotype_row_length_mismatch_is_error(client):
    headers, _ = make_user(client)
    rows = [f"{i},{i},{150 + n}" for n, i in enumerate(D.IDS)]
    rows[4] = "S04,S04"
    rep = _validate(client, headers, _ds(client, headers, pheno=D.pheno_csv(header="FID,IID,PHENO_HEIGHT", rows=rows)))
    assert "PHENOTYPE_ROW_LENGTH" in _codes(rep["errors"])


def test_two_value_trait_not_coded_01_gets_warning(client):
    headers, _ = make_user(client)
    rows = [f"{i},{i},{150 + n},{3 if n % 2 else 5}" for n, i in enumerate(D.IDS)]
    rep = _validate(client, headers, _ds(client, headers, pheno=D.pheno_csv(header="FID,IID,PHENO_HEIGHT,PHENO_GROUP", rows=rows)))
    assert rep["status"] == "valid" and "PHENOTYPE_BINARY_CODING" in _codes(rep["warnings"])


# ---------- no. 6: PLINK satu kesatuan ----------
def test_plink_single_file_is_not_complete_genotype(client):
    headers, _ = make_user(client)
    r = upload(client, headers, "genotype", [("g.bed", D.bed())])
    body = r.json()
    assert body["ready_for_validation"] is False and body["validation_status"] == "incomplete"
    assert ".bim" in body["missing"][0] and ".fam" in body["missing"][0]
    ds = body["dataset_id"]
    upload(client, headers, "phenotype", [("p.csv", D.pheno_csv())], dataset_id=ds)
    rep = _validate(client, headers, ds)
    assert rep["status"] == "incomplete" and rep["can_proceed"] is False and "GENOTYPE_INCOMPLETE" in _codes(rep["errors"])


def test_plink_files_added_one_by_one_become_complete(client):
    headers, _ = make_user(client)
    ds = upload(client, headers, "genotype", [("g.bed", D.bed())]).json()["dataset_id"]
    upload(client, headers, "genotype", [("g.bim", D.bim())], dataset_id=ds)
    r = upload(client, headers, "genotype", [("g.fam", D.fam())], dataset_id=ds)
    assert r.json()["genotype_complete"] if "genotype_complete" in r.json() else True
    assert r.json()["missing"] == ["fenotipe belum diunggah"]
    assert len(r.json()["dataset_files"]) == 3


def test_plink_files_must_match_each_other(client):
    headers, _ = make_user(client)
    ds = _ds(client, headers, files=[("g.bed", D.bed(12, 4)), ("g.bim", D.bim(7)), ("g.fam", D.fam())], pheno=D.pheno_csv())
    rep = _validate(client, headers, ds)
    assert rep["status"] == "invalid" and "BED_SIZE_MISMATCH" in _codes(rep["errors"])


def test_vcf_and_plink_together_is_rejected(client):
    headers, _ = make_user(client)
    ds = upload(client, headers, "genotype", D.plink_files()).json()["dataset_id"]
    response = upload(
        client, headers, "genotype",
        [("x.vcf", b"##fileformat=VCFv4.2\\n")],
        dataset_id=ds
    )

    assert response.status_code == 400
    body = response.json()
    detail = body.get("detail", body)
    assert detail["code"] == "MIXED_GENOTYPE_FORMAT"

def test_deleting_a_file_updates_dataset_status(client):
    headers, _ = make_user(client)
    ds = _ds(client, headers, pheno=D.pheno_csv())
    assert _validate(client, headers, ds)["status"] == "valid"
    assert client.get(f"/api/gwas/datasets/{ds}", headers=headers).json()["validation_status"] == "valid"

    r = client.delete(f"/api/gwas/datasets/{ds}/files/genotype.fam", headers=headers)
    assert r.status_code == 200 and r.json()["deleted"] == "genotype.fam"
    state = r.json()["dataset"]
    assert state["validation_status"] == "incomplete" and state["can_proceed"] is False and state["ready_for_validation"] is False
    assert ".fam" in state["missing"][0]

    detail = client.get(f"/api/gwas/datasets/{ds}", headers=headers).json()
    assert detail["validation_status"] == "incomplete" and detail["validation"] is None
    assert client.get("/api/gwas/datasets", headers=headers).json()["items"][0]["validation_status"] == "incomplete"
    assert err_code(client.get(f"/api/gwas/datasets/{ds}/traits", headers=headers)) == "DATASET_NOT_VALIDATED"
    cfg = {"dataset_id": ds, "trait_name": "PHENO_HEIGHT", "trait_type": "quantitative"}
    chk = client.post("/api/gwas/config/check", headers=headers, json=cfg).json()
    assert chk["valid"] is False and chk["errors"][0]["code"] == "DATASET_INCOMPLETE"
    assert client.post("/api/gwas/analyses", headers=headers, json=cfg).status_code == 422


def test_reupload_after_delete_requires_validation_again(client):
    headers, _ = make_user(client)
    ds = _ds(client, headers, pheno=D.pheno_csv())
    _validate(client, headers, ds)
    client.delete(f"/api/gwas/datasets/{ds}/files/genotype.fam", headers=headers)
    r = upload(client, headers, "genotype", [("g.fam", D.fam())], dataset_id=ds)
    assert r.json()["ready_for_validation"] is True and r.json()["validation_status"] == "not_validated"
    assert _validate(client, headers, ds)["status"] == "valid"


def test_file_changed_after_validation_makes_status_stale(client, tmp_path):
    from app.config import get_settings
    headers, uid = make_user(client)
    ds = _ds(client, headers, pheno=D.pheno_csv())
    _validate(client, headers, ds)
    f = Path(get_settings().storage_dir).resolve() / f"u{uid}" / f"ds_{ds}" / "phenotype.csv"
    f.write_bytes(D.pheno_csv(rows=[f"{i},{i},1,0,40,L" for i in D.IDS]))      # berkas diganti di belakang layar
    assert client.get(f"/api/gwas/datasets/{ds}", headers=headers).json()["validation_status"] == "stale"
    chk = client.post("/api/gwas/config/check", headers=headers, json={"dataset_id": ds, "trait_name": "PHENO_HEIGHT", "trait_type": "quantitative"}).json()
    assert chk["errors"][0]["code"] == "DATASET_CHANGED"


def test_delete_file_blocked_when_dataset_used_by_analysis(client):
    from tests.helpers import synthetic_analysis
    headers, uid = make_user(client)
    aid = synthetic_analysis(uid, [0.5] * 20)
    ds = client.get(f"/api/gwas/analyses/{aid}", headers=headers).json()["dataset_id"]
    r = client.delete(f"/api/gwas/datasets/{ds}/files/genotype.bed", headers=headers)
    assert r.status_code == 409 and err_code(r) == "DATASET_IN_USE"


def test_delete_dataset_needs_confirmation(client):
    from app.config import get_settings
    headers, uid = make_user(client)
    ds = _ds(client, headers, pheno=D.pheno_csv())
    r = client.delete(f"/api/gwas/datasets/{ds}", headers=headers)
    assert r.status_code == 409 and err_code(r) == "CONFIRMATION_REQUIRED"
    will = r.json()["detail"]["details"]["will_delete"]
    assert will["n_files"] == 4 and will["n_analyses"] == 0 and "genotype.bed" in will["files"]
    folder = Path(get_settings().storage_dir).resolve() / f"u{uid}" / f"ds_{ds}"
    assert folder.exists()                                              # belum ada yang terhapus
    r = client.delete(f"/api/gwas/datasets/{ds}?confirm=true", headers=headers)
    assert r.status_code == 200 and r.json()["files_deleted"] == 4 and not folder.exists()
    assert client.get(f"/api/gwas/datasets/{ds}", headers=headers).status_code == 404


def test_delete_dataset_lists_analyses_and_results_in_confirmation(client):
    from tests.helpers import synthetic_analysis
    headers, uid = make_user(client)
    aid = synthetic_analysis(uid, [0.5] * 20)
    ds = client.get(f"/api/gwas/analyses/{aid}", headers=headers).json()["dataset_id"]
    will = client.delete(f"/api/gwas/datasets/{ds}", headers=headers).json()["detail"]["details"]["will_delete"]
    assert will["n_analyses"] == 1 and will["n_result_rows"] == 20
    assert client.delete(f"/api/gwas/datasets/{ds}?confirm=true", headers=headers).json()["analyses_deleted"] == 1


def test_other_users_cannot_touch_my_dataset(client):
    h1, _ = make_user(client, "a@ipb.ac.id")
    h2, _ = make_user(client, "b@ipb.ac.id")
    ds = _ds(client, h1, pheno=D.pheno_csv())
    assert client.get(f"/api/gwas/datasets/{ds}", headers=h2).status_code == 404
    assert client.delete(f"/api/gwas/datasets/{ds}?confirm=true", headers=h2).status_code == 404
    assert client.delete(f"/api/gwas/datasets/{ds}/files/genotype.bed", headers=h2).status_code == 404
    assert client.get("/api/gwas/datasets", headers=h2).json()["items"] == []


# ---------- no. 8: error dan warning dibedakan ----------
def test_missing_phenotype_values_is_warning_not_invalid(client):
    headers, _ = make_user(client)
    rows = [f"{i},{i},{'NA' if n in (2, 5) else 150 + n},{n % 2},{30 + n},L" for n, i in enumerate(D.IDS)]
    rep = _validate(client, headers, _ds(client, headers, pheno=D.pheno_csv(rows=rows)))
    assert rep["status"] == "valid" and rep["can_proceed"] is True and rep["errors"] == []
    w = next(x for x in rep["warnings"] if x["code"] == "PHENOTYPE_MISSING_VALUES")
    assert "PHENO_HEIGHT: 2 dari 12" in w["message"] and w["impact"] == "Perlu penanganan lebih lanjut"
    assert {t["name"]: t["n_missing"] for t in rep["phenotype"]["traits"]}["PHENO_HEIGHT"] == 2


def test_missing_genotype_calls_is_warning_handled_by_qc(client):
    headers, _ = make_user(client)
    ds = _ds(client, headers, files=D.plink_files(missing_in_variant=1), pheno=D.pheno_csv())
    rep = _validate(client, headers, ds)
    assert rep["status"] == "valid"
    w = next(x for x in rep["warnings"] if x["code"] == "GENOTYPE_MISSING_CALLS")
    assert w["impact"] == "Dapat ditangani pada tahap QC" and "1 dari 4" in w["message"]


def test_no_missing_calls_no_warning(client):
    headers, _ = make_user(client)
    rep = _validate(client, headers, _ds(client, headers, pheno=D.pheno_csv()))
    assert "GENOTYPE_MISSING_CALLS" not in _codes(rep["warnings"])


def test_sample_id_mismatch_is_error_but_partial_is_warning(client):
    headers, _ = make_user(client)
    bad = _validate(client, headers, _ds(client, headers, pheno=D.pheno_csv(ids=[f"X{i}" for i in range(12)])))
    assert bad["status"] == "invalid" and "NO_MATCHING_SAMPLES" in _codes(bad["errors"])
    assert bad["errors"][0]["impact"] == "Analisis tidak dapat dilanjutkan"
    ids = D.IDS[:10] + ["Z1", "Z2"]
    part = _validate(client, headers, _ds(client, headers, pheno=D.pheno_csv(ids=ids)))
    assert part["status"] == "valid" and "SAMPLES_PARTIALLY_MATCHED" in _codes(part["warnings"])
    assert part["matching"]["matched"] == 10 and part["matching"]["unmatched_total"] == 4


def test_too_few_matching_samples_is_error(client):
    headers, _ = make_user(client)
    rep = _validate(client, headers, _ds(client, headers, pheno=D.pheno_csv(ids=D.IDS[:5] + [f"Q{i}" for i in range(7)])))
    assert "TOO_FEW_SAMPLES" in _codes(rep["errors"])


def test_every_issue_has_message_hint_and_impact(client):
    headers, _ = make_user(client)
    rep = _validate(client, headers, _ds(client, headers, files=[("g.bed", D.bed())], pheno=D.pheno_csv(ids=[f"X{i}" for i in range(12)])))
    for issue in rep["errors"] + rep["warnings"]:
        assert issue["message"] and issue["impact"] and "hint" in issue


def test_sample_cohort_is_valid_with_real_files(client):
    headers, _ = make_user(client)
    ds = valid_sample_dataset(client, headers)
    rep = client.get(f"/api/gwas/datasets/{ds}", headers=headers).json()
    assert rep["can_proceed"] and rep["validation"]["matching"]["matched"] == 120
    assert rep["validation"]["genotype"]["n_variants"] == 300


def test_standard_header_accepts_unprefixed_columns(client):
    headers, _ = make_user(client)
    rows = [f"{i},{i},{150 + n},{30 + n}" for n, i in enumerate(D.IDS)]
    pheno = D.pheno_csv(header="FID,IID,Height,COV_AGE", rows=rows)
    rep = _validate(client, headers, _ds(client, headers, pheno=pheno))

    assert rep["status"] == "valid"
    assert rep["can_proceed"] is True
    assert any(
        trait["name"] == "Height"
        for trait in rep["phenotype"]["traits"]
    )


def test_phenotype_accepts_indv_as_single_id_column(client):
    headers, _ = make_user(client)
    rows = [f"{i},{150 + n},{30 + n}" for n, i in enumerate(D.IDS)]
    pheno = D.pheno_csv(header="INDV,Height,Age", rows=rows)
    rep = _validate(client, headers, _ds(client, headers, pheno=pheno))

    assert rep["status"] == "valid"
    assert rep["can_proceed"] is True
    assert any(
        trait["name"] == "Height"
        for trait in rep["phenotype"]["traits"]
    )

def test_minus_nine_is_missing_before_trait_classification(client):
    headers, _ = make_user(client)
    rows = [
        f"{i},{i},{150 + n},{'-9' if n in (1, 4) else n % 2},{30 + n},{'L' if n % 2 else 'P'}"
        for n, i in enumerate(D.IDS)
    ]
    rep = _validate(client, headers, _ds(client, headers, pheno=D.pheno_csv(rows=rows)))
    assert rep["status"] == "valid", rep["errors"]
    traits = {x["name"]: x for x in rep["phenotype"]["traits"]}
    assert traits["PHENO_DISEASE"]["n_valid"] == 10
    assert traits["PHENO_DISEASE"]["n_missing"] == 2


def test_matching_uses_both_fid_and_iid(client):
    headers, _ = make_user(client)
    rows = [f"WRONG,{i},{150 + n},{n % 2},{30 + n},{'L' if n % 2 else 'P'}"
            for n, i in enumerate(D.IDS)]
    rep = _validate(client, headers, _ds(client, headers, pheno=D.pheno_csv(rows=rows)))
    assert rep["status"] == "invalid"
    assert rep["matching"]["matched"] == 0
    assert "NO_MATCHING_SAMPLES" in _codes(rep["errors"])

