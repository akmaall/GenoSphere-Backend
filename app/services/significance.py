"""Aturan ambang signifikansi, satu tempat untuk semua.

Dipakai oleh konfigurasi analisis (Step 4), post-processing (Step 9), dan semua endpoint hasil, jadi arti
setiap metode tidak bisa berbeda antar halaman. Modul ini murni aturan, tidak menyentuh database.

Lima metode dan cara kerjanya:

  genome_wide  p-value harus <= 5 x 10^-8. Ambang ini tetap, tidak bisa diubah.
  suggestive   p-value harus <= 1 x 10^-5. Ambang ini tetap, tidak bisa diubah.
  bonferroni   p-value harus <= 0,05 dibagi jumlah varian yang BENAR-BENAR diuji pada analisis ini
               (varian yang lolos QC dan punya p-value), bukan jumlah varian pada berkas unggahan.
  fdr          Benjamini-Hochberg. Setiap p-value diubah menjadi q-value (p-value terkoreksi). Varian signifikan
               bila q-value <= q yang dipilih (bawaan 0,05).
  custom       p-value harus <= angka yang dimasukkan pengguna. Angka ini wajib diisi.

Parameter `threshold` hanya berarti untuk dua metode: custom (batas p-value) dan fdr (nilai q).
Pada tiga metode lain parameter itu DIABAIKAN dan hasilnya memberi tahu bahwa nilainya tidak dipakai,
supaya angka lama yang tersisa di formulir tidak mengubah arti metode tanpa disadari.
"""
GENOME_WIDE_P = 5e-8
SUGGESTIVE_P = 1e-5
BONFERRONI_ALPHA = 0.05
DEFAULT_FDR_Q = 0.05

METHODS: dict[str, dict] = {
    "genome_wide": {"label": "Genome-wide (5e-8)", "uses_threshold": False,
                    "description": "Varian signifikan bila p-value <= 5 x 10^-8. Ambang tetap yang umum dipakai pada GWAS."},
    "suggestive": {"label": "Suggestive (1e-5)", "uses_threshold": False,
                   "description": "Varian dianggap menjanjikan bila p-value <= 1 x 10^-5. Ambang tetap, lebih longgar dari genome-wide."},
    "bonferroni": {"label": "Bonferroni", "uses_threshold": False,
                   "description": "Varian signifikan bila p-value <= 0,05 dibagi jumlah varian yang diuji pada analisis ini."},
    "fdr": {"label": "FDR (Benjamini-Hochberg)", "uses_threshold": True, "threshold_meaning": "nilai q (batas false discovery rate)",
            "description": "Setiap p-value diubah menjadi q-value (p-value terkoreksi) dengan metode Benjamini-Hochberg. "
                           "Varian signifikan bila q-value <= q yang dipilih (bawaan 0,05)."},
    "custom": {"label": "Ambang sendiri", "uses_threshold": True, "threshold_meaning": "batas p-value",
               "description": "Varian signifikan bila p-value <= angka yang Anda masukkan. Angka wajib diisi."},
}
FIXED_THRESHOLD_METHODS = ("genome_wide", "suggestive", "bonferroni")


def infer_method(value: float) -> str:
    """Menebak metode dari angka polos: 5e-8 berarti genome_wide, 1e-5 berarti suggestive, selain itu custom."""
    if abs(value - GENOME_WIDE_P) < 1e-12:
        return "genome_wide"
    if abs(value - SUGGESTIVE_P) < 1e-12:
        return "suggestive"
    return "custom"


def check_rule(method: str, threshold: float | None) -> dict:
    """Memeriksa kombinasi metode dan threshold, lalu mengembalikan aturan yang SAH dipakai.

    Hasil: {"method", "threshold", "ignored_threshold", "errors", "notes"}
      threshold          angka yang dipakai (batas p untuk custom, q untuk fdr), None untuk tiga metode lain
      ignored_threshold  True bila pengguna mengirim angka tetapi metodenya tidak memakai angka itu
      errors             alasan penolakan (custom tanpa angka, angka di luar rentang)
      notes              penjelasan untuk pengguna tentang yang terjadi pada threshold
    """
    errors, notes, ignored = [], [], False
    used: float | None = None
    if method in FIXED_THRESHOLD_METHODS:
        if threshold is not None:
            ignored = True
            fixed = {"genome_wide": "5e-8", "suggestive": "1e-5", "bonferroni": "0,05 dibagi jumlah varian yang diuji"}[method]
            notes.append(f"Nilai threshold yang dikirim ({threshold:g}) diabaikan karena metode {method} memakai ambang bawaan: {fixed}. "
                         "Pilih metode custom bila ingin memakai angka sendiri.")
    elif method == "fdr":
        used = DEFAULT_FDR_Q if threshold is None else threshold
        if not 0 < used <= 0.5:
            errors.append("Nilai q untuk FDR harus lebih dari 0 dan paling besar 0,5 (contoh umum: 0,05 atau 0,1).")
    elif method == "custom":
        if threshold is None:
            errors.append("Metode custom memerlukan nilai threshold (batas p-value), misalnya 0.001.")
        elif not 0 < threshold <= 1:
            errors.append("Threshold custom harus lebih dari 0 dan paling besar 1.")
        else:
            used = threshold
    else:
        errors.append(f"Metode '{method}' tidak dikenal. Pilihan: {', '.join(METHODS)}.")
    return {"method": method, "threshold": used, "ignored_threshold": ignored, "errors": errors, "notes": notes}
