"""Bentuk respons (dan beberapa isi permintaan) untuk modul GWAS.

Setiap endpoint GWAS memakai salah satu kelas di sini sebagai `response_model`. Tujuannya supaya Swagger menampilkan
bentuk jawaban yang sebenarnya beserta arti setiap kolom, tidak lagi tulisan "string"
(catatan analis no. 9, 14, dan 25). Frontend juga bisa membaca file ini sebagai kontrak data.

Tiap kolom punya `description` dalam bahasa Indonesia. Beberapa kelas punya contoh isi (`example`) yang muncul di Swagger.
"""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services import ui_adapter
from app.services.config_rules import QCParams, SignificanceMethod


class Out(BaseModel):
    """Dasar semua respons. protected_namespaces dikosongkan karena kolom seperti `model_used` dipakai sebagai nama kolom biasa."""

    model_config = ConfigDict(protected_namespaces=())


# ---- Dataset dan berkas ----
class FileInfo(Out):
    """Keterangan satu berkas pada dataset."""
    slot: str = Field(description="Nama tersimpan di server, misalnya genotype.bed atau phenotype.csv. Dipakai untuk menghapus berkas.")
    kind: Literal["genotype", "phenotype"] | None = Field(None, description="Jenis berkas.")
    filename: str = Field(description="Nama asli berkas dari komputer pengguna (hanya untuk ditampilkan).")
    format: str = Field(description="Format berkas: bed, bim, fam, vcf, vcf.gz, csv, tsv, txt, atau pheno.")
    size_bytes: int = Field(description="Ukuran berkas dalam byte.")
    uploaded_at: str | None = Field(None, description="Waktu unggah (UTC, format ISO 8601).")


class UploadOut(Out):
    """Jawaban setelah unggah berkas. `files` berisi berkas yang berhasil diunggah pada permintaan ini,
    `dataset_files` berisi seluruh berkas yang kini ada di dataset."""

    status: Literal["success"] = "success"
    message: str = Field(description="Pesan singkat untuk pengguna.")
    dataset_id: int = Field(description="Nomor dataset. Kirim lagi nomor ini pada unggahan berikutnya agar berkas masuk ke dataset yang sama.")
    files: list[FileInfo] = Field(description="Berkas yang berhasil diunggah pada permintaan ini.")
    dataset_files: list[FileInfo] = Field(description="Semua berkas yang saat ini ada di dataset (genotipe dan fenotipe).")
    ready_for_validation: bool = Field(description="true bila genotipe dan fenotipe sudah lengkap sehingga tombol validasi boleh aktif.")
    missing: list[str] = Field(description="Yang masih kurang, dalam kalimat. Kosong bila sudah lengkap.")
    validation_status: str = Field(description="Status dataset: incomplete, not_validated, stale, valid, atau invalid.")
    validation_label: str = Field(description="Teks status yang boleh langsung ditampilkan.")
    next: str = Field(description="Petunjuk langkah berikutnya.")
    resource_limits: dict[str, Any] = Field(default_factory=dict, description="Kuota per file/dataset dan ruang disk yang terdeteksi saat upload.")

    model_config = ConfigDict(protected_namespaces=(), json_schema_extra={"example": {
        "status": "success", "message": "File genotipe berhasil diunggah", "dataset_id": 12,
        "files": [{"slot": "genotype.bed", "kind": "genotype", "filename": "kohort.bed", "format": "bed", "size_bytes": 1048576, "uploaded_at": "2026-11-10T06:00:00+00:00"}],
        "dataset_files": [{"slot": "genotype.bed", "kind": "genotype", "filename": "kohort.bed", "format": "bed", "size_bytes": 1048576, "uploaded_at": "2026-11-10T06:00:00+00:00"}],
        "ready_for_validation": False, "missing": ["genotipe PLINK belum lengkap: .bim, .fam belum diunggah", "fenotipe belum diunggah"],
        "validation_status": "incomplete", "validation_label": "Berkas belum lengkap",
        "next": "Unggah berkas yang masih kurang dengan dataset_id yang sama.",
        "resource_limits": {"per_file_limit_bytes": 2147483648, "dataset_remaining_before_bytes": 6442450944,
                            "dataset_remaining_after_bytes": 6441402368, "disk_free_bytes": 10000000000, "is_estimate": True}}})


class DatasetOut(Out):
    """Keadaan satu dataset: berkas, kelengkapan, dan status validasi."""
    dataset_id: int
    name: str | None = Field(None, description="Nama dataset di layar.")
    organism: str | None = None
    source_domain: str | None = Field(None, description="kesehatan, farmasi, agrikultur, atau peternakan.")
    files: list[FileInfo]
    ready_for_validation: bool
    missing: list[str]
    genotype_complete: bool = Field(description="true bila genotipe lengkap: set PLINK .bed + .bim + .fam, atau satu VCF.")
    phenotype_present: bool
    validation_status: str = Field(description="incomplete = berkas belum lengkap | not_validated = belum divalidasi | "
                                               "stale = berkas berubah sesudah validasi | valid | invalid.")
    validation_label: str
    can_proceed: bool = Field(description="true hanya bila status valid, artinya dataset boleh dikonfigurasi dan dianalisis.")
    n_analyses: int = Field(0, description="Jumlah analisis yang memakai dataset ini.")


class DatasetListOut(Out):
    """Daftar dataset milik pengguna."""
    items: list[DatasetOut]


class DeleteFileOut(Out):
    """Jawaban setelah satu berkas dihapus."""
    deleted: str = Field(description="Slot berkas yang dihapus.")
    message: str
    dataset: DatasetOut = Field(description="Keadaan dataset SESUDAH penghapusan. Statusnya sudah diperbarui (misalnya dari valid menjadi incomplete).")

    model_config = ConfigDict(json_schema_extra={"example": {
        "deleted": "genotype.fam", "message": "Berkas genotype.fam dihapus. Status dataset diperbarui menjadi: Berkas belum lengkap.",
        "dataset": {"dataset_id": 12, "validation_status": "incomplete", "can_proceed": False}}})


class DeleteDatasetOut(Out):
    """Jawaban setelah dataset dihapus."""
    deleted: int = Field(description="Nomor dataset yang dihapus.")
    analyses_deleted: int
    files_deleted: int
    message: str


class Issue(Out):
    """Satu temuan validasi: error (menghentikan analisis) atau warning (analisis tetap bisa jalan)."""
    code: str = Field(description="Kode singkat masalah.")
    message: str = Field(description="Penjelasan masalah dalam bahasa Indonesia.")
    hint: str = Field("", description="Cara memperbaikinya.")
    impact: str = Field("", description="Pengaruhnya ke analisis.")


class GenotypeInfo(Out):
    """Ringkasan berkas genotipe hasil validasi."""
    format: str | None = Field(None, description="plink atau vcf.")
    n_samples: int = 0
    n_variants: int = 0
    files: list[dict[str, Any]] = Field(default_factory=list, description="Berkas genotipe: name dan size_bytes.")


class TraitInfo(Out):
    """Ringkasan satu kolom trait pada fenotipe."""
    name: str
    type: Literal["binary", "quantitative"] | None
    n: int = Field(description="Jumlah nilai terisi.")
    n_valid: int = Field(description="Jumlah nilai terisi (sama dengan n).")
    n_missing: int = Field(description="Jumlah sampel dengan nilai kosong.")
    usable: bool
    summary: str
    coding: list[float] | None = Field(None, description="Kode dua kelas untuk trait biner, misalnya [0.0, 1.0].")
    n_cases: int | None = Field(None, description="Jumlah kasus (trait biner).")
    n_controls: int | None = Field(None, description="Jumlah kontrol (trait biner).")


class CategoricalColumn(Out):
    """Kolom kategori sederhana (misalnya jenis kelamin) beserta isi kategorinya."""
    name: str
    levels: list[str]


class PhenotypeInfo(Out):
    """Ringkasan berkas fenotipe hasil validasi."""
    file: str | None = None
    n_samples: int = 0
    size_bytes: int = 0
    columns: list[str] = Field(default_factory=list)
    traits: list[TraitInfo] = Field(default_factory=list, description="Kolom yang bisa dianalisis sebagai trait.")
    numeric_columns: list[str] = Field(default_factory=list, description="Kolom numerik berprefiks COV_ yang bisa dipakai sebagai kovariat.")
    categorical_columns: list[CategoricalColumn] = Field(default_factory=list, description="Kolom kategori sederhana, misalnya jenis kelamin L/P.")


class Matching(Out):
    """Hasil pencocokan Sample ID antara genotipe dan fenotipe."""
    genotype_samples: int
    phenotype_samples: int
    matched: int = Field(description="Jumlah Sample ID yang ada di genotipe dan di fenotipe.")
    unmatched_genotype: int
    unmatched_phenotype: int
    unmatched_total: int
    matched_percent: float
    unmatched_genotype_examples: list[str]
    unmatched_phenotype_examples: list[str]


class ValidationReportOut(Out):
    """Laporan validasi lengkap."""
    validated_at: str
    status: Literal["valid", "invalid", "incomplete"] = Field(description="valid = tidak ada error (warning boleh ada). invalid = ada berkas salah. incomplete = berkas belum lengkap.")
    can_proceed: bool = Field(description="true bila analisis boleh dilanjutkan.")
    summary_message: str
    errors: list[Issue] = Field(description="Masalah yang MENGHENTIKAN analisis.")
    warnings: list[Issue] = Field(description="Peringatan. Analisis tetap bisa dilanjutkan.")
    genotype: GenotypeInfo
    phenotype: PhenotypeInfo
    matching: Matching


class ValidateOut(DatasetOut, ValidationReportOut):
    """Hasil POST /validate: keadaan dataset digabung dengan laporan validasinya."""


class DatasetDetailOut(DatasetOut):
    """Detail dataset: keadaan dataset ditambah laporan validasi terakhir."""
    validation: ValidationReportOut | None = Field(None, description="Laporan validasi terakhir. null bila belum pernah divalidasi.")


class TraitModel(Out):
    """Satu model yang boleh dipakai untuk sebuah trait."""
    value: str
    label: str
    recommended: bool


class TraitChoice(Out):
    """Satu trait yang bisa dipilih, beserta model yang cocok."""
    name: str
    type: Literal["binary", "quantitative"]
    n_valid: int
    n_missing: int
    summary: str
    n_cases: int | None = None
    n_controls: int | None = None
    recommended_model: str = Field(description="Model yang dipilih bila Anda mengirim model = auto.")
    models: list[TraitModel] = Field(description="Model yang boleh dipakai untuk trait ini.")


class CovariateCandidate(Out):
    """Satu kolom fenotipe yang bisa dipilih sebagai kovariat."""
    name: str
    kind: Literal["numeric", "categorical"]
    levels: list[str] | None = None


class TraitsOut(Out):
    """Pilihan fenotipe (trait) pada sebuah dataset, dipakai untuk dropdown pemilihan trait sebelum analisis."""

    dataset_id: int
    traits: list[TraitChoice]
    covariate_candidates: list[CovariateCandidate] = Field(description="Kolom fenotipe yang bisa dipilih sebagai kovariat.")
    note: str


# ---- Konfigurasi ----
class ParameterDoc(Out):
    """Penjelasan satu parameter untuk Guided Mode."""
    name: str
    label: str
    help: str
    options: list[Any] | None = None
    range: list[float] | None = None
    default: Any = None


class ParametersOut(Out):
    """Penjelasan semua parameter dan arti tiap metode ambang."""
    parameters: list[ParameterDoc]
    thresholds: dict[str, str] = Field(description="Arti tiap metode ambang signifikansi.")


class DomainChoice(Out):
    """Satu pilihan domain riset untuk dropdown."""
    value: str
    label: str
    description: str
    is_recorded_domain: bool | None = Field(None, description="Hanya muncul bila dataset_id dikirim: true bila domain ini sama dengan domain dataset.")


class PresetOut(Out):
    """Nilai parameter yang diisi otomatis oleh satu domain riset."""
    qc: QCParams
    use_pca: bool
    pca_components: int | None
    deskripsi: str


class PresetsOut(Out):
    """Domain riset yang tersedia untuk Guided Mode dan preset parameter tiap domain."""

    domains: list[DomainChoice] = Field(description="Pilihan untuk dropdown domain riset.")
    presets: dict[str, PresetOut] = Field(description="Nilai parameter yang diisi otomatis untuk setiap domain.")


class ModelInfo(Out):
    """Keterangan satu model GWAS beserta syaratnya."""
    value: str
    label: str
    engine: str
    trait_types: list[str]
    effect_size: str
    supports_covariates: bool
    supports_pca: bool
    traits_per_run: int
    requirements: list[str]
    description: str


class ModelsOut(Out):
    """Daftar model GWAS."""
    models: list[ModelInfo]
    auto_choice: dict[str, str] = Field(description="Model yang dipilih bila model = auto, menurut tipe trait.")
    note: str


class OptionsOut(Out):
    """Isi semua dropdown."""
    options: dict[str, list[dict[str, Any]]] = Field(description="Isi setiap dropdown. `label` tampil di layar, `value` dikirim ke API.")


class ConfigIssue(Out):
    """Satu masalah atau peringatan pada konfigurasi."""
    code: str
    message: str
    hint: str = ""


class CovariateSources(Out):
    """Asal variabel pengontrol: kolom dari fenotipe dan komponen utama dari PCA genotipe."""
    phenotype_columns: list[str] = Field(description="Kovariat yang diambil dari berkas fenotipe, misalnya Age dan Sex.")
    principal_components: int = Field(description="Jumlah komponen utama dari PCA genotipe yang ikut dipakai. 0 bila PCA tidak dipakai.")


class ConfigOut(Out):
    """Konfigurasi setelah dinormalisasi: nilai bawaan terisi dan label UI sudah diterjemahkan."""

    dataset_id: int
    analysis_mode: str
    domain: str | None
    trait_name: str
    trait_type: str
    model: str
    qc: QCParams
    use_pca: bool
    pca_components: int | None = Field(description="null bila use_pca = false.")
    covariates: list[str]
    covariate_sources: CovariateSources
    significance_method: str
    significance_threshold: float | None
    analysis_goal: str | None
    execution_mode: str


class RuntimeEstimate(Out):
    """Perkiraan kasar lama analisis."""
    seconds: int
    label: str
    n_samples: int
    n_variants: int
    note: str


class ConfigCheckOut(Out):
    """Hasil pemeriksaan konfigurasi."""
    valid: bool = Field(description="true bila konfigurasi boleh dijalankan.")
    errors: list[ConfigIssue]
    warnings: list[ConfigIssue]
    config: ConfigOut
    estimate: RuntimeEstimate | None = None

    model_config = ConfigDict(protected_namespaces=(), json_schema_extra={"example": {
        "valid": True, "errors": [],
        "warnings": [{"code": "PCA_COMPONENTS_IGNORED", "message": "pca_components (10) diabaikan karena use_pca bernilai false.", "hint": ""}],
        "config": {"dataset_id": 12, "analysis_mode": "manual", "domain": None, "trait_name": "Height", "trait_type": "quantitative",
                   "model": "linear", "qc": {"skip_qc": False, "maf": 0.05, "call_rate": 0.95, "hwe_p": 1e-6}, "use_pca": False,
                   "pca_components": None, "covariates": ["Age", "Sex"],
                   "covariate_sources": {"phenotype_columns": ["Age", "Sex"], "principal_components": 0},
                   "significance_method": "genome_wide", "significance_threshold": None, "analysis_goal": None, "execution_mode": "auto"},
        "estimate": {"seconds": 6, "label": "< 1 menit", "n_samples": 120, "n_variants": 5000, "note": "Perkiraan kasar dari jumlah sampel x varian."}}})


# ---- Analisis: membuat, tahap, status, detail ----
class StageState(Out):
    """Keadaan satu tahap analisis."""
    name: Literal["qc", "pca", "gwas", "save"]
    label: str
    state: Literal["pending", "running", "done", "skipped", "failed"]


class AnalysisCreatedOut(Out):
    """Jawaban setelah analisis dibuat dan dijadwalkan."""
    analysis_id: int
    status: Literal["queued"]
    model: str = Field(description="Nama model yang dipakai, misalnya Linear Regression.")
    execution_mode: str
    next_step: str | None = Field(None, description="Pada mode staged: langkah berikutnya setelah QC selesai (pca). Pada mode auto: null.")
    warnings: list[ConfigIssue]
    config: ConfigOut
    resource_preflight: dict[str, Any] = Field(default_factory=dict)


class PcaRequest(BaseModel):
    """Step 6 (mode staged): hanya soal PCA. Kovariat dari fenotipe diatur terpisah lewat PUT /analyses/{id}/covariates."""

    pca_components: Literal[5, 10, 20] = Field(5, description="Jumlah PC yang diminta (5, 10, atau 20); hasil aktual dapat parsial.")
    use_pca: bool = True

    model_config = ConfigDict(json_schema_extra={"example": {"use_pca": True, "pca_components": 5}})

    @model_validator(mode="before")
    @classmethod
    def accept_ui_labels(cls, raw):
        """Menerima pilihan dropdown seperti 'Sertakan Hasil PCA (10 PC)' atau '10 PC' dan mengubahnya menjadi angka."""
        if not isinstance(raw, dict):
            return raw
        d = dict(raw)
        if isinstance(d.get("population_structure"), str):
            use, n = ui_adapter.parse_population_structure(d.pop("population_structure"))
            d.setdefault("use_pca", use)
            if n is not None:
                d.setdefault("pca_components", n)
        if d.get("pca_components") is None and d.get("use_pca") is False:
            d.pop("pca_components", None)
        if "pca_components" in d:
            d["pca_components"] = ui_adapter.parse_pc(d["pca_components"])
        return d


class CovariatesRequest(BaseModel):
    """Kovariat dari DATA FENOTIPE. Komponen utama PCA tidak ditulis di sini."""

    covariates: list[str] = Field(default_factory=list, max_length=20,
                                  description='Misalnya ["age", "sex"]. Daftar kosong berarti tanpa kovariat tambahan.')

    model_config = ConfigDict(json_schema_extra={"example": {"covariates": ["age", "sex"]}})

    @model_validator(mode="before")
    @classmethod
    def accept_ui_labels(cls, raw):
        """Menerima teks dropdown seperti 'Age + Sex' dan mengubahnya menjadi daftar kolom."""
        if isinstance(raw, dict) and "covariates" in raw:
            return {**raw, "covariates": ui_adapter.split_covariates(raw["covariates"])}
        return raw


class RunRequest(BaseModel):
    """Step 7 (mode staged): pilih model, dan boleh mengubah ambang awal."""

    model: Literal["auto", "linear", "logistic", "lmm"] | None = Field(None, description="Kosong berarti tetap memakai model pada konfigurasi.")
    significance_method: SignificanceMethod | None = None
    significance_threshold: float | None = Field(None, gt=0.0, le=1.0, description="Hanya untuk metode custom dan fdr.")

    model_config = ConfigDict(json_schema_extra={"example": {"model": "auto", "significance_method": "genome_wide"}})

    @model_validator(mode="before")
    @classmethod
    def accept_ui_labels(cls, raw):
        """Menerima nama model dan label ambang dari dropdown frontend."""
        if not isinstance(raw, dict):
            return raw
        d = dict(raw)
        if "model" in d:
            d["model"] = ui_adapter.norm_model(d["model"])
        thr = d.get("significance_threshold")
        if isinstance(thr, str):
            meth, num = ui_adapter.parse_threshold(thr)
            if meth:
                d.setdefault("significance_method", meth)
            d["significance_threshold"] = num if meth in ("fdr", "custom") else None
        return d


class StageOut(Out):
    """Jawaban untuk langkah-langkah mode staged (PCA, kovariat, jalankan)."""

    analysis_id: int
    status: str
    next_step: str | None = Field(None, description="Langkah berikutnya yang boleh dipanggil: pca, gwas, atau null.")
    message: str
    warnings: list[ConfigIssue] = Field(default_factory=list)
    config: ConfigOut


class ProgressBrief(Out):
    """Ringkasan progres singkat."""
    percent: int = Field(description="0 sampai 100.")
    stage: str | None
    message: str | None


class AnalysisItem(Out):
    """Satu baris riwayat analisis."""
    analysis_id: int
    dataset_id: int
    status: str
    analysis_mode: str | None
    model_used: str | None
    qc_skipped: bool | None
    pca_used: bool | None
    trait_name: str | None
    execution_mode: str
    created_at: str | None
    finished_at: str | None


class AnalysisListOut(Out):
    """Riwayat analisis."""
    items: list[AnalysisItem]


class AnalysisConfiguration(Out):
    """Konfigurasi yang dipakai sebuah analisis, dalam bentuk yang mudah dibaca."""
    trait_name: str | None
    trait_type: str | None
    method: str | None = Field(description="Kode model: linear, logistic, atau lmm.")
    qc: bool = Field(description="true bila QC dijalankan, false bila dilewati.")
    qc_parameters: QCParams | None
    pca: bool = Field(description="true bila PCA dipakai.")
    number_of_pcs: int | None = Field(description="Jumlah komponen utama. null bila PCA tidak dipakai.")
    covariates: list[str] = Field(description="Kovariat dari data fenotipe.")
    covariate_sources: CovariateSources | None
    significance_method: str | None
    significance_threshold: float | None
    analysis_mode: str | None
    execution_mode: str


class AnalysisDetailOut(Out):
    """Detail LENGKAP satu analisis: konfigurasi yang dipakai, ringkasan progres, dan waktu.
    Untuk memantau analisis yang sedang berjalan gunakan endpoint /status yang lebih ringan."""

    analysis_id: int
    dataset_id: int
    status: str
    model_used: str | None = Field(description="Nama model, misalnya Linear Regression.")
    configuration: AnalysisConfiguration
    progress: ProgressBrief
    attempts: int = Field(description="Sudah berapa kali analisis dijalankan (1 = belum pernah diulang).")
    result_available: bool = Field(description="true bila hasil sudah bisa dilihat dan diekspor (status done).")
    created_at: str | None
    updated_at: str | None
    finished_at: str | None

    model_config = ConfigDict(protected_namespaces=(), json_schema_extra={"example": {
        "analysis_id": 12, "dataset_id": 5, "status": "running", "model_used": "Linear Regression",
        "configuration": {"trait_name": "Height", "trait_type": "quantitative", "method": "linear", "qc": True,
                          "qc_parameters": {"skip_qc": False, "maf": 0.05, "call_rate": 0.95, "hwe_p": 1e-6}, "pca": True, "number_of_pcs": 10,
                          "covariates": ["Age"], "covariate_sources": {"phenotype_columns": ["Age"], "principal_components": 10},
                          "significance_method": "genome_wide", "significance_threshold": None, "analysis_mode": "manual", "execution_mode": "auto"},
        "progress": {"percent": 45, "stage": "gwas", "message": "Analisis asosiasi: Linear Regression"}, "attempts": 1,
        "result_available": False, "created_at": "2026-11-05T06:00:00+00:00", "updated_at": "2026-11-05T06:15:00+00:00", "finished_at": None}})


class ErrorInfo(Out):
    """Keterangan kegagalan sebuah analisis."""
    code: str
    message: str
    hint: str = ""


class AnalysisStatusOut(Out):
    """Status RINGAN untuk polling tiap beberapa detik (progress bar). Isinya sengaja sedikit: tidak ada konfigurasi."""

    analysis_id: int
    status: Literal["queued", "running", "awaiting", "done", "failed"]
    stage: str | None = Field(description="Tahap internal yang sedang jalan.")
    stages: list[StageState] = Field(description="Daftar tahap berurutan beserta keadaannya. Sama untuk mode auto dan staged.")
    percent: int
    message: str | None
    elapsed_seconds: int | None
    estimated_remaining_seconds: int | None
    error: ErrorInfo | None
    started_at: str | None
    finished_at: str | None
    updated_at: str | None
    next_step: str | None
    execution_mode: str
    attempts: int
    can_continue: bool = Field(description="true pada mode staged bila tahap selesai dan menunggu langkah berikutnya.")
    can_retry: bool = Field(description="true bila status failed sehingga POST /retry boleh dipanggil.")

    model_config = ConfigDict(json_schema_extra={"example": {
        "analysis_id": 12, "status": "running", "stage": "gwas",
        "stages": [{"name": "qc", "label": "Quality Control", "state": "done"}, {"name": "pca", "label": "PCA", "state": "done"},
                   {"name": "gwas", "label": "Analisis asosiasi", "state": "running"}, {"name": "save", "label": "Menyimpan hasil", "state": "pending"}],
        "percent": 60, "message": "Analisis asosiasi: Linear Regression", "elapsed_seconds": 42, "estimated_remaining_seconds": 28,
        "error": None, "started_at": "2026-11-05T06:00:00+00:00", "finished_at": None, "updated_at": "2026-11-05T06:00:42+00:00",
        "next_step": None, "execution_mode": "auto", "attempts": 1, "can_continue": False, "can_retry": False}})


class LogOut(Out):
    """Log dibaca bertahap. Kirim `next_offset` dari jawaban ini sebagai `offset` pada permintaan berikutnya."""

    logs: list[str] = Field(description="Baris log pada permintaan ini (paling banyak sebanyak limit).")
    offset: int = Field(description="Posisi awal pembacaan pada permintaan ini.")
    limit: int = Field(description="Jumlah baris maksimal yang diminta.")
    next_offset: int = Field(description="Posisi untuk permintaan berikutnya.")
    has_more: bool = Field(description="true bila masih ada log yang belum terkirim. Frontend boleh langsung meminta lagi.")
    finished: bool = Field(description="true bila analisis sudah selesai atau gagal.")
    waiting: bool = Field(description="true bila analisis menunggu langkah berikutnya (mode staged).")

    model_config = ConfigDict(json_schema_extra={"example": {
        "logs": ["[2026-11-05 06:00:01] [INPUT] Menyiapkan data input", "[2026-11-05 06:00:03] [QC] Quality Control"],
        "offset": 0, "limit": 200, "next_offset": 118, "has_more": False, "finished": False, "waiting": False}})


class RetryOut(Out):
    """Jawaban permintaan coba ulang: apakah dijadwalkan, percobaan ke berapa, dan dari tahap mana."""
    analysis_id: int
    scheduled: bool = Field(description="true bila percobaan ulang berhasil dijadwalkan.")
    status: str
    attempt: int = Field(description="Nomor percobaan baru (2 berarti pengulangan pertama).")
    retry_from: str = Field(description="all = dari awal. qc, pca, atau gwas = hanya tahap yang gagal (mode staged).")
    message: str

    model_config = ConfigDict(json_schema_extra={"example": {
        "analysis_id": 12, "scheduled": True, "status": "queued", "attempt": 2, "retry_from": "all",
        "message": "Percobaan ulang dijadwalkan dari awal. Pantau lewat /status."}})


class QcSummaryOut(Out):
    """Ringkasan QC; metrik yang tidak dapat dihasilkan alat ditandai unavailable, bukan diperkirakan."""
    skipped: bool
    parameters: QCParams
    variants_before: int
    variants_after: int
    variants_removed: int
    samples_before: int
    samples_after: int
    removed_by_filter: dict[str, int] = Field(description="Jumlah varian yang dibuang tiap filter (maf, geno, hwe).")
    variants_removed_percent: float = 0.0
    samples_removed_percent: float = 0.0
    metrics_before: dict[str, Any] = Field(default_factory=dict)
    metrics_after: dict[str, Any] = Field(default_factory=dict)
    warnings: list[dict[str, str]] = Field(default_factory=list)
    filter_parameters: dict[str, Any] = Field(default_factory=dict)
    filter_breakdown: dict[str, Any] = Field(default_factory=dict)


class PcaPoint(Out):
    """Koordinat satu sampel pada ruang komponen utama."""
    sample_id: str
    trait: str | None = Field(None, description="Nilai trait sampel, untuk mewarnai titik.")
    pcs: list[float]


class PcaOut(Out):
    """Data scatter plot PCA dengan requested/actual count yang eksplisit."""
    components: list[str] = Field(description="Nama komponen yang benar-benar tersedia.")
    eigenvalues: list[float]
    points: list[PcaPoint]
    requested_components: int = 5
    actual_components: int = 0
    status: Literal["complete", "partial", "unavailable"] = "unavailable"
    warnings: list[str] = Field(default_factory=list)
    variance_explained: list[float] = Field(default_factory=list)
    cumulative_variance_explained: list[float] = Field(default_factory=list)


class DataUsageOut(Out):
    """Ringkasan penggunaan data yang dicatat pada setiap tahapan pipeline."""
    schema_version: str
    uploaded: dict[str, Any]
    preprocessing: dict[str, Any]
    qc: dict[str, Any]
    pca: dict[str, Any]
    association: dict[str, Any]
    summary: dict[str, Any]
    timing: dict[str, Any]
    software_versions: dict[str, Any]
    outputs: dict[str, Any]


class AIInterpretationOut(Out):
    """Status dan hasil interpretasi statistik eksternal."""
    analysis_id: int
    status: Literal["queued", "running", "done", "failed", "unavailable", "stale"]
    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    schema_version: str | None = None
    attempts: int = 0
    input_hash: str | None = None
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    token_usage: dict[str, Any] | None = None
    queued_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class AIInterpretationRetryOut(Out):
    analysis_id: int
    status: Literal["queued", "unavailable"]
    error_code: str | None = None
    message: str


# ---- Post-processing dan hasil ----
class PostprocessRequest(BaseModel):
    """Menentukan varian mana yang disebut signifikan. Aturan lengkap ada di GET /api/gwas/config/options (significance_methods)."""

    method: SignificanceMethod = Field("genome_wide", description="genome_wide (5e-8) | suggestive (1e-5) | bonferroni (0,05 / jumlah varian diuji) | "
                                                                  "fdr (Benjamini-Hochberg) | custom (batas p-value sendiri).")
    threshold: float | None = Field(None, gt=0.0, le=1.0,
                                    description="Dipakai HANYA untuk custom (batas p-value, wajib) dan fdr (nilai q, bawaan 0,05). "
                                                "Untuk metode lain diabaikan, dan respons menyebutkan bahwa nilai itu tidak dipakai.")

    model_config = ConfigDict(json_schema_extra={"examples": [{"method": "genome_wide"}, {"method": "fdr", "threshold": 0.05},
                                                                {"method": "custom", "threshold": 0.001}]})

    @model_validator(mode="before")
    @classmethod
    def accept_ui_labels(cls, raw):
        """Menerima label ambang dari dropdown, misalnya '0.05 (FDR)' atau '5e-8 (Bonferroni)'."""
        if not isinstance(raw, dict):
            return raw
        d = dict(raw)
        if isinstance(d.get("threshold"), str):
            _, num = ui_adapter.parse_threshold(d["threshold"])
            d["threshold"] = num
        m = d.get("method")
        if isinstance(m, str) and m not in ("genome_wide", "suggestive", "bonferroni", "fdr", "custom"):
            meth, num = ui_adapter.parse_threshold(m)
            if meth:
                d["method"] = meth
                if meth in ("custom", "fdr") and d.get("threshold") is None:
                    d["threshold"] = num
                elif meth not in ("custom", "fdr"):
                    d["threshold"] = None   # label seperti "5e-8 (Bonferroni)" memakai ambang bawaan metodenya
        return d


class SignificanceRule(Out):
    """Aturan signifikansi yang SEDANG BERLAKU pada sebuah analisis. Semua tabel dan grafik hasil mengikuti aturan ini."""

    method: str
    label: str
    threshold_p: float = Field(description="Batas p-value yang dipakai. Varian dengan p-value <= angka ini dinyatakan signifikan. "
                                           "Pada FDR, ini batas p-value yang setara dengan q-value <= q (0 bila tidak ada yang lolos).")
    threshold_source: Literal["default_for_method", "computed_from_n_tests", "computed_by_benjamini_hochberg", "user_input"] = Field(
        description="Asal angka: bawaan metode, dihitung dari jumlah varian diuji (Bonferroni), dihitung Benjamini-Hochberg (FDR), atau dari pengguna.")
    adjustment: Literal["none", "bonferroni", "benjamini_hochberg"] = Field(description="Koreksi pengujian berganda yang dipakai.")
    n_tests: int = Field(description="Jumlah varian yang benar-benar diuji (lolos QC dan punya p-value). Dasar hitung Bonferroni dan FDR.")
    q: float | None = Field(None, description="Nilai q (hanya FDR).")
    alpha: float | None = Field(None, description="Tingkat signifikansi keseluruhan (hanya Bonferroni): 0,05.")
    n_significant: int
    ignored_threshold: bool = Field(description="true bila threshold yang dikirim tidak dipakai karena metodenya punya ambang bawaan.")
    notes: list[str] = Field(description="Penjelasan tambahan untuk pengguna, misalnya alasan threshold diabaikan.")
    explanation: str = Field(description="Penjelasan satu paragraf tentang aturan ini dan hasilnya, siap ditampilkan.")
    applied_at: str = Field(description="Kapan aturan ini diterapkan. Berubah setiap kali post-processing dijalankan.")


class PostprocessOut(Out):
    """Jawaban setelah ambang signifikansi diterapkan."""
    analysis_id: int
    significance: SignificanceRule
    message: str

    model_config = ConfigDict(json_schema_extra={"example": {
        "analysis_id": 12, "message": "Ambang signifikansi diterapkan. 14 dari 4.850 varian dinyatakan signifikan.",
        "significance": {"method": "bonferroni", "label": "Bonferroni", "threshold_p": 1.03e-05, "threshold_source": "computed_from_n_tests",
                         "adjustment": "bonferroni", "n_tests": 4850, "q": None, "alpha": 0.05, "n_significant": 14, "ignored_threshold": False,
                         "notes": [], "explanation": "Bonferroni: 0,05 dibagi 4.850 varian yang diuji ...", "applied_at": "2026-11-05T06:20:00+00:00"}}})


class VariantRow(Out):
    """Satu baris hasil: satu varian beserta statistiknya."""
    chromosome: str
    position: int
    rsid: str | None
    ref_allele: str | None
    alt_allele: str | None
    p_value: float = Field(description="p-value mentah dari uji asosiasi.")
    p_bonferroni: float = Field(description="p-value setelah koreksi Bonferroni: p x jumlah varian diuji, paling besar 1.")
    q_value: float | None = Field(None, description="q-value Benjamini-Hochberg. Terisi hanya bila metode aktif adalah fdr.")
    effect_size: float = Field(description="BETA (linear dan LMM) atau ln(odds ratio) (logistik).")
    odds_ratio: float | None = Field(None, description="Hanya untuk regresi logistik.")
    is_significant: bool = Field(description="Signifikan menurut aturan post-processing yang berlaku.")
    chr: str = Field(description="Sama dengan chromosome (nama yang dipakai store frontend).")
    pos: int
    snp: str | None
    pvalue: float


class SummaryOut(Out):
    """Ringkasan hasil GWAS."""

    analysis_id: int
    model_used: str | None
    trait_name: str | None
    n_samples: int | None = Field(None, description="Jumlah sampel yang ikut dianalisis.")
    n_variants_tested: int = Field(description="Total varian yang dianalisis.")
    n_significant: int = Field(description="Jumlah varian signifikan menurut aturan yang berlaku.")
    best_variant: VariantRow | None = Field(description="Varian dengan p-value terkecil.")
    smallest_p_value: float | None
    lambda_gc: float | None = Field(description="Faktor inflasi genomik. Sekitar 1 berarti baik.")
    significance: SignificanceRule | None = Field(description="Metode dan ambang signifikansi yang sedang dipakai.")
    reference_thresholds: dict[str, float] = Field(description="Ambang pembanding: genome_wide, suggestive, bonferroni.")

    model_config = ConfigDict(protected_namespaces=(), json_schema_extra={"example": {
        "analysis_id": 12, "model_used": "Linear Regression", "trait_name": "Height", "n_samples": 120, "n_variants_tested": 4850, "n_significant": 14,
        "best_variant": {"chromosome": "5", "position": 1203344, "rsid": "rs12345", "ref_allele": "A", "alt_allele": "G", "p_value": 2.1e-11,
                         "p_bonferroni": 1.0e-7, "q_value": None, "effect_size": 0.84, "odds_ratio": None, "is_significant": True,
                         "chr": "5", "pos": 1203344, "snp": "rs12345", "pvalue": 2.1e-11},
        "smallest_p_value": 2.1e-11, "lambda_gc": 1.02, "significance": None,
        "reference_thresholds": {"genome_wide": 5e-8, "suggestive": 1e-5, "bonferroni": 1.03e-5}}})


class SignificantOut(Out):
    """Tabel varian signifikan. Tanpa parameter threshold, tabel mengikuti hasil post-processing TERAKHIR."""

    total: int
    limit: int
    offset: int
    items: list[VariantRow]
    source: Literal["saved_postprocess", "query_parameter"] = Field(
        description="saved_postprocess = mengikuti aturan post-processing terakhir. query_parameter = memakai threshold dari parameter permintaan.")
    threshold_used: float | None = Field(description="Batas p-value yang dipakai pada tabel ini.")
    significance: SignificanceRule | None = Field(description="Aturan yang berlaku (null bila memakai parameter threshold).")


class TopOut(Out):
    """Varian terbaik. Selalu diurutkan dari p-value terkecil dan TIDAK dibatasi pada varian signifikan."""

    limit: int
    sorted_by: Literal["p_value"] = "p_value"
    order: Literal["ascending"] = "ascending"
    only_significant: bool = Field(False, description="Selalu false: kandidat terbaik tetap tampil walau belum lolos ambang.")
    n_significant_in_list: int
    items: list[VariantRow]
    significance: SignificanceRule | None


class ManhattanOut(Out):
    """Data Manhattan plot. Pada format columns, larik x, neg_log10_p, dan seterusnya sejajar (indeks sama = titik sama)."""

    filtered: bool = Field(description="true bila jumlah titik dikurangi.")
    total_variants: int
    displayed_variants: int
    significant_total: int = Field(description="Jumlah varian signifikan pada analisis.")
    significant_displayed: int = Field(description="Jumlah varian signifikan yang ikut tergambar.")
    all_significant_displayed: bool = Field(description="true bila SEMUA varian signifikan tergambar. Pengurangan titik tidak membuang varian signifikan.")
    message: str | None
    chromosome_order: list[str]
    chromosome_max_position: dict[str, int]
    chromosome_ticks: dict[str, list]
    thresholds: dict[str, float | None] = Field(description="Garis ambang pada skala -log10(p). `active` = ambang yang sedang berlaku.")
    significance: SignificanceRule | None
    chromosome: list[str] | None = None
    position: list[int] | None = None
    rsid: list[str | None] | None = None
    pvalue: list[float] | None = None
    x: list[float] | None = Field(None, description="Posisi sumbu x kumulatif seluruh genom.")
    neg_log10_p: list[float] | None = None
    is_significant: list[bool] | None = None
    items: list[dict[str, Any]] | None = Field(None, description="Hanya pada format=rows: satu objek per titik.")


class QQOut(Out):
    """Data QQ plot lengkap dengan p-value teramati, p-value harapan, dan lambda GC. Arti tiap istilah ada di `definitions`."""

    n: int = Field(description="Jumlah varian yang diuji.")
    lambda_gc: float | None = Field(description="Faktor inflasi genomik (lambda GC).")
    filtered: bool
    displayed_variants: int
    observed_p: list[float] = Field(description="p-value teramati, urut naik.")
    expected_p: list[float] = Field(description="p-value harapan untuk titik yang sama: (i - 0,5) / n.")
    observed_neg_log10_p: list[float] = Field(description="-log10 dari observed_p. Sumbu y grafik.")
    expected_neg_log10_p: list[float] = Field(description="-log10 dari expected_p. Sumbu x grafik.")
    definitions: dict[str, str] = Field(description="Definisi observed p-value, expected p-value, lambda GC, dan cara membaca grafik.")


# ---- Ekspor konfigurasi ----
class SoftwareVersions(Out):
    """Versi perangkat lunak yang dipakai sebuah analisis."""
    genosphere_api: str = Field(description="Versi aplikasi backend GenoSphere.")
    pipeline: str = Field(description="Versi alur kerja analisis.")
    plink2: str | None = None
    gemma: str | None = Field(None, description="Hanya ada bila model LMM dipakai.")
    python: str | None = None


class ConfigExportOut(Out):
    """Isi berkas JSON konfigurasi yang diunduh. Cukup untuk mengulang analisis yang sama."""

    schema_version: str = Field(description="Versi bentuk berkas ini.")
    exported_at: str
    analysis_id: int
    status: str
    model_used: str | None
    dataset: dict[str, Any] = Field(description="Dataset yang dianalisis: nomor, nama, domain, jumlah sampel dan varian.")
    configuration: dict[str, Any] = Field(description="Seluruh parameter analisis: trait, model, QC, PCA, kovariat, dan ambang signifikansi.")
    significance_applied: SignificanceRule | None = Field(description="Aturan signifikansi yang berlaku saat berkas diekspor.")
    software_versions: SoftwareVersions | None = Field(description="Versi perangkat lunak yang dipakai. null bila analisis belum selesai.")
    duration_seconds: float | None
