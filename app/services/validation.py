"""Validasi data genotipe dan fenotipe (Step 2 di antarmuka; UC-01, FR-07 sampai FR-09, BR-01, BR-02, NFR-05).

Tujuannya memastikan berkas yang diunggah lengkap, formatnya benar, tidak rusak, dan Sample ID genotipe
cocok dengan fenotipe, sebelum analisis boleh dilanjutkan. Pemeriksaan murni Python, tanpa PLINK, jadi cepat
dan bisa diuji tanpa alat luar.

Setiap temuan dibagi menjadi dua jenis, karena tidak semua masalah berarti data tidak bisa dipakai:

  error    Analisis TIDAK bisa dilanjutkan sampai diperbaiki. Contoh: berkas .fam tidak ada, atau tidak
           ada Sample ID yang cocok antara genotipe dan fenotipe.
  warning  Analisis BISA dilanjutkan, tetapi pengguna perlu tahu. Contoh: sebagian sampel punya nilai
           fenotipe kosong, atau ada varian dengan data genotipe hilang (nanti ditangani tahap QC).

Status dataset yang dihasilkan:

  valid       tidak ada error (warning boleh ada). Dataset boleh dikonfigurasi.
  invalid     ada berkas yang formatnya atau isinya salah.
  incomplete  berkas belum lengkap, misalnya baru .bed dan .bim, .fam belum diunggah.
  (belum_divalidasi dan berubah dihitung di dataset_status, bukan di laporan.)

Setiap temuan berbentuk {code, message, hint, impact}: pesan jelas, cara memperbaikinya, dan dampaknya ke analisis.
"""
import csv
import gzip
import json
import math
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path

MISSING_TOKENS = {"", "na", "n/a", "nan", "null", ".", "-", "-9"}  # token missing untuk nilai fenotipe/kovariat
MAX_LINE_ERRORS = 5          # tampilkan paling banyak 5 nomor baris bermasalah per berkas
MAX_CATEGORY_LEVELS = 10     # kolom teks dengan kategori sebanyak ini atau kurang boleh dipakai sebagai kovariat
MIN_SAMPLES = 10             # minimum sampel yang cocok agar uji asosiasi masuk akal (BR-03)
SMALL_SAMPLE_WARNING = 50    # di bawah ini hasil analisis kurang bertenaga, pengguna diberi peringatan
BED_SCAN_LIMIT = 50_000      # jumlah varian paling banyak yang diperiksa saat menghitung data genotipe hilang
MIXED_COLUMN_RATIO = 0.8     # kolom dengan >= 80% angka tetapi ada isi teks dianggap campuran (salah format)
ID_COLUMN_NAMES = (
    "iid",
    "id",
    "sample",
    "sampleid",
    "sample_id",
    "sample_code",
    "indv",
)

IMPACT_BLOCK = "Analisis tidak dapat dilanjutkan"
IMPACT_HANDLE = "Perlu penanganan lebih lanjut"
IMPACT_QC = "Dapat ditangani pada tahap QC"
IMPACT_INFO = "Analisis tetap bisa berjalan"

# Error yang berarti "berkas belum ada", bukan "berkas salah". Bila hanya ini yang ada, status = incomplete.
_INCOMPLETE_CODES = {"GENOTYPE_MISSING", "GENOTYPE_INCOMPLETE", "PHENOTYPE_MISSING"}

STATUS_LABELS = {
    "valid": "Valid",
    "invalid": "Tidak valid",
    "incomplete": "Berkas belum lengkap",
    "not_validated": "Belum divalidasi",
    "stale": "Berkas berubah setelah validasi, validasi ulang",
}


class _Problems:
    """Pengumpul error dan warning supaya bentuk laporannya seragam."""

    def __init__(self):
        """Menyiapkan dua daftar kosong: error dan warning."""
        self.errors: list[dict] = []
        self.warnings: list[dict] = []

    def error(self, code: str, message: str, hint: str = "", impact: str = IMPACT_BLOCK):
        """Mencatat satu error, yaitu masalah yang menghentikan analisis."""
        self.errors.append({"code": code, "message": message, "hint": hint, "impact": impact})

    def warn(self, code: str, message: str, hint: str = "", impact: str = IMPACT_INFO):
        """Mencatat satu warning, yaitu peringatan yang tidak menghentikan analisis."""
        self.warnings.append({"code": code, "message": message, "hint": hint, "impact": impact})


def _is_float(token: str) -> bool:
    """True bila teks bisa dibaca sebagai angka yang terhingga."""
    try:
        return math.isfinite(float(token))
    except ValueError:
        return False


# ---- Mencari berkas dan menghitung kelengkapan (murah, tidak membaca isi berkas) ----
def find_genotype_files(ddir: Path) -> dict:
    """Peta ekstensi ke path untuk berkas genotipe yang benar-benar ada di folder dataset."""
    found = {}
    for ext in (".vcf.gz", ".vcf", ".bed", ".bim", ".fam"):
        f = ddir / f"genotype{ext}"
        if f.exists():
            found[ext] = f
    return found




# =========================================================
# Automatic phenotype header normalization
# =========================================================

PHENOTYPE_HEADER_ALIASES = {
    # Family ID
    "FID": "FID",
    "FAMILY_ID": "FID",
    "FAMILYID": "FID",

    # Individual ID
    "IID": "IID",
    "INDV": "IID",
    "SAMPLE_ID": "IID",
    "SAMPLEID": "IID",
    "INDIVIDUAL_ID": "IID",

    # Covariates
    "AGE": "COV_AGE",
    "UMUR": "COV_AGE",
    "AGE_YEARS": "COV_AGE",
    "COV_AGE": "COV_AGE",

    "SEX": "COV_SEX",
    "GENDER": "COV_SEX",
    "COV_SEX": "COV_SEX",

    "BMI": "COV_BMI",
    "COV_BMI": "COV_BMI",

    "BATCH": "COV_BATCH",
    "COV_BATCH": "COV_BATCH",

    # Phenotypes
    "HEIGHT": "PHENO_HEIGHT",
    "TINGGI": "PHENO_HEIGHT",
    "PHENO_HEIGHT": "PHENO_HEIGHT",

    "DISEASE": "PHENO_DISEASE",
    "STATUS": "PHENO_DISEASE",
    "CASE_CONTROL": "PHENO_DISEASE",
    "PHENO_DISEASE": "PHENO_DISEASE",

    "CD8": "PHENO_CD8",
    "PHENO_CD8": "PHENO_CD8",

    "MCH": "PHENO_MCH",
    "PHENO_MCH": "PHENO_MCH",

    "SEX_TEMP_B": "PHENO_SEX_TEMP_B",
    "PHENO_SEX_TEMP_B": "PHENO_SEX_TEMP_B",

    "VOL_2012": "PHENO_VOL_2012",
    "PHENO_VOL_2012": "PHENO_VOL_2012",
}


def _normalize_header_token(value: str) -> str:
    value = str(value).strip().lstrip("#").upper()
    value = re.sub(r"[^A-Z0-9]+", "_", value)
    return re.sub(r"_+", "_", value).strip("_")





def find_phenotype_file(ddir: Path) -> Path | None:
    """Mencari berkas fenotipe di folder dataset. None bila belum ada."""
    for ext in (".csv", ".tsv", ".txt", ".pheno"):
        f = ddir / f"phenotype{ext}"
        if f.exists():
            return f
    return None


def completeness(ddir: Path) -> dict:
    """Memeriksa apakah berkas dataset sudah cukup untuk divalidasi.

    Set PLINK dianggap SATU KESATUAN: .bed, .bim, dan .fam harus ada bersama. Menerima satu atau dua
    berkas saja tidak dihitung sebagai genotipe lengkap.
    """
    geno = find_genotype_files(ddir)
    plink_have = [e for e in (".bed", ".bim", ".fam") if e in geno]
    plink_ok = len(plink_have) == 3
    vcf = ".vcf.gz" in geno or ".vcf" in geno
    missing: list[str] = []
    if plink_have and vcf:
        missing.append("genotipe: pilih satu format saja, VCF atau PLINK (.bed/.bim/.fam), jangan keduanya")
    elif not (vcf or plink_ok):
        if plink_have:
            lacking = ", ".join(e for e in (".bed", ".bim", ".fam") if e not in geno)
            missing.append(f"genotipe PLINK belum lengkap: {lacking} belum diunggah")
        else:
            missing.append("genotipe belum diunggah (.vcf atau set PLINK .bed/.bim/.fam)")
    if find_phenotype_file(ddir) is None:
        missing.append("fenotipe belum diunggah")
    return {
        "genotype_complete": (plink_ok or vcf) and not (plink_have and vcf),
        "genotype_format": "vcf" if vcf and not plink_have else ("plink" if plink_ok else None),
        "phenotype_present": find_phenotype_file(ddir) is not None,
        "ready_for_validation": not missing,
        "missing": missing,
    }


def files_fingerprint(ddir: Path) -> dict:
    """Ukuran dan waktu ubah tiap berkas data. Disimpan saat validasi, dibandingkan lagi sebelum analisis,
    supaya hasil validasi yang sudah usang (berkas diganti atau dihapus sesudahnya) tidak dipakai."""
    out = {}
    for f in list(find_genotype_files(ddir).values()) + ([find_phenotype_file(ddir)] if find_phenotype_file(ddir) else []):
        st = f.stat()
        out[f.name] = [st.st_size, st.st_mtime_ns]
    return out


def load_report(ddir: Path) -> dict | None:
    """Membaca laporan validasi terakhir (validation.json). None bila belum pernah divalidasi."""
    f = ddir / "validation.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def dataset_status(ddir: Path) -> dict:
    """Status dataset saat ini, selalu dihitung dari isi folder (bukan hanya dari laporan lama).

    Karena itu menghapus berkas langsung mengubah status: dataset yang tadinya valid menjadi `incomplete`
    begitu salah satu berkasnya hilang, dan `stale` bila berkas diganti setelah validasi.
    """
    comp = completeness(ddir)
    report = load_report(ddir)
    if not comp["ready_for_validation"]:
        status = "incomplete"
    elif report is None:
        status = "not_validated"
    elif report.get("fingerprint") != files_fingerprint(ddir):
        status = "stale"
    else:
        status = report["status"]
    return {"status": status, "label": STATUS_LABELS[status], "can_proceed": status == "valid"}


def current_valid_report(ddir: Path) -> dict | None:
    """Laporan validasi hanya bila masih berlaku untuk berkas yang ada sekarang, selain itu None."""
    report = load_report(ddir)
    if report is None or report.get("fingerprint") != files_fingerprint(ddir):
        return None
    return report


# ---- Genotipe ----
def _missing_table(valid_pairs: int) -> bytes:
    """Tabel 256 entri: untuk tiap nilai byte, berapa pasangan bit (genotipe) yang berkode 01 (hilang).
    Pada berkas .bed setiap byte memuat 4 genotipe, dibaca dari bit terendah. `valid_pairs` membatasi
    jumlah pasangan yang dihitung, dipakai untuk byte terakhir tiap varian yang mungkin tidak penuh."""
    return bytes(sum(1 for i in range(valid_pairs) if (b >> (2 * i)) & 3 == 1) for b in range(256))


def _bed_missing_stats(bed: Path, n_samples: int, n_variants: int) -> dict:
    """Menghitung data genotipe hilang pada berkas .bed (kode 01 = hilang) untuk paling banyak BED_SCAN_LIMIT varian pertama."""
    per_variant = math.ceil(n_samples / 4)
    last_pairs = n_samples - 4 * (per_variant - 1)
    full, last = _missing_table(4), _missing_table(last_pairs)
    checked = min(n_variants, BED_SCAN_LIMIT)
    with_missing = calls_missing = 0
    with open(bed, "rb") as fh:
        fh.seek(3)
        for _ in range(checked):
            row = fh.read(per_variant)
            if len(row) < per_variant:
                break
            miss = sum(row[:-1].translate(full)) + last[row[-1]]
            if miss:
                with_missing += 1
                calls_missing += miss
    return {"variants_checked": checked, "variants_with_missing": with_missing, "missing_calls": calls_missing,
            "total_calls": checked * n_samples}


def _warn_missing_genotypes(stats: dict, pb: _Problems) -> None:
    """Menambahkan peringatan bila ada data genotipe hilang. Dampaknya ditangani tahap QC."""
    if not stats["variants_with_missing"]:
        return
    pct = 100 * stats["missing_calls"] / max(1, stats["total_calls"])
    scope = "varian" if stats["variants_checked"] < BED_SCAN_LIMIT else f"{stats['variants_checked']:,} varian pertama"
    pb.warn("GENOTYPE_MISSING_CALLS",
            f"Ada {stats['variants_with_missing']:,} dari {stats['variants_checked']:,} {scope} yang diperiksa memiliki data genotipe hilang "
            f"({pct:.2f}% dari seluruh pembacaan genotipe).",
            "Tidak perlu tindakan. Varian dengan data hilang terlalu banyak akan disaring pada tahap Quality Control (parameter call rate).",
            IMPACT_QC)


def _check_bed_set(files: dict, pb: _Problems) -> dict:
    """Memeriksa set PLINK sebagai satu kesatuan: .fam (sampel), .bim (varian), dan .bed (biner) harus cocok satu sama lain."""
    info = {"format": "plink", "n_samples": 0, "n_variants": 0, "sample_ids": []}
    missing = [e for e in (".bed", ".bim", ".fam") if e not in files]
    if missing:
        pb.error("GENOTYPE_INCOMPLETE", f"Berkas genotipe PLINK belum lengkap: {', '.join(missing)} belum diunggah.",
                 "Unggah ketiga berkas .bed, .bim, dan .fam dari dataset yang sama. Analisis baru bisa berjalan bila ketiganya ada.")
        return info

    # .fam: 6 kolom per sampel (FID IID PID MID SEX PHENO). IID harus unik.
    ids, bad = [], []
    try:
        with open(files[".fam"], encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                parts = line.split()
                if len(parts) < 6:
                    bad.append(n)
                else:
                    ids.append(parts[1])
    except UnicodeDecodeError:
        pb.error("CORRUPT_FILE", "Berkas .fam rusak (bukan teks yang valid).", "Ekspor ulang berkas PLINK dari sumber aslinya.")
        return info
    if bad:
        pb.error("FAM_FORMAT", f".fam memiliki baris dengan kolom kurang dari 6 (baris {', '.join(map(str, bad[:MAX_LINE_ERRORS]))}).",
                 "Setiap baris .fam harus berisi: FID IID PID MID SEX PHENO.")
    if len(set(ids)) != len(ids):
        pb.error("DUPLICATE_SAMPLE_ID", "Terdapat Sample ID (kolom ke-2 .fam) yang duplikat.", "Pastikan IID setiap sampel unik.")
    info["n_samples"], info["sample_ids"] = len(ids), ids

    # .bim: 6 kolom per varian (CHR ID CM POS A1 A2).
    n_var, bad = 0, []
    try:
        with open(files[".bim"], encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                parts = line.split()
                if len(parts) < 6 or not parts[3].lstrip("-").isdigit():
                    bad.append(n)
                else:
                    n_var += 1
    except UnicodeDecodeError:
        pb.error("CORRUPT_FILE", "Berkas .bim rusak (bukan teks yang valid).", "Ekspor ulang berkas PLINK dari sumber aslinya.")
        return info
    if bad:
        pb.error("BIM_FORMAT", f".bim memiliki baris tidak valid (baris {', '.join(map(str, bad[:MAX_LINE_ERRORS]))}).",
                 "Setiap baris .bim harus berisi: CHR ID CM POS A1 A2, dengan POS berupa angka.")
    info["n_variants"] = n_var

    # .bed: 3 byte pembuka (0x6c 0x1b 0x01) lalu ceil(jumlah_sampel / 4) byte untuk setiap varian.
    bed = files[".bed"]
    with open(bed, "rb") as fh:
        head = fh.read(3)
    if head[:2] != b"\x6c\x1b":
        pb.error("BED_CORRUPT", "Berkas .bed bukan berkas biner PLINK yang valid (header tidak dikenali).",
                 "Pastikan berkas dihasilkan oleh PLINK (--make-bed) dan tidak rusak saat diunggah.")
    elif head[2:3] != b"\x01":
        pb.error("BED_MODE", "Berkas .bed memakai mode individu-mayor yang tidak didukung.",
                 "Konversi ulang dengan PLINK 2: plink2 --bfile <nama> --make-bed.")
    elif not bad and ids and n_var:
        expected = 3 + n_var * math.ceil(len(ids) / 4)
        actual = bed.stat().st_size
        if actual != expected:
            pb.error("BED_SIZE_MISMATCH",
                     f"Ukuran .bed ({actual} byte) tidak sesuai dengan jumlah sampel ({len(ids)}) dan varian ({n_var}) di .fam dan .bim "
                     f"(seharusnya {expected} byte).",
                     "Kemungkinan salah satu berkas rusak atau berasal dari dataset yang berbeda. Unggah ulang ketiganya dari sumber yang sama.")
        else:
            _warn_missing_genotypes(_bed_missing_stats(bed, len(ids), n_var), pb)
    return info


def _check_vcf(path: Path, pb: _Problems) -> dict:
    """Memeriksa VCF: baris header #CHROM, jumlah kolom konsisten di setiap baris data, dan nama sampel unik."""
    info = {"format": "vcf", "n_samples": 0, "n_variants": 0, "sample_ids": []}
    opener = gzip.open if path.name.endswith(".gz") else open
    header, n_var, bad = None, 0, []
    with_missing = missing_calls = 0
    try:
        with opener(path, "rt", encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                if line.startswith("##") or not line.strip():
                    continue
                line = line.rstrip("\r\n")
                if line.startswith("#CHROM"):
                    header = line.split("\t")
                    continue
                if header is None:
                    pb.error("VCF_NO_HEADER", f"Baris header '#CHROM' tidak ditemukan sebelum data (baris {n}).",
                             "VCF yang valid harus memiliki baris '#CHROM POS ID REF ALT ... FORMAT <sampel>'.")
                    return info
                parts = line.split("\t")
                if len(parts) != len(header) or not parts[1].isdigit():
                    bad.append(n)
                else:
                    n_var += 1
                    miss = sum(1 for g in parts[9:] if g[:1] == ".")   # genotipe "./." atau "." berarti hilang
                    if miss:
                        with_missing += 1
                        missing_calls += miss
    except (UnicodeDecodeError, OSError, EOFError, gzip.BadGzipFile):
        pb.error("CORRUPT_FILE", "Berkas VCF rusak atau tidak dapat dibaca.", "Pastikan berkas tidak terpotong, lalu unggah ulang.")
        return info
    if header is None:
        pb.error("VCF_NO_HEADER", "Baris header '#CHROM' tidak ditemukan.", "Pastikan berkas adalah VCF yang valid.")
        return info
    if len(header) < 10 or header[8] != "FORMAT":
        pb.error("VCF_NO_SAMPLES", "VCF tidak memiliki kolom genotipe sampel (FORMAT dan minimal satu sampel).",
                 "Gunakan VCF multi-sampel hasil variant calling.")
        return info
    if bad:
        pb.error("VCF_FORMAT", f"VCF memiliki baris dengan jumlah kolom atau posisi tidak valid (baris {', '.join(map(str, bad[:MAX_LINE_ERRORS]))}).",
                 "Setiap baris data harus punya kolom sebanyak header, dipisahkan TAB, dan POS berupa angka.")
    ids = header[9:]
    if len(set(ids)) != len(ids):
        pb.error("DUPLICATE_SAMPLE_ID", "Terdapat nama sampel yang duplikat pada header VCF.", "Pastikan nama setiap sampel unik.")
    info.update(n_samples=len(ids), n_variants=n_var, sample_ids=ids)
    _warn_missing_genotypes({"variants_checked": n_var, "variants_with_missing": with_missing, "missing_calls": missing_calls,
                             "total_calls": n_var * len(ids)}, pb)
    return info


def check_genotype(ddir: Path, pb: _Problems) -> dict:
    """Memeriksa genotipe dan menambahkan pasangan identitas (FID, IID).

    Untuk VCF, proses konversi pipeline menggunakan --double-id, sehingga FID=IID=nama sampel VCF.
    Untuk PLINK, pasangan diambil langsung dari kolom pertama dan kedua berkas .fam.
    """
    files = find_genotype_files(ddir)
    vcf = files.get(".vcf.gz") or files.get(".vcf")
    plink_any = any(e in files for e in (".bed", ".bim", ".fam"))
    if not files:
        pb.error("GENOTYPE_MISSING", "Berkas genotipe belum diunggah.", "Unggah .vcf atau set PLINK .bed/.bim/.fam.")
        return {"format": None, "files": [], "n_samples": 0, "n_variants": 0, "sample_ids": [], "sample_pairs": []}
    if vcf and plink_any:
        pb.error("GENOTYPE_AMBIGUOUS", "Terdapat dua format genotipe sekaligus (VCF dan PLINK).",
                 "Pilih salah satu format. Hapus berkas yang tidak dipakai, atau buat dataset baru.")
        info = {"format": None, "n_samples": 0, "n_variants": 0, "sample_ids": [], "sample_pairs": []}
    elif vcf:
        info = _check_vcf(vcf, pb)
        ids = info.get("sample_ids", [])
        info["sample_pairs"] = [(sample_id, sample_id) for sample_id in ids]
    else:
        info = _check_bed_set(files, pb)
        pairs = []
        fam_path = files.get(".fam")
        if fam_path is not None:
            try:
                with fam_path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        fields = line.split()
                        if len(fields) >= 2:
                            pairs.append((fields[0], fields[1]))
            except (OSError, UnicodeDecodeError):
                pass  # _check_bed_set already records the relevant .fam error.
        info["sample_pairs"] = pairs
    info["files"] = [{"name": p.name, "size_bytes": p.stat().st_size} for p in files.values()]
    return info



# ---- Fenotipe ----
def read_phenotype_table(path: Path) -> tuple[list[str], list[list[str]]]:
    """Membaca CSV/TSV/TXT dengan mempertahankan nama header asli.

    Spasi di sekitar nama header dibersihkan, tetapi kapitalisasi dan nama
    kolom sumber dipertahankan. Pencocokan nama dilakukan secara case-insensitive.
    """
    ext = path.suffix.lower()
    with open(path, encoding="utf-8-sig", newline="") as fh:
        text = fh.read()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if ext == ".csv":
        rows = list(csv.reader(lines, delimiter=","))
    elif ext == ".tsv":
        rows = list(csv.reader(lines, delimiter="\t"))
    else:
        rows = [re.split(r"\t|\s+", ln.strip()) for ln in lines]
    if not rows:
        return [], []
    header = [h.strip().lstrip("#").strip() for h in rows[0]]
    data = [[c.strip() for c in row] for row in rows[1:]]
    return header, data



def phenotype_id_columns(header: list[str]) -> tuple[int | None, int | None]:
    """Mengembalikan indeks (FID, IID).

    Dataset boleh memakai pasangan FID/IID atau satu kolom ID individu.
    Untuk satu ID, FID dipulihkan dari pasangan genotipe berdasarkan IID.
    """
    normalized = [_normalize_header_token(h) for h in header]

    fid_aliases = {"FID", "FAMILY_ID", "FAMILYID"}
    iid_aliases = {
        "IID", "INDV", "SAMPLE_ID", "SAMPLEID", "INDIVIDUAL_ID",
        "INDIVIDUAL", "SAMPLE", "ID"
    }

    fid_hits = [i for i, h in enumerate(normalized) if h in fid_aliases]
    iid_hits = [i for i, h in enumerate(normalized) if h in iid_aliases]

    if len(fid_hits) == 1 and len(iid_hits) == 1 and fid_hits[0] != iid_hits[0]:
        return fid_hits[0], iid_hits[0]

    if not fid_hits and len(iid_hits) == 1:
        return None, iid_hits[0]

    return None, None


def id_column_index(header: list[str]) -> int | None:
    """Indeks kolom ID individu, baik format IID maupun ID tunggal."""
    return phenotype_id_columns(header)[1]



def _describe_column(name: str, values: list[str]) -> dict | None:
    """Menentukan tipe trait sebuah kolom. Hasil None bila kolom bukan angka.

    binary        hanya dua nilai berbeda dengan kode 0/1 atau 1/2 (kontrol/kasus)
    quantitative  angka kontinu atau diskrit selain itu
    Selalu mencatat jumlah nilai terisi (n_valid) dan kosong (n_missing).
    """
    present = [v for v in values if v.lower() not in MISSING_TOKENS]
    n_missing = len(values) - len(present)
    if not present or not all(_is_float(v) for v in present):
        return None
    nums = [float(v) for v in present]
    distinct = sorted(set(nums))
    base = {"name": name, "n": len(nums), "n_valid": len(nums), "n_missing": n_missing}
    if len(distinct) < 2:
        return {**base, "type": None, "usable": False, "summary": f"n={len(nums)}; nilai konstan"}
    if distinct in ([0.0, 1.0], [1.0, 2.0]):
        high = distinct[1]
        cases = sum(1 for x in nums if x == high)
        return {**base, "type": "binary", "usable": True, "coding": [distinct[0], distinct[1]],
                "n_cases": cases, "n_controls": len(nums) - cases,
                "summary": f"n={len(nums)}; kasus={cases}; kontrol={len(nums) - cases}"}
    return {**base, "type": "quantitative", "usable": True, "two_values": len(distinct) == 2,
            "summary": f"n={len(nums)}; mean={statistics.fmean(nums):.4g}; sd={statistics.pstdev(nums):.4g}"}


def check_phenotype(ddir: Path, pb: _Problems, genotype_pairs=None) -> dict:
    """Validasi fenotipe dengan pasangan FID/IID atau satu kolom ID individu.

    PHENO_ dan COV_ mempertahankan aturan khususnya. Kolom tanpa prefix
    diklasifikasikan berdasarkan tipe dan variasi nilainya.
    """
    path = find_phenotype_file(ddir)
    empty = {"file": None, "n_samples": 0, "columns": [], "traits": [],
             "numeric_columns": [], "categorical_columns": [], "sample_ids": [], "sample_pairs": []}
    if path is None:
        pb.error("PHENOTYPE_MISSING", "Berkas fenotipe belum diunggah.",
                 "Unggah berkas fenotipe .csv, .tsv, .txt atau .pheno.")
        return empty
    try:
        header, rows = read_phenotype_table(path)
    except (UnicodeDecodeError, csv.Error, OSError):
        pb.error("CORRUPT_FILE", "Berkas fenotipe rusak atau bukan teks yang valid.",
                 "Simpan ulang sebagai CSV atau TSV berenkode UTF-8.")
        return empty
    if not header or not rows:
        pb.error("PHENOTYPE_EMPTY", "Berkas fenotipe kosong atau hanya berisi header.",
                 "Isi data fenotipe di bawah baris header.")
        return empty
    if len(header) == 1 and ";" in header[0]:
        pb.error("PHENOTYPE_DELIMITER", "Pemisah kolom ';' tidak didukung.",
                 "Gunakan koma (CSV) atau TAB (TSV).")
        return {**empty, "file": path.name, "columns": header}

    header_issues = []
    fid_idx, iid_idx = phenotype_id_columns(header)
    if len(header) < 2 or iid_idx is None:
        header_issues.append(
            "harus ada satu kolom ID individu yang dikenali "
            "(misalnya IID, INDV, SAMPLE_ID), atau pasangan FID dan IID"
        )
    duplicates = sorted({h for h in header if header.count(h) > 1})
    if duplicates:
        header_issues.append("nama header duplikat: " + ", ".join(duplicates))
    empty_headers = [str(i + 1) for i, h in enumerate(header) if not h]
    if empty_headers:
        header_issues.append("header kosong pada kolom: " + ", ".join(empty_headers))
    if header_issues:
        pb.error("PHENOTYPE_HEADER_INVALID", "Format header fenotipe tidak valid. " + "; ".join(header_issues) + ".",
                 "Gunakan satu kolom ID individu yang cocok dengan IID genotipe, atau pasangan FID dan IID. Nama kolom trait dan kovariat bebas.")
        return {**empty, "file": path.name, "columns": header}


    bad_rows = [n for n, row in enumerate(rows, 2) if len(row) != len(header)]
    if bad_rows:
        pb.error("PHENOTYPE_ROW_LENGTH",
                 f"Jumlah kolom tidak sama dengan header pada baris {', '.join(map(str, bad_rows[:MAX_LINE_ERRORS]))}.",
                 "Pastikan setiap baris memiliki jumlah kolom yang sama dengan header (periksa koma atau TAB yang hilang).")
        return {**empty, "file": path.name, "columns": header}

    id_missing = {"", "na", "n/a", "nan", "null", ".", "-"}
    genotype_pairs = list(genotype_pairs or [])
    genotype_by_iid = {}
    duplicate_genotype_iids = set()

    for fid, iid in genotype_pairs:
        if iid in genotype_by_iid and genotype_by_iid[iid] != fid:
            duplicate_genotype_iids.add(iid)
        else:
            genotype_by_iid[iid] = fid

    iids = [row[iid_idx] for row in rows]
    if fid_idx is not None:
        fids = [row[fid_idx] for row in rows]
    else:
        fids = [genotype_by_iid.get(iid, "") for iid in iids]

    empty_id_rows = [
        n for n, (fid, iid) in enumerate(zip(fids, iids), 2)
        if iid.lower() in id_missing or (fid_idx is not None and fid.lower() in id_missing)
    ]
    if empty_id_rows:
        pb.error(
            "PHENOTYPE_EMPTY_ID",
            f"ID kosong pada baris {', '.join(map(str, empty_id_rows[:MAX_LINE_ERRORS]))}.",
            "Isi ID individu pada setiap baris."
        )

    duplicate_iids = len(set(iids)) != len(iids)
    if duplicate_iids:
        pb.error(
            "DUPLICATE_SAMPLE_ID",
            "Terdapat ID individu duplikat pada berkas fenotipe.",
            "Gunakan ID individu yang unik."
        )

    traits, numeric, categorical = [], [], []
    cov_missing = []
    id_indices = {iid_idx}
    if fid_idx is not None:
        id_indices.add(fid_idx)

    def add_covariate_candidate(name, column, present):
        """Klasifikasi kandidat kovariat numerik atau kategorikal."""
        n_missing = len(column) - len(present)

        if not present:
            cov_missing.append(f"{name}: seluruh nilai missing")
            return

        if all(_is_float(value) for value in present):
            numeric.append(name)
            if n_missing:
                cov_missing.append(f"{name}: {n_missing} missing")
            return

        numeric_count = sum(1 for value in present if _is_float(value))
        if numeric_count / len(present) >= MIXED_COLUMN_RATIO:
            invalid_rows = [
                n for n, value in enumerate(column, 2)
                if value.lower() not in MISSING_TOKENS and not _is_float(value)
            ]
            pb.error(
                "PHENOTYPE_MIXED_VALUES",
                f"Kolom kovariat '{name}' mencampur angka dan teks pada baris "
                f"{','.join(map(str, invalid_rows[:MAX_LINE_ERRORS]))}.",
                "Perbaiki nilai kovariat agar konsisten numerik atau kategorikal."
            )
            return

        levels = sorted(set(present))
        if 2 <= len(levels) <= MAX_CATEGORY_LEVELS:
            categorical.append({"name": name, "levels": levels})
            if n_missing:
                cov_missing.append(f"{name}: {n_missing} missing")
        else:
            cov_missing.append(
                f"{name}: jumlah kategori tidak didukung ({len(levels)})"
            )

    for index, name in enumerate(header):
        if index in id_indices:
            continue

        column = [row[index] for row in rows]
        present = [
            value for value in column
            if value.lower() not in MISSING_TOKENS
        ]

        upper_name = name.strip().upper()

        # PHENO_ tetap mengikuti aturan lama: wajib numerik dan bervariasi.
        if upper_name.startswith("PHENO_"):
            description = _describe_column(name, column)

            if description is None:
                if not present:
                    pb.error(
                        "PHENOTYPE_TRAIT_ALL_MISSING",
                        f"Kolom target '{name}' tidak memiliki nilai terisi.",
                        "Isi nilai fenotipe atau pilih target lain."
                    )
                else:
                    invalid_rows = [
                        n for n, value in enumerate(column, 2)
                        if value.lower() not in MISSING_TOKENS
                        and not _is_float(value)
                    ]
                    numeric_count = sum(
                        1 for value in present if _is_float(value)
                    )
                    code = (
                        "PHENOTYPE_MIXED_VALUES"
                        if numeric_count / len(present) >= MIXED_COLUMN_RATIO
                        else "PHENOTYPE_NON_NUMERIC"
                    )
                    pb.error(
                        code,
                        f"Kolom target '{name}' harus numerik; nilai tidak valid "
                        f"ditemukan pada baris "
                        f"{', '.join(map(str, invalid_rows[:MAX_LINE_ERRORS]))}.",
                        "Perbaiki nilai target menjadi angka. Untuk nilai hilang "
                        "gunakan NA, sel kosong, atau -9 sesuai konvensi dataset."
                    )
                continue

            if not description["usable"]:
                pb.error(
                    "PHENOTYPE_TRAIT_CONSTANT",
                    f"Kolom target '{name}' hanya memiliki satu nilai berbeda.",
                    "Pilih target dengan variasi nilai yang dapat dianalisis."
                )
                continue

            traits.append(description)
            numeric.append(name)
            continue

        # COV_ tetap menjadi kandidat kovariat, bukan trait.
        if upper_name.startswith("COV_"):
            add_covariate_candidate(name, column, present)
            continue

        # Header tanpa prefix:
        # Kolom numerik bervariasi dapat dipilih sebagai trait.
        # Kolom numerik juga tersedia sebagai kandidat kovariat.
        description = _describe_column(name, column)
        if description is not None and description["usable"]:
            traits.append(description)
            numeric.append(name)
            continue

        # Kolom teks atau numerik konstan tidak dipaksakan menjadi trait.
        # Kolom kategorikal sederhana tetap dapat dipilih sebagai kovariat.
        add_covariate_candidate(name, column, present)

    if not traits and not pb.errors:
        pb.error("PHENOTYPE_NO_TRAIT", "Tidak ditemukan kolom numerik dengan variasi nilai yang dapat dianalisis.",
                 "Pastikan setidaknya satu kolom trait berisi angka yang bervariasi. Kode biner yang didukung adalah 0/1 atau 1/2.")

    with_missing = [t for t in traits if t["n_missing"] > 0]
    if with_missing:
        parts = "; ".join(f"{t['name']}: {t['n_missing']} dari {t['n_missing'] + t['n_valid']} sampel"
                           for t in with_missing[:5])
        pb.warn("PHENOTYPE_MISSING_VALUES",
                f"Sebagian sampel memiliki nilai target missing ({parts}).",
                "Sampel tersebut dikeluarkan hanya dari analisis target yang dipilih; data asli tidak diubah.",
                IMPACT_HANDLE)
    if cov_missing:
        pb.warn("COVARIATE_MISSING_OR_UNSUPPORTED", "Informasi kovariat: " + "; ".join(cov_missing[:8]) + ".",
                "Kovariat missing akan mengecualikan sampel hanya bila kolom tersebut dipilih. Kovariat dengan kategori tidak didukung tidak muncul sebagai kandidat.",
                IMPACT_HANDLE)
    for trait in traits:
        if trait.get("two_values"):
            pb.warn("PHENOTYPE_BINARY_CODING",
                    f"Target '{trait['name']}' hanya memiliki dua nilai berbeda, tetapi bukan kode 0/1 atau 1/2, sehingga dibaca sebagai kuantitatif.",
                    "Bila ini trait kasus/kontrol, kodekan ulang menjadi 0/1 atau 1/2.", IMPACT_HANDLE)

    return {"file": path.name, "n_samples": len(iids), "columns": header, "traits": traits,
            "numeric_columns": numeric, "categorical_columns": categorical,
            "sample_ids": iids, "sample_pairs": list(zip(fids, iids))}



# ---- Laporan lengkap ----
def validate_dataset(ddir: Path) -> dict:
    """Validasi genotipe/fenotipe, termasuk kecocokan pasangan FID/IID."""
    pb = _Problems()
    geno = check_genotype(ddir, pb)
    pheno = check_phenotype(ddir, pb, geno.get("sample_pairs", []))

    g_ids = geno.pop("sample_ids", [])
    p_ids = pheno.pop("sample_ids", [])
    g_pairs = {tuple(pair) for pair in geno.pop("sample_pairs", [])}
    p_pairs = {tuple(pair) for pair in pheno.pop("sample_pairs", [])}
    matched = sorted(g_pairs & p_pairs)
    geno_only = sorted(g_pairs - p_pairs)
    pheno_only = sorted(p_pairs - g_pairs)

    def show_pair(pair):
        return f"{pair[0]}:{pair[1]}"

    if g_pairs and p_pairs:
        if not matched:
            pb.error("NO_MATCHING_SAMPLES", "Tidak ada pasangan FID/IID yang cocok antara genotipe dan fenotipe.",
                     "Pastikan kolom FID dan IID pada fenotipe sama persis dengan kolom pertama dan kedua .fam. "
                     "Untuk VCF, pipeline menggunakan FID=IID=nama sampel VCF.")
        elif len(matched) < MIN_SAMPLES:
            pb.error("TOO_FEW_SAMPLES", f"Hanya {len(matched)} pasangan FID/IID yang cocok, minimal {MIN_SAMPLES} diperlukan.",
                     "Tambahkan sampel atau perbaiki pasangan FID/IID yang tidak cocok.")
        if geno_only or pheno_only:
            pb.warn("SAMPLES_PARTIALLY_MATCHED",
                    f"{len(geno_only)} pasangan hanya ada di genotipe dan {len(pheno_only)} hanya ada di fenotipe.",
                    "Sampel yang pasangan FID/IID-nya tidak cocok dikeluarkan dari analisis. Periksa kedua kolom ID bila jumlahnya tidak sesuai harapan.",
                    "Sampel yang tidak cocok tidak ikut dianalisis")
        if len(matched) >= MIN_SAMPLES and len(matched) < SMALL_SAMPLE_WARNING:
            pb.warn("LOW_SAMPLE_COUNT", f"Jumlah sampel cocok hanya {len(matched)}. Hasil GWAS dengan sampel sedikit kurang bertenaga secara statistik.",
                    "Gunakan lebih banyak sampel bila tersedia.", "Analisis tetap bisa berjalan, hasil kurang dapat diandalkan")

    pheno_file = find_phenotype_file(ddir)
    pheno["size_bytes"] = pheno_file.stat().st_size if pheno_file else 0
    n_geno = len(g_ids)

    if not pb.errors:
        status = "valid"
    elif {e["code"] for e in pb.errors} <= _INCOMPLETE_CODES:
        status = "incomplete"
    else:
        status = "invalid"

    if status == "valid":
        note = f" Ada {len(pb.warnings)} peringatan yang sebaiknya dibaca." if pb.warnings else ""
        summary = "Tidak ditemukan error pada struktur data. Dataset siap dikonfigurasi." + note
    else:
        label = "belum lengkap" if status == "incomplete" else "bermasalah"
        summary = f"Dataset {label}: ditemukan {len(pb.errors)} error. {pb.errors[0]['message']}"

    report = {
        "validated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": status,
        "can_proceed": status == "valid",
        "summary_message": summary,
        "errors": pb.errors,
        "warnings": pb.warnings,
        "genotype": geno,
        "phenotype": pheno,
        "matching": {
            "genotype_samples": len(g_ids), "phenotype_samples": len(p_ids), "matched": len(matched),
            "unmatched_genotype": len(geno_only), "unmatched_phenotype": len(pheno_only),
            "unmatched_total": len(geno_only) + len(pheno_only),
            "matched_percent": round(100 * len(matched) / n_geno, 1) if n_geno else 0.0,
            "unmatched_genotype_examples": [show_pair(pair) for pair in geno_only[:10]],
            "unmatched_phenotype_examples": [show_pair(pair) for pair in pheno_only[:10]],
        },
        "fingerprint": files_fingerprint(ddir),
    }
    (ddir / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report

