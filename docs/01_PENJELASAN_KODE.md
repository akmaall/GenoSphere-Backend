# Penjelasan kode

Dokumen ini menjelaskan setiap berkas kode: untuk apa dibuat, apa isi pentingnya, dan apa yang terjadi saat dijalankan. Urutannya dari yang paling dasar sampai yang paling dekat ke pengguna. Di dalam kodenya sendiri, setiap fungsi dan kelas juga punya penjelasan singkat dalam bahasa yang sama.

Istilah yang dipakai di dokumen proyek (SKPL, SDP, LKP) ditulis di komentar kode, misalnya FR-07 untuk kebutuhan fungsional nomor 7, BR-02 untuk aturan bisnis nomor 2, dan UC-03 untuk use case nomor 3, supaya mudah ditelusuri ke dokumennya.

## Gambaran besar

Saat pengguna menekan tombol di layar, permintaan masuk ke `main.py`, diteruskan ke router (`routers/auth.py` atau `routers/gwas.py`), lalu router memanggil modul di `services/` yang mengerjakan pekerjaan sebenarnya. Hasilnya dikirim kembali dalam bentuk yang sudah ditentukan di `schemas.py` atau `schemas_gwas.py`. Data disimpan di PostgreSQL (lewat `models.py`) dan di folder penyimpanan (lewat `services/storage.py`).

Analisis GWAS yang lama tidak dikerjakan di dalam permintaan. Router hanya mencatat analisis dan menyuruh `services/jobs.py` mengerjakannya di latar belakang, sehingga pengguna langsung mendapat jawaban dan bisa memantau progresnya.

## app/config.py

Tempat semua pengaturan. Kelas `Settings` membaca nilai dari variabel lingkungan atau berkas `.env`, misalnya alamat database, kunci rahasia, lokasi PLINK 2, dan batas ukuran unggahan. `SECRET_KEY` wajib ada dan minimal 32 karakter, kalau tidak aplikasi menolak menyala, supaya tidak ada yang menjalankan server dengan kunci lemah. Properti `sqlalchemy_url` menyusun alamat database dengan aman, termasuk bila kata sandi berisi karakter khusus. `APP_VERSION` dan `PIPELINE_VERSION` adalah nomor versi yang dicatat pada setiap analisis agar hasilnya bisa ditelusuri.

## app/database.py

Membuat koneksi ke database dan menyediakan `get_db`, yaitu satu sesi database untuk satu permintaan yang ditutup otomatis setelah permintaan selesai. `Base` adalah kelas dasar semua tabel.

## app/models.py

Pemetaan enam tabel ke kelas Python. Lima tabel mengikuti ERD tim (LKP 9): `Accession` (dataset), `Phenotype` (ringkasan trait), `Snp` (daftar varian), `GwasAnalysis` (sesi analisis), dan `GwasResult` (hasil per varian). Tabel keenam, `User`, menyimpan akun. Satu catatan: ERD tidak punya kolom pemilik pada tabel dataset, jadi pemilik dicatat pada awalan lokasi berkas (`u3/ds_12/genotype` artinya dataset 12 milik pengguna 3), dan kepemilikan selalu diperiksa sebelum data dibuka (BR-06). Kolom `p_value` dan `effect_size` bertipe angka desimal presisi ganda supaya p-value sekecil 1e-30 tidak dibulatkan menjadi nol.

## app/errors.py

Satu format kesalahan untuk seluruh API: `{"detail": {"code": ..., "message": ..., "errors": [...]}}`. `code` dibaca program frontend, `message` boleh langsung tampil ke pengguna. Ada tiga penangan: kesalahan isian (422), semua `HTTPException` termasuk alamat tidak ditemukan (404) dan metode salah (405), dan kesalahan tak terduga (500, rincian teknis hanya masuk log server). `APIError` adalah cara kita melempar kesalahan terstruktur dari kode sendiri. Pesan 422 diterjemahkan ke kalimat Indonesia, dan nilai yang dikirim pengguna (misalnya kata sandi) tidak dipantulkan kembali di respons.

## app/security.py dan app/deps.py

`security.py` mengurus kata sandi dan token. Kata sandi disimpan sebagai hash argon2, tidak pernah apa adanya. Token login adalah JWT berumur sesuai `ACCESS_TOKEN_EXPIRE_MINUTES`. Token pemulihan kata sandi berjenis lain dan memuat sidik jari hash kata sandi, jadi begitu kata sandi berganti, token lama otomatis tidak berlaku. `burn_verify_time` membuat waktu respons login sama baik akunnya ada maupun tidak, supaya orang tidak bisa menebak email mana yang terdaftar.

`deps.py` berisi `get_current_user`, penjaga endpoint: membaca header `Authorization: Bearer <token>`, memeriksa tokennya, dan mengembalikan akun pemiliknya. Tanpa token yang sah jawabannya 401 dengan kode `TOKEN_MISSING`, `TOKEN_INVALID`, atau `TOKEN_EXPIRED`. `ensure_owner` menegakkan aturan bahwa data milik orang lain dijawab 404 (bukan 403) supaya keberadaannya tidak terbongkar.

## app/mailer.py

Mengirim email tautan pemulihan kata sandi. Bila `SMTP_HOST` kosong (saat pengembangan), tautannya dicetak di log server. Dijalankan sebagai tugas latar belakang, jadi kegagalan kirim email tidak mengubah jawaban yang sudah diterima pengguna.

## app/schemas.py dan app/schemas_gwas.py

Keduanya menentukan bentuk data. `schemas.py` untuk modul akun. `schemas_gwas.py` untuk modul GWAS: setiap endpoint punya kelas respons, dan setiap kolom punya keterangan artinya. Inilah yang membuat Swagger menampilkan isi jawaban yang sebenarnya, tidak hanya tulisan "string". Kelas `PcaRequest`, `CovariatesRequest`, `RunRequest`, dan `PostprocessRequest` juga menerjemahkan isi dropdown frontend (misalnya "10 PC" atau "0.05 (FDR)") menjadi nilai yang dipahami backend.

## app/main.py

Merakit aplikasi: memasang CORS supaya frontend di alamat lain boleh memanggil, memasang tiga penangan kesalahan, memasang router, dan menyediakan `/health`. Fungsi `custom_openapi` mengganti dokumentasi 422 bawaan FastAPI dengan bentuk yang sebenarnya dikirim server, sehingga dokumentasi dan kenyataan sama. Saat server menyala, `recover_interrupted_jobs` menandai analisis yang terputus (server sempat mati) sebagai gagal supaya bisa diulang.

## app/routers/auth.py

Endpoint akun. `register` membuat akun (email disimpan huruf kecil, username dibuat dari email bila kosong). `login` adalah satu-satunya alamat login dan menerima JSON maupun form, jadi frontend tidak bisa salah memilih alamat. Alamat lama `/login/json` masih hidup tetapi disembunyikan dari Swagger. Tanggapan login berisi `access_token` yang harus disimpan frontend lalu dikirim pada setiap permintaan berikutnya. `me` memeriksa token sekaligus mengambil data akun. `forgot-password` selalu menjawab sama, baik email terdaftar maupun tidak. `reset-password` mengganti kata sandi dari tautan.

## app/routers/gwas.py

Semua endpoint GWAS, disusun mengikuti 10 langkah di antarmuka. Beberapa fungsi bantu di bagian atas (`_get_dataset`, `_get_analysis`) mengambil data sambil memastikan milik pengguna yang login. Bagian-bagiannya:

Unggah. Router menerima satu berkas per request pada field `file`. Fungsi internal `_upload_many` tetap memakai struktur list untuk validasi/persistensi bersama, tetapi endpoint tidak mengekspos upload multi-file dalam satu request. BED/BIM/FAM dikirim berurutan dengan `dataset_id` yang sama. Berkas diperiksa sebelum disimpan. Nama berkas dari pengguna tidak pernah dipakai sebagai nama di disk. Berkas disimpan dengan nama tetap (misalnya `genotype.bed`), nama aslinya hanya dicatat untuk ditampilkan.

Dataset dan validasi. Status dataset dihitung dari isi folder pada saat diminta, jadi menghapus berkas langsung mengubah status. Hapus dataset memakai dua langkah: panggilan pertama dijawab 409 beserta daftar yang akan terhapus, panggilan kedua dengan `confirm=true` baru menghapus.

Konfigurasi. `/config/check` memeriksa pengaturan tanpa menjalankan apa pun. `/analyses` memeriksa lalu menjalankan.

Mode bertahap. `/pca`, `/covariates`, dan `/run` melanjutkan analisis setelah QC selesai. PCA dan kovariat sengaja dipisah.

Pemantauan. `/status` ringan untuk polling, `/{id}` detail lengkap, `/log` dibaca bertahap dengan `offset` dan `limit`, `/retry` hanya untuk analisis berstatus `failed`, dan WebSocket `/ws` mengalirkan log dan progres langsung.

Hasil dan ekspor. Semua endpoint hasil menolak analisis yang belum selesai (BR-05). Ekspor mengirim berkas sungguhan dengan `Content-Type` yang sesuai.

## app/services/validation.py

Memeriksa berkas genotipe dan fenotipe tanpa PLINK, jadi cepat dan bisa diuji sendiri. Temuan dibagi dua jenis: error (analisis tidak bisa lanjut) dan warning (analisis bisa lanjut, pengguna perlu tahu). Setiap temuan punya kode, pesan, saran perbaikan, dan dampaknya.

Genotipe diperiksa sebagai satu kesatuan. Set PLINK baru dianggap lengkap bila `.bed`, `.bim`, dan `.fam` ada semua, dan ukuran `.bed` dicocokkan dengan jumlah sampel dan varian dari dua berkas lainnya. Data genotipe yang hilang dihitung (dari `.bed` dengan tabel bit, dari VCF dengan membaca kolom GT) dan dilaporkan sebagai warning karena ditangani QC.

Fenotipe diperiksa isinya: ada kolom ID sampel, jumlah kolom tiap baris sama, ID tidak kosong dan tidak ganda, ada kolom trait, nilai kosong dihitung per trait (warning), dan kolom angka yang disusupi teks ditolak. Tipe trait dikenali otomatis: dua nilai berkode 0/1 atau 1/2 berarti biner, selain itu kuantitatif.

`dataset_status` menghitung status dari keadaan folder: `incomplete`, `not_validated`, `stale` (berkas berubah sejak divalidasi, dikenali dari ukuran dan waktu ubah yang dicatat saat validasi), `valid`, atau `invalid`.

## app/services/config_rules.py

Aturan konfigurasi. `AnalysisConfig` adalah isi permintaan analisis dengan batas nilai tiap parameter. `MODEL_CATALOG` mendaftar tiga model (regresi linear, logistik, LMM) beserta jenis trait, program, dan syaratnya. `check_config` menerapkan aturan bisnis: dataset harus valid dan belum berubah, trait harus ada dan tipenya cocok, model harus cocok dengan tipe trait, regresi logistik butuh cukup kasus dan kontrol, kovariat harus ada di fenotipe, jumlah komponen PCA yang diminta hanya 5, 10, atau 20; jumlah aktual ditentukan dari output PCA dan dapat lebih kecil (partial). Variabel pengontrol dibedakan jelas: kovariat dari fenotipe (`covariates`) dan komponen utama dari genotipe (`use_pca`, `pca_components`), dan keduanya dirangkum di `covariate_sources`. `PRESETS` berisi nilai otomatis Guided Mode per domain.

## app/services/significance.py

Aturan lima metode ambang signifikansi: genome-wide (p ≤ 5e-8), suggestive (p ≤ 1e-5), Bonferroni (0,05 dibagi jumlah varian yang diuji), FDR Benjamini-Hochberg (q-value ≤ q), dan custom (batas p dari pengguna). Nilai `threshold` hanya dipakai oleh FDR dan custom. Pada metode lain nilainya diabaikan dan jawaban memberi tahu hal itu.

## app/services/ui_adapter.py

Penerjemah isi dropdown frontend menjadi nilai baku, misalnya "Logistic Regression" menjadi `logistic`, "MAF >= 0.05" menjadi 0.05, "5 PC" menjadi 5, dan "Age + Sex" menjadi `["Age", "Sex"]`. Nilai yang tidak dikenali dikembalikan apa adanya supaya validasi menolaknya dengan pesan jelas. Modul ini juga memberi perkiraan kasar lama analisis.

## app/services/storage.py

Satu-satunya tempat yang tahu di mana berkas disimpan. Susunannya `uploads/u{pengguna}/ds_{dataset}/...`. Unggahan ditulis ke berkas sementara dulu lalu dipindah, jadi unggahan yang putus tidak meninggalkan berkas setengah jadi. Ada batas ukuran per berkas. Fungsi `dataset_dir` menolak alamat yang keluar dari folder penyimpanan.

## app/services/tools.py

Satu pintu untuk menjalankan PLINK 2 dan GEMMA. Perintah dikirim sebagai daftar argumen (bukan teks shell), jadi tidak ada risiko command injection. Keluaran program ditulis langsung ke log analisis. Ada batas waktu, dan kegagalan (program tidak ada, kehabisan memori, waktu habis) menjadi `ToolError` dengan pesan yang jelas.

## app/services/gwas_engine.py

Alur analisis sebenarnya, dipecah menjadi tiga tahap. `stage_qc` mengubah VCF ke PLINK bila perlu, menyiapkan tabel fenotipe, dan menjalankan QC (MAF, call rate, HWE). `stage_pca` memangkas varian yang berkorelasi lalu menghitung komponen utama dari genotipe, atau dilewati bila PCA tidak dipakai. `stage_gwas` menggabungkan kovariat fenotipe dan komponen utama menjadi satu berkas pengontrol, lalu menjalankan uji asosiasi dengan PLINK 2 (regresi linear atau logistik) atau GEMMA (LMM). QC dan PCA memakai sampel yang nilai trait-nya terisi, sedangkan tahap GWAS menambah syarat kovariatnya juga terisi. `import_results` menyimpan hasil ke database per kelompok varian supaya memori tetap kecil.

## app/services/jobs.py

Pengelola analisis di latar belakang. Menjaga status di database (`queued`, `running`, `awaiting`, `done`, `failed`), berkas `progress.json` dan `analysis.log` yang dibaca endpoint pemantauan, dan batas jumlah analisis bersamaan. Empat tahap tampil di layar: `qc`, `pca`, `gwas`, `save`, masing-masing berstatus `pending`, `running`, `done`, `skipped`, atau `failed`. Urutan dan arti tahapnya sama untuk mode auto dan staged, perbedaannya hanya mode staged berhenti di status `awaiting` setelah QC dan setelah PCA. Setelah analisis selesai, varian signifikan ditandai lewat jalur yang sama dengan post-processing.

## app/services/results.py

Semua yang menjawab pertanyaan hasil. `apply_significance` adalah satu-satunya tempat yang mengisi kolom `is_significant`, lalu menyimpan aturan yang berlaku, jadi tabel, grafik, dan ringkasan selalu memakai ambang yang sama. Manhattan selalu mempertahankan semua varian signifikan saat jumlah titik dikurangi. QQ plot mengembalikan p-value teramati, p-value harapan, versi -log10, dan lambda GC, lengkap dengan definisi tiap istilah. Hasil diurutkan dengan `(p_value, result_id)` supaya peringkat tidak berubah antar permintaan, dan q-value Benjamini-Hochberg dihitung langsung di database untuk halaman yang diminta. Ekspor ke CSV, TSV, dan JSON dialirkan sedikit demi sedikit, Excel dibuat dalam mode hemat memori.

## sql/schema.sql

Pembuat semua tabel dan indeks. Aman dijalankan berulang. Indeks `(analysis_id, p_value, result_id)` mempercepat pengurutan dan perhitungan peringkat pada hasil jutaan varian.

## tests/

Tes otomatis mengikuti laporan verifikasi terbaru di `docs/REVISI_BACKEND_2026-11.md`. `test_auth.py` dan `test_error_format.py` menguji akun, format error, login, dan token. `test_upload_validation.py` untuk unggah, validasi, dan penghapusan. `test_config.py` untuk aturan konfigurasi. `test_results.py` untuk post-processing, grafik, tabel, dan ekspor dengan angka yang dihitung tangan. `test_pipeline.py` menjalankan PLINK 2 dan GEMMA asli. `test_postgres.py` khusus PostgreSQL.


## Revisi backend November 2026

Source code terbaru menambahkan kebijakan resource-aware di `services/resource_policy.py`, pencatatan penggunaan sampel/varian di `services/jobs.py`, laporan metrik QC aktual dari output PLINK2, PCA partial-output dengan batas memori fallback, serta tabel dan layanan antrean AI statistik yang terpisah. Rincian implementasi, endpoint, konfigurasi, batas verifikasi, dan hasil tes terbaru ada pada `REVISI_BACKEND_2026-11.md`.
