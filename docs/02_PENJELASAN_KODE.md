# 02. Penjelasan kode yang direvisi/ditambah (basis v0.3.0 dan revisi 2026-11)

Format: **apa → fungsinya → mengapa**. Tag asal: **[A]** = revisi saya sebelumnya, **[B]** = revisi lain yang diadopsi. Berkas yang tidak disebut pada bagian historis tidak otomatis berarti tidak berubah pada revisi 2026-11; lihat bagian pembaruan dan laporan audit terbaru di `REVISI_BACKEND_2026-11.md`.

## A. Konfigurasi dan aplikasi
* **`config.py`** [A+B]: `cors_origins` berisi `localhost:3000` dan `127.0.0.1:3000`; `cors_origin_regex` mengizinkan `localhost` di port mana pun (Next.js pindah ke 3001 bila 3000 terpakai; kosongkan di produksi); `postgres_db` default `gwas` [B]; `app_version` mengikuti nilai konfigurasi proyek; revisi ini tidak mengubah versi aplikasi semata-mata untuk dokumentasi.
* **`main.py`** [A+B]: CORS memakai pengaturan, bukan `"*"`; `PATCH` diizinkan. `/health` kini membandingkan kolom model dengan kolom nyata database dan menjawab `schema: "needs_migration"` + daftar kolom bila `004_user_profile.sql` belum dijalankan, supaya kolom yang belum ada tidak baru ketahuan sebagai error 500.
* **`errors.py`** [A]: label `fullName`, `confirmPassword`, `institution`, `bio` agar pesan validasi Indonesia tetap benar untuk input camelCase.

## B. Akun dan profil
* **`schemas.py`** [A+B]: alias camelCase (`AliasChoices`) pada `full_name` dan `confirm_password`; `institution`, `bio` pada register; `ProfileUpdate` (hanya field terkirim yang berubah); `UserOut` memuat `institution`, `bio`; **`LoginJSON`** [B] (`identifier` / `email` / `username` + `password`).
* **`models.py`** [A+B]: `users.institution` (VARCHAR 150) dan `users.bio` (TEXT). **Hanya ini** perubahan skema; tabel analisis/hasil sesuai ERD final LKP 6.
* **`routers/auth.py`**: `_authenticate` [B] adalah inti login (cari email/username, tolak password panjang sebelum hashing, `burn_verify_time` untuk user tak dikenal, cek aktif) yang dipakai bersama `POST /login` (form OAuth2) dan **`POST /login/json`** [B]; perilakunya identik dengan kode lama. `_suggest_username` [A] membuat username unik dari email bila kosong (`akmal.f@ipb.ac.id` → `akmal.f`, `akmal.f2`, ...) karena form Register tidak memintanya padahal Profil menampilkan `@username`. `PATCH /me` mengubah nama/institusi/bio saja (email, username, sandi sengaja tidak lewat jalur ini; Profil di frontend berstatus read-only).
* **`sql/004_user_profile.sql`** [B]: dua `ADD COLUMN IF NOT EXISTS` (aman diulang).

## C. Upload dan penyimpanan (Step 1)
* **`services/storage.py`** [A]: `.pheno` didukung (juga `validation.py` [A+B]); `.gz` polos dipetakan ke `.vcf.gz` (isi tetap divalidasi di Step 2); `record_file`/`list_files` menyimpan nama asli berkas di `files.json` (hanya teks tampilan, tidak pernah jadi path); `SLOT_RE` whitelist nama berkas yang boleh dihapus; `remove_slot`, `remove_dataset_dir` (dengan pengaman agar folder induk tak terhapus).
* **`routers/gwas.py`**: endpoint upload menerima satu berkas pada field `file` per request. Fungsi internal `_upload_many` mempertahankan jalur bersama untuk validasi/persistensi; tiga berkas PLINK diunggah berurutan menggunakan `dataset_id` yang sama. Respons mengembalikan `files[]`, `ready_for_validation`, dan `missing[]`. Form `name` = nama proyek di header UI. `DELETE /datasets/{id}/files/{slot}` (tombol ✕; ditolak 409 bila dataset sudah dipakai analisis) dan `DELETE /datasets/{id}` (hapus dataset+analisis+hasil+folder; ditolak bila ada analisis berjalan). `dataset_id` kini `int` (path non-angka → 422).

## D. Validasi dan konfigurasi (Step 2-4)
* **`services/validation.py`** [A]: `phenotype.size_bytes`, `matching.matched_percent`, `unmatched_total`, `summary_message` untuk kartu/banner Step 2.
* **`services/ui_adapter.py`** [A, baru]: menerjemahkan label dropdown ke nilai API (`"Biner (Case / Control)"`→`binary`, `"Linear Mixed Model (LMM)"`→`lmm`, `"MAF >= 0.05, Call Rate >= 0.95"`→`{maf, call_rate}`, `"Sertakan Hasil PCA (5 PC)"`→`use_pca=True, pca_components=5`, `"5e-8 (Bonferroni)"`→`genome_wide`, `"0.05 (FDR)"`→`fdr`, `"Excel (.xlsx)"`→`xlsx`) dan padanan kovariat (`"Usia"`→kolom `Age`, `"Jenis Kelamin"`→`Sex`). Nilai tak dikenal dikembalikan apa adanya agar Pydantic menolaknya dengan pesan jelas. Juga `estimate_runtime` (perkiraan kasar durasi).
* **`services/config_rules.py`**: `AnalysisConfig` + `analysis_goal`, `execution_mode` (`auto|staged`), validator *before* yang memanggil `ui_adapter` [A]; Guided Mode memakai `source_domain` dataset bila `domain` tak dikirim (aturan domain-harus-cocok tetap) [A]; kovariat label UI dipetakan ke kolom nyata sebelum divalidasi [A]; **`FRONTEND_OPTIONS`** [B, ditambah opsi Excel] untuk `GET /config/options`.
* **`routers/gwas.py`**: `GET /config/options` [B]; `POST /config/check` menambah `estimate` {seconds, label} [A].

## E. Eksekusi bertahap (Step 4-7) [A]
* **`services/gwas_engine.py`**: `run_pipeline` dipecah menjadi `stage_qc` (VCF→PLINK, tabel fenotipe, QC), `stage_pca`, `stage_gwas`; default tetap berurutan sehingga **mode `auto` identik dengan perilaku lama** (hasil `rs101` teratas tetap). Tahap PCA/GWAS memuat ulang tabel fenotipe karena kovariat bisa berubah di Step 6, dan `--keep keep.txt` memastikan sampel yang sama dipakai di PCA/GLM/GEMMA.
* **`services/jobs.py`**: `run_analysis_job(id, plan)` dengan `plan` ∈ `all|qc|pca|gwas`. Pada mode staged, selesai `qc`/`pca` status menjadi **`awaiting`** (progres 35%/50%, `next_step` = `pca`/`gwas`); hanya tahap pertama membersihkan keluaran lama; `retry_plan` mencatat tahap yang gagal agar "coba ulang" hanya mengulang tahap itu; `awaiting` tidak ditandai gagal saat server restart.
* **`routers/gwas.py`**: `POST /analyses` (+`execution_mode`); **`POST /analyses/{id}/pca`** (Step 6: `"5 PC"`, `"Usia, Jenis Kelamin"`; `use_pca=false` tanpa job) dan **`POST /analyses/{id}/run`** (Step 7: `"Logistic Regression"`); `_require_stage` (`NOT_STAGED`/`WRONG_STAGE`) dan `_revalidate` (validasi ulang + simpan ke `params.json`); status memuat `next_step`, `can_continue`, `execution_mode`; WebSocket menutup di `awaiting`; `GET …/pca` memuat `eigenvalues`; daftar analisis memuat `finished_at`.

## F. Hasil, ambang, ekspor (Step 8-10)
* **`services/results.py`**: `_row` + alias `chr/pos/snp/pvalue` (sama dengan tipe `gwasResults` di store) [A]; `manhattan` + `pvalue`, `x` kumulatif, `chromosome_ticks`, `thresholds`, dan `format=rows` [A]; **`fdr_threshold`** (Benjamini-Hochberg dengan `row_number()` di database) [A≈B; kedua versi sama]; ekspor `export_text_lines` (CSV/TSV, streaming), `export_json_chunks`, `export_xlsx_chunks` (openpyxl *write-only* + spooled file, dialirkan per potongan, maksimum 1.000.000 baris) [A].
* **`routers/gwas.py`**: `PostprocessRequest` menerima `fdr` dan label Step 8; `export?format=csv|tsv|json|xlsx` (label UI diterima; format lain → 400).

## G. Tes
* `test_frontend_contract.py` [A, 37 tes]: meniru frontend, termasuk alur staged dengan PLINK asli.
* `test_fe_integration.py` [B, diadaptasi, 5 tes]: login JSON, opsi dropdown, FDR dengan data sintetis yang nilai Benjamini-Hochberg-nya dihitung tangan (menangkap salah hitung yang tidak terlihat di data simulasi), ekspor.
* `test_postgres_schema.py`: diperbaiki (lihat dokumen 01); kini menguji jalur database tim dan toleransi terhadap kolom ekstra varian `003_gwas_schema_alignment.sql`.
* `test_auth.py`, `test_gwas.py`: masing-masing satu asersi disesuaikan (username otomatis; Guided Mode memakai domain dataset).


## H. Revisi November 2026

* **`services/resource_policy.py`**: mendeteksi CPU, RAM/cgroup dan disk, menghitung batas per berkas serta sisa kuota dataset, memperkirakan kebutuhan preflight, menentukan concurrency analisis, dan membatasi memori PCA fallback. Angka perkiraan bukan jaminan kebutuhan aktual tool eksternal.
* **`services/gwas_engine.py`**: PCA utama tetap PLINK2; bila output aktual lebih sedikit daripada permintaan, respons menyatakan `partial`. Fallback Python membaca PLINK BED per varian dan menggunakan matriks Gram sampel×sampel dengan batas memori. QC menyimpan statistik hanya bila tersedia dari keluaran PLINK2.
* **`services/jobs.py`**: mencatat data usage pada tahap pipeline dan mengunci concurrency global dengan PostgreSQL advisory lock ketika PostgreSQL dipakai. Semaphor proses tetap menjadi perlindungan lokal, bukan pengganti lock lintas worker.
* **`services/ai_interpretation.py`** dan **`services/ai_jobs.py`**: payload statistik terde-identifikasi, adapter OpenAI-compatible, schema validator, antrean terpisah, retry terbatas, serta status `queued/running/done/failed/unavailable/stale`. Kegagalan AI tidak mengubah status GWAS.
* **`models.py`**, **`sql/005_gwas_interpretation.sql`**, dan **`sql/schema.sql`**: penyimpanan interpretasi per analisis.
* **`routers/gwas.py`**: endpoint Data Usage Summary, ekspor QC/Manhattan/QQ/PCA, interpretasi dan retry, serta resource preflight sebelum enqueue.
