"""Aturan konfigurasi analisis (UC-02, Step 3 sampai 4 di antarmuka).

Isi file ini:
  - parameter apa saja yang boleh diatur pengguna dan batas nilainya (QCParams, AnalysisConfig)
  - katalog model GWAS beserta syarat masing-masing (MODEL_CATALOG)
  - preset Guided Mode per domain riset (PRESETS)
  - pilihan kovariat yang diambil dari data fenotipe (COVARIATE_OPTIONS)
  - check_config(), yang menerapkan aturan bisnis sebelum analisis boleh dijalankan
"""
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.services import significance, ui_adapter
from app.services.validation import MIN_SAMPLES

Domain = Literal["kesehatan", "farmasi", "agrikultur", "peternakan"]
SignificanceMethod = Literal["genome_wide", "suggestive", "bonferroni", "fdr", "custom"]

PCA_CHOICES = (5, 10, 20)        # pilihan komponen utama sesuai keputusan final proyek
DEFAULT_PCA = 5                  # dipakai bila PCA dinyalakan tetapi jumlahnya tidak dipilih
MIN_PER_CLASS = 5                # regresi logistik: jumlah kasus dan kontrol paling sedikit (di bawah ini ditolak)
WARN_PER_CLASS = 20              # di bawah ini hasilnya diberi peringatan

MODEL_LABEL = {"linear": "Linear Regression", "logistic": "Logistic Regression", "lmm": "LMM (GEMMA)"}


class QCParams(BaseModel):
    """Parameter Quality Control (FR-10). Dipetakan ke opsi PLINK 2: --maf, --geno (sama dengan 1 dikurangi call_rate), dan --hwe."""

    skip_qc: bool = Field(False, description="Lewati QC. Tidak disarankan karena varian berkualitas rendah dapat memunculkan asosiasi palsu.")
    maf: float = Field(0.05, ge=0.0, le=0.5, description="Frekuensi alel minor minimum. Varian di bawah nilai ini dibuang.")
    call_rate: float = Field(0.95, ge=0.5, le=1.0, description="Proporsi sampel yang harus terbaca per varian. 0,95 berarti boleh hilang maksimal 5%.")
    hwe_p: float | None = Field(1e-6, gt=0.0, le=1.0, description="Ambang p-value uji Hardy-Weinberg. null berarti uji HWE tidak dipakai.")


class AnalysisConfig(BaseModel):
    """Isi permintaan 'konfirmasi dan jalankan' (Step 4). Bentuk JSON mengikuti SKPL Tabel 6 (Parameter Analisis)."""

    dataset_id: int = Field(ge=1, description="Nomor dataset yang akan dianalisis.")
    analysis_mode: Literal["manual", "guided"] = Field("manual", description="manual: semua parameter diisi sendiri. guided: parameter diisi otomatis dari preset domain.")
    domain: Domain | None = Field(None, description="Domain riset untuk Guided Mode. Bila kosong, memakai domain yang tercatat pada dataset.")
    trait_name: str = Field(min_length=1, max_length=100, description="Nama kolom trait yang dianalisis. Daftarnya ada di GET /api/gwas/datasets/{id}/traits.")
    trait_type: Literal["binary", "quantitative"] = Field(description="binary: kasus/kontrol (0/1 atau 1/2). quantitative: angka kontinu.")
    model: Literal["auto", "linear", "logistic", "lmm"] = Field("auto", description="auto memilih model dari tipe trait. Aturan tiap model ada di GET /api/gwas/config/models.")
    qc: QCParams = QCParams()
    use_pca: bool = Field(False, description="Hitung komponen utama (PCA) dari data genotipe untuk mengoreksi struktur populasi.")
    pca_components: int | None = Field(
        None, description="Jumlah PC yang diminta: 5, 10, atau 20. Hasil aktual dapat lebih sedikit bila data tidak mendukung.")
    covariates: list[str] = Field(
        default_factory=list, max_length=20,
        description="Kovariat dari DATA FENOTIPE, misalnya [\"age\", \"sex\"] atau [\"bmi\"]. Daftar kosong berarti tanpa kovariat tambahan. "
                    "Komponen utama PCA TIDAK ditulis di sini, karena diatur lewat use_pca dan pca_components.")
    significance_method: SignificanceMethod = Field("genome_wide", description="Metode penentuan varian signifikan. Aturannya ada di GET /api/gwas/config/options.")
    significance_threshold: float | None = Field(
        None, gt=0.0, le=1.0,
        description="Dipakai hanya untuk metode custom (batas p-value, wajib) dan fdr (nilai q, bawaan 0,05). Untuk metode lain diabaikan.")
    analysis_goal: str | None = Field(None, max_length=100, description="Pilihan 'Tujuan Analisis' di Step 3. Hanya dicatat.")
    execution_mode: Literal["auto", "staged"] = Field(
        "auto", description="auto: QC, PCA, dan GWAS berjalan sekali jalan. staged: berhenti setelah tiap tahap agar hasilnya bisa dilihat dulu.")

    @model_validator(mode="after")
    def validate_pca_choice(self):
        if self.pca_components is not None and self.pca_components not in PCA_CHOICES:
            raise ValueError(f"pca_components harus salah satu dari {PCA_CHOICES}.")
        return self

    @model_validator(mode="before")
    @classmethod
    def accept_ui_labels(cls, raw):
        """Menerima isi dropdown frontend apa adanya (lihat services/ui_adapter.py)."""
        return ui_adapter.normalize_config_input(raw)


PRESETS: dict[str, dict] = {
    "kesehatan":  {"qc": {"maf": 0.01, "call_rate": 0.95, "hwe_p": 1e-6}, "use_pca": True, "pca_components": 10,
                   "deskripsi": "Kohort manusia: varian dengan MAF rendah masih diterima, struktur populasi dikontrol dengan 10 komponen utama."},
    "farmasi":    {"qc": {"maf": 0.01, "call_rate": 0.95, "hwe_p": 1e-6}, "use_pca": True, "pca_components": 5,
                   "deskripsi": "Studi respons obat (farmakogenomik): QC ketat dan 5 komponen utama."},
    "agrikultur": {"qc": {"maf": 0.05, "call_rate": 0.90, "hwe_p": None}, "use_pca": True, "pca_components": 5,
                   "deskripsi": "Tanaman atau galur murni: uji HWE dimatikan karena populasinya inbred, MAF 5%."},
    "peternakan": {"qc": {"maf": 0.05, "call_rate": 0.90, "hwe_p": 1e-6}, "use_pca": True, "pca_components": 5,
                   "deskripsi": "Ternak: MAF 5% dan 5 komponen utama untuk struktur populasi dan kekerabatan."},
}

MODEL_CATALOG: dict[str, dict] = {
    "linear": {
        "value": "linear", "label": "Linear Regression", "engine": "PLINK 2 (--glm)",
        "trait_types": ["quantitative"], "effect_size": "BETA (perubahan nilai trait per tambahan satu alel)",
        "supports_covariates": True, "supports_pca": True, "traits_per_run": 1,
        "requirements": [f"Trait berupa angka kontinu (kuantitatif).", f"Minimal {MIN_SAMPLES} sampel yang cocok antara genotipe dan fenotipe."],
        "description": "Cocok untuk trait kuantitatif seperti tinggi badan, kadar gula darah, atau hasil panen. Satu analisis menguji satu trait.",
    },
    "logistic": {
        "value": "logistic", "label": "Logistic Regression", "engine": "PLINK 2 (--glm)",
        "trait_types": ["binary"], "effect_size": "ln(odds ratio). Gunakan exp(nilai) untuk mendapat odds ratio",
        "supports_covariates": True, "supports_pca": True, "traits_per_run": 1,
        "requirements": ["Trait biner berkode 0/1 atau 1/2 (kontrol/kasus).",
                         f"Jumlah kasus dan kontrol masing-masing minimal {MIN_PER_CLASS} (di bawah {WARN_PER_CLASS} diberi peringatan)."],
        "description": "Cocok untuk trait kasus/kontrol, misalnya sakit atau sehat. Satu analisis menguji satu trait.",
    },
    "lmm": {
        "value": "lmm", "label": "Linear Mixed Model (LMM)", "engine": "GEMMA",
        "trait_types": ["quantitative"], "effect_size": "BETA (perubahan nilai trait per tambahan satu alel)",
        "supports_covariates": True, "supports_pca": True, "traits_per_run": 1,
        "requirements": ["Trait berupa angka kontinu (kuantitatif).", "Program GEMMA terpasang di server.",
                         "Menghitung matriks kinship (kekerabatan antar sampel), jadi lebih lama dari regresi linear."],
        "description": "Cocok bila sampel berkerabat atau struktur populasinya kuat. Kekerabatan sudah dikontrol lewat kinship, "
                       "sehingga PCA tambahan boleh dipakai tetapi biasanya tidak diperlukan.",
    },
}

COVARIATE_OPTIONS = [
    {"value": "none", "label": "Tanpa kovariat tambahan", "covariates": []},
    {"value": "age", "label": "Usia (Age)", "covariates": ["age"]},
    {"value": "sex", "label": "Jenis kelamin (Sex)", "covariates": ["sex"]},
    {"value": "age_sex", "label": "Usia + Jenis kelamin (Age + Sex)", "covariates": ["age", "sex"]},
    {"value": "bmi", "label": "BMI", "covariates": ["bmi"]},
]

PARAMETER_DOCS = [
    {"name": "trait_name", "label": "Fenotipe (trait) yang dianalisis",
     "help": "Satu kolom trait pada berkas fenotipe. Daftar yang bisa dipilih ada di GET /api/gwas/datasets/{id}/traits."},
    {"name": "trait_type", "label": "Tipe trait", "options": ["binary", "quantitative"],
     "help": "Binary berarti kasus/kontrol (kode 0/1 atau 1/2). Quantitative berarti angka kontinu, misalnya tinggi badan atau kadar gula."},
    {"name": "model", "label": "Model GWAS", "options": ["auto", "linear", "logistic", "lmm"],
     "help": "auto memilih dari tipe trait: binary memakai Logistic Regression, quantitative memakai Linear Regression. "
             "Tiap model punya syarat sendiri, lihat GET /api/gwas/config/models."},
    {"name": "qc.maf", "label": "MAF minimum", "range": [0.0, 0.5], "default": 0.05,
     "help": "Varian dengan frekuensi alel minor di bawah nilai ini dibuang karena kurang bertenaga secara statistik."},
    {"name": "qc.call_rate", "label": "Call rate minimum", "range": [0.5, 1.0], "default": 0.95,
     "help": "Varian yang terbaca pada kurang dari proporsi sampel ini dibuang. Nilai 0,95 berarti data hilang maksimal 5%."},
    {"name": "qc.hwe_p", "label": "Ambang p-value HWE", "range": [0.0, 1.0], "default": 1e-6,
     "help": "Varian yang sangat menyimpang dari keseimbangan Hardy-Weinberg (p lebih kecil dari ambang) dibuang. null berarti tidak dipakai."},
    {"name": "qc.skip_qc", "label": "Lewati QC", "default": False,
     "help": "Tidak disarankan. Varian berkualitas rendah dapat memunculkan asosiasi palsu."},
    {"name": "use_pca", "label": "Koreksi struktur populasi (PCA)", "default": False,
     "help": "PCA meringkas perbedaan genetik antar sampel menjadi beberapa komponen utama. Komponen itu lalu ikut dimodelkan "
             "sebagai pengontrol supaya perbedaan keturunan tidak terbaca sebagai asosiasi palsu."},
    {"name": "pca_components", "label": "Jumlah komponen utama", "options": list(PCA_CHOICES), "default": DEFAULT_PCA,
     "help": "Jumlah PC yang diminta (5, 10, atau 20). Jumlah aktual dapat lebih sedikit bergantung pada data. Hanya dipakai bila PCA dinyalakan."},
    {"name": "covariates", "label": "Kovariat dari data fenotipe",
     "help": "Kolom pada berkas fenotipe, seperti usia, jenis kelamin, atau BMI, yang ikut dimodelkan. Ini berbeda dari komponen utama PCA yang dihitung dari genotipe."},
    {"name": "significance_method", "label": "Metode ambang signifikansi", "options": list(significance.METHODS), "default": "genome_wide",
     "help": "Menentukan varian mana yang disebut signifikan. Bisa diganti lagi di tahap post-processing tanpa mengulang analisis."},
    {"name": "significance_threshold", "label": "Nilai ambang (hanya custom dan fdr)", "range": [0.0, 1.0],
     "help": "Untuk metode custom: batas p-value. Untuk fdr: nilai q. Pada metode lain diabaikan."},
]


def resolve_model(trait_type: str, model: str) -> tuple[str | None, str | None]:
    if model == "auto":
        return ("logistic" if trait_type == "binary" else "linear"), None
    allowed = {"binary": {"logistic"}, "quantitative": {"linear", "lmm"}}[trait_type]
    if model not in allowed:
        return None, (f"Model '{MODEL_LABEL[model]}' tidak cocok untuk trait {'biner' if trait_type == 'binary' else 'kuantitatif'}. "
                      f"Pilihan yang cocok: {', '.join(MODEL_LABEL[m] for m in sorted(allowed))}.")
    return model, None


def models_for_trait(trait_type: str | None) -> list[dict]:
    return [{"value": k, "label": v["label"], "recommended": (k == ("logistic" if trait_type == "binary" else "linear"))}
            for k, v in MODEL_CATALOG.items() if trait_type in v["trait_types"]]


_DATASET_PROBLEM = {
    "incomplete": ("DATASET_INCOMPLETE", "Berkas dataset belum lengkap, sehingga belum bisa dikonfigurasi.",
                   "Lengkapi berkas genotipe (.bed/.bim/.fam atau .vcf) dan fenotipe, lalu jalankan validasi."),
    "stale": ("DATASET_CHANGED", "Berkas dataset berubah setelah validasi terakhir, jadi hasil validasi sudah tidak berlaku.",
              "Jalankan validasi data lagi (Step 2)."),
    "invalid": ("DATASET_INVALID", "Dataset tidak valid, sehingga belum bisa dikonfigurasi.",
                "Perbaiki semua error pada hasil validasi, lalu validasi ulang."),
    "not_validated": ("DATASET_NOT_VALIDATED", "Dataset belum divalidasi, sehingga belum bisa dikonfigurasi.",
                      "Jalankan validasi data (Step 2) terlebih dahulu."),
}


def check_config(cfg: AnalysisConfig, report: dict | None, source_domain: str | None,
                 fields_set: set[str] | None = None, ds_status: str = "valid"):
    errors: list[dict] = []
    warnings: list[dict] = []

    def err(code, message, hint=""):
        errors.append({"code": code, "message": message, "hint": hint})

    def warn(code, message):
        warnings.append({"code": code, "message": message})

    data = cfg.model_dump()
    data["covariate_sources"] = {"phenotype_columns": list(cfg.covariates), "principal_components": 0}
    fields_set = fields_set or set()

    if report is None or report.get("status") != "valid":
        code, message, hint = _DATASET_PROBLEM.get(ds_status if ds_status != "valid" else "not_validated", _DATASET_PROBLEM["not_validated"])
        err(code, message, hint)
        return data, errors, warnings

    if cfg.analysis_mode == "guided":
        recorded = (source_domain or "").lower()
        domain = cfg.domain or (recorded if recorded in PRESETS else None)
        data["domain"] = domain
        if domain is None:
            err("DOMAIN_REQUIRED", "Guided Mode memerlukan domain riset (kirim `domain`, atau isi `source_domain` saat membuat dataset).",
                f"Pilih salah satu: {', '.join(PRESETS)}.")
        elif recorded != domain:
            err("DOMAIN_MISMATCH", f"Dataset ini terdaftar pada domain '{source_domain or 'belum diisi'}', bukan '{domain}'.",
                "Pilih preset sesuai domain dataset, atau pakai Manual Mode.")
        else:
            preset = PRESETS[domain]
            if "qc" not in fields_set:
                data["qc"] = {**data["qc"], **preset["qc"]}
            if "use_pca" not in fields_set:
                data["use_pca"] = preset["use_pca"]
            if "pca_components" not in fields_set:
                data["pca_components"] = preset["pca_components"]

    traits = {t["name"].casefold(): t for t in report["phenotype"]["traits"]}
    trait = traits.get(cfg.trait_name.strip().casefold())
    if trait is None:
        err("TRAIT_NOT_FOUND", f"Trait '{cfg.trait_name}' tidak ditemukan pada berkas fenotipe.",
            f"Trait yang tersedia: {', '.join(traits) or '-'}.")
    elif trait["type"] != cfg.trait_type:
        err("TRAIT_TYPE_MISMATCH",
            f"Trait '{cfg.trait_name}' terdeteksi bertipe {'biner' if trait['type'] == 'binary' else 'kuantitatif'}, "
            f"bukan {'biner' if cfg.trait_type == 'binary' else 'kuantitatif'}.",
            "Sesuaikan tipe trait dengan datanya (biner berarti kode 0/1 atau 1/2).")
    elif trait["n_valid"] < MIN_SAMPLES:
        err("TRAIT_TOO_FEW_VALUES", f"Trait '{cfg.trait_name}' hanya punya {trait['n_valid']} nilai terisi, minimal {MIN_SAMPLES}.",
            "Lengkapi nilai fenotipe atau pilih trait lain.")

    model, msg = resolve_model(cfg.trait_type, cfg.model)
    if msg:
        err("MODEL_TRAIT_MISMATCH", msg, "Pakai model 'auto' agar dipilih otomatis sesuai tipe trait.")
    data["model"] = model or cfg.model
    if trait and model == "logistic" and trait["type"] == "binary":
        small = min(trait["n_cases"], trait["n_controls"])
        if small < MIN_PER_CLASS:
            err("BINARY_CLASS_TOO_SMALL",
                f"Regresi logistik memerlukan minimal {MIN_PER_CLASS} kasus dan {MIN_PER_CLASS} kontrol. "
                f"Trait ini punya {trait['n_cases']} kasus dan {trait['n_controls']} kontrol.",
                "Tambahkan sampel pada kelompok yang kurang, atau pilih trait lain.")
        elif small < WARN_PER_CLASS:
            warn("BINARY_CLASS_SMALL", f"Salah satu kelompok hanya berisi {small} sampel (kasus {trait['n_cases']}, kontrol {trait['n_controls']}). "
                                       "Hasil regresi logistik bisa tidak stabil.")

    columns = report["phenotype"]["columns"]
    numeric = set(report["phenotype"]["numeric_columns"])
    categorical = {c["name"] for c in report["phenotype"].get("categorical_columns", [])}
    covariates = [ui_adapter.resolve_covariate(c, columns) for c in cfg.covariates]
    data["covariates"] = covariates
    for c in covariates:
        actual = next((column for column in columns if column.casefold() == c.casefold()), None)
        if c.casefold() == cfg.trait_name.strip().casefold():
            err("COVARIATE_IS_TRAIT", f"Kovariat '{c}' sama dengan trait yang diuji.", "Hapus dari daftar kovariat.")
        elif actual is None:
            err("COVARIATE_NOT_FOUND", f"Kovariat '{c}' tidak ada pada berkas fenotipe.", f"Kolom yang tersedia: {', '.join(columns)}.")
        elif actual not in numeric and actual not in categorical:
            err("COVARIATE_NOT_SUPPORTED", f"Kovariat '{c}' tidak dapat dipakai: isinya bukan angka dan jumlah kategorinya kurang dari 2 atau lebih dari 10.",
                "Pakai kolom angka (misalnya usia) atau kategori sederhana (misalnya jenis kelamin L/P).")
    if len(set(covariates)) != len(covariates):
        err("COVARIATE_DUPLICATE", "Ada kovariat yang dipilih lebih dari sekali.", "Hapus yang dobel.")

    n = report["matching"]["matched"]
    if n < MIN_SAMPLES:
        err("TOO_FEW_SAMPLES", f"Hanya {n} sampel yang cocok, minimal {MIN_SAMPLES} diperlukan.", "Perbaiki dataset.")
    if not data["use_pca"]:
        if data.get("pca_components") is not None and "pca_components" in fields_set:
            warn("PCA_COMPONENTS_IGNORED", f"pca_components ({data['pca_components']}) diabaikan karena use_pca bernilai false.")
        data["pca_components"] = None
    else:
        if data.get("pca_components") is None:
            data["pca_components"] = DEFAULT_PCA
            warn("PCA_COMPONENTS_DEFAULT", f"Jumlah komponen PCA tidak dipilih, dipakai nilai bawaan {DEFAULT_PCA}.")
        # Requested PC may exceed the numerical rank of the dataset. Pipeline produces
        # the maximum available PCs and records a partial result instead of rejecting the run.
        if cfg.model == "lmm":
            warn("PCA_WITH_LMM", "LMM sudah mengontrol kekerabatan lewat matriks kinship, jadi PCA tambahan biasanya tidak diperlukan (tetap boleh).")
    if data["qc"]["skip_qc"]:
        warn("QC_SKIPPED", "QC dilewati. Varian berkualitas rendah dapat memunculkan asosiation palsu.")

    rule = significance.check_rule(cfg.significance_method, cfg.significance_threshold)
    for e in rule["errors"]:
        err("SIGNIFICANCE_INVALID", e)
    for note in rule["notes"]:
        warn("THRESHOLD_IGNORED", note)
    data["significance_threshold"] = rule["threshold"]

    data["covariate_sources"] = {
        "phenotype_columns": list(cfg.covariates),
        "principal_components": data["pca_components"] if data["use_pca"] else 0,
    }
    return data, errors, warnings


FRONTEND_OPTIONS = {
    "analysis_goal": [{"label": "Analisis Asosiasi Genomik", "value": "Analisis Asosiasi Genomik"},
                      {"label": "Studi Asosiasi Lainnya", "value": "Studi Asosiasi Lainnya"}],
    "trait_type": [{"label": "Biner (Case / Control)", "value": "binary"}, {"label": "Kuantitatif (Kontinu)", "value": "quantitative"}],
    "model": [{"label": "Logistic Regression", "value": "logistic", "trait_type": "binary"},
              {"label": "Linear Regression", "value": "linear", "trait_type": "quantitative"},
              {"label": "Linear Mixed Model (LMM)", "value": "lmm", "trait_type": "quantitative"}],
    "maf": [{"label": f"MAF >= {v:.2f}", "value": v} for v in (0.05, 0.01, 0.10)],
    "qc_presets": [{"label": f"MAF >= {m:.2f}, Call Rate >= {c:.2f}", "maf": m, "call_rate": c}
                   for m, c in ((0.05, 0.95), (0.01, 0.90), (0.10, 0.98))],
    "population_structure": [{"label": "Sertakan Hasil PCA (5 PC)", "use_pca": True, "pca_components": 5},
                             {"label": "Sertakan Hasil PCA (10 PC)", "use_pca": True, "pca_components": 10},
                             {"label": "Sertakan Hasil PCA (20 PC)", "use_pca": True, "pca_components": 20},
                             {"label": "Tanpa Koreksi PCA", "use_pca": False, "pca_components": None}],
    "pca_components": [{"label": f"{n} PC", "value": n} for n in PCA_CHOICES],
    "covariates": COVARIATE_OPTIONS,
    "significance": [{"label": "5e-8 (Genome-wide)", "method": "genome_wide", "threshold": None},
                     {"label": "1e-5 (Suggestive)", "method": "suggestive", "threshold": None},
                     {"label": "Bonferroni (0,05 / jumlah varian)", "method": "bonferroni", "threshold": None},
                     {"label": "0.05 (FDR)", "method": "fdr", "threshold": 0.05},
                     {"label": "Ambang sendiri (custom)", "method": "custom", "threshold": None}],
    "export_formats": [{"label": "CSV (.csv)", "value": "csv"}, {"label": "TSV (.tsv)", "value": "tsv"},
                       {"label": "JSON (.json)", "value": "json"}, {"label": "Excel (.xlsx)", "value": "xlsx"}],
}