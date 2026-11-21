# Kontrak API untuk frontend

Dokumen ini untuk Front-End Developer. Isinya urutan pemanggilan, bentuk data yang penting, arti status, dan daftar kode kesalahan. Bentuk lengkap setiap respons ada di Swagger (`/docs`). Kode contoh siap pakai ada di `frontend-integration/lib/api.ts`.

## Aturan umum

Alamat dasar mengikuti `NEXT_PUBLIC_API_URL`, misalnya `http://localhost:8000`. Semua alamat di bawah ini diawali `/api`.

Token. Setelah `POST /api/login` berhasil, simpan `access_token` dari jawabannya. Kirim pada setiap permintaan lain lewat header `Authorization: Bearer <access_token>`. Bila server menjawab 401, hapus token dan arahkan pengguna ke halaman login.

Format kesalahan. Semua kesalahan berbentuk:

```json
{"detail": {"code": "VALIDATION_ERROR", "message": "Pesan yang boleh ditampilkan.", "hint": "Saran perbaikan (kadang ada).",
            "errors": [{"field": "confirm_password", "message": "Konfirmasi kata sandi tidak sama dengan kata sandi."}],
            "details": {}}}
```

Gunakan `code` untuk logika program dan `message` untuk ditampilkan. `errors` ada pada 422 dan pada konfigurasi yang ditolak, `details` ada pada beberapa 409.

## Login

Satu alamat: `POST /api/login`. Kirim JSON `{"identifier": "email atau username", "password": "..."}`. Jawaban:

```json
{"access_token": "...", "token_type": "bearer", "expires_in": 3600, "user": {"user_id": 3, "full_name": "...", "email": "...", "username": "..."}}
```

Alamat lama `/api/login/json` masih hidup tetapi sebaiknya tidak dipakai lagi.

## Alur satu analisis

### Step 1, unggah

`POST /api/gwas/upload-genotype` dan `POST /api/gwas/upload-phenotype` memakai `multipart/form-data`. Setiap request mengirim tepat satu berkas pada field `file`. Kolom form opsional: `dataset_id` (isi untuk menambah berkas ke dataset yang sama), `name`, `organism`, dan `source_domain`.

Genotipe: satu `.vcf`/`.vcf.gz`, atau tiga berkas `.bed`, `.bim`, `.fam`. Untuk format PLINK, kirim tiga request berurutan; gunakan `dataset_id` dari respons pertama untuk request kedua dan ketiga. Fenotipe: satu `.csv`, `.tsv`, `.txt`, atau `.pheno`.

Jawaban:

```json
{"status": "success", "message": "1 file genotipe berhasil diunggah", "dataset_id": 12,
 "files": [{"slot": "genotype.bed", "filename": "kohort.bed", "format": "bed", "size_bytes": 1048576}],
 "dataset_files": [], "ready_for_validation": false, "missing": ["fenotipe belum diunggah"],
 "validation_status": "incomplete", "validation_label": "Berkas belum lengkap", "next": "..."}
```

`files` berisi berkas yang berhasil diunggah pada permintaan ini (satu item). `dataset_files` berisi semua berkas dataset. Aktifkan tombol validasi bila `ready_for_validation` true, dan tampilkan `missing` selama masih false. Simpan `dataset_id` untuk langkah berikutnya.

Hapus satu berkas: `DELETE /api/gwas/datasets/{id}/files/{slot}`, dengan `slot` dari `files[].slot`. Jawabannya memuat `dataset` dengan status terbaru. Menghapus berkas dari dataset yang tadinya valid mengubahnya menjadi `incomplete`.

Hapus dataset memakai dua langkah:
1. `DELETE /api/gwas/datasets/{id}` tanpa parameter dijawab 409 dengan `code = CONFIRMATION_REQUIRED` dan `details.will_delete` (jumlah berkas, analisis, dan baris hasil). Tampilkan isinya di dialog konfirmasi.
2. Setelah pengguna setuju, panggil `DELETE /api/gwas/datasets/{id}?confirm=true`.

### Step 2, validasi

`POST /api/gwas/datasets/{id}/validate`. Jawaban memuat `status` (`valid`, `invalid`, `incomplete`), `can_proceed`, `summary_message`, `errors`, `warnings`, `genotype`, `phenotype`, dan `matching`. Setiap anggota `errors` dan `warnings` berisi `code`, `message`, `hint`, dan `impact`.

Error menghentikan analisis. Warning tidak. Tombol lanjut aktif bila `can_proceed` true, dan warning ditampilkan sebagai peringatan kuning.

Status dataset (`validation_status` pada daftar dan detail dataset):

| Nilai | Arti |
|---|---|
| `incomplete` | berkas belum lengkap |
| `not_validated` | lengkap tetapi belum divalidasi |
| `stale` | berkas berubah setelah validasi, validasi ulang |
| `valid` | siap dikonfigurasi |
| `invalid` | ada berkas yang salah |

Dropdown fenotipe: `GET /api/gwas/datasets/{id}/traits` (hanya untuk dataset valid) mengirim trait yang bisa dianalisis beserta tipe dan model yang cocok, dan `covariate_candidates` untuk pilihan kovariat.

### Step 3 dan 4, konfigurasi

Isi dropdown dari `GET /api/gwas/config/options`. Domain riset untuk Guided Mode dari `GET /api/gwas/config/presets`. Penjelasan model dari `GET /api/gwas/config/models`. Penjelasan parameter dari `GET /api/gwas/config/parameters`.

`POST /api/gwas/config/check` memeriksa konfigurasi tanpa menjalankan apa pun. Isinya sama dengan `POST /api/gwas/analyses`:

```json
{"dataset_id": 12, "trait_name": "Height", "trait_type": "quantitative", "model": "auto",
 "qc": {"maf": 0.05, "call_rate": 0.95, "hwe_p": 1e-6},
 "use_pca": true, "pca_components": 10,
 "covariates": ["Age", "Sex"],
 "significance_method": "genome_wide",
 "execution_mode": "auto"}
```

Aturan yang perlu diketahui:
- `pca_components` hanya 5, 10, atau 20, dan hanya berlaku bila `use_pca` true. Bila `use_pca` false, nilainya diabaikan dan jawaban memberi warning.
- `covariates` hanya berisi kolom dari berkas fenotipe. Komponen utama PCA tidak ditulis di sini, karena sumbernya genotipe dan diatur lewat `use_pca` dan `pca_components`. Jawaban `config.covariate_sources` menunjukkan keduanya.
- `significance_threshold` hanya dipakai oleh `custom` (batas p-value, wajib) dan `fdr` (nilai q). Untuk metode lain diabaikan.
- `model` boleh `auto`. Model manual harus cocok dengan tipe trait.
- Teks dropdown boleh dikirim apa adanya, misalnya `"Logistic Regression"`, `"Biner (Case / Control)"`, `"5 PC"`, `"Age + Sex"`, atau `"0.05 (FDR)"`.

Jawaban `config/check`: `valid`, `errors`, `warnings`, `config` (konfigurasi setelah nilai bawaan terisi), dan `estimate` (teks "Estimasi Waktu").

`POST /api/gwas/analyses` menjawab 202 dengan `analysis_id`. Bila konfigurasi belum bisa dijalankan, jawabannya 422 `CONFIG_INVALID` dengan `errors`.

### Step 5 sampai 7, QC, PCA, dan GWAS

Mode auto (`execution_mode = auto`): satu kali panggilan, lalu pantau sampai selesai.

Mode bertahap (`execution_mode = staged`): analisis berhenti setelah QC. Urutannya:

| Langkah | Panggilan | Syarat |
|---|---|---|
| 1 | `POST /analyses` dengan `execution_mode = staged` | menjalankan QC, lalu status `awaiting` dengan `next_step = pca` |
| 2 | `GET /analyses/{id}/qc-summary` | menampilkan hasil QC (Step 5) |
| 3 | `PUT /analyses/{id}/covariates` dengan `{"covariates": ["age", "sex"]}` | opsional, boleh dipanggil selama `awaiting` |
| 4 | `POST /analyses/{id}/pca` dengan `{"use_pca": true, "pca_components": 10}` | pilihan PC hanya 5, 10, 20; jumlah aktual dapat lebih kecil dan status PCA dapat `partial` atau `unavailable` |
| 5 | `GET /analyses/{id}/pca` | scatter plot PCA, setelah PCA selesai |
| 6 | `POST /analyses/{id}/run` dengan `{"model": "auto"}` | hanya saat `next_step = gwas` |

Panggilan yang tidak pada waktunya dijawab 409 `WRONG_STAGE` (dengan `details.next_step` yang seharusnya) atau `NOT_STAGED` bila analisisnya mode auto.

Status analisis (`status`):

| Nilai | Arti |
|---|---|
| `queued` | menunggu giliran dijalankan |
| `running` | sedang berjalan |
| `awaiting` | mode bertahap, satu tahap selesai dan menunggu langkah berikutnya |
| `done` | selesai, hasil bisa dilihat |
| `failed` | gagal, lihat `error` dan boleh dicoba ulang |

Tahap yang ditampilkan (`stages` pada `/status`), sama untuk mode auto dan bertahap: `qc`, `pca`, `gwas`, `save`. Tiap tahap bernilai `pending`, `running`, `done`, `skipped` (PCA tidak dipakai), atau `failed`.

Memantau:
- `GET /analyses/{id}/status`: ringan, untuk polling tiap 1 sampai 2 detik. Berisi `status`, `stages`, `percent`, `message`, `estimated_remaining_seconds`, `error`, `next_step`, `can_continue`, dan `can_retry`.
- `GET /analyses/{id}`: detail lengkap untuk halaman detail, memuat konfigurasi (`trait_type`, `method`, `qc`, `pca`, `number_of_pcs`, `covariates`), `attempts`, `created_at`, `updated_at`, `finished_at`.
- `GET /analyses/{id}/log?offset=0&limit=200`: log bertahap. Jawaban berisi `logs`, `offset`, `limit`, `next_offset`, `has_more`. Pakai `next_offset` sebagai `offset` berikutnya, dan ulangi selama `has_more` true.
- WebSocket `ws://host/api/gwas/analyses/{id}/ws?token=<access_token>`: log dan progres langsung. Ditutup server saat `done`, `failed`, atau `awaiting`. Kode penutupan 4401 berarti token salah, 4404 berarti analisis bukan milik pengguna.

Coba ulang: `POST /analyses/{id}/retry`, hanya untuk status `failed`. Jawaban memuat `scheduled`, `attempt`, `retry_from`, dan `message`. Status lain dijawab 409 `NOT_RETRYABLE`.

### Step 8, ambang signifikansi

`POST /analyses/{id}/postprocess` dengan `{"method": "...", "threshold": ...}`. Boleh dipanggil berkali-kali tanpa mengulang analisis.

| Metode | Aturan | `threshold` |
|---|---|---|
| `genome_wide` | p ≤ 5e-8 | diabaikan |
| `suggestive` | p ≤ 1e-5 | diabaikan |
| `bonferroni` | p ≤ 0,05 dibagi jumlah varian yang diuji | diabaikan |
| `fdr` | Benjamini-Hochberg, q-value ≤ q | nilai q, bawaan 0,05 |
| `custom` | p ≤ nilai pengguna | wajib, batas p-value |

Jawaban `significance` memuat metode, batas p-value yang dipakai (`threshold_p`), jumlah varian yang diuji (`n_tests`), jumlah signifikan, `ignored_threshold`, `notes`, dan `explanation` (satu paragraf penjelasan siap tampil).

### Step 9 dan 10, hasil dan unduhan

Semua endpoint hasil baru bisa dipanggil bila status `done`, kalau tidak 409 `ANALYSIS_NOT_FINISHED`. Semuanya memuat `significance`, yaitu aturan yang sedang berlaku, jadi tabel dan grafik selalu mengikuti post-processing terakhir.

| Endpoint | Isi |
|---|---|
| `/results/summary` | total varian diuji, jumlah signifikan, varian terbaik, p-value terkecil, lambda GC, metode dan ambang |
| `/results/manhattan?format=rows` | titik grafik. `all_significant_displayed` selalu true kecuali varian signifikan melebihi batas titik |
| `/results/qq` | `observed_p`, `expected_p`, versi -log10, `lambda_gc`, dan `definitions` |
| `/results/significant` | tabel varian signifikan menurut aturan terakhir. Parameter `threshold` hanya sementara |
| `/results/top` | varian terbaik menurut p-value terkecil, tidak dibatasi pada yang signifikan |
| `/export?format=csv` | berkas unduhan. Format: csv, tsv, json, xlsx. Tambahkan `only_significant=true` bila perlu |
| `/config/export` | berkas JSON konfigurasi dan versi perangkat lunak |

Unduhan butuh header `Authorization`, jadi ambil sebagai blob lalu simpan lewat tautan sementara. Contohnya ada di `gwas.download` dan `gwas.downloadConfig` pada `api.ts`. Nama berkas ada di header `Content-Disposition`.

## Daftar kode kesalahan

Autentikasi dan akun

| Kode | Status | Arti |
|---|---|---|
| `TOKEN_MISSING`, `TOKEN_INVALID`, `TOKEN_EXPIRED` | 401 | belum login, token rusak, token kedaluwarsa |
| `INVALID_CREDENTIALS` | 401 | email, username, atau kata sandi salah |
| `USER_INACTIVE` | 403 | akun dinonaktifkan |
| `EMAIL_ALREADY_REGISTERED`, `USERNAME_ALREADY_TAKEN`, `ACCOUNT_ALREADY_EXISTS` | 409 | pendaftaran bentrok |
| `RESET_TOKEN_INVALID`, `RESET_TOKEN_EXPIRED` | 400 | tautan pemulihan salah atau kedaluwarsa |
| `UNSUPPORTED_MEDIA_TYPE` | 415 | login tidak dikirim sebagai JSON atau form |
| `VALIDATION_ERROR` | 422 | isian belum valid, lihat `errors` |

Data dan berkas

| Kode | Status | Arti |
|---|---|---|
| `RESOURCE_NOT_FOUND`, `FILE_NOT_FOUND` | 404 | data tidak ada atau bukan milik pengguna |
| `INVALID_GENOTYPE_FORMAT`, `INVALID_PHENOTYPE_FORMAT` | 400 | ekstensi berkas tidak didukung |
| `NO_FILE`, `TOO_MANY_FILES`, `DUPLICATE_FILE`, `INVALID_DOMAIN`, `INVALID_SLOT` | 400 | unggahan tidak sesuai aturan |
| `FILE_TOO_LARGE` | 413 | melebihi batas ukuran |
| `DATASET_IN_USE` | 409 | dataset sudah dipakai analisis, berkasnya tidak bisa diubah |
| `CONFIRMATION_REQUIRED` | 409 | hapus dataset butuh `confirm=true`, lihat `details.will_delete` |
| `ANALYSIS_RUNNING` | 409 | masih ada analisis berjalan pada dataset |
| `DATASET_NOT_VALIDATED` | 409 | dataset belum bisa dipakai untuk daftar trait |

Konfigurasi dan analisis

| Kode | Status | Arti |
|---|---|---|
| `CONFIG_INVALID` | 422 | konfigurasi ditolak, alasannya ada di `errors[].code` |
| `WRONG_STAGE`, `NOT_STAGED` | 409 | langkah mode bertahap tidak pada waktunya |
| `NOT_RETRYABLE` | 409 | coba ulang hanya untuk status `failed` |
| `ANALYSIS_NOT_FINISHED` | 409 | hasil dan ekspor menunggu status `done` |
| `STAGE_NOT_READY` | 409 | tahap yang diminta belum selesai |
| `THRESHOLD_REQUIRED`, `SIGNIFICANCE_INVALID` | 422 | ambang tidak sah |
| `INVALID_EXPORT_FORMAT` | 400 | format ekspor tidak dikenal |

Alasan konfigurasi ditolak (`errors[].code` pada `CONFIG_INVALID` dan `config/check`)

`DATASET_INCOMPLETE`, `DATASET_NOT_VALIDATED`, `DATASET_CHANGED`, `DATASET_INVALID`, `DOMAIN_REQUIRED`, `DOMAIN_MISMATCH`, `TRAIT_NOT_FOUND`, `TRAIT_TYPE_MISMATCH`, `TRAIT_TOO_FEW_VALUES`, `MODEL_TRAIT_MISMATCH`, `BINARY_CLASS_TOO_SMALL`, `COVARIATE_NOT_FOUND`, `COVARIATE_IS_TRAIT`, `COVARIATE_NOT_SUPPORTED`, `COVARIATE_DUPLICATE`, `TOO_FEW_SAMPLES`, `SIGNIFICANCE_INVALID`, `TOOL_NOT_AVAILABLE`. Permintaan 2 PC atau nilai selain 5/10/20 ditolak sebagai validasi input.

Peringatan konfigurasi (`warnings[].code`)

`PCA_COMPONENTS_IGNORED`, `PCA_COMPONENTS_DEFAULT`, `PCA_WITH_LMM`, `QC_SKIPPED`, `BINARY_CLASS_SMALL`, `THRESHOLD_IGNORED`. Peringatan hasil PCA dan keterbatasan sumber daya tersedia pada objek hasil/preflight terkait.

Temuan validasi data (`errors[].code` dan `warnings[].code` pada laporan validasi)

Error: `GENOTYPE_MISSING`, `GENOTYPE_INCOMPLETE`, `GENOTYPE_AMBIGUOUS`, `BED_CORRUPT`, `BED_MODE`, `BED_SIZE_MISMATCH`, `BIM_FORMAT`, `FAM_FORMAT`, `VCF_FORMAT`, `VCF_NO_HEADER`, `VCF_NO_SAMPLES`, `CORRUPT_FILE`, `DUPLICATE_SAMPLE_ID`, `PHENOTYPE_MISSING`, `PHENOTYPE_EMPTY`, `PHENOTYPE_DELIMITER`, `PHENOTYPE_NO_ID`, `PHENOTYPE_EMPTY_ID`, `PHENOTYPE_ROW_LENGTH`, `PHENOTYPE_NO_TRAIT`, `PHENOTYPE_MIXED_VALUES`, `NO_MATCHING_SAMPLES`, `TOO_FEW_SAMPLES`.

Warning: `GENOTYPE_MISSING_CALLS`, `PHENOTYPE_MISSING_VALUES`, `PHENOTYPE_BINARY_CODING`, `SAMPLES_PARTIALLY_MATCHED`, `LOW_SAMPLE_COUNT`.

## Perubahan penting bagi frontend

Bila frontend sudah memanggil API versi pengembangan sebelumnya, hal berikut perlu disesuaikan:

1. Login memakai `POST /api/login` dengan JSON. Alamat lama masih hidup.
2. Setiap request unggahan memakai satu field `file`. Untuk BED/BIM/FAM, kirim tiga request dengan `dataset_id` yang sama.
3. Respons unggah memuat `files` (daftar satu item) dengan `slot`, `filename`, `format`, dan juga `dataset_files`.
4. `warnings` pada validasi dan konfigurasi berupa objek (`code`, `message`, `hint`, `impact`), tidak lagi teks.
5. Status dataset memakai `validation_status` dengan nilai bahasa Inggris (`incomplete`, `not_validated`, `stale`, `valid`, `invalid`) disertai `validation_label` berbahasa Indonesia untuk tampilan.
6. `pca_components` hanya 5, 10, atau 20. Kovariat fenotipe diatur terpisah dari PCA.
7. Mode bertahap: PCA, kovariat, dan penjalanan analisis sudah menjadi tiga endpoint terpisah.
8. Log memakai `offset` dan `limit`, jawabannya `logs`, `next_offset`, dan `has_more`.
9. QQ plot memakai `observed_p`, `expected_p`, `observed_neg_log10_p`, `expected_neg_log10_p`, dan `lambda_gc`.
10. Post-processing memakai `method` dan `threshold`. Jawabannya objek `significance` yang juga muncul di semua endpoint hasil.


## Data Usage Summary, QC, ekspor data sumber, dan interpretasi

Endpoint tambahan (semua memerlukan autentikasi dan memeriksa kepemilikan analisis):

| Method | Path | Keterangan |
|---|---|---|
| GET | `/api/gwas/analyses/{id}/data-usage` | Ringkasan penggunaan sampel/varian dari upload hingga hasil |
| GET | `/api/gwas/analyses/{id}/data-usage/export?format=json|csv` | Unduh Data Usage Summary |
| GET | `/api/gwas/analyses/{id}/qc-summary/export?format=json|csv` | Unduh ringkasan QC |
| GET | `/api/gwas/analyses/{id}/results/manhattan/export?format=csv|tsv|json` | Data sumber lengkap Manhattan, tidak sama dengan titik downsample untuk tampilan |
| GET | `/api/gwas/analyses/{id}/results/qq/export?format=csv|tsv|json` | Data sumber QQ plot |
| GET | `/api/gwas/analyses/{id}/pca/export?format=csv|tsv|json` | Koordinat PC per sampel dan metadata jumlah PC |
| GET | `/api/gwas/analyses/{id}/interpretation` | Status dan hasil interpretasi statistik AI |
| GET | `/api/gwas/analyses/{id}/interpretation/export?format=json` | Unduh file hasil interpretasi AI (JSON) |
| POST | `/api/gwas/analyses/{id}/interpretation/retry` | Jadwalkan ulang interpretasi yang gagal/tidak tersedia/stale sesuai batas percobaan |

PCA mengembalikan `requested_components`, `actual_components`, `status` (`complete`, `partial`, `unavailable`), `warnings`, serta variance explained bila eigenvalues tersedia. Backend tidak mengarang variance explained ketika output PLINK2 yang tersedia tidak memuat eigenvalues yang diperlukan.

AI adalah proses terpisah setelah hasil GWAS tersimpan. Status interpretasi (`queued`, `running`, `done`, `failed`, `unavailable`, `stale`) tidak mengubah status GWAS. Interpretasi hanya mencakup statistik; tidak melakukan gene annotation, prediksi risiko, atau klaim kausalitas.
