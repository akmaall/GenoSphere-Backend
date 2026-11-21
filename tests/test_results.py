"""Catatan analis no. 15 sampai 25: post-processing, ringkasan, Manhattan, QQ, tabel hasil, ekspor, dan skema respons.

Memakai analisis sintetis (p-value ditentukan sendiri) supaya setiap angka bisa dihitung tangan, tanpa menjalankan PLINK.
"""
import io
import json
import math
import random
from statistics import NormalDist

from openpyxl import load_workbook

from app.main import app
from tests.helpers import err_code, make_user, synthetic_analysis

R = "/api/gwas/analyses"


def _bh_reference(ps, q):
    """Benjamini-Hochberg versi sederhana di Python murni, sebagai pembanding independen untuk hitungan SQL."""
    n = len(ps)
    order = sorted(range(n), key=lambda i: (ps[i], i))
    raw = [ps[i] * n / (k + 1) for k, i in enumerate(order)]
    adj = [0.0] * n
    running = 1.0
    for k in range(n - 1, -1, -1):
        running = min(running, raw[k])
        adj[k] = min(1.0, running)
    n_sig = sum(1 for a in adj if a <= q)
    return adj, n_sig, [ps[i] for i in order]


def _pp(client, headers, aid, **body):
    return client.post(f"{R}/{aid}/postprocess", headers=headers, json=body)


def _setup(client, pvalues, **kw):
    headers, uid = make_user(client)
    return headers, synthetic_analysis(uid, pvalues, **kw)


# ---------- no. 15 dan 21: aturan ambang ----------
def test_genome_wide_and_suggestive_use_fixed_thresholds(client):
    headers, aid = _setup(client, [1e-9, 4e-8, 6e-8, 2e-6, 0.3, 0.9])
    r = _pp(client, headers, aid, method="genome_wide").json()
    assert r["significance"]["threshold_p"] == 5e-8 and r["significance"]["n_significant"] == 2
    assert r["significance"]["threshold_source"] == "default_for_method" and r["significance"]["adjustment"] == "none"
    r = _pp(client, headers, aid, method="suggestive").json()
    assert r["significance"]["threshold_p"] == 1e-5 and r["significance"]["n_significant"] == 4


def test_threshold_is_ignored_for_methods_with_builtin_value(client):
    headers, aid = _setup(client, [1e-9, 4e-8, 6e-8, 0.3])
    r = _pp(client, headers, aid, method="genome_wide", threshold=1).json()           # contoh lama di Swagger: threshold = 1
    s = r["significance"]
    assert s["threshold_p"] == 5e-8 and s["n_significant"] == 2 and s["ignored_threshold"] is True
    assert "diabaikan" in s["notes"][0] and "5e-8" in s["notes"][0]
    clean = _pp(client, headers, aid, method="genome_wide").json()["significance"]
    assert clean["ignored_threshold"] is False and clean["notes"] == []


def test_bonferroni_uses_number_of_variants_actually_tested(client):
    """Jumlah pengujian = varian yang punya p-value, bukan semua baris. Baris tanpa p-value tidak dihitung."""
    pvals = [1e-6, 3e-5] + [0.5] * 98 + [None] * 50
    headers, aid = _setup(client, pvals)
    s = _pp(client, headers, aid, method="bonferroni").json()["significance"]
    assert s["n_tests"] == 100 and s["threshold_p"] == 0.05 / 100 and s["alpha"] == 0.05
    assert s["threshold_source"] == "computed_from_n_tests" and s["adjustment"] == "bonferroni"
    assert s["n_significant"] == 2 and "100" in s["explanation"] and "bukan jumlah varian pada berkas" in s["explanation"]


def test_bonferroni_threshold_parameter_is_ignored(client):
    headers, aid = _setup(client, [1e-6] + [0.5] * 99)
    s = _pp(client, headers, aid, method="bonferroni", threshold=0.9).json()["significance"]
    assert s["threshold_p"] == 0.05 / 100 and s["ignored_threshold"] is True


def test_fdr_matches_independent_benjamini_hochberg(client):
    rnd = random.Random(7)
    ps = [rnd.random() ** 3 for _ in range(300)]                          # campuran p-value kecil dan besar
    headers, aid = _setup(client, ps)
    adj, n_sig, ordered = _bh_reference(ps, 0.05)
    s = _pp(client, headers, aid, method="fdr", threshold=0.05).json()["significance"]
    assert s["n_significant"] == n_sig and s["q"] == 0.05 and s["adjustment"] == "benjamini_hochberg"
    assert s["threshold_source"] == "computed_by_benjamini_hochberg" and "q-value" in s["explanation"]
    if n_sig:
        assert math.isclose(s["threshold_p"], ordered[n_sig - 1])
    top = client.get(f"{R}/{aid}/results/top?limit=50", headers=headers).json()
    for rank, row in enumerate(top["items"]):
        assert math.isclose(row["q_value"], adj[rank], rel_tol=1e-9), rank        # q-value tiap baris sama dengan hitungan pembanding
        assert row["is_significant"] == (adj[rank] <= 0.05)


def test_fdr_default_q_and_no_discoveries(client):
    headers, aid = _setup(client, [0.2, 0.4, 0.6, 0.8, 0.9])
    s = _pp(client, headers, aid, method="fdr").json()["significance"]
    assert s["q"] == 0.05 and s["n_significant"] == 0 and s["threshold_p"] == 0.0 and "Tidak ada" in s["explanation"]


def test_fdr_q_pages_are_consistent_with_offset(client):
    rnd = random.Random(3)
    ps = [rnd.random() ** 4 for _ in range(120)]
    headers, aid = _setup(client, ps)
    _pp(client, headers, aid, method="fdr", threshold=0.1)
    adj, n_sig, _ = _bh_reference(ps, 0.1)
    page = client.get(f"{R}/{aid}/results/significant?limit=5&offset=3", headers=headers).json()
    assert page["total"] == n_sig
    for i, row in enumerate(page["items"]):
        assert math.isclose(row["q_value"], adj[3 + i], rel_tol=1e-9)


def test_custom_needs_threshold_and_uses_it(client):
    headers, aid = _setup(client, [0.001, 0.004, 0.02, 0.5])
    r = _pp(client, headers, aid, method="custom")
    assert r.status_code == 422 and err_code(r) == "THRESHOLD_REQUIRED"
    s = _pp(client, headers, aid, method="custom", threshold=0.005).json()["significance"]
    assert s["n_significant"] == 2 and s["threshold_source"] == "user_input" and s["threshold_p"] == 0.005


def test_invalid_fdr_q_is_rejected(client):
    headers, aid = _setup(client, [0.001, 0.5])
    r = _pp(client, headers, aid, method="fdr", threshold=0.9)
    assert r.status_code == 422 and err_code(r) == "SIGNIFICANCE_INVALID"


def test_postprocess_accepts_ui_labels(client):
    headers, aid = _setup(client, [1e-9, 0.5])
    assert _pp(client, headers, aid, method="5e-8 (Bonferroni)").json()["significance"]["method"] == "genome_wide"
    assert _pp(client, headers, aid, method="0.05 (FDR)").json()["significance"]["method"] == "fdr"
    assert _pp(client, headers, aid, method="1e-5 (Suggestive)").json()["significance"]["method"] == "suggestive"


def test_postprocess_requires_finished_analysis(client):
    from app.models import GwasAnalysis
    from tests.conftest import TestingSession
    headers, aid = _setup(client, [0.1] * 5)
    db = TestingSession()
    db.get(GwasAnalysis, aid).status = "running"
    db.commit()
    db.close()
    r = _pp(client, headers, aid, method="genome_wide")
    assert r.status_code == 409 and err_code(r) == "ANALYSIS_NOT_FINISHED"


# ---------- no. 19: tabel signifikan mengikuti post-processing terakhir ----------
def test_significant_table_follows_latest_postprocess(client):
    headers, aid = _setup(client, [1e-9, 1e-6, 1e-4, 0.01, 0.5], method="genome_wide")
    first = client.get(f"{R}/{aid}/results/significant", headers=headers).json()
    assert first["total"] == 1 and first["source"] == "saved_postprocess" and first["significance"]["method"] == "genome_wide"
    _pp(client, headers, aid, method="suggestive")
    second = client.get(f"{R}/{aid}/results/significant", headers=headers).json()
    assert second["total"] == 2 and second["significance"]["method"] == "suggestive" and second["threshold_used"] == 1e-5
    _pp(client, headers, aid, method="custom", threshold=0.05)
    third = client.get(f"{R}/{aid}/results/significant", headers=headers).json()
    assert third["total"] == 4 and [i["p_value"] for i in third["items"]] == [1e-9, 1e-6, 1e-4, 0.01]
    assert all(i["is_significant"] for i in third["items"])
    assert client.get(f"{R}/{aid}/results/summary", headers=headers).json()["n_significant"] == 4


def test_significant_threshold_parameter_is_temporary(client):
    headers, aid = _setup(client, [1e-9, 1e-6, 1e-4, 0.5])
    q = client.get(f"{R}/{aid}/results/significant?threshold=0.001", headers=headers).json()
    assert q["total"] == 3 and q["source"] == "query_parameter" and q["significance"] is None
    assert client.get(f"{R}/{aid}/results/significant", headers=headers).json()["total"] == 1     # tersimpan tidak berubah


def test_all_result_endpoints_report_same_rule(client):
    headers, aid = _setup(client, [1e-9, 1e-6, 0.5] * 10)
    _pp(client, headers, aid, method="suggestive")
    rules = [client.get(f"{R}/{aid}/results/{p}", headers=headers).json()["significance"] for p in ("summary", "top", "manhattan")]
    rules.append(client.get(f"{R}/{aid}/results/significant", headers=headers).json()["significance"])
    assert all(r["method"] == "suggestive" and r["applied_at"] == rules[0]["applied_at"] for r in rules)


# ---------- no. 20: Top diurutkan p-value terkecil dan tidak dibatasi signifikan ----------
def test_top_is_sorted_by_smallest_p_and_not_limited_to_significant(client):
    headers, aid = _setup(client, [0.9, 0.3, 0.05, 0.7, 0.2, 0.6])           # tidak ada yang signifikan
    top = client.get(f"{R}/{aid}/results/top?limit=3", headers=headers).json()
    assert [i["p_value"] for i in top["items"]] == [0.05, 0.2, 0.3]
    assert top["sorted_by"] == "p_value" and top["order"] == "ascending" and top["only_significant"] is False
    assert top["n_significant_in_list"] == 0 and top["limit"] == 3


def test_top_ties_are_stable(client):
    headers, aid = _setup(client, [0.1] * 8)
    a = [i["rsid"] for i in client.get(f"{R}/{aid}/results/top?limit=8", headers=headers).json()["items"]]
    b = [i["rsid"] for i in client.get(f"{R}/{aid}/results/top?limit=8", headers=headers).json()["items"]]
    assert a == b


# ---------- no. 16: ringkasan ----------
def test_summary_content(client):
    headers, aid = _setup(client, [1e-12, 1e-9, 0.01, 0.4, 0.8], logistic=True, effect=math.log(2))
    s = client.get(f"{R}/{aid}/results/summary", headers=headers).json()
    assert s["n_variants_tested"] == 5 and s["n_significant"] == 2 and s["smallest_p_value"] == 1e-12
    assert s["best_variant"]["p_value"] == 1e-12 and math.isclose(s["best_variant"]["odds_ratio"], 2.0)
    assert s["significance"]["method"] == "genome_wide" and s["significance"]["threshold_p"] == 5e-8
    assert s["model_used"] == "Logistic Regression" and s["lambda_gc"] is not None
    assert s["reference_thresholds"]["bonferroni"] == 0.05 / 5


# ---------- no. 17: Manhattan tidak membuang varian signifikan ----------
def test_manhattan_keeps_every_significant_variant_when_thinning(client):
    rnd = random.Random(11)
    ps = [rnd.random() for _ in range(4000)]
    chroms = [str(1 + i % 3) for i in range(4000)]
    headers, aid = _setup(client, ps, chroms=chroms)
    _pp(client, headers, aid, method="custom", threshold=0.01)                    # ambang longgar: banyak yang p di atas 1e-3 tetapi signifikan
    n_sig = client.get(f"{R}/{aid}/results/summary", headers=headers).json()["n_significant"]
    assert 20 < n_sig < 80
    m = client.get(f"{R}/{aid}/results/manhattan?max_points=1000", headers=headers).json()
    assert m["filtered"] is True and m["displayed_variants"] <= 1000 and "Semua varian signifikan" in m["message"]
    assert m["significant_total"] == n_sig and m["significant_displayed"] == n_sig and m["all_significant_displayed"] is True
    assert sum(m["is_significant"]) == n_sig
    assert all(len(m[k]) == len(m["x"]) for k in ("chromosome", "position", "pvalue", "neg_log10_p", "is_significant"))


def test_manhattan_reports_when_significant_exceed_point_limit(client):
    headers, aid = _setup(client, [0.001 + i * 1e-5 for i in range(3000)])
    _pp(client, headers, aid, method="custom", threshold=0.5)                     # semua varian signifikan
    m = client.get(f"{R}/{aid}/results/manhattan?max_points=1000", headers=headers).json()
    assert m["displayed_variants"] <= 1000 and m["all_significant_displayed"] is False and m["significant_total"] == 3000
    shown = sorted(p for p in m["pvalue"])
    assert shown == sorted(0.001 + i * 1e-5 for i in range(len(shown)))           # yang tampil adalah p-value terkecil


def test_manhattan_unfiltered_and_rows_format(client):
    headers, aid = _setup(client, [1e-9, 0.5, 0.2], chroms=["2", "1", "X"])
    m = client.get(f"{R}/{aid}/results/manhattan", headers=headers).json()
    assert m["filtered"] is False and m["displayed_variants"] == 3 and m["message"] is None
    assert m["chromosome_order"] == ["1", "2", "X"] and m["thresholds"]["active"] == round(-math.log10(5e-8), 4)
    rows = client.get(f"{R}/{aid}/results/manhattan?format=rows", headers=headers).json()["items"]
    assert {"chr", "pos", "snp", "pvalue", "neg_log10_p", "x", "is_significant"} <= set(rows[0])


# ---------- no. 18 dan 22: QQ ----------
def test_qq_has_expected_observed_and_lambda_with_definitions(client):
    ps = [0.001, 0.01, 0.2, 0.5, 0.9]
    headers, aid = _setup(client, ps)
    q = client.get(f"{R}/{aid}/results/qq", headers=headers).json()
    assert q["n"] == 5 and q["filtered"] is False and q["observed_p"] == sorted(ps)
    assert q["expected_p"] == [0.1, 0.3, 0.5, 0.7, 0.9]                              # (i + 0,5) / n untuk i = 0..4
    assert q["observed_neg_log10_p"][0] == 3.0 and q["expected_neg_log10_p"][0] == 1.0
    assert set(q["definitions"]) == {"observed_p", "expected_p", "lambda_gc", "plot"} and "0,4549" in q["definitions"]["lambda_gc"]
    chi2 = NormalDist().inv_cdf(1 - 0.2 / 2) ** 2                                      # median p = 0,2
    assert math.isclose(q["lambda_gc"], round(chi2 / 0.4549364, 4))


def test_lambda_is_one_when_no_inflation_and_high_when_inflated(client):
    headers, aid = _setup(client, [0.5] * 1000)
    assert client.get(f"{R}/{aid}/results/qq", headers=headers).json()["lambda_gc"] == 1.0
    headers2, uid = make_user(client, "dua@ipb.ac.id")
    aid2 = synthetic_analysis(uid, [0.1] * 1000)
    lam = client.get(f"{R}/{aid2}/results/qq", headers=headers2).json()["lambda_gc"]
    assert math.isclose(lam, round(NormalDist().inv_cdf(0.95) ** 2 / 0.4549364, 4))


def test_lambda_with_even_count_averages_two_middle_values(client):
    headers, aid = _setup(client, [0.2, 0.2, 0.8, 0.8])
    lam = client.get(f"{R}/{aid}/results/qq", headers=headers).json()["lambda_gc"]
    c = lambda p: NormalDist().inv_cdf(1 - p / 2) ** 2   # noqa: E731
    assert math.isclose(lam, round((c(0.2) + c(0.8)) / 2 / 0.4549364, 4))


def test_qq_downsampling_keeps_extreme_tail_and_last_point(client):
    rnd = random.Random(5)
    ps = [rnd.random() for _ in range(4000)]
    headers, aid = _setup(client, ps)
    q = client.get(f"{R}/{aid}/results/qq?max_points=1000", headers=headers).json()
    srt = sorted(ps)
    assert q["filtered"] is True and q["displayed_variants"] <= 1000 and q["n"] == 4000
    assert q["observed_p"][:500] == srt[:500] and q["observed_p"][-1] == srt[-1]     # ujung paling signifikan dan titik terakhir tetap ada
    assert q["observed_p"] == sorted(q["observed_p"]) and q["lambda_gc"] is not None


# ---------- no. 23: ekspor sebagai berkas dengan MIME yang sesuai ----------
def test_export_returns_files_with_proper_mime_and_headers(client):
    headers, aid = _setup(client, [1e-9, 0.01, 0.5, 0.7])
    want = {"csv": "text/csv", "tsv": "text/tab-separated-values", "json": "application/json",
            "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
    for fmt, mime in want.items():
        r = client.get(f"{R}/{aid}/export?format={fmt}", headers=headers)
        assert r.status_code == 200 and r.headers["content-type"].startswith(mime), fmt
        assert r.headers["content-disposition"] == f'attachment; filename="gwas_analysis_{aid}.{fmt}"'
        assert r.headers["x-content-type-options"] == "nosniff"


def test_export_contents_are_readable(client):
    headers, aid = _setup(client, [1e-9, 0.01, 0.5, 0.7])
    csv_lines = client.get(f"{R}/{aid}/export?format=csv", headers=headers).text.strip().splitlines()
    assert csv_lines[0].startswith("chromosome,position,rsid") and len(csv_lines) == 5
    assert csv_lines[1].split(",")[-1] == "true" and csv_lines[2].split(",")[-1] == "false"
    tsv = client.get(f"{R}/{aid}/export?format=tsv", headers=headers).text.splitlines()
    assert len(tsv[0].split("\t")) == len(tsv[1].split("\t"))
    data = json.loads(client.get(f"{R}/{aid}/export?format=json", headers=headers).content)
    assert len(data) == 4 and data[0]["p_value"] == 1e-9
    wb = load_workbook(io.BytesIO(client.get(f"{R}/{aid}/export?format=xlsx", headers=headers).content))
    assert wb.active.max_row == 5
    assert len(client.get(f"{R}/{aid}/export?format=csv&only_significant=true", headers=headers).text.strip().splitlines()) == 2
    assert client.get(f"{R}/{aid}/export?format=Excel%20(.xlsx)", headers=headers).headers["content-type"].endswith("sheet")


def test_export_rejects_unknown_format(client):
    headers, aid = _setup(client, [0.1] * 3)
    r = client.get(f"{R}/{aid}/export?format=pdf", headers=headers)
    assert r.status_code == 400 and err_code(r) == "INVALID_EXPORT_FORMAT"


def test_export_docs_declare_binary_files_not_string(client):
    op = app.openapi()["paths"]["/api/gwas/analyses/{analysis_id}/export"]["get"]["responses"]["200"]
    assert set(op["content"]) == {"text/csv", "text/tab-separated-values", "application/json",
                                  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
    assert all(c["schema"] == {"type": "string", "format": "binary"} for c in op["content"].values())


# ---------- no. 24: ekspor konfigurasi ----------
def test_config_export_has_defined_structure(client):
    headers, aid = _setup(client, [1e-9, 0.5, 0.7])
    r = client.get(f"{R}/{aid}/config/export", headers=headers)
    assert r.headers["content-type"].startswith("application/json") and "attachment" in r.headers["content-disposition"]
    body = r.json()
    assert body["schema_version"] == "1.0" and body["analysis_id"] == aid and body["exported_at"]
    assert body["configuration"]["trait_name"] == "T" and body["configuration"]["covariates"] == []
    assert body["significance_applied"]["method"] == "genome_wide"
    assert set(body["dataset"]) == {"dataset_id", "name", "organism", "source_domain", "n_samples_matched", "n_variants"}
    assert body["software_versions"] is None            # analisis sintetis tidak pernah menjalankan program apa pun


# ---------- no. 25: tidak ada respons "string" di dokumentasi ----------
def test_every_gwas_endpoint_documents_its_success_response():
    spec = app.openapi()
    undocumented = []
    for path, item in spec["paths"].items():
        for method, op in item.items():
            ok = {c: r for c, r in op["responses"].items() if c.startswith("2")}
            for code, r in ok.items():
                schema = r.get("content", {}).get("application/json", {}).get("schema")
                files = [k for k in r.get("content", {}) if k != "application/json"]
                if not schema and not files:
                    undocumented.append((method.upper(), path))
                elif schema and not (schema.get("$ref") or schema.get("properties") or schema.get("items") or schema.get("anyOf")
                                     or schema.get("format") == "binary"):
                    undocumented.append((method.upper(), path))
    assert undocumented == []
