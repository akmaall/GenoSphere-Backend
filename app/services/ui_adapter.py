"""Penerjemah nilai dropdown frontend menjadi nilai baku yang dipahami backend.

Dropdown di frontend memakai teks yang enak dibaca, misalnya "Logistic Regression", "Biner (Case / Control)",
"MAF >= 0.05", "5 PC", "Usia", atau "5e-8 (Bonferroni)". Backend bekerja dengan nilai baku seperti "logistic",
"binary", 0.05, 5, dan kolom "Age". Modul ini menerjemahkan keduanya, jadi frontend boleh mengirim isi dropdown
apa adanya, dan nilai baku juga tetap diterima.

Isinya murni fungsi, tanpa database atau PLINK. Nilai yang tidak dikenali dikembalikan apa adanya, lalu validasi
Pydantic yang menolaknya dengan pesan jelas. Modul ini tidak pernah menebak diam-diam.
"""
import re

_NUM = r"([0-9]*\.?[0-9]+(?:[eE][-+]?\d+)?)"


def norm_trait_type(v):
    """Mengubah label tipe trait di dropdown menjadi 'binary' atau 'quantitative'."""
    if not isinstance(v, str):
        return v
    t = v.strip().lower()
    if t in ("binary", "quantitative"):
        return t
    if "biner" in t or "binary" in t or "case" in t:
        return "binary"
    if "kuantitatif" in t or "quantitative" in t or "kontinu" in t or "continuous" in t:
        return "quantitative"
    return v


def norm_model(v):
    """Mengubah nama model di dropdown menjadi kode model (linear, logistic, lmm, atau auto)."""
    if not isinstance(v, str):
        return v
    t = v.strip().lower()
    if t in ("auto", "linear", "logistic", "lmm"):
        return t
    if "lmm" in t or "mixed" in t or "gemma" in t:   # dicek lebih dulu: "Linear Mixed Model" memuat kata "linear"
        return "lmm"
    if "logistic" in t or "logistik" in t:
        return "logistic"
    if "linear" in t or "linier" in t:
        return "linear"
    if "otomatis" in t:
        return "auto"
    return v


def _first_float(text: str, label_pattern: str | None = None):
    """Mengambil angka pertama dari sebuah teks, misalnya 0.05 dari 'MAF >= 0.05'."""
    m = re.search(label_pattern + r"\s*(?:>=|≥|>|=|:)?\s*" + _NUM, text, re.I) if label_pattern else re.search(_NUM, text)
    return float(m.group(1)) if m else None


def parse_qc_text(text: str) -> dict:
    """'MAF >= 0.05, Call Rate >= 0.95' -> {'maf': 0.05, 'call_rate': 0.95} (hanya yang ditemukan)."""
    out = {}
    for key, pat in (("maf", r"maf"), ("call_rate", r"call\s*rate"), ("hwe_p", r"hwe(?:\s*p(?:-?value)?)?")):
        val = _first_float(text, pat)
        if val is not None:
            out[key] = val
    return out


def norm_qc(v):
    """Menerima dict (nilai boleh berupa teks), atau satu teks gabungan."""
    if isinstance(v, str):
        return parse_qc_text(v)
    if isinstance(v, dict):
        out = dict(v)
        for key, pat in (("maf", r"maf"), ("call_rate", r"call\s*rate"), ("hwe_p", r"hwe")):
            if isinstance(out.get(key), str):
                val = _first_float(out[key], pat) if re.search(pat, out[key], re.I) else _first_float(out[key])
                out[key] = val if val is not None else out[key]
        return out
    return v


def parse_pc(v):
    """'5 PC' / '10 PC' / 5 -> 5 / 10 / 5."""
    if isinstance(v, str):
        m = re.search(r"(\d+)", v)
        return int(m.group(1)) if m else v
    return v


def parse_population_structure(text: str):
    """'Sertakan Hasil PCA (5 PC)' -> (True, 5); 'Tanpa Koreksi Struktur Populasi' -> (False, None)."""
    t = text.lower()
    if any(w in t for w in ("tanpa", "tidak", "none", "no ")):
        return False, None
    m = re.search(r"(\d+)\s*pc", t)
    return True, (int(m.group(1)) if m else None)


COVARIATE_ALIASES = {
    "usia": ("cov_age", "age", "usia", "umur"),
    "age": ("cov_age", "age", "usia", "umur"),
    "jenis kelamin": ("cov_sex", "sex", "gender", "jenis_kelamin", "jk", "kelamin"),
    "sex": ("cov_sex", "sex", "gender", "jenis_kelamin", "jk", "kelamin"),
    "bmi": ("cov_bmi", "bmi", "body_mass_index"),
}
# Pilihan gabungan di dropdown kovariat yang berarti lebih dari satu kolom.
COVARIATE_COMBOS = {"age_sex": ["age", "sex"], "usia_jenis_kelamin": ["age", "sex"]}
_NO_COVARIATE = re.compile(r"^(tanpa|tidak ada|none|no additional)", re.I)


def split_covariates(v):
    """Mengubah isian kovariat menjadi daftar nama. Contoh:
    "Age + Sex" menjadi ["Age", "Sex"], "age_sex" menjadi ["age", "sex"], "No additional covariate" menjadi []."""
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, list):
        return v
    out: list = []
    for item in v:
        if not isinstance(item, str):
            out.append(item)
            continue
        text = item.strip()
        if _NO_COVARIATE.search(text):
            continue
        if text.lower() in COVARIATE_COMBOS:
            out.extend(COVARIATE_COMBOS[text.lower()])
            continue
        out.extend(p.strip() for p in re.split(r"[,;+]", text) if p.strip())
    return list(dict.fromkeys(out))   # buang duplikat tetapi pertahankan urutan


def resolve_covariate(label: str, columns: list[str]) -> str:
    """Mengubah label pilihan ('Usia', 'sex') menjadi nama kolom nyata di berkas fenotipe ('Age', 'Sex').
    Bila tidak ada padanan, label dikembalikan apa adanya dan pemeriksaan konfigurasi yang melaporkannya."""
    if label in columns:
        return label
    lowered = {c.lower(): c for c in columns}
    key = label.strip().lower()
    if key in lowered:
        return lowered[key]
    for alias in COVARIATE_ALIASES.get(key, ()):
        if alias in lowered:
            return lowered[alias]
    return label


def parse_threshold(v):
    """Mengubah teks pilihan ambang di UI menjadi (metode, angka).

    '5e-8 (Bonferroni)' menjadi ('genome_wide', 5e-8). Angkanya 5e-8 adalah ambang genome-wide, jadi kata
                        "Bonferroni" pada label UI itu hanya tulisan, bukan metode Bonferroni sungguhan.
    '1e-5 (Suggestive)' menjadi ('suggestive', 1e-5)
    '0.05 (FDR)'        menjadi ('fdr', 0.05), yaitu Benjamini-Hochberg dengan q = 0,05
    angka lain          menjadi ('custom', angka)
    """
    if isinstance(v, (int, float)):
        return "custom", float(v)
    if not isinstance(v, str):
        return None, None
    t = v.strip().lower()
    if t in ("genome_wide", "suggestive", "bonferroni", "custom", "fdr"):
        return t, None
    num = _first_float(t)
    if "fdr" in t:
        return "fdr", num if num is not None else 0.05
    if num is not None and abs(num - 5e-8) < 1e-12:
        return "genome_wide", num
    if num is not None and abs(num - 1e-5) < 1e-12:
        return "suggestive", num
    if "bonferroni" in t and num is None:
        return "bonferroni", None
    if "genome" in t and num is None:
        return "genome_wide", None
    if "suggestive" in t and num is None:
        return "suggestive", None
    return ("custom", num) if num is not None else (None, None)


def parse_export_format(v: str) -> str:
    """'CSV (.csv)' / 'Excel (.xlsx)' / 'TSV' / 'JSON' -> csv|tsv|json|xlsx (selain itu dikembalikan huruf kecil)."""
    t = (v or "csv").strip().lower()
    for key, names in (("xlsx", ("xlsx", "excel")), ("tsv", ("tsv",)), ("json", ("json",)), ("csv", ("csv",))):
        if any(n in t for n in names):
            return key
    return t


def normalize_config_input(raw):
    """Dipakai sebagai model_validator(mode='before') pada AnalysisConfig. Input asli tidak diubah."""
    if not isinstance(raw, dict):
        return raw
    d = dict(raw)
    if "trait_type" in d:
        d["trait_type"] = norm_trait_type(d["trait_type"])
    if "model" in d:
        d["model"] = norm_model(d["model"])
    if "qc" in d:
        d["qc"] = norm_qc(d["qc"])
    if isinstance(d.get("parameter_qc"), str):               # kartu "Parameter QC" Step 4
        parsed = parse_qc_text(d.pop("parameter_qc"))
        d["qc"] = {**parsed, **(d.get("qc") if isinstance(d.get("qc"), dict) else {})}
    d.pop("parameter_qc", None)
    if isinstance(d.get("population_structure"), str):       # dropdown "Struktur Populasi" Step 3
        use, n = parse_population_structure(d.pop("population_structure"))
        d.setdefault("use_pca", use)
        if n is not None:
            d.setdefault("pca_components", n)
    d.pop("population_structure", None)
    if "pca_components" in d:
        d["pca_components"] = parse_pc(d["pca_components"])
    if "covariates" in d:
        d["covariates"] = split_covariates(d["covariates"])
    # Ambang signifikansi bisa datang sebagai teks dropdown ("0.05 (FDR)"), angka polos, atau metode + angka terpisah.
    thr = d.get("significance_threshold")
    if isinstance(thr, str):
        method, num = parse_threshold(thr)
        if method:
            d.setdefault("significance_method", method)
        d["significance_threshold"] = num if method in ("fdr", "custom") else None   # metode tetap tidak memakai angka
    elif isinstance(thr, (int, float)) and not isinstance(thr, bool) and "significance_method" not in d:
        from app.services.significance import infer_method
        d["significance_method"] = infer_method(float(thr))
        if d["significance_method"] != "custom":
            d["significance_threshold"] = None   # 5e-8 dan 1e-5 sudah melekat pada metodenya
    return d


def estimate_runtime(n_samples: int, n_variants: int, model: str, use_pca: bool) -> dict:
    """Perkiraan kasar lama analisis untuk kartu 'Estimasi Waktu' di Step 4. Hitungannya sederhana (sampel x varian), bukan jaminan."""
    work = max(n_samples, 1) * max(n_variants, 1)
    seconds = 5 + work / 4e6
    if use_pca:
        seconds *= 1.5
    if model == "lmm":
        seconds *= 3
    seconds = int(round(seconds))
    label = "< 1 menit" if seconds < 60 else f"~{max(1, round(seconds / 60))} menit"
    return {"seconds": seconds, "label": label, "n_samples": n_samples, "n_variants": n_variants,
            "note": "Perkiraan kasar dari jumlah sampel x varian. Waktu sebenarnya bergantung pada kemampuan server."}
