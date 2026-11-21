"""Kueri hasil untuk Dashboard Viewer (Modul 3 pada LKP 4; UC-04, UC-05; Step 8 sampai 10 di antarmuka).

File ini mengubah baris tabel `gwas_result` menjadi bentuk yang siap dipakai layar: data grafik Manhattan dan QQ,
tabel varian signifikan, tabel varian terbaik, ringkasan, dan berkas ekspor.

Aturan penting:

1. Satu jalur untuk "mana varian yang signifikan". Hanya apply_significance() yang boleh mengisi kolom
   is_significant. Fungsi itu menyimpan aturan yang sedang berlaku (metode, ambang, jumlah pengujian) dan semua
   endpoint hasil membacanya, jadi tabel, grafik, dan ringkasan selalu memakai ambang yang sama
   (catatan analis no. 19).

2. Pengurangan titik grafik tidak boleh menghilangkan varian penting. Seluruh varian signifikan selalu ikut
   ditampilkan, baru sisanya disampling (catatan no. 17).

3. Semua urutan memakai (p_value, result_id). Dengan urutan yang pasti, peringkat sebuah varian tidak berubah dari satu
   permintaan ke permintaan berikutnya, dan q-value Benjamini-Hochberg bisa dihitung per halaman dengan benar.

4. Respons grafik berbentuk kolom (larik sejajar), lebih ringkas untuk Plotly scattergl.
"""
import json
import math
from collections.abc import Iterator
from datetime import datetime, timezone
from statistics import NormalDist

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models import GwasResult, Snp
from app.services import significance as sig

NEG_LOG_CAP = 300.0       # -log10(p) dibatasi supaya p yang sangat kecil tidak menjadi tak hingga
SUGGESTIVE_CUT = 1e-3     # saat menyederhanakan Manhattan, varian dengan p sekecil ini dipertahankan dulu
LAMBDA_MEDIAN_CHI2 = 0.4549364   # median distribusi chi-kuadrat dengan 1 derajat bebas, pembagi pada rumus lambda GC
ORDER = (GwasResult.p_value, GwasResult.result_id)   # urutan baku: p-value terkecil dulu, seri diputus dengan result_id

QQ_DEFINITIONS = {
    "observed_p": "p-value hasil uji asosiasi untuk setiap varian, diurutkan dari yang terkecil.",
    "expected_p": "p-value yang diharapkan bila TIDAK ADA asosiasi sama sekali. Varian ke-i dari n varian diberi nilai (i - 0,5) / n, "
                  "yaitu p-value yang tersebar merata antara 0 dan 1.",
    "lambda_gc": "Faktor inflasi genomik (lambda GC) = median chi-kuadrat hasil pengamatan dibagi 0,4549 (median chi-kuadrat 1 derajat bebas "
                 "pada kondisi tanpa asosiasi). Chi-kuadrat tiap varian diperoleh dari p-value-nya. Nilai sekitar 1 berarti tidak ada inflasi. "
                 "Di atas 1,1 biasanya menandakan struktur populasi atau faktor pengganggu yang belum dikontrol.",
    "plot": "Grafik QQ menggambar -log10(expected_p) di sumbu x dan -log10(observed_p) di sumbu y. Titik yang mengikuti garis diagonal "
            "berarti sebaran p-value sesuai harapan. Titik yang melengkung ke atas di ujung kanan menandakan kandidat asosiasi.",
}


def neg_log10(p: float) -> float:
    """Mengubah p-value menjadi -log10(p) untuk grafik. Nilainya dibatasi agar p yang sangat kecil tidak menjadi tak hingga."""
    return min(NEG_LOG_CAP, -math.log10(max(p, 1e-300)))


def chrom_key(c: str) -> tuple:
    """Urutan natural kromosom: 1, 2, ..., 22, lalu X, Y, MT, lalu yang lain."""
    c = str(c).lower().removeprefix("chr")
    if c.isdigit():
        return (0, int(c), "")
    return (1, {"x": 23, "y": 24, "mt": 25, "m": 25}.get(c, 99), c)


def _base(analysis_id: int):
    """Kueri dasar hasil sebuah analisis: varian digabung dengan statistiknya."""
    return (select(Snp.chromosome, Snp.position, Snp.rsid, Snp.ref_allele, Snp.alt_allele,
                   GwasResult.p_value, GwasResult.effect_size, GwasResult.is_significant, GwasResult.result_id)
            .join(Snp, Snp.snp_id == GwasResult.snp_id).where(GwasResult.analysis_id == analysis_id))


def n_tests(db: Session, analysis_id: int) -> int:
    """Jumlah varian yang benar-benar diuji pada analisis ini: varian yang lolos QC dan punya p-value.
    Angka inilah yang dipakai Bonferroni dan FDR, bukan jumlah varian pada berkas yang diunggah."""
    return db.scalar(select(func.count()).select_from(GwasResult)
                     .where(GwasResult.analysis_id == analysis_id, GwasResult.p_value.is_not(None))) or 0


def counts(db: Session, analysis_id: int) -> dict:
    """Jumlah varian yang diuji dan jumlah yang signifikan pada sebuah analisis."""
    n = n_tests(db, analysis_id)
    s = db.scalar(select(func.count()).select_from(GwasResult)
                  .where(GwasResult.analysis_id == analysis_id, GwasResult.is_significant.is_(True))) or 0
    return {"n_tested": n, "n_significant": s}


# ---- Post-processing: menentukan varian signifikan ----
def fdr_cutoff(db: Session, analysis_id: int, q: float) -> float:
    """Batas p-value Benjamini-Hochberg: p terbesar yang memenuhi p(k) <= (k / n) x q. Bernilai 0 bila tidak ada yang lolos.

    Varian dengan p-value <= batas ini persis sama dengan varian yang q-value-nya <= q. Dihitung langsung di database
    dengan window function agar jutaan p-value tidak perlu dimuat ke memori.
    """
    n = n_tests(db, analysis_id)
    if n == 0:
        return 0.0
    ranked = (select(GwasResult.p_value.label("p"), func.row_number().over(order_by=ORDER).label("rk"))
              .where(GwasResult.analysis_id == analysis_id, GwasResult.p_value.is_not(None)).subquery())
    cut = db.scalar(select(func.max(ranked.c.p)).where(ranked.c.p <= ranked.c.rk * q / n))
    return float(cut) if cut is not None else 0.0


def build_rule(db: Session, analysis_id: int, method: str, threshold: float | None) -> dict:
    """Menghitung batas p-value yang berlaku untuk metode dan threshold yang dipilih, lengkap dengan penjelasannya.
    Belum menulis apa pun ke database."""
    chk = sig.check_rule(method, threshold)
    n = n_tests(db, analysis_id)
    rule = {"method": method, "label": sig.METHODS[method]["label"], "n_tests": n, "q": None, "alpha": None,
            "ignored_threshold": chk["ignored_threshold"], "notes": chk["notes"]}
    if method == "genome_wide":
        rule.update(threshold_p=sig.GENOME_WIDE_P, threshold_source="default_for_method", adjustment="none")
    elif method == "suggestive":
        rule.update(threshold_p=sig.SUGGESTIVE_P, threshold_source="default_for_method", adjustment="none")
    elif method == "bonferroni":
        rule.update(threshold_p=sig.BONFERRONI_ALPHA / max(1, n), alpha=sig.BONFERRONI_ALPHA,
                    threshold_source="computed_from_n_tests", adjustment="bonferroni")
    elif method == "fdr":
        q = chk["threshold"]
        rule.update(threshold_p=fdr_cutoff(db, analysis_id, q), q=q, threshold_source="computed_by_benjamini_hochberg",
                    adjustment="benjamini_hochberg")
    else:
        rule.update(threshold_p=chk["threshold"], threshold_source="user_input", adjustment="none")
    return rule


def _explain(rule: dict, n_sig: int) -> str:
    """Menyusun penjelasan satu paragraf tentang aturan signifikansi yang berlaku dan hasilnya."""
    m, n, cut = rule["method"], rule["n_tests"], rule["threshold_p"]
    tail = f" Hasilnya {n_sig:,} dari {n:,} varian yang diuji dinyatakan signifikan."
    if m == "genome_wide":
        return "Varian signifikan bila p-value <= 5e-8 (ambang genome-wide, tetap)." + tail
    if m == "suggestive":
        return "Varian dianggap menjanjikan bila p-value <= 1e-5 (ambang suggestive, tetap)." + tail
    if m == "bonferroni":
        return (f"Bonferroni: 0,05 dibagi {n:,} varian yang diuji (bukan jumlah varian pada berkas unggahan) = {cut:.3g}. "
                "Varian signifikan bila p-value <= angka itu." + tail)
    if m == "fdr":
        base = (f"Benjamini-Hochberg dengan q = {rule['q']:g}. Setiap p-value diubah menjadi q-value (p-value terkoreksi), dan varian "
                f"signifikan bila q-value <= {rule['q']:g}.")
        if cut > 0:
            return base + f" Batas p-value yang setara adalah {cut:.3g}." + tail
        return base + " Tidak ada varian yang lolos." + tail
    return f"Varian signifikan bila p-value <= {cut:g} (ambang yang Anda masukkan)." + tail


def apply_significance(db: Session, analysis_id: int, method: str, threshold: float | None) -> dict:
    """Post-processing (FR-17): menandai ulang varian signifikan tanpa mengulang analisis.

    Satu-satunya tempat yang mengisi is_significant. Pembaruan seluruh baris dilakukan dalam satu perintah dan satu commit,
    jadi tidak pernah ada kondisi setengah-lama setengah-baru. Mengembalikan aturan yang berlaku untuk disimpan
    (postprocess.json) dan ditampilkan.
    """
    problems = sig.check_rule(method, threshold)["errors"]
    if problems:
        raise ValueError("; ".join(problems))
    rule = build_rule(db, analysis_id, method, threshold)
    db.execute(update(GwasResult).where(GwasResult.analysis_id == analysis_id)
               .values(is_significant=GwasResult.p_value <= rule["threshold_p"]))
    db.commit()
    c = counts(db, analysis_id)
    rule["n_significant"] = c["n_significant"]
    rule["applied_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rule["explanation"] = _explain(rule, c["n_significant"])
    return rule


def save_rule(rdir, rule: dict) -> None:
    """Menyimpan aturan signifikansi yang berlaku ke postprocess.json."""
    (rdir / "postprocess.json").write_text(json.dumps(rule, ensure_ascii=False, indent=2), encoding="utf-8")


def load_rule(rdir) -> dict | None:
    """Membaca aturan signifikansi yang berlaku. None bila belum pernah diterapkan."""
    f = rdir / "postprocess.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def bh_qvalues(db: Session, analysis_id: int, offset: int, limit: int) -> dict[int, float]:
    """q-value Benjamini-Hochberg untuk varian peringkat offset+1 sampai offset+limit (peringkat menurut urutan p-value).

    q(k) = nilai terkecil dari p(j) x n / j untuk semua j >= k, dibatasi paling besar 1. Dihitung di database dengan dua window
    function, dan hanya halaman yang diminta yang dikembalikan. Kunci hasilnya adalah nomor peringkat (mulai dari 1).
    """
    n = n_tests(db, analysis_id)
    if n == 0:
        return {}
    ranked = (select(GwasResult.p_value.label("p"), func.row_number().over(order_by=ORDER).label("rk"))
              .where(GwasResult.analysis_id == analysis_id, GwasResult.p_value.is_not(None)).subquery())
    adj = select(ranked.c.rk, func.min(ranked.c.p * n / ranked.c.rk).over(order_by=ranked.c.rk.desc()).label("q")).subquery()
    rows = db.execute(select(adj.c.rk, adj.c.q).where(adj.c.rk > offset, adj.c.rk <= offset + limit)).all()
    return {int(rk): min(1.0, float(q)) for rk, q in rows}


# ---- Baris hasil ----
def _row(r, n: int, is_logistic: bool, q_value: float | None = None) -> dict:
    """Mengubah satu baris database menjadi objek hasil untuk frontend, lengkap dengan p Bonferroni dan odds ratio bila relevan."""
    chrom, pos, rsid, ref, alt, p, eff, is_sig, _ = r
    return {"chromosome": chrom, "position": pos, "rsid": rsid, "ref_allele": ref, "alt_allele": alt,
            "p_value": p, "p_bonferroni": min(1.0, p * n), "q_value": q_value, "effect_size": eff,
            "odds_ratio": math.exp(eff) if is_logistic and abs(eff) < 700 else None, "is_significant": bool(is_sig),
            # Nama alias yang sama dengan tipe `gwasResults` di store frontend ({chr, pos, snp, pvalue}),
            # jadi baris API bisa langsung dimasukkan ke store tanpa dipetakan ulang.
            "chr": chrom, "pos": pos, "snp": rsid, "pvalue": p}


def _with_q(rows: list, offset: int, rule: dict | None, db: Session, analysis_id: int, n: int, is_logistic: bool) -> list[dict]:
    """Membentuk baris hasil. Untuk metode FDR, q-value ikut dihitung. Halaman yang dipanggil harus berurutan p-value
    dari peringkat offset+1 (itulah urutan tabel Top dan Significant)."""
    qs = bh_qvalues(db, analysis_id, offset, len(rows)) if rule and rule.get("method") == "fdr" and rows else {}
    return [_row(r, n, is_logistic, qs.get(offset + i + 1)) for i, r in enumerate(rows)]


def summary(db: Session, analysis_id: int, rule: dict | None, is_logistic: bool) -> dict:
    """Ringkasan hasil GWAS (catatan no. 16): jumlah varian teruji, jumlah signifikan, varian terbaik, p-value terkecil,
    serta metode dan ambang yang sedang dipakai."""
    c = counts(db, analysis_id)
    best = db.execute(_base(analysis_id).order_by(*ORDER).limit(1)).first()
    n = max(1, c["n_tested"])
    best_row = _with_q([best], 0, rule, db, analysis_id, n, is_logistic)[0] if best else None
    return {"n_variants_tested": c["n_tested"], "n_significant": c["n_significant"], "best_variant": best_row,
            "smallest_p_value": best_row["p_value"] if best_row else None,
            "lambda_gc": lambda_gc(db, analysis_id), "reference_thresholds": {
                "genome_wide": sig.GENOME_WIDE_P, "suggestive": sig.SUGGESTIVE_P, "bonferroni": sig.BONFERRONI_ALPHA / n}}


def significant(db: Session, analysis_id: int, limit: int, offset: int, threshold: float | None,
                rule: dict | None, is_logistic: bool) -> dict:
    """Tabel varian signifikan, p-value terkecil dulu.

    Tanpa `threshold`, tabel mengikuti aturan hasil post-processing TERAKHIR (kolom is_significant). Dengan `threshold`,
    tabel dibuat dari batas p-value yang dikirim saja dan tidak mengubah apa pun yang tersimpan.
    """
    q = _base(analysis_id)
    if threshold is not None:
        q = q.where(GwasResult.p_value <= threshold)
        source = "query_parameter"
    else:
        q = q.where(GwasResult.is_significant.is_(True))
        source = "saved_postprocess"
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    n = max(1, n_tests(db, analysis_id))
    rows = db.execute(q.order_by(*ORDER).limit(limit).offset(offset)).all()
    items = _with_q(rows, offset, rule, db, analysis_id, n, is_logistic)
    return {"total": total, "limit": limit, "offset": offset, "items": items, "source": source,
            "threshold_used": threshold if threshold is not None else (rule or {}).get("threshold_p"),
            "significance": rule if threshold is None else None}


def top(db: Session, analysis_id: int, limit: int, rule: dict | None, is_logistic: bool) -> dict:
    """Varian terbaik menurut p-value terkecil. Sengaja TIDAK dibatasi pada varian signifikan, supaya kandidat terbaik
    tetap terlihat walaupun tidak ada yang melewati ambang (catatan no. 20)."""
    n = max(1, n_tests(db, analysis_id))
    rows = db.execute(_base(analysis_id).order_by(*ORDER).limit(limit)).all()
    items = _with_q(rows, 0, rule, db, analysis_id, n, is_logistic)
    return {"limit": limit, "sorted_by": "p_value", "order": "ascending", "only_significant": False,
            "n_significant_in_list": sum(1 for i in items if i["is_significant"]), "items": items, "significance": rule}


# ---- Grafik ----
def manhattan(db: Session, analysis_id: int, max_points: int, rule: dict | None, as_rows: bool = False) -> dict:
    """Data Manhattan plot. Bila variannya lebih banyak dari max_points, titik dikurangi dengan urutan prioritas:
      1. semua varian signifikan (menurut aturan post-processing yang berlaku). Tidak pernah dibuang.
      2. varian dengan p <= 1e-3 yang terbaik.
      3. sampel merata dari sisanya.
    Bila varian signifikan sendiri lebih banyak dari max_points, yang ditampilkan adalah yang p-value-nya terkecil dan
    respons menandainya lewat all_significant_displayed = false."""
    c = counts(db, analysis_id)
    total = c["n_tested"]
    filtered = total > max_points
    if not filtered:
        rows = db.execute(_base(analysis_id)).all()
    else:
        sig_rows = db.execute(_base(analysis_id).where(GwasResult.is_significant.is_(True)).order_by(*ORDER).limit(max_points)).all()
        budget = max(0, max_points - len(sig_rows))
        top_budget = budget // 2
        best = db.execute(_base(analysis_id).where(GwasResult.is_significant.is_not(True), GwasResult.p_value <= SUGGESTIVE_CUT)
                          .order_by(*ORDER).limit(top_budget)).all() if top_budget else []
        # Batas p yang sudah tercakup. Bila jatah "terbaik" terpakai penuh, batasnya adalah p terakhir yang masuk.
        pcut = best[-1][5] if best and len(best) >= top_budget else SUGGESTIVE_CUT
        rest_budget = budget - len(best)
        rest = []
        if rest_budget > 0:
            remaining = max(1, total - len(sig_rows) - len(best))
            stride = math.ceil(remaining / rest_budget)
            rest = db.execute(_base(analysis_id).where(GwasResult.is_significant.is_not(True), GwasResult.p_value > pcut,
                                                       GwasResult.result_id % stride == 0)).all()
        rows = sig_rows + best + rest
    rows = sorted(rows, key=lambda r: (chrom_key(r[0]), r[1]))
    sig_shown = sum(1 for r in rows if r[7])
    lengths: dict[str, int] = {}
    for r in rows:
        lengths[r[0]] = max(lengths.get(r[0], 0), r[1])
    chroms = sorted(lengths, key=chrom_key)
    # Sumbu x kumulatif: kromosom disambung berurutan, plus posisi label kromosom, siap dipakai Plotly atau Chart.js.
    offsets, acc = {}, 0
    for ch in chroms:
        offsets[ch] = acc
        acc += lengths[ch]
    out = {
        "filtered": filtered, "total_variants": total, "displayed_variants": len(rows),
        "significant_total": c["n_significant"], "significant_displayed": sig_shown,
        "all_significant_displayed": sig_shown >= c["n_significant"],
        "message": (f"Grafik menampilkan {len(rows):,} dari {total:,} varian. Semua varian signifikan dan varian dengan p-value terkecil "
                    "dipertahankan, sisanya diambil sampelnya agar grafik tetap ringan.") if filtered else None,
        "chromosome_order": chroms, "chromosome_max_position": lengths,
        "chromosome_ticks": {"labels": chroms, "positions": [offsets[ch] + lengths[ch] / 2 for ch in chroms]},
        # Garis ambang pada skala -log10(p). Garis "active" adalah ambang yang sedang berlaku dari post-processing.
        "thresholds": {"genome_wide": round(-math.log10(sig.GENOME_WIDE_P), 4), "suggestive": round(-math.log10(sig.SUGGESTIVE_P), 4),
                       "bonferroni": round(-math.log10(sig.BONFERRONI_ALPHA / max(1, total)), 4),
                       "active": round(-math.log10(rule["threshold_p"]), 4) if rule and rule.get("threshold_p") else None},
        "significance": rule,
    }
    if as_rows:   # bentuk baris, sama dengan `gwasResults` di frontend
        out["items"] = [{"chr": r[0], "pos": r[1], "snp": r[2], "pvalue": r[5], "neg_log10_p": round(neg_log10(r[5]), 4),
                         "x": offsets[r[0]] + r[1], "is_significant": bool(r[7])} for r in rows]
    else:         # bentuk kolom, lebih hemat ukuran untuk ratusan ribu titik
        out.update({"chromosome": [r[0] for r in rows], "position": [r[1] for r in rows], "rsid": [r[2] for r in rows],
                    "pvalue": [r[5] for r in rows], "x": [offsets[r[0]] + r[1] for r in rows],
                    "neg_log10_p": [round(neg_log10(r[5]), 4) for r in rows], "is_significant": [bool(r[7]) for r in rows]})
    return out


def _chi2_from_p(p: float) -> float:
    """Chi-kuadrat 1 derajat bebas yang sesuai dengan sebuah p-value dua sisi."""
    return NormalDist().inv_cdf(1 - max(min(p, 1.0), 1e-300) / 2) ** 2


def _median_p(db: Session, analysis_id: int, n: int) -> tuple[float, float]:
    """Dua p-value di tengah urutan (sama bila n ganjil). Diambil langsung dengan OFFSET, tanpa memuat semua p-value."""
    lo, hi = (n - 1) // 2, n // 2
    rows = db.execute(select(GwasResult.p_value).where(GwasResult.analysis_id == analysis_id, GwasResult.p_value.is_not(None))
                      .order_by(*ORDER).offset(lo).limit(hi - lo + 1)).scalars().all()
    return rows[0], rows[-1]


def lambda_gc(db: Session, analysis_id: int) -> float | None:
    """Faktor inflasi genomik: median chi-kuadrat hasil pengamatan dibagi 0,4549."""
    n = n_tests(db, analysis_id)
    if n == 0:
        return None
    a, b = _median_p(db, analysis_id, n)
    return round((_chi2_from_p(a) + _chi2_from_p(b)) / 2 / LAMBDA_MEDIAN_CHI2, 4)


def qq(db: Session, analysis_id: int, max_points: int) -> dict:
    """Data QQ plot lengkap (catatan no. 18 dan 22): p-value teramati, p-value harapan, versi -log10-nya, dan lambda GC.

    p-value diurutkan naik dan dibaca sambil dialirkan dari database. Yang disimpan di memori hanya titik yang akan dikirim.
    Bila variannya melebihi max_points, titik di ujung (paling signifikan) dipertahankan semua dan sisanya diambil merata.
    """
    n = n_tests(db, analysis_id)
    if n == 0:
        return {"n": 0, "lambda_gc": None, "filtered": False, "displayed_variants": 0, "observed_p": [], "expected_p": [],
                "observed_neg_log10_p": [], "expected_neg_log10_p": [], "definitions": QQ_DEFINITIONS}
    filtered = n > max_points
    if filtered:
        head = min(2000, max_points // 2)
        stride = math.ceil((n - head) / max(1, max_points - head - 1))   # satu jatah dicadangkan untuk titik terakhir
        wanted = lambda i: i < head or (i - head) % stride == 0 or i == n - 1   # noqa: E731
    else:
        wanted = lambda i: True   # noqa: E731
    obs, exp = [], []
    stream = db.execute(select(GwasResult.p_value).where(GwasResult.analysis_id == analysis_id, GwasResult.p_value.is_not(None))
                        .order_by(*ORDER).execution_options(yield_per=10000))
    for i, (p,) in enumerate(stream):
        if wanted(i):
            obs.append(p)
            exp.append((i + 0.5) / n)
    return {"n": n, "lambda_gc": lambda_gc(db, analysis_id), "filtered": filtered, "displayed_variants": len(obs),
            "observed_p": obs, "expected_p": [round(e, 12) for e in exp],
            "observed_neg_log10_p": [round(neg_log10(p), 4) for p in obs],
            "expected_neg_log10_p": [round(-math.log10(e), 4) for e in exp],
            "definitions": QQ_DEFINITIONS}


# ---- Ekspor (UC-05, FR-20) ----
EXPORT_COLUMNS = ["chromosome", "position", "rsid", "ref_allele", "alt_allele", "effect_size", "odds_ratio",
                  "p_value", "p_bonferroni", "is_significant"]
EXPORT_FORMATS = {"csv": "text/csv; charset=utf-8", "tsv": "text/tab-separated-values; charset=utf-8",
                  "json": "application/json",
                  "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
XLSX_MAX_ROWS = 1_000_000   # batas satu lembar Excel adalah 1.048.576 baris


def _export_dicts(db: Session, analysis_id: int, only_significant: bool, is_logistic: bool) -> Iterator[dict]:
    """Mengalirkan baris hasil untuk ekspor, diurutkan dari p-value terkecil."""
    n = max(1, n_tests(db, analysis_id))
    q = _base(analysis_id)
    if only_significant:
        q = q.where(GwasResult.is_significant.is_(True))
    for r in db.execute(q.order_by(*ORDER).execution_options(yield_per=10000)):
        yield _row(r, n, is_logistic)


def _fmt(v, spec: str = ".6g") -> str:
    """Memformat angka untuk berkas ekspor. Nilai kosong menjadi teks kosong."""
    return "" if v is None else format(v, spec)


def export_text_lines(db: Session, analysis_id: int, only_significant: bool, is_logistic: bool, delimiter: str = ",") -> Iterator[str]:
    """Baris teks CSV (pemisah koma) atau TSV (pemisah TAB), diurutkan dari p-value terkecil dan dialirkan sedikit demi sedikit."""
    yield delimiter.join(EXPORT_COLUMNS) + "\n"
    for d in _export_dicts(db, analysis_id, only_significant, is_logistic):
        rsid = str(d["rsid"] or "")
        rsid = '"' + rsid.replace('"', '""') + '"' if delimiter == "," else rsid.replace("\t", " ")
        yield delimiter.join([str(d["chromosome"]), str(d["position"]), rsid, str(d["ref_allele"]), str(d["alt_allele"]),
                              _fmt(d["effect_size"]), _fmt(d["odds_ratio"]), _fmt(d["p_value"]),
                              _fmt(d["p_bonferroni"]), str(d["is_significant"]).lower()]) + "\n"


def export_json_chunks(db: Session, analysis_id: int, only_significant: bool, is_logistic: bool) -> Iterator[str]:
    """Larik JSON yang dialirkan satu objek per varian, tanpa memuat seluruh hasil ke memori."""
    yield "["
    first = True
    for d in _export_dicts(db, analysis_id, only_significant, is_logistic):
        yield ("" if first else ",") + json.dumps({k: d[k] for k in EXPORT_COLUMNS}, ensure_ascii=False)
        first = False
    yield "]"


def export_xlsx_chunks(db: Session, analysis_id: int, only_significant: bool, is_logistic: bool,
                       chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
    """Bangun XLSX write-only lalu alirkan dari spool file; file besar beralih ke disk setelah 8 MiB."""
    import tempfile

    from openpyxl import Workbook

    with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b") as buf:
        wb = Workbook(write_only=True)
        ws = wb.create_sheet("GWAS Results")
        ws.append(EXPORT_COLUMNS)
        for i, d in enumerate(_export_dicts(db, analysis_id, only_significant, is_logistic)):
            if i >= XLSX_MAX_ROWS:
                break
            ws.append([d["chromosome"], d["position"], d["rsid"], d["ref_allele"], d["alt_allele"], d["effect_size"],
                       d["odds_ratio"], d["p_value"], d["p_bonferroni"], d["is_significant"]])
        wb.save(buf)
        buf.seek(0)
        while chunk := buf.read(max(64 * 1024, chunk_size)):
            yield chunk


def export_xlsx_bytes(db: Session, analysis_id: int, only_significant: bool, is_logistic: bool) -> bytes:
    """Kompatibilitas untuk pemanggil lama; endpoint HTTP memakai export_xlsx_chunks agar tidak menahan file di RAM."""
    return b"".join(export_xlsx_chunks(db, analysis_id, only_significant, is_logistic))


PLOT_SOURCE_COLUMNS = ["chromosome", "position", "rsid", "p_value", "neg_log10_p", "is_significant"]


def export_plot_source(db: Session, analysis_id: int, plot: str, fmt: str) -> Iterator[str]:
    """Stream complete plot-source data, never the downsampled display arrays."""
    import csv
    import io

    if plot not in {"manhattan", "qq"} or fmt not in {"csv", "tsv", "json"}:
        raise ValueError("Unsupported plot export")
    delimiter = "," if fmt == "csv" else "\t"
    n = n_tests(db, analysis_id)
    if plot == "manhattan":
        query = _base(analysis_id).order_by(Snp.chromosome, Snp.position, GwasResult.result_id).execution_options(yield_per=10000)
        if fmt == "json":
            yield '{"metadata":' + json.dumps({"scope": "complete_source", "downsampled": False, "total_variants": n}, separators=(",", ":")) + ',"items":['
            first = True
            for row in db.execute(query):
                item = {"chromosome": row[0], "position": row[1], "rsid": row[2], "p_value": row[5],
                        "neg_log10_p": round(neg_log10(row[5]), 6) if row[5] is not None else None,
                        "is_significant": bool(row[7])}
                yield ("" if first else ",") + json.dumps(item, ensure_ascii=False)
                first = False
            yield "]}"
            return
        yield "# scope=complete_source; downsampled=false; displayed_only=false\n"
        yield delimiter.join(PLOT_SOURCE_COLUMNS) + "\n"
        for row in db.execute(query):
            buf = io.StringIO()
            csv.writer(buf, delimiter=delimiter, lineterminator="\n").writerow(
                [row[0], row[1], row[2] or "", row[5], round(neg_log10(row[5]), 6) if row[5] is not None else "", bool(row[7])])
            yield buf.getvalue()
        return

    if fmt == "json":
        metadata = {"scope": "complete_source", "downsampled": False, "total_variants": n, "lambda_gc": lambda_gc(db, analysis_id)}
        yield '{"metadata":' + json.dumps(metadata, separators=(",", ":")) + ',"items":['
        first = True
        query = (select(GwasResult.p_value).where(GwasResult.analysis_id == analysis_id, GwasResult.p_value.is_not(None))
                 .order_by(*ORDER).execution_options(yield_per=10000))
        for i, (pval,) in enumerate(db.execute(query)):
            item = {"rank": i + 1, "observed_p": pval, "expected_p": (i + 0.5) / n,
                    "observed_neg_log10_p": round(neg_log10(pval), 6),
                    "expected_neg_log10_p": round(-math.log10((i + 0.5) / n), 6)}
            yield ("" if first else ",") + json.dumps(item, separators=(",", ":"))
            first = False
        yield "]}"
        return
    yield "# scope=complete_source; downsampled=false; lambda_gc=" + str(lambda_gc(db, analysis_id)) + "\n"
    cols = ["rank", "observed_p", "expected_p", "observed_neg_log10_p", "expected_neg_log10_p"]
    yield delimiter.join(cols) + "\n"
    query = (select(GwasResult.p_value).where(GwasResult.analysis_id == analysis_id, GwasResult.p_value.is_not(None))
             .order_by(*ORDER).execution_options(yield_per=10000))
    for i, (pval,) in enumerate(db.execute(query)):
        expected = (i + 0.5) / n
        buf = io.StringIO()
        csv.writer(buf, delimiter=delimiter, lineterminator="\n").writerow(
            [i + 1, pval, expected, round(neg_log10(pval), 6), round(-math.log10(expected), 6)])
        yield buf.getvalue()
