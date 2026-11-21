# Panduan Modul GWAS (GenoSphere-GWAS Backend)

Dokumen ini menjelaskan setiap berkas kode, apa yang terjadi saat dijalankan, cara mengujinya, dan dari bagian mana di SKPL/SDP/LKP setiap keputusan berasal. Bila dokumen bertentangan, LKP 6 dianggap paling berlaku, lalu LKP 5, LKP 4, SKPL, SDP, dan Proposal. Form wawancara yang diterima tidak berisi jawaban, sehingga tidak dipakai sebagai acuan.

## 1. Alur dari sudut pandang pengguna

Pengguna login, lalu mengunggah genotipe dan fenotipe (Step 1). Server memeriksa kelengkapan, format, dan kecocokan Sample ID (Step 2). Pengguna mengatur parameter dan mengonfirmasinya (Step 3 dan 4). Sejak titik ini server menjalankan **seluruh pipeline otomatis di latar belakang**: Quality Control, PCA bila dipilih, uji asosiasi, lalu menyimpan hasil ke database (Step 5 sampai 7), sesuai NFR-06 yang meminta tahapan berjalan tanpa intervensi manual. Frontend memantau lewat WebSocket atau polling, lalu menampilkan ringkasan QC dan scatter PCA begitu tahap itu selesai. Setelah analisis `done`, pengguna menyaring signifikansi (Step 8), melihat Manhattan, QQ, dan tabel SNP (Step 9), mengunduh CSV, dan menyimpan konfigurasi (Step 10).

## 2. Pemetaan Step UI ke endpoint

Semua endpoint berawalan `/api/gwas` dan memerlukan token login. Kesalahan selalu berbentuk `{"detail": {"code", "message", ...}}`.

| Step UI | Endpoint | Fungsi |
|---|---|---|
| 1 | `POST /upload-genotype`, `POST /upload-phenotype` | Unggah satu berkas per panggilan. Panggilan pertama tanpa `dataset_id` membuat dataset baru; panggilan berikutnya mengirim `dataset_id` yang sama. Field opsional: `organism`, `source_domain` |
| 2 | `POST /datasets/{id}/validate` | Laporan validasi: format, kelengkapan, jumlah sampel cocok, daftar trait |
| 3 | `GET /config/parameters`, `GET /config/presets`, `POST /config/check` | Bantuan parameter, preset Guided Mode, dan cek konfigurasi tanpa menjalankan |
| 4 | `POST /analyses` | Mengunci konfigurasi dan menjalankan pipeline (202, status `queued`) |
| 5 sampai 7 | `GET /analyses/{id}/status`, `/log`, `/qc-summary`, `/pca`, `WS /analyses/{id}/ws?token=` | Progres, log, ringkasan QC, titik PCA, aliran real-time |
| 8 | `POST /analyses/{id}/postprocess` | Ambang `genome_wide` (5e-8), `suggestive` (1e-5), `bonferroni` (0,05/N), atau `custom` |
| 9 | `GET /analyses/{id}/results/summary`, `/manhattan`, `/qq`, `/significant`, `/top` | Data grafik dan tabel |
| 10 | `GET /analyses/{id}/export`, `/config/export`, `GET /analyses` | CSV hasil, konfigurasi JSON, riwayat |
| lain | `POST /analyses/{id}/retry`, `GET /datasets`, `GET /datasets/{id}`, `GET /analyses/{id}` | Coba ulang yang gagal, daftar dataset, detail |

## 3. Fungsi tiap berkas dan apa yang terjadi saat dijalankan

**Berkas yang sudah ada dan tidak diubah logikanya.** `app/security.py` meng-hash kata sandi (Argon2id) dan membuat/memeriksa token JWT; dipanggil setiap login dan setiap request berotorisasi. `app/deps.py` berisi `get_current_user` (menolak request tanpa token valid) dan `ensure_owner` (membuat data milik orang lain terjawab 404); setiap endpoint GWAS memakainya, itulah penegakan BR-06. `app/routers/auth.py` melayani register, login, `/me`, serta lupa dan reset kata sandi. `app/mailer.py` mengirim email pemulihan lewat SMTP atau mencetak tautannya di log bila SMTP kosong. `app/database.py` membuka koneksi PostgreSQL. `sql/001` membuat tabel `users` dan `sql/002` menautkan `gwas_analysis.user_id` ke `users`; keduanya sudah Anda jalankan.

**Berkas yang diubah secara aditif.** `app/config.py` mendapat pengaturan GWAS (semua punya nilai bawaan, jadi `.env` lama tetap berjalan). `app/errors.py` mendapat pesan Indonesia untuk batas angka dan pilihan nilai, serta nama field bersarang seperti `qc.maf`. `app/models.py` mendapat lima kelas yang memetakan tabel ERD final (`accession`, `phenotype`, `gwas_analysis`, `snp`, `gwas_result`); kelas-kelas ini hanya "cermin" tabel yang sudah ada dan tidak membuat tabel baru di PostgreSQL. `app/main.py` mendapat satu fungsi `lifespan` yang saat server hidup menandai analisis yang sedang berjalan ketika server mati sebagai `failed`, supaya bisa di-retry.

**Berkas baru.**

`app/routers/gwas.py` adalah semua endpoint di atas. Dua endpoint upload lama dipertahankan dengan path yang sama. Perbaikannya: nama berkas dari klien tidak lagi dipakai (sebelumnya nama seperti `../../.env` dapat menulis di luar folder upload), dibatasi ukuran, ditolak bila bukan format BR-01, dan sekarang dicatat sebagai dataset. Respons upload pada source saat ini memuat `status`, `message`, `dataset_id`, `files`, `dataset_files`, kelengkapan, serta metadata batas resource. Field `saved_path` tidak diekspos. Setiap request mengirim satu file pada field `file`; format PLINK BED/BIM/FAM diunggah berurutan dengan `dataset_id` yang sama.

`app/services/storage.py` adalah satu-satunya tempat yang tahu lokasi berkas (`uploads/u{user_id}/ds_{dataset_id}/...`). Saat produksi pindah ke object storage, cukup berkas ini yang diganti.

`app/services/validation.py` memeriksa berkas tanpa PLINK: header `.bed`, ukuran `.bed` terhadap jumlah sampel dan varian, format `.bim`/`.fam`/VCF baris demi baris, duplikasi ID, kolom ID di fenotipe, dan deteksi tipe trait (biner 0/1 atau 1/2, selain itu kuantitatif). Setiap masalah berisi `code`, `message`, dan `hint`. Hasilnya disimpan di `validation.json`, dan metadata trait ditulis ke tabel `phenotype`.

`app/services/config_rules.py` berisi parameter yang boleh diatur, batas nilainya, preset empat domain, dan aturan bisnis: dataset harus tervalidasi (BR-02), model harus cocok dengan tipe trait (`auto` memilih sendiri), preset hanya untuk dataset dengan domain yang sama (BR-04), kovariat harus ada dan layak, validasi PC dan batas jumlah sampel (aturan historis; ketentuan terbaru: PC parsial didukung dan jumlah aktual ditentukan dari output PCA).

`app/services/tools.py` menjalankan PLINK 2 dan GEMMA sebagai proses terpisah dengan batas waktu, tanpa shell, dan keluarannya langsung ditulis ke log analisis. Kegagalan (termasuk kehabisan memori) menjadi `ToolError` berpesan jelas.

`app/services/gwas_engine.py` adalah mesin pipeline. Bila genotipe VCF, dikonversi dulu ke PLINK. Lalu QC (`--maf`, `--geno`, `--hwe`), PCA (LD pruning lalu `--pca`), uji asosiasi (`--glm` linear atau logistik, atau GEMMA `-lmm 4` untuk LMM), kemudian hasil diparse dan disimpan ke `snp` dan `gwas_result` per 5.000 varian. Varian yang sama (kromosom, posisi, REF, ALT) dipakai ulang antar-analisis.

`app/services/jobs.py` mengelola status `queued → running → done/failed` di database, `progress.json` (tahap, persen, estimasi sisa waktu), `analysis.log`, `params.json` dan `run_meta.json` (parameter dan versi tool, untuk reproduksibilitas NFR-07), serta batas analisis yang berjalan bersamaan. Kegagalan di dalam job tidak menjatuhkan server (NFR-08).

`app/services/results.py` mengubah isi database menjadi data Manhattan, QQ (beserta λ genomic inflation), tabel signifikan, dan CSV. Bila varian lebih banyak dari `MAX_PLOT_POINTS`, semua varian paling signifikan dipertahankan, sisanya disampling, dan respons memuat `filtered: true` beserta pesan untuk UI (UC-04 Alt-04).

`sql/003_gwas_module.sql` mengubah `gwas_result.p_value` dan `effect_size` dari `NUMERIC(15,10)` menjadi `DOUBLE PRECISION`, serta menambah indeks. **Wajib dijalankan sebelum analisis pertama.** Tipe lama hanya menyimpan 10 angka di belakang koma sehingga p-value di bawah 1e-10, yaitu hasil GWAS terkuat, tersimpan sebagai 0. SDP sendiri mendefinisikannya `FLOAT`. Tanpa 003, analisis berhenti dengan pesan yang menunjuk ke berkas ini, bukan menyimpan angka yang salah.

`tests/test_gwas.py` berisi 29 tes, termasuk menjalankan PLINK 2 dan GEMMA sungguhan pada data simulasi dengan satu SNP kausal. Tes yang butuh tool otomatis dilewati bila tool tidak terpasang. `sample_data/` berisi data simulasi siap unggah (120 sampel, 300 SNP; SNP kausal `rs101`) karena contoh di `uploads/user_1` hanya 4 sampel, di bawah minimum 10.

## 4. Menjalankan dan mengujinya

Salin berkas baru ke `~/backend` (jangan menimpa `.env`), lalu di WSL:

```bash
cd ~/backend && source .venv/bin/activate
psql -U postgres -h localhost -d gwas -f sql/003_gwas_module.sql
psql -U postgres -h localhost -d gwas -f sql/004_user_profile.sql
psql -U postgres -h localhost -d gwas -f sql/005_gwas_interpretation.sql
python -m pytest -q            # pada audit 2026-11: 189 passed, 53 skipped; lihat laporan verifikasi
uvicorn app.main:app --reload --port 8000
```

Di Swagger (`http://127.0.0.1:8000/docs`): login lewat tombol **Authorize**; untuk demo lokal, gunakan berkas simulasi kecil pada `sample_data/` (berkas ini bukan data biologis nyata); `POST /api/gwas/upload-genotype` dengan `sample_data/cohort_sim.vcf` dan isi `source_domain = kesehatan`; catat `dataset_id`; `POST /upload-phenotype` dengan `phenotype_sim.csv` dan `dataset_id` tersebut; `POST /datasets/{id}/validate`; lalu `POST /analyses` dengan isi berikut.

```json
{"dataset_id": 1, "trait_name": "Height", "trait_type": "quantitative", "model": "auto",
 "use_pca": true, "pca_components": 5, "covariates": ["Age", "Sex"]}
```

Pantau `GET /analyses/{id}/status` sampai `done`; `rs101` harus muncul sebagai varian terbaik di `/results/top`. Untuk trait biner ganti `trait_name` menjadi `Disease` dan `trait_type` menjadi `binary`. Untuk LMM, `model: "lmm"` pada trait kuantitatif.

## 5. Integrasi Next.js

Unggah memakai `FormData` dengan field `file` (dan `dataset_id`), bukan JSON. Login memakai `application/x-www-form-urlencoded` (`username`, `password`). Untuk Step 7:

```ts
const ws = new WebSocket(`ws://127.0.0.1:8000/api/gwas/analyses/${id}/ws?token=${token}`);
ws.onmessage = (e) => { const m = JSON.parse(e.data);   // {type:"log", lines:[...]} atau {type:"progress", percent, stage, state, ...}
};
```

Koneksi ditutup server saat analisis `done` atau `failed`. Kode tutup 4401 berarti token tidak valid dan 4404 berarti analisis bukan milik pengguna. Untuk Manhattan, gunakan `chromosome_max_position` dan `chromosome_order` untuk menghitung offset kumulatif sumbu X, dan `neg_log10_p` sebagai sumbu Y.

## 6. Keputusan dan asumsi yang perlu disetujui tim

1. **Satu baris `accession` = satu dataset yang diunggah.** ERD LKP 6 mewajibkan `gwas_analysis.accession_id`, tetapi tidak ada kolom sampel pada `phenotype`, sehingga fenotipe per sampel tidak bisa disimpan di database. Nilai fenotipe tetap di berkas; tabel `phenotype` menyimpan metadata per trait (nama, tipe, ringkasan).
2. **Pemilik dataset dicatat lewat awalan folder** (`u3/ds_12/...` di `accession.genotype_file_path`) karena ERD final tidak punya `accession.user_id`. Pemilik analisis memakai `gwas_analysis.user_id` dari migrasi 002. Menambah `accession.user_id` lebih rapi; itu perubahan ERD yang perlu persetujuan SA.
3. **Angka preset Guided Mode adalah asumsi** (dokumen tidak menyebut nilainya). Silakan ubah `PRESETS` di `config_rules.py`.
4. **Format genotipe**: VCF diterima dan dikonversi otomatis (UI LKP 6 menyebutnya; SDP menandainya dukungan tambahan). `.ped/.map` yang sebelumnya diterima kode lama sekarang ditolak karena engine tidak memprosesnya dan BR-01 hanya mendaftar VCF dan `.bed/.bim/.fam`.
5. **Kovariat**: kolom angka dipakai apa adanya; kolom teks dengan 2 sampai 10 kategori (mis. L/P) dikodekan otomatis menjadi kolom 0/1, sehingga "Jenis Kelamin" di UI dapat dipakai.
6. **`effect_size`** berisi BETA (linear, LMM) atau ln(OR) (logistik); respons menambah `odds_ratio` untuk logistik. Karena ERD tidak punya kolom SE dan p-value terkoreksi, `p_bonferroni` dihitung saat data dibaca.
7. **Mengubah jumlah PC setelah melihat hasil QC** berarti membuat analisis baru, karena pipeline berjalan otomatis dari awal sampai akhir.
8. **Job berjalan di dalam proses FastAPI** (cukup untuk MVP dan beban kelompok). Bila nanti perlu skala besar, ganti `jobs.py` dengan antrean terpisah seperti Celery/Redis tanpa mengubah router. Analisis yang sedang berjalan saat server mati ditandai `failed` dan dapat di-retry.
9. **CORS**: `main.py` Anda memakai `allow_origins=["*"]`, aman untuk pengembangan. Untuk produksi, ganti dengan daftar origin frontend. Folder `uploads/` sudah ditambahkan ke `.gitignore` agar data genom tidak ikut ke GitHub.


---

## Revisi 0.3.0 (historis) dan pembaruan source 2026-11

* Butir "mengubah jumlah PC setelah melihat hasil QC berarti membuat analisis baru" **masih berlaku untuk mode `auto`**. Pada mode `staged` PC dan kovariat ditetapkan di Step 6 setelah QC selesai (QC tidak diulang).
* Status analisis bertambah satu nilai pada mode staged: **`awaiting`** (tahap selesai, menunggu langkah berikutnya).
* Migrasi tambahan: **`sql/004_user_profile.sql`** saja (kolom profil; aman diulang). Skema analisis/hasil tetap sesuai ERD final LKP 6.
* Detail lengkap: `01_PERBANDINGAN_REVISI_DAN_KEPUTUSAN.md`, `02_PENJELASAN_KODE.md`, `03_INTEGRASI_FRONTEND.md`.


### Pembaruan source November 2026

Pilihan PCA hanya 5, 10, dan 20 PC. Jumlah PC aktual bisa lebih kecil dan dilaporkan sebagai `partial`; bila PCA tidak dapat dihasilkan, pipeline tidak berpura-pura bahwa koreksi struktur populasi berhasil. Laporan QC/Data Usage Summary, ekspor data sumber Manhattan/QQ/PCA, dan interpretasi statistik AI opsional tersedia melalui endpoint tambahan. Detail terbaru ada di `03_KONTRAK_API_UNTUK_FRONTEND.md` serta `REVISI_BACKEND_2026-11.md`.
