"""Alur analisis dengan PLINK 2 dan GEMMA ASLI (dilewati otomatis bila programnya tidak terpasang).

Mencakup catatan analis no. 10 (mode auto dan staged konsisten, PCA terpisah dari kovariat), no. 12 (retry), no. 13 (log),
no. 14 (status berbeda dari detail), dan no. 24 (versi perangkat lunak pada ekspor konfigurasi).
"""
import json
from pathlib import Path

import pytest

from app.config import get_settings
from app.services import jobs
from tests.conftest import TestingSession
from tests.helpers import (HAS_GEMMA, HAS_PLINK, base_config, err_code, make_user, synthetic_analysis, valid_sample_dataset, wait_for)

pytestmark = pytest.mark.skipif(not HAS_PLINK, reason="PLINK 2 tidak terpasang")
A = "/api/gwas/analyses"
STAGES_DONE = [("qc", "done"), ("pca", "done"), ("gwas", "done"), ("save", "done")]


def _stages(status):
    return [(s["name"], s["state"]) for s in status["stages"]]


def _run_auto(client, headers, ds, **over):
    r = client.post(A, headers=headers, json=base_config(ds, **over))
    assert r.status_code == 202, r.text
    aid = r.json()["analysis_id"]
    return aid, wait_for(client, headers, aid)


def _fingerprint(client, headers, aid):
    top = client.get(f"{A}/{aid}/results/top?limit=10", headers=headers).json()["items"]
    s = client.get(f"{A}/{aid}/results/summary", headers=headers).json()
    return [(i["rsid"], round(i["p_value"], 12), round(i["effect_size"], 9)) for i in top], s["n_variants_tested"], s["n_significant"]


def test_auto_mode_full_run_with_pca_and_covariates(client):
    headers, _ = make_user(client)
    ds = valid_sample_dataset(client, headers)
    aid, status = _run_auto(client, headers, ds, use_pca=True, pca_components=5, covariates=["Age", "Sex"])
    assert status["status"] == "done" and status["percent"] == 100 and status["error"] is None
    assert _stages(status) == STAGES_DONE and status["can_continue"] is False and status["can_retry"] is False

    qc = client.get(f"{A}/{aid}/qc-summary", headers=headers).json()
    assert qc["variants_before"] == 300 and 0 < qc["variants_after"] <= 300 and qc["samples_after"] == 120
    pca = client.get(f"{A}/{aid}/pca", headers=headers).json()
    assert pca["components"] == ["PC1", "PC2", "PC3", "PC4", "PC5"] and len(pca["points"]) == 120
    s = client.get(f"{A}/{aid}/results/summary", headers=headers).json()
    assert s["n_variants_tested"] == qc["variants_after"] and s["n_samples"] == 120 and s["trait_name"] == "PHENO_HEIGHT"
    assert s["significance"]["method"] == "genome_wide" and s["best_variant"]["p_value"] == s["smallest_p_value"]
    assert client.get(f"{A}/{aid}/results/top?limit=3", headers=headers).json()["items"][0]["p_value"] == s["smallest_p_value"]
    assert 0.2 < s["lambda_gc"] < 3


def test_pcs_and_covariates_are_both_used_but_come_from_different_sources(client):
    """Berkas pengontrol untuk PLINK memuat kolom kovariat fenotipe DAN komponen utama, dengan nama yang berbeda."""
    headers, uid = make_user(client)
    ds = valid_sample_dataset(client, headers)
    aid, _ = _run_auto(client, headers, ds, use_pca=True, pca_components=5, covariates=["Age", "Sex"])
    rdir = Path(get_settings().storage_dir).resolve() / f"u{uid}" / f"ds_{ds}" / "runs" / f"a{aid}"
    header = (rdir / "covar.txt").read_text().splitlines()[0].split("\t")
    assert header[:2] == ["FID", "IID"] and "COV_AGE" in header and any(h.startswith("COV_SEX_") for h in header)
    assert [h for h in header if h.startswith("PC")] == ["PC1", "PC2", "PC3", "PC4", "PC5"]
    cfg = client.get(f"{A}/{aid}", headers=headers).json()["configuration"]
    assert cfg["covariates"] == ["COV_AGE", "COV_SEX"] and cfg["number_of_pcs"] == 5
    assert cfg["covariate_sources"] == {"phenotype_columns": ["Age", "Sex"], "principal_components": 5}


def test_without_pca_no_pc_columns_and_pca_stage_skipped(client):
    headers, uid = make_user(client)
    ds = valid_sample_dataset(client, headers)
    aid, status = _run_auto(client, headers, ds, use_pca=False, covariates=["Age"])
    assert status["status"] == "done" and _stages(status) == [("qc", "done"), ("pca", "skipped"), ("gwas", "done"), ("save", "done")]
    rdir = Path(get_settings().storage_dir).resolve() / f"u{uid}" / f"ds_{ds}" / "runs" / f"a{aid}"
    assert (rdir / "covar.txt").read_text().splitlines()[0].split("\t") == ["FID", "IID", "COV_AGE"]
    cfg = client.get(f"{A}/{aid}", headers=headers).json()["configuration"]
    assert cfg["pca"] is False and cfg["number_of_pcs"] is None
    assert client.get(f"{A}/{aid}/pca", headers=headers).status_code == 404


def test_no_covariates_no_pca_runs_plain_glm(client):
    headers, _ = make_user(client)
    ds = valid_sample_dataset(client, headers)
    aid, status = _run_auto(client, headers, ds, use_pca=False, covariates=[])
    assert status["status"] == "done" and client.get(f"{A}/{aid}/results/summary", headers=headers).json()["n_variants_tested"] > 0


# ---------- no. 10: mode auto dan staged konsisten ----------
def _staged_flow(client, headers, ds, use_pca, covariates):
    cfg = base_config(ds, execution_mode="staged", use_pca=use_pca, pca_components=5 if use_pca else None, covariates=covariates)
    r = client.post(A, headers=headers, json=cfg)
    assert r.status_code == 202 and r.json()["next_step"] == "pca"
    aid = r.json()["analysis_id"]
    s = wait_for(client, headers, aid, states=("awaiting", "failed"))
    assert s["status"] == "awaiting" and s["next_step"] == "pca" and s["can_continue"] is True
    assert _stages(s) == [("qc", "done"), ("pca", "pending"), ("gwas", "pending"), ("save", "pending")]
    # /run sebelum PCA diputuskan harus ditolak
    r = client.post(f"{A}/{aid}/run", headers=headers, json={})
    assert r.status_code == 409 and err_code(r) == "WRONG_STAGE" and r.json()["detail"]["details"]["next_step"] == "pca"
    # kovariat dari fenotipe diatur terpisah dari PCA
    r = client.put(f"{A}/{aid}/covariates", headers=headers, json={"covariates": covariates})
    assert r.status_code == 200 and r.json()["status"] == "awaiting"
    r = client.post(f"{A}/{aid}/pca", headers=headers, json={"use_pca": use_pca, "pca_components": 5 if use_pca else None})
    if use_pca:
        assert r.status_code == 202
        s = wait_for(client, headers, aid, states=("awaiting", "failed"))
        assert s["status"] == "awaiting" and s["next_step"] == "gwas"
        assert _stages(s)[:2] == [("qc", "done"), ("pca", "done")]
    else:
        assert r.status_code == 200 and r.json()["next_step"] == "gwas"
        assert _stages(client.get(f"{A}/{aid}/status", headers=headers).json())[1] == ("pca", "skipped")
    r = client.post(f"{A}/{aid}/run", headers=headers, json={})
    assert r.status_code == 202
    return aid, wait_for(client, headers, aid)


@pytest.mark.parametrize("use_pca", [True, False])
def test_staged_and_auto_give_identical_stages_and_results(client, use_pca):
    headers, _ = make_user(client)
    ds = valid_sample_dataset(client, headers)
    covs = ["Age", "Sex"]
    auto_id, auto_status = _run_auto(client, headers, ds, use_pca=use_pca, pca_components=5 if use_pca else None, covariates=covs)
    staged_id, staged_status = _staged_flow(client, headers, ds, use_pca, covs)
    assert auto_status["status"] == staged_status["status"] == "done"
    assert _stages(auto_status) == _stages(staged_status)                       # urutan dan keadaan tahap sama
    assert auto_status["percent"] == staged_status["percent"] == 100
    assert _fingerprint(client, headers, auto_id) == _fingerprint(client, headers, staged_id)   # hasil analisis sama persis


def test_staged_rules_and_wrong_calls(client):
    headers, _ = make_user(client)
    ds = valid_sample_dataset(client, headers)
    auto_id, _ = _run_auto(client, headers, ds)
    r = client.post(f"{A}/{auto_id}/pca", headers=headers, json={"use_pca": False})
    assert r.status_code == 409 and err_code(r) == "NOT_STAGED"
    assert err_code(client.put(f"{A}/{auto_id}/covariates", headers=headers, json={"covariates": []})) == "NOT_STAGED"

    r = client.post(A, headers=headers, json=base_config(ds, execution_mode="staged"))
    aid = r.json()["analysis_id"]
    wait_for(client, headers, aid, states=("awaiting",))
    bad = client.put(f"{A}/{aid}/covariates", headers=headers, json={"covariates": ["tidak_ada"]})
    assert bad.status_code == 422 and bad.json()["detail"]["errors"][0]["code"] == "COVARIATE_NOT_FOUND"
    bad = client.post(f"{A}/{aid}/pca", headers=headers, json={"use_pca": True, "pca_components": 7})
    assert bad.status_code == 422
    ok = client.post(f"{A}/{aid}/pca", headers=headers, json={"use_pca": False, "pca_components": 10})
    assert ok.status_code == 200 and any(w["code"] == "PCA_COMPONENTS_IGNORED" for w in ok.json()["warnings"])
    assert ok.json()["config"]["pca_components"] is None
    # setelah PCA dilewati, PCA boleh diubah lagi selama belum /run
    again = client.post(f"{A}/{aid}/pca", headers=headers, json={"use_pca": True, "pca_components": 10})
    assert again.status_code == 202
    assert wait_for(client, headers, aid, states=("awaiting",))["next_step"] == "gwas"
    assert client.get(f"{A}/{aid}/pca", headers=headers).json()["components"][-1] == "PC10"


def test_staged_run_can_change_model_and_threshold(client):
    headers, _ = make_user(client)
    ds = valid_sample_dataset(client, headers)
    r = client.post(A, headers=headers, json=base_config(ds, trait_name="PHENO_DISEASE", trait_type="binary", execution_mode="staged"))
    aid = r.json()["analysis_id"]
    wait_for(client, headers, aid, states=("awaiting",))
    client.post(f"{A}/{aid}/pca", headers=headers, json={"use_pca": False})
    r = client.post(f"{A}/{aid}/run", headers=headers, json={"model": "linear"})                 # linear tidak cocok untuk trait biner
    assert r.status_code == 422 and r.json()["detail"]["errors"][0]["code"] == "MODEL_TRAIT_MISMATCH"
    r = client.post(f"{A}/{aid}/run", headers=headers, json={"model": "logistic", "significance_method": "suggestive"})
    assert r.status_code == 202
    assert wait_for(client, headers, aid)["status"] == "done"
    assert client.get(f"{A}/{aid}/results/summary", headers=headers).json()["significance"]["method"] == "suggestive"


# ---------- model lain ----------
def test_logistic_regression_binary_trait(client):
    headers, _ = make_user(client)
    ds = valid_sample_dataset(client, headers)
    aid, status = _run_auto(client, headers, ds, trait_name="PHENO_DISEASE", trait_type="binary", use_pca=True, pca_components=5, covariates=["Age"])
    assert status["status"] == "done", status
    assert client.get(f"{A}/{aid}", headers=headers).json()["model_used"] == "Logistic Regression"
    best = client.get(f"{A}/{aid}/results/top?limit=1", headers=headers).json()["items"][0]
    assert best["odds_ratio"] is not None and best["odds_ratio"] > 0


@pytest.mark.skipif(not HAS_GEMMA, reason="GEMMA tidak terpasang")
def test_lmm_with_gemma(client):
    headers, _ = make_user(client)
    ds = valid_sample_dataset(client, headers)
    aid, status = _run_auto(client, headers, ds, model="lmm", covariates=["Age"])
    assert status["status"] == "done", status
    assert client.get(f"{A}/{aid}", headers=headers).json()["model_used"] == "LMM (GEMMA)"
    assert client.get(f"{A}/{aid}/results/summary", headers=headers).json()["n_variants_tested"] > 0
    versions = client.get(f"{A}/{aid}/config/export", headers=headers).json()["software_versions"]
    assert versions["gemma"] and versions["plink2"]


# ---------- no. 24: ekspor konfigurasi memuat versi ----------
def test_config_export_records_software_versions(client):
    headers, _ = make_user(client)
    ds = valid_sample_dataset(client, headers)
    aid, _ = _run_auto(client, headers, ds, use_pca=True, pca_components=5, covariates=["Age"])
    body = client.get(f"{A}/{aid}/config/export", headers=headers).json()
    v = body["software_versions"]
    assert v["genosphere_api"] == "1.0.0" and v["pipeline"].startswith("genosphere-pipeline") and "PLINK" in v["plink2"] and v["python"]
    assert v["gemma"] is None                                              # GEMMA tidak dipakai pada model linear
    assert body["configuration"]["use_pca"] is True and body["configuration"]["pca_components"] == 5
    assert body["dataset"]["n_samples_matched"] == 120 and body["dataset"]["n_variants"] == 300
    assert body["duration_seconds"] is not None and body["significance_applied"]["method"] == "genome_wide"


# ---------- no. 14: status dan detail berbeda jelas ----------
def test_status_is_light_and_detail_is_full(client):
    headers, _ = make_user(client)
    ds = valid_sample_dataset(client, headers)
    aid, _ = _run_auto(client, headers, ds, use_pca=True, pca_components=10, covariates=["Age"])
    status = client.get(f"{A}/{aid}/status", headers=headers).json()
    detail = client.get(f"{A}/{aid}", headers=headers).json()
    assert "configuration" not in status and "stages" in status and "estimated_remaining_seconds" in status
    assert "stages" not in detail and set(detail["configuration"]) >= {"trait_type", "method", "qc", "pca", "number_of_pcs"}
    assert detail["configuration"]["method"] == "linear" and detail["configuration"]["qc"] is True
    assert detail["configuration"]["number_of_pcs"] == 10 and detail["status"] == "done" and detail["result_available"] is True
    assert detail["progress"]["percent"] == 100 and detail["created_at"] and detail["updated_at"] and detail["finished_at"]
    assert detail["attempts"] == 1
    items = client.get(A, headers=headers).json()["items"]
    assert items[0]["analysis_id"] == aid and items[0]["trait_name"] == "PHENO_HEIGHT"


# ---------- no. 13: log dengan limit dan offset ----------
def test_log_pagination_covers_whole_log_without_gaps(client):
    headers, _ = make_user(client)
    ds = valid_sample_dataset(client, headers)
    aid, _ = _run_auto(client, headers, ds, use_pca=True, pca_components=5)
    full = client.get(f"{A}/{aid}/log?limit=1000", headers=headers).json()
    assert full["has_more"] is False and full["finished"] is True and full["offset"] == 0 and full["limit"] == 1000
    assert len(full["logs"]) > 10
    collected, offset, rounds = [], 0, 0
    while True:
        page = client.get(f"{A}/{aid}/log?offset={offset}&limit=7", headers=headers).json()
        assert len(page["logs"]) <= 7 and page["limit"] == 7 and page["offset"] == offset
        collected += page["logs"]
        offset, rounds = page["next_offset"], rounds + 1
        if not page["has_more"]:
            break
        assert rounds < 500
    assert collected == full["logs"]                                        # halaman demi halaman sama dengan seluruh log
    assert offset == full["next_offset"]
    assert client.get(f"{A}/{aid}/log?offset={offset}", headers=headers).json()["logs"] == []   # sudah habis


def test_log_limit_is_validated(client):
    headers, uid = make_user(client)
    aid = synthetic_analysis(uid, [0.5] * 5)
    assert client.get(f"{A}/{aid}/log?limit=0", headers=headers).status_code == 422
    assert client.get(f"{A}/{aid}/log?limit=5000", headers=headers).status_code == 422
    assert client.get(f"{A}/{aid}/log?offset=-1", headers=headers).status_code == 422
    empty = client.get(f"{A}/{aid}/log", headers=headers).json()
    assert empty["logs"] == [] and empty["has_more"] is False and empty["next_offset"] == 0 and empty["limit"] == 200


def test_log_does_not_return_half_written_line_while_running(client):
    from app.models import GwasAnalysis
    headers, uid = make_user(client)
    aid = synthetic_analysis(uid, [0.5] * 5)
    db = TestingSession()
    a = db.get(GwasAnalysis, aid)
    a.status = "running"
    db.commit()
    log = jobs.run_dir_for(db, a) / "analysis.log"
    db.close()
    log.write_text("baris satu\nbaris dua\nbaris tiga belum selesa", encoding="utf-8")
    page = client.get(f"{A}/{aid}/log", headers=headers).json()
    assert page["logs"] == ["baris satu", "baris dua"] and page["finished"] is False
    assert page["next_offset"] == len("baris satu\nbaris dua\n")
    log.write_text("baris satu\nbaris dua\nbaris tiga selesai\n", encoding="utf-8")
    nxt = client.get(f"{A}/{aid}/log?offset={page['next_offset']}", headers=headers).json()
    assert nxt["logs"] == ["baris tiga selesai"]


# ---------- no. 12: retry ----------
def test_retry_only_for_failed_and_reports_scheduling(client, monkeypatch):
    headers, uid = make_user(client)
    ds = valid_sample_dataset(client, headers)
    settings = get_settings()
    monkeypatch.setattr(settings, "plink2_bin", "plink2-tidak-ada")                 # membuat analisis gagal
    aid, status = _run_auto(client, headers, ds)
    assert status["status"] == "failed" and status["error"]["code"] == "TOOL_NOT_FOUND" and status["can_retry"] is True
    assert _stages(status)[0] == ("qc", "failed") and client.get(f"{A}/{aid}", headers=headers).json()["attempts"] == 1

    monkeypatch.undo()                                                              # program kembali tersedia
    r = client.post(f"{A}/{aid}/retry", headers=headers)
    assert r.status_code == 202
    body = r.json()
    assert body["scheduled"] is True and body["attempt"] == 2 and body["retry_from"] == "all" and "dari awal" in body["message"]
    final = wait_for(client, headers, aid)
    assert final["status"] == "done" and final["attempts"] == 2 and _stages(final) == [("qc", "done"), ("pca", "skipped"), ("gwas", "done"), ("save", "done")] and final["error"] is None

    again = client.post(f"{A}/{aid}/retry", headers=headers)                         # sudah selesai, tidak boleh diulang
    assert again.status_code == 409 and err_code(again) == "NOT_RETRYABLE"
    assert again.json()["detail"]["details"] == {"status": "done", "retryable_statuses": ["failed"]}


@pytest.mark.parametrize("state", ["queued", "running", "awaiting"])
def test_retry_rejected_for_every_status_except_failed(client, state):
    from app.models import GwasAnalysis
    headers, uid = make_user(client)
    aid = synthetic_analysis(uid, [0.5] * 5)
    db = TestingSession()
    db.get(GwasAnalysis, aid).status = state
    db.commit()
    db.close()
    r = client.post(f"{A}/{aid}/retry", headers=headers)
    assert r.status_code == 409 and err_code(r) == "NOT_RETRYABLE" and state in r.json()["detail"]["message"]


def test_server_restart_marks_interrupted_analysis_failed_and_retryable(client):
    from app.models import GwasAnalysis
    headers, uid = make_user(client)
    aid = synthetic_analysis(uid, [0.5] * 5)
    db = TestingSession()
    db.get(GwasAnalysis, aid).status = "running"
    db.commit()
    db.close()
    assert jobs.recover_interrupted_jobs() == 1
    s = client.get(f"{A}/{aid}/status", headers=headers).json()
    assert s["status"] == "failed" and s["error"]["code"] == "INTERRUPTED" and s["can_retry"] is True


def test_analysis_of_other_user_is_hidden(client):
    h1, uid1 = make_user(client, "satu@ipb.ac.id")
    h2, _ = make_user(client, "dua@ipb.ac.id")
    aid = synthetic_analysis(uid1, [0.5] * 5)
    for path in ("", "/status", "/log", "/results/summary", "/export", "/config/export", "/results/qq", "/results/manhattan"):
        assert client.get(f"{A}/{aid}{path}", headers=h2).status_code == 404, path
    assert client.post(f"{A}/{aid}/retry", headers=h2).status_code == 404
    assert client.post(f"{A}/{aid}/postprocess", headers=h2, json={"method": "genome_wide"}).status_code == 404
    assert client.get(A, headers=h2).json()["items"] == []


def test_results_blocked_until_done(client):
    from app.models import GwasAnalysis
    headers, uid = make_user(client)
    aid = synthetic_analysis(uid, [0.5] * 5)
    db = TestingSession()
    db.get(GwasAnalysis, aid).status = "running"
    db.commit()
    db.close()
    for path in ("/results/summary", "/results/manhattan", "/results/qq", "/results/top", "/results/significant", "/export"):
        r = client.get(f"{A}/{aid}{path}", headers=headers)
        assert r.status_code == 409 and err_code(r) == "ANALYSIS_NOT_FINISHED", path


def test_dataset_changed_during_staged_flow_is_rejected_clearly(client):
    """Bila berkas dataset diubah di tengah mode staged, langkah berikutnya ditolak dengan pesan jelas, bukan menghasilkan hasil yang keliru."""
    headers, uid = make_user(client)
    ds = valid_sample_dataset(client, headers)
    r = client.post(A, headers=headers, json=base_config(ds, execution_mode="staged"))
    aid = r.json()["analysis_id"]
    wait_for(client, headers, aid, states=("awaiting",))
    f = Path(get_settings().storage_dir).resolve() / f"u{uid}" / f"ds_{ds}" / "phenotype.csv"
    f.write_bytes(f.read_bytes() + b"\n")                                      # berkas berubah sesudah validasi
    r = client.post(f"{A}/{aid}/pca", headers=headers, json={"use_pca": False})       # ditolak sejak langkah PCA
    assert r.status_code == 422 and r.json()["detail"]["errors"][0]["code"] == "DATASET_CHANGED"
    r = client.post(f"{A}/{aid}/run", headers=headers, json={})                      # dan analisis tidak bisa lanjut ke GWAS
    assert r.status_code == 409 and err_code(r) == "WRONG_STAGE"


# ---------- WebSocket ----------
def test_websocket_streams_progress_and_closes_when_done(client):
    headers, uid = make_user(client)
    aid = synthetic_analysis(uid, [0.5] * 5)
    token = headers["Authorization"].split()[1]
    with client.websocket_connect(f"{A}/{aid}/ws?token={token}") as ws:
        msg = ws.receive_json()
        while msg["type"] != "progress":
            msg = ws.receive_json()
        assert msg["state"] == "done" and msg["percent"] == 100 and [s["name"] for s in msg["stages"]] == ["qc", "pca", "gwas", "save"]


def test_websocket_rejects_bad_token_and_foreign_analysis(client):
    from starlette.websockets import WebSocketDisconnect
    h1, uid1 = make_user(client, "satu@ipb.ac.id")
    h2, _ = make_user(client, "dua@ipb.ac.id")
    aid = synthetic_analysis(uid1, [0.5] * 5)
    for token, code in (("salah", 4401), (h2["Authorization"].split()[1], 4404)):
        with pytest.raises(WebSocketDisconnect) as e:
            with client.websocket_connect(f"{A}/{aid}/ws?token={token}") as ws:
                ws.receive_json()
        assert e.value.code == code
