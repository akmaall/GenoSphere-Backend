> **Catatan historis:** dokumen ini membandingkan source code versi 0.3.0 sebelumnya. Beberapa kontrak dan keputusan telah berubah pada revisi November 2026. Gunakan `REVISI_BACKEND_2026-11.md` dan OpenAPI source terbaru untuk kondisi aktual.

# 01. Perbandingan dua revisi, hasil uji, dan keputusan versi gabungan (v0.3.0)

Yang dibandingkan: **A** = `GenoSphere_backend_revisi_v0.2.0.zip` (revisi saya sebelumnya), **B** = `genosphere-backend-revisi.zip` (revisi lain), dan **ORIG** = `backend.zip` milik Anda (`backend_1.zip` identik byte-per-byte dengan `backend.zip`, md5 sama). Acuan: `PROYEK_PPL.zip` dan frontend `genosphere-sharing.zip`.

## Kesimpulan

* **Tidak ada yang bisa langsung terintegrasi tanpa kerja di frontend**, karena frontend belum memanggil API sama sekali. Yang bisa dinilai: seberapa jauh backend **siap** melayani frontend itu.
* **A jauh lebih siap daripada B**: 32 dari 34 syarat terpenuhi dibanding 13 (ORIG: 3). Perbedaan terbesar: B tidak menyelesaikan alur Step 5→6→7 (PCA/model dipilih setelah QC), upload PLINK 3 berkas sekali kirim, Excel, `.gz`, hapus berkas, dan kovariat berlabel UI ("Usia", "Jenis Kelamin").
* **Tetapi A belum final**: (1) A tidak punya `POST /api/login/json` dan `GET /config/options` yang ada di B; (2) A menambah kolom database mengikuti SDP (`se`, `p_value_adjusted`, `qc_params`, ...) padahal **ERD final tim di LKP 6 (gambar) tidak memuat kolom itu**. LKP 6 lebih baru dan lebih spesifik daripada SDP, jadi A melenceng dari skema tim.
* **Karena itu saya menyusun versi gabungan v0.3.0**: dasar A + dua fitur B (login JSON, opsi dropdown) + tes data sintetis B untuk FDR, dan **menarik kembali** semua kolom database tambahan A. Hasilnya cocok dengan database yang sudah Anda jalankan (001, 002, 003_gwas_module) ditambah satu migrasi kecil `004_user_profile.sql` (dari B).

## Uji netral (34 syarat dari frontend + dokumen PPL, dijalankan identik ke keempat versi)

Syarat disusun dari kode frontend dan dokumen PPL, bukan dari salah satu revisi. PLINK 2 dan GEMMA asli terpasang.

| Hasil | ORIG | B | A | **Gabungan** |
|---|---|---|---|---|
| Syarat terpenuhi | 3 / 34 | 13 / 34 | 32 / 34 | **32 / 34** |
| Tes bawaan lulus, tanpa PostgreSQL | 65 (+2 dilewati) | 86 (+2) | 102 (+2) | **107 (+3 dilewati)** |
| Tes skema di PostgreSQL asli | 2 gagal (tes usang) | 2 gagal (tes usang) | 2 lulus | **110 lulus total, 0 dilewati** |

Rincian yang membedakan (✔ = lolos):

| Syarat | B | A | Gabungan |
|---|---|---|---|
| Register tanpa username → `@username` terisi di Profil | ✘ | ✔ | ✔ |
| Login JSON untuk kolom "Email / Username" | ✔ | ✘ | ✔ |
| Genotipe `.gz` polos; 3 berkas PLINK sekali kirim; nama asli berkas; hapus berkas/dataset | ✘ | ✔ | ✔ |
| Validasi: % cocok, ukuran fenotipe, teks banner Step 2 | ✘ | ✔ | ✔ |
| Config dengan label dropdown **dan** kovariat "Usia, Jenis Kelamin" (kolom nyata Age/Sex) | ✘ | ✔ | ✔ |
| Estimasi waktu, Guided Mode tanpa dropdown domain | ✘ | ✔ | ✔ |
| **Alur bertahap Step 5→6→7** (QC dulu, PCA/kovariat, lalu model) | ✘ | ✔ | ✔ |
| Manhattan (p mentah, sumbu-x kumulatif, tick), baris `{chr,pos,snp,pvalue}` | ✘ | ✔ | ✔ |
| Ekspor Excel `.xlsx` sungguhan | ✘ | ✔ | ✔ |
| CORS tetap jalan saat Next.js pindah ke port 3001 | ✘ | ✔ | ✔ |
| `GET /config/options` (opsi dropdown dari server) | ✔ | ✘ | ✔ |
| `/health` melaporkan migrasi belum dijalankan; `dataset_id` non-angka → 422 | ✘ | ✔ | ✔ |
| Kolom SDP tambahan di database (R31/R32) | ✘ | ✔ | ✘ **(sengaja)** |

## Keputusan untuk konflik dokumen (aturan: yang terbaru dan paling spesifik menang)

1. **Kolom database.** SDP (Tabel 10/11) memuat `qc_params`, `se`, `p_value_adjusted`, dst.; ERD final di LKP 6 (Gambar 1) hanya memuat `GWAS_Analysis(analysis_id, accession_id, analysis_mode, qc_skipped, pca_used, model_used, status)` dan `GWAS_Result(result_id, analysis_id, snp_id, p_value, effect_size, is_significant)`. **Gabungan mengikuti LKP 6.** Konfigurasi tetap tersimpan di `params.json`/`run_meta.json` di folder analisis (seperti kode asli Anda). FDR dihitung langsung di database tanpa kolom `p_value_adjusted`. Satu-satunya kolom baru: `users.institution` dan `users.bio` (halaman Profil, LKP 8).
2. **SKPL AC-04 (konfigurasi dikunci sekali) vs urutan UI di LKP 7/frontend (PCA dan model dipilih setelah QC).** Alur UI yang menang, tetapi dibuat **opsional**: mode `auto` (default, perilaku lama Anda, konfigurasi QC dikunci sekali) dan mode `staged` (QC dikunci saat Step 4; PCA, kovariat, dan model ditetapkan di Step 6-7 tanpa mengulang QC). Detailnya di `02` dan `03`.
3. **Status analisis.** SDP menyebut empat status. Mode `staged` menambah satu: `awaiting` (tahap selesai, menunggu langkah berikutnya). Mode `auto` hanya memakai empat status asli.
4. **Penerjemah label dropdown.** Dua versi ada (`fe_compat.py` di B, `ui_adapter.py` di A). Saya pakai `ui_adapter.py` karena mencakup semua kemampuan B ditambah teks "MAF >= 0.05, Call Rate >= 0.95", "Sertakan Hasil PCA (5 PC)", dan padanan kovariat ("Usia" → `Age`). `fe_compat.py` tidak dibawa agar tidak ada dua penerjemah.
5. **Excel.** B sengaja tidak membuat `.xlsx` (menambah dependensi, SKPL hanya mewajibkan CSV). Frontend Step 10 menampilkan opsi Excel, jadi gabungan tetap mendukungnya dengan satu dependensi (`openpyxl`). Jika tim lebih suka tanpa Excel, hapus opsi itu di frontend dan `openpyxl` dari `requirements.txt`.
6. **Nama database.** B menyeragamkan default ke `gwas`; `.env` Anda sudah `POSTGRES_DB=gwas`, jadi dibawa ke gabungan (default `gwas`, header SQL `-d gwas`).

## Temuan lain yang saya perbaiki

* `tests/test_postgres_schema.py` di backend Anda (dan di B) usang: memakai kolom `params` yang tidak ada di model, sehingga **2 tes gagal begitu dijalankan di PostgreSQL asli**. Selama ini tidak ketahuan karena tes itu selalu dilewati tanpa PostgreSQL. A "lulus" hanya karena A menambah kolom itu ke model. Pada gabungan tes ini disesuaikan dengan skema tim (003_gwas_module + 004_user_profile) dan berjalan di PostgreSQL asli.
* Revisi saya sebelumnya mengklaim kesesuaian SDP lewat kolom tambahan; itu keliru terhadap ERD final LKP 6 (lihat keputusan 1) dan sudah ditarik.

## Yang tidak berubah dan tidak diuji ulang

Autentikasi JWT/Argon2id, `ensure_owner`, validasi dataset, mesin QC/PCA/GLM/GEMMA (hasil biologis `rs101` tetap teratas), WebSocket. Frontend belum diuji end-to-end di browser (karena belum memanggil API). Versi alat di lingkungan uji: PLINK 2 `2.00a5.12` dan GEMMA `0.98.3`, bukan versi di komputer Anda.
