"""Penyimpanan berkas pengguna dan hasil analisis.

Hanya file ini yang tahu di folder mana berkas disimpan. Bagian lain cukup memanggil fungsi di sini,
jadi bila nanti penyimpanan dipindah ke Object Storage (rencana di SDP), yang diganti hanya file ini.

Susunan folder (di dalam settings.storage_dir, bawaan "uploads/"):

    u{user_id}/ds_{accession_id}/genotype.bed .bim .fam   atau   genotype.vcf / genotype.vcf.gz
                                 phenotype.csv / .tsv / .txt / .pheno
                                 files.json        catatan nama asli, ukuran, dan waktu unggah
                                 validation.json   hasil validasi (Step 2)
                                 runs/a{analysis_id}/...   semua keluaran satu analisis

Soal keamanan: nama berkas dari pengguna tidak pernah dipakai sebagai nama di disk. Berkas selalu disimpan
dengan nama tetap menurut jenisnya (genotype.bed, phenotype.csv, dst.), jadi nama seperti "../../.env"
tidak bisa menimpa berkas lain di server. Nama asli hanya dicatat sebagai teks untuk ditampilkan.
"""
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import UploadFile

from app.config import get_settings
from app.errors import APIError

# Urutan penting: ".vcf.gz" harus dicek sebelum ".vcf" dan ".gz".
GENOTYPE_EXTS = (".vcf.gz", ".vcf", ".bed", ".bim", ".fam")
PHENOTYPE_EXTS = (".csv", ".tsv", ".txt", ".pheno")   # .pheno adalah format fenotipe milik PLINK
DOMAINS = ("kesehatan", "farmasi", "agrikultur", "peternakan")   # domain riset pada Guided Mode


def storage_root() -> Path:
    """Folder induk penyimpanan. Dibuat otomatis bila belum ada."""
    root = Path(get_settings().storage_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def dataset_prefix(user_id: int, accession_id: int) -> str:
    """Awalan folder dataset, misalnya 'u3/ds_12'."""
    return f"u{user_id}/ds_{accession_id}"


def dataset_folder(genotype_file_path: str) -> str:
    """accession.genotype_file_path berisi 'u3/ds_12/genotype'. Fungsi ini mengambil folder-nya saja: 'u3/ds_12'."""
    return genotype_file_path.rsplit("/", 1)[0]


def prefix_owner(path: str | None) -> int | None:
    """Membaca nomor pemilik dari awalan folder ('u3/ds_12/...' berarti pengguna 3). None bila bentuknya tidak dikenal."""
    m = re.match(r"^u(\d+)/ds_\d+", path or "")
    return int(m.group(1)) if m else None


def dataset_dir(prefix: str) -> Path:
    """Alamat lengkap folder dataset. Menolak alamat yang keluar dari folder penyimpanan."""
    root = storage_root()
    p = (root / prefix).resolve()
    if root not in p.parents:
        raise APIError(400, "INVALID_PATH", "Lokasi penyimpanan tidak valid.")
    return p


def match_ext(filename: str | None, allowed: tuple[str, ...]) -> str | None:
    """Mengembalikan ekstensi yang cocok (huruf besar atau kecil sama saja), atau None bila tidak ada yang cocok."""
    name = (filename or "").lower()
    found = next((e for e in allowed if name.endswith(e)), None)
    # Kolom unggah genotipe di frontend menerima ".gz" polos. Satu-satunya genotipe terkompresi yang didukung
    # adalah VCF, jadi ".gz" dibaca sebagai ".vcf.gz". Isinya tetap diperiksa saat validasi.
    if found is None and ".vcf.gz" in allowed and name.endswith(".gz"):
        return ".vcf.gz"
    return found


def save_upload(upload: UploadFile, dest: Path, max_bytes: int | None = None, too_large_code: str = "FILE_TOO_LARGE") -> int:
    """Menyimpan unggahan sedikit demi sedikit ke `dest` dan mengembalikan ukurannya dalam byte.

    Data ditulis ke berkas sementara dulu, baru dipindah ke nama akhir. Dengan begitu unggahan yang putus
    di tengah jalan atau terlalu besar tidak meninggalkan berkas setengah jadi.
    Bila melebihi max_upload_mb, dijawab HTTP 413.
    """
    limit = max_bytes if max_bytes is not None else get_settings().max_upload_mb * 1024 * 1024
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=".upload-")
    size = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := upload.file.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    code = too_large_code if too_large_code in {"FILE_TOO_LARGE", "DATASET_QUOTA_EXCEEDED"} else "FILE_TOO_LARGE"
                    explanation = ("batas maksimum per berkas dari administrator" if code == "FILE_TOO_LARGE"
                                   else "sisa kuota dataset atau ruang disk yang tersedia")
                    raise APIError(413, code,
                                   f"Ukuran berkas melampaui batas {limit / (1024 * 1024):.1f} MiB berdasarkan {explanation}.",
                                   hint="Periksa MAX_UPLOAD_MB, MAX_DATASET_UPLOAD_MB, atau ruang disk server; hapus berkas yang tidak diperlukan bila kuota dataset penuh.")
                out.write(chunk)
        os.replace(tmp_name, dest)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    return size


def run_dir(ddir: Path, analysis_id: int) -> Path:
    """Folder keluaran satu analisis (log, parameter, hasil antara)."""
    d = ddir / "runs" / f"a{analysis_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---- Catatan berkas (nama asli, ukuran, waktu unggah) dan penghapusan ----
FILES_META = "files.json"
SLOT_RE = re.compile(r"^(genotype\.(vcf\.gz|vcf|bed|bim|fam)|phenotype\.(csv|tsv|txt|pheno))$")


def slot_format(slot: str) -> str:
    """Format berkas dari nama slot-nya: 'genotype.vcf.gz' menjadi 'vcf.gz', 'phenotype.csv' menjadi 'csv'."""
    return slot.split(".", 1)[1]


def record_file(ddir: Path, slot: str, original_name: str | None, size: int) -> None:
    """Mencatat nama asli berkas dari pengguna. Hanya untuk ditampilkan, tidak pernah dipakai sebagai alamat."""
    meta = load_files(ddir)
    meta[slot] = {"slot": slot, "original_name": (original_name or slot)[:255], "size_bytes": size,
                  "uploaded_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    (ddir / FILES_META).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def load_files(ddir: Path) -> dict:
    """Membaca catatan nama asli dan ukuran berkas (files.json). {} bila belum ada."""
    f = ddir / FILES_META
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def file_entry(ddir: Path, slot: str) -> dict:
    """Keterangan satu berkas dalam bentuk yang dikirim ke frontend."""
    m = load_files(ddir).get(slot, {})
    p = ddir / slot
    return {"slot": slot, "kind": "genotype" if slot.startswith("genotype") else "phenotype",
            "filename": m.get("original_name", slot), "format": slot_format(slot),
            "size_bytes": p.stat().st_size if p.exists() else m.get("size_bytes", 0),
            "uploaded_at": m.get("uploaded_at")}


def list_files(ddir: Path) -> list[dict]:
    """Daftar berkas dataset yang benar-benar ada di disk, lengkap dengan nama aslinya."""
    if not ddir.exists():
        return []
    return [file_entry(ddir, p.name) for p in sorted(ddir.glob("*")) if SLOT_RE.match(p.name)]


def remove_slot(ddir: Path, slot: str) -> bool:
    """Menghapus satu berkas dataset beserta hasil validasi lamanya, karena hasil itu sudah tidak berlaku.
    True bila ada berkas yang terhapus. Status dataset dihitung ulang dari isi folder (lihat validation.dataset_status)."""
    p = ddir / slot
    if not SLOT_RE.match(slot) or not p.exists():
        return False
    p.unlink()
    meta = load_files(ddir)
    if meta.pop(slot, None) is not None:
        (ddir / FILES_META).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (ddir / "validation.json").unlink(missing_ok=True)
    return True


def remove_dataset_dir(ddir: Path) -> None:
    """Menghapus seluruh folder dataset (berkas unggahan dan semua keluaran analisis). Aman dipanggil bila folder sudah tidak ada."""
    root = storage_root()
    if ddir.resolve() != root and root in ddir.resolve().parents:   # pengaman: folder induk tidak boleh ikut terhapus
        shutil.rmtree(ddir, ignore_errors=True)
