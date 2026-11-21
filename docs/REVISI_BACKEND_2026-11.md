# Laporan Audit dan Revisi Backend GenoSphere-GWAS

Tanggal audit: 14 November 2026  
Basis: arsip source code `GenoSphere.zip` yang diberikan pengguna.  
Strategi: revisi bertahap pada backend yang sudah ada; bukan pembangunan ulang.

## 1. Ringkasan hasil

Backend tetap menggunakan FastAPI, SQLAlchemy, PostgreSQL, autentikasi JWT, PLINK 2, dan GEMMA sesuai arsitektur awal. Struktur router, model data utama, mekanisme penyimpanan hasil, Guided/Manual Mode, serta alur analisis yang sudah ada dipertahankan.

Perubahan utama:

1. Pilihan PCA dibatasi ke 5, 10, dan 20 PC. Jumlah aktual dibaca dari output PCA; hasil dapat `complete`, `partial`, atau `unavailable`.
2. Fallback PCA Python membaca BED per varian dan membangun matriks Gram sampel × sampel. Estimasi memori diperiksa sebelum perhitungan.
3. Sample accounting memisahkan kategori sampel yang dikeluarkan agar tidak terhitung ganda. Validasi jumlah kasus/kontrol dilakukan setelah preprocessing.
4. QC mencatat missingness varian/sampel, MAF, HWE, jumlah sebelum/sesudah filter, persentase perubahan, dan jumlah yang dihapus menurut filter bila dapat diperoleh dari log PLINK2.
5. Data Usage Summary disimpan bersama metadata run dan disediakan melalui API serta ekspor JSON/CSV.
6. Batas upload mempertimbangkan ukuran per berkas, total dataset, disk kosong, dan cadangan disk. Preflight analisis memperkirakan kebutuhan RAM/disk serta concurrency berdasarkan kapasitas host/cgroup yang terdeteksi.
7. Data sumber Manhattan, QQ, dan PCA tersedia untuk diunduh terpisah dari titik grafik yang mungkin di-downsample. Ekspor tabel CSV/TSV/JSON dan XLSX dipertahankan; XLSX memakai write-only workbook, spooled file, streaming, dan batas baris.
8. Interpretasi statistik AI dibuat sebagai proses opsional setelah hasil GWAS tersimpan. Kegagalan AI tidak mengubah status hasil GWAS.
9. Respons error HTTP, validasi, dan dokumentasi OpenAPI diseragamkan.
10. Panduan API/frontend dan tes lama diselaraskan dengan kontrak aktual, termasuk upload satu berkas per request.

## 2. PCA dan konsistensi kovariat

- Nilai yang diterima API dan dropdown: `5`, `10`, `20`. Nilai lain, termasuk `2` dan `3`, ditolak.
- Default mengikuti konfigurasi awal proyek, yaitu 5 PC.
- `requested_components` menyimpan permintaan pengguna; `actual_components` berasal dari header/output eigenvector yang benar-benar dihasilkan.
- Status PCA: `complete`, `partial`, atau `unavailable`.
- Warning menjelaskan mengapa jumlah aktual kurang dari permintaan.
- Variance explained dan cumulative variance explained dimasukkan jika eigenvalues tersedia dan dapat dihitung.
- Jika PCA diminta tetapi tidak ada komponen yang berhasil dihasilkan, pipeline tidak melanjutkan analisis seolah-olah koreksi struktur populasi tersedia.
- PLINK2 tetap metode utama. Fallback Python diperiksa terhadap anggaran memori dan membaca BED secara bertahap.
- PC yang benar-benar dihasilkan tetap ditambahkan ke kovariat model asosiasi bersama kovariat fenotipe yang dipilih. Tidak ada asumsi bahwa jumlah PC aktual selalu sama dengan jumlah permintaan.

## 3. Data Usage Summary dan laporan QC

### Data Usage Summary

Ringkasan run mencatat:

- Nama berkas asli, format, ukuran tiap berkas, dan total ukuran dataset.
- Jumlah sampel/varian genotipe, sampel fenotipe, pasangan yang cocok, dan yang tidak cocok menurut laporan validasi.
- Sample accounting preprocessing: tidak memiliki pasangan genotipe, missing trait, missing covariates, retained, serta kasus/kontrol setelah filtering untuk model logistik.
- Jumlah sampel/varian sebelum dan setelah QC, jumlah/persentase varian yang dihapus, statistik QC, parameter filter, dan penghapusan yang dapat diatribusikan ke filter.
- Jumlah PC yang diminta/aktual, status PCA, jumlah sampel PCA, jumlah SNP diuji, hasil yang disimpan, dan SNP signifikan.
- Model, jumlah sampel model, versi perangkat lunak, waktu mulai/selesai, durasi, dan daftar output.

Kategori preprocessing dibuat eksklusif dalam urutan pemeriksaan pipeline, sehingga satu baris phenotype tidak masuk ke lebih dari satu kategori pengeluaran.

### QC

Statistik missingness, frekuensi alel, dan HWE dibaca dari output PLINK2. Perintah pelaporan `--hardy` ditambahkan tanpa mengubah aturan filter atau threshold yang sudah ada. Ringkasan HWE mencakup laporan autosom dan kromosom X bila output terkait tersedia. Statistik yang tidak dihasilkan PLINK2 ditandai tidak tersedia, bukan diisi dengan angka perkiraan.

Jumlah varian/sampel yang dikeluarkan berdasarkan filter diparsing dari log PLINK2; selisih yang tidak dapat diatribusikan disimpan sebagai `unattributed_variant_removal` agar laporan tidak mengarang penyebab.

## 4. Adaptive upload dan resource preflight

Konfigurasi yang dapat diubah administrator tersedia di `.env.example` dan `app/config.py`, meliputi:

- `MAX_UPLOAD_MB`: batas satu berkas.
- `MAX_DATASET_UPLOAD_MB`: kuota total berkas dalam satu dataset.
- `UPLOAD_DISK_RESERVE_MB`: cadangan disk untuk upload.
- `ANALYSIS_MEMORY_RESERVE_MB`, `ANALYSIS_MEMORY_PER_JOB_MB`, `ANALYSIS_DISK_RESERVE_MB`.
- `PCA_FALLBACK_MEMORY_LIMIT_MB`, `PCA_FALLBACK_MEMORY_FRACTION`.
- `MAX_CONCURRENT_JOBS`.

Pemeriksaan resource mencoba membaca RAM/CPU/disk dari host dan cgroup. Jika kapasitas tidak terdeteksi, backend menggunakan batas konfigurasi konservatif dan memberi warning. Upload ditulis streaming per potongan ke file sementara, lalu dipindah secara atomik setelah lolos batas.

Preflight GWAS mempertimbangkan ukuran dataset, sampel, varian, kebutuhan PCA, estimasi RAM/disk, serta jumlah pekerjaan aktif. Estimasi adalah heuristik admission control, bukan jaminan kebutuhan pasti PLINK2/GEMMA.

Concurrency analisis GWAS memakai semaphore lokal dan PostgreSQL advisory-lock slots untuk membatasi job lintas worker ketika backend berjalan pada PostgreSQL. Pada database SQLite/test, hanya semaphore lokal yang tersedia.

## 5. PLINK2, GEMMA, dan ekspor

- PLINK2 tetap digunakan untuk QC, LD pruning/PCA, regresi linear/logistik, dan statistik yang diminta pipeline.
- Perintah dan perilaku model GEMMA, opsi LMM, kinship, parser, serta pemilihan hasil dipertahankan. Tidak ada perubahan metode/model GEMMA.
- Penambahan `--hardy` adalah pelaporan statistik QC, bukan perubahan threshold HWE.
- Data sumber Manhattan/QQ diekspor lengkap dan diberi metadata `complete_source` serta `downsampled=false`. Data untuk tampilan grafik tetap dapat dibatasi `MAX_PLOT_POINTS`.
- Ekspor PCA memuat koordinat PC yang dihasilkan serta metadata jumlah PC dan variance explained bila tersedia.
- XLSX menggunakan OpenPyXL write-only, spooled temporary file (beralih ke disk setelah 8 MiB), streaming response, dan batas `XLSX_MAX_ROWS` sebesar 1.000.000 baris.
- File PNG/SVG grafik tidak dirender backend. Frontend tetap bertanggung jawab menyimpan hasil render visual.

## 6. AI Interpretation Engine

Komponen terpisah berada di:

- `app/services/ai_interpretation.py`: payload builder, adapter provider, dan validator respons.
- `app/services/ai_jobs.py`: antrean status, eksekusi worker, retry terbatas, dan recovery saat startup.
- `app/models.py`: tabel `GwasInterpretation`.
- `sql/005_gwas_interpretation.sql`: migrasi tabel interpretasi.

Sistem hanya mengirim ringkasan statistik, diagnostik QC/PCA/QQ, metadata model/software, dan Top SNP sesuai konfigurasi (default 50). Data genotipe/fenotipe mentah, sample ID, koordinat PCA per individu, dan API key tidak dimasukkan ke payload.

Respons AI harus sesuai schema JSON. Validator memeriksa SNP, evidence references, jumlah SNP signifikan, dan statistik Top SNP terhadap payload sumber. Klaim fungsi biologis gen, gene annotation, kausalitas, prediksi risiko penyakit, dan klaim klinis berada di luar cakupan dan ditolak.

Status interpretasi terpisah: `queued`, `running`, `done`, `failed`, `unavailable`, `stale`. Status GWAS tetap `done` bila AI tidak dikonfigurasi atau gagal. Provider yang saat ini diimplementasikan adalah `openai_compatible`; adapter lain dapat ditambahkan tanpa mengubah payload builder/worker inti.

**Batas deployment AI:** antrean/status disimpan di database dan job yang terputus dicoba dipulihkan saat startup, tetapi eksekusi worker memakai `ThreadPoolExecutor` per proses FastAPI. `AI_MAX_CONCURRENT_JOBS` adalah batas per proses, bukan batas cluster-wide untuk beberapa worker/server. Deployment multi-worker yang membutuhkan batas global AI harus memakai worker tunggal atau menambahkan distributed worker/lock khusus AI.

## 7. Endpoint tambahan

Semua endpoint berikut memeriksa kepemilikan analisis:

| Method | Endpoint | Fungsi |
|---|---|---|
| GET | `/api/gwas/analyses/{analysis_id}/data-usage` | Data Usage Summary |
| GET | `/api/gwas/analyses/{analysis_id}/data-usage/export?format=json\|csv` | Unduh Data Usage Summary |
| GET | `/api/gwas/analyses/{analysis_id}/qc-summary/export?format=json\|csv` | Unduh laporan QC |
| GET | `/api/gwas/analyses/{analysis_id}/results/manhattan/export?format=csv\|tsv\|json` | Unduh data sumber Manhattan lengkap |
| GET | `/api/gwas/analyses/{analysis_id}/results/qq/export?format=csv\|tsv\|json` | Unduh data sumber QQ lengkap termasuk diagnostik lambda GC pada metadata |
| GET | `/api/gwas/analyses/{analysis_id}/pca/export?format=csv\|tsv\|json` | Unduh koordinat dan metadata PCA |
| GET | `/api/gwas/analyses/{analysis_id}/interpretation` | Status/hasil interpretasi statistik AI |
| GET | `/api/gwas/analyses/{analysis_id}/interpretation/export?format=json` | Unduh hasil interpretasi AI sebagai JSON |
| POST | `/api/gwas/analyses/{analysis_id}/interpretation/retry` | Retry interpretasi sesuai status dan batas percobaan |

Kontrak upload yang dipertahankan adalah satu field multipart `file` per request. Dataset PLINK BED/BIM/FAM diunggah sebagai tiga request dengan `dataset_id` sama.

## 8. Database dan migrasi

- Model ORM `GwasInterpretation` ditambahkan dengan unique relation ke analisis dan indeks status antrean.
- Kolom JSON memakai `JSONB` pada PostgreSQL dan varian `JSON` pada SQLite test.
- `sql/schema.sql` diperbarui untuk instalasi baru.
- Untuk database existing yang sudah menerapkan skema GWAS dan profil pengguna sampai migrasi 004, jalankan sekali:

```bash
psql -U postgres -d gwas -f sql/005_gwas_interpretation.sql
```

Jangan menjalankan semua file migrasi secara acak pada database produksi. Periksa terlebih dahulu migrasi yang sudah diterapkan, terutama file migrasi bernomor 003 yang merupakan dua skrip berbeda pada source awal.

## 9. Instalasi dan menjalankan

Contoh untuk lingkungan WSL/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Isi POSTGRES_PASSWORD dan SECRET_KEY pada .env.
# SECRET_KEY minimal 32 karakter; buat nilai baru dengan: openssl rand -hex 32
```

Untuk database baru, buat database PostgreSQL terlebih dahulu lalu gunakan skema final:

```bash
createdb -U postgres gwas
psql -U postgres -d gwas -f sql/schema.sql
```

Untuk database lama, terapkan hanya migrasi yang belum ada. Jika migrasi 001–004 sudah diterapkan, jalankan migrasi 005 seperti bagian sebelumnya.

Jalankan backend:

```bash
uvicorn app.main:app --reload --port 8000
```

Swagger tersedia pada `http://localhost:8000/docs`.

### Konfigurasi alat eksternal dan AI

- Atur `PLINK2_BIN` dan `GEMMA_BIN` ke nama executable atau path binary yang terpasang.
- Isi variabel `AI_PROVIDER`, `AI_API_BASE_URL`, `AI_MODEL`, dan `AI_API_KEY` pada environment server bila fitur AI akan diaktifkan.
- Tanpa provider/API key AI, analisis GWAS tetap berjalan dan interpretasi berstatus `unavailable`.
- Jangan mengirim `.env` ke frontend, Git, atau arsip source.

Jalankan tes dengan:

```bash
pytest -q
```

Tes PostgreSQL integrasi memerlukan `TEST_POSTGRES_URL` yang menunjuk database test terpisah. Jangan arahkan tes ke database produksi.

## 10. Hasil verifikasi pada lingkungan audit

Tanggal eksekusi: 14 November 2026.

| Pemeriksaan | Hasil |
|---|---|
| `python -m compileall -q app tests` | Berhasil |
| Automated test suite | **190 passed, 53 skipped, 0 failed** |
| OpenAPI | Endpoint tambahan terdaftar dan respons file memiliki schema binary |
| ORM type compile | `JSONB` untuk PostgreSQL, `JSON` untuk SQLite |
| Regression test fitur revisi | Lulus, termasuk PCA parsial, HWE metrics, upload quota, dan validasi AI |
| Ekspor XLSX | Test unduh dan pembacaan workbook lulus |
| Ekspor AI JSON | Test unduh dan pembatasan kepemilikan lulus |

53 tes dilewati karena executable PLINK2/GEMMA tidak tersedia atau `TEST_POSTGRES_URL` tidak diisi. Provider AI eksternal juga tidak dikonfigurasi, sehingga tidak ada klaim pengujian end-to-end terhadap layanan AI, PLINK2, GEMMA, atau PostgreSQL live.

## 11. Batasan dan tindak lanjut deployment

1. Instalasi PLINK2/GEMMA dan uji seluruh pipeline dengan executable pada server target masih harus dilakukan. Tes yang mensyaratkan binary tersebut terdeteksi sebagai skipped di lingkungan audit ini.
2. PostgreSQL live belum dihubungkan pada sesi audit. Jalankan migrasi 005 pada database staging, kemudian jalankan tes dengan `TEST_POSTGRES_URL` sebelum rilis.
3. Provider AI eksternal belum dipanggil karena kredensial/provider tidak disediakan. Uji timeout, biaya usage, dan respons provider pada staging dengan API key yang disimpan di environment.
4. Adaptive resource check menggunakan estimasi konservatif, bukan jaminan penggunaan RAM/disk PLINK2/GEMMA pada semua dataset.
5. `AI_MAX_CONCURRENT_JOBS` berlaku per proses FastAPI. Untuk beberapa worker/server dengan kebutuhan batas AI global, gunakan worker terpusat atau distributed lock khusus AI.
6. File PNG/SVG grafik harus diekspor dari frontend setelah grafik dirender. Backend hanya menyediakan data sumbernya.
7. Perubahan API dan dokumentasi frontend sudah disiapkan, tetapi source code frontend tidak termasuk dalam arsip backend yang diaudit.
8. `requirements.txt` tidak memerlukan dependency baru: `httpx` dan `openpyxl` sudah tercantum pada requirements proyek.

## 12. File utama yang berubah

**Backend dan skema:**

- `app/config.py`, `app/main.py`, `app/models.py`.
- `app/routers/gwas.py`, `app/routers/analysis.py`, `app/routers/qc.py`.
- `app/schemas_gwas.py`, `app/schemas/analysis.py`, `app/schemas/qc.py`.
- `app/services/config_rules.py`, `app/services/gwas_engine.py`, `app/services/jobs.py`, `app/services/results.py`, `app/services/storage.py`.
- Baru: `app/services/resource_policy.py`, `app/services/ai_interpretation.py`, `app/services/ai_jobs.py`.
- `sql/schema.sql`, `sql/005_gwas_interpretation.sql`, `.env.example`.

**Tests dan dokumentasi:**

- Regression/integration tests diperbarui agar mengikuti kontrak upload dan PCA terbaru; ditambah `tests/test_revision_features.py`.
- README, panduan modul, penjelasan kode, kontrak API, dan panduan integrasi frontend diperbarui.

Arsip distribusi menyertakan `sample_data/` hanya sebagai dataset simulasi kecil untuk uji coba lokal. Dataset tersebut bukan data biologis nyata. File environment rahasia, virtual environment, cache, bytecode, dan repository Git tidak disertakan.
