# Tanggapan atas catatan System Analyst untuk Backend

Dokumen ini merupakan catatan historis audit versi sebelumnya untuk 25 butir di "Catatan untuk BE". Sebagian implementasi dan kontrak API kemudian berubah. Untuk status terbaru, gunakan `REVISI_BACKEND_2026-11.md` dan Swagger dari source code saat ini. Angka pengujian historis di bawah tidak boleh dipakai sebagai hasil verifikasi revisi terbaru. Pengujian PLINK2, GEMMA, dan PostgreSQL hanya dapat diklaim berhasil bila executable/layanan tersebut benar-benar tersedia pada lingkungan pengujian.

## 1. Format error auth belum seragam

Benar. Server sebenarnya mengirim satu format (`{"detail": {"code", "message", "errors"}}`), tetapi dokumentasi Swagger masih menuliskan format bawaan FastAPI untuk 422 (`detail` berupa daftar `loc`, `msg`, `type`). Frontend yang membaca Swagger bisa memakai format yang salah.

Perbaikan: `custom_openapi` di `main.py` mengganti semua dokumentasi 422 dengan format yang sebenarnya, dan tiga penangan di `errors.py` memastikan semua jenis kesalahan (isian salah, alamat tidak ada, metode salah, kesalahan server, kesalahan buatan sendiri) memakai format yang sama. Frontend cukup membaca `detail.code` dan `detail.message`. Tes: `test_error_format.py`.

## 2. Login ada dua endpoint

Benar, dan itu menjadi sumber salah kirim JSON ke endpoint form. Sekarang hanya ada satu alamat login resmi, `POST /api/login`, yang menerima JSON (dipakai frontend) maupun form (dipakai tombol Authorize di Swagger), jadi tidak ada lagi pilihan yang bisa salah. Alamat lama `/api/login/json` tetap hidup supaya frontend yang sudah memanggilnya tidak rusak, tetapi disembunyikan dari Swagger. Tes: `test_login_single_documented_endpoint`, `test_login_accepts_json_and_form_on_same_endpoint`.

## 3. Token harus disimpan dan dikirim

Sisi backend: jawaban login memuat `access_token`, `token_type`, dan `expires_in`. Setiap endpoint yang butuh login menolak permintaan tanpa header `Authorization: Bearer <token>` dengan 401 dan kode yang jelas (`TOKEN_MISSING`, `TOKEN_INVALID`, `TOKEN_EXPIRED`), dan CORS mengizinkan header itu. `GET /api/me` bisa dipakai frontend untuk memeriksa token yang tersimpan. Sisi frontend: `frontend-integration/lib/api.ts` menyimpan token ke localStorage saat login lalu menyertakannya otomatis pada setiap permintaan. Tes: `test_protected_endpoints_need_the_saved_token`, `test_cors_allows_authorization_header`.

## 4. Kolom `file` dan `files` pada upload

Koreksi kontrak untuk source code saat ini: endpoint upload menerima satu field `file` per request. Tiga berkas PLINK dikirim dalam tiga request berurutan dengan `dataset_id` yang sama. Fungsi internal `_upload_many` menerima list satu elemen agar validasi dan penyimpanan tetap memakai jalur bersama; ini tidak berarti API menerima beberapa file dalam satu request. Dokumentasi lama yang menyebut field `files` tidak berlaku untuk versi source saat ini.

## 5. Validasi isi fenotipe (perlu diputuskan tim, sampaikan ke Pram)

Validasi memeriksa isi, tidak hanya ekstensi. Pertanyaan di catatan dijawab satu per satu: ada kolom ID sampel (`PHENOTYPE_NO_ID`, nama yang diterima IID, ID, Sample, Sample_ID, Sample_code), ada kolom trait (`PHENOTYPE_NO_TRAIT`), nilai trait kosong (dihitung per trait dan dilaporkan sebagai warning `PHENOTYPE_MISSING_VALUES`), ID duplikat (`DUPLICATE_SAMPLE_ID`), ID kosong (`PHENOTYPE_EMPTY_ID`), jumlah kolom tiap baris tidak sama (`PHENOTYPE_ROW_LENGTH`), dan format nilai sesuai tipe trait (kolom angka yang disusupi teks ditolak dengan `PHENOTYPE_MIXED_VALUES`, trait biner harus berkode 0/1 atau 1/2, dua nilai dengan kode lain dibaca kuantitatif disertai warning). Karena butir ini minta dibahas dulu dengan Pram, aturan yang saya pakai tertulis di dokumen keputusan supaya bisa disetujui atau diubah. Tes: `test_upload_validation.py` bagian "no. 5".

## 6. Kelengkapan file PLINK

Set `.bed`, `.bim`, dan `.fam` diperlakukan sebagai satu kesatuan. Menerima satu atau dua berkas saja tidak dihitung genotipe lengkap: tombol validasi tidak aktif (`ready_for_validation = false`), `missing` menyebut berkas yang kurang, dan bila tetap divalidasi hasilnya `incomplete` dengan kode `GENOTYPE_INCOMPLETE`. Bila ketiganya ada, isinya dicocokkan: ukuran `.bed` harus sama dengan yang dihitung dari jumlah sampel di `.fam` dan jumlah varian di `.bim` (`BED_SIZE_MISMATCH`), header `.bed` diperiksa, kolom `.fam` dan `.bim` diperiksa, dan VCF bersamaan dengan PLINK ditolak karena ambigu. Analisis tidak bisa dibuat bila data belum lengkap. Tes: `test_plink_single_file_is_not_complete_genotype`, `test_plink_files_must_match_each_other`.

## 7. Penghapusan file memengaruhi status validasi

Status dataset dihitung dari isi folder setiap kali diminta, jadi menghapus `.fam` dari dataset yang tadinya valid langsung membuatnya `incomplete` dan tidak bisa dianalisis. Jawaban penghapusan memuat keadaan dataset yang sudah diperbarui, jadi frontend tidak perlu bertanya lagi. Berkas yang diganti di balik layar setelah validasi membuat status `stale` (hasil validasi lama tidak berlaku), dan konfigurasi ditolak dengan `DATASET_CHANGED`.

Penghapusan dataset memakai konfirmasi dua langkah di sisi backend, supaya frontend punya dasar untuk dialog: panggilan `DELETE /datasets/{id}` tanpa `confirm` dijawab 409 `CONFIRMATION_REQUIRED` beserta rincian yang akan terhapus (jumlah berkas, jumlah analisis, jumlah baris hasil). Setelah pengguna setuju, panggil lagi dengan `?confirm=true`. Dataset yang sudah dipakai analisis tidak bisa diubah berkasnya satu per satu, hanya dihapus utuh dengan konfirmasi. Tes: `test_deleting_a_file_updates_dataset_status`, `test_file_changed_after_validation_makes_status_stale`, `test_delete_dataset_needs_confirmation`.

## 8. Error dan warning dibedakan

Laporan validasi punya dua daftar, `errors` dan `warnings`. Setiap temuan berisi `code`, `message`, `hint` (cara memperbaiki), dan `impact` (pengaruh ke analisis), sama dengan kolom "Jenis, Contoh, Dampak" di tabel catatan. Status dataset: `valid` bila tidak ada error (warning boleh ada), `invalid` bila ada berkas salah, `incomplete` bila berkas belum lengkap.

Pemetaan contoh di catatan: berkas `.fam` tidak ada adalah error. Sample ID genotipe dan fenotipe tidak cocok sama sekali adalah error (`NO_MATCHING_SAMPLES`), sedangkan tidak cocok sebagian adalah warning (`SAMPLES_PARTIALLY_MATCHED`). Sebagian sampel punya nilai fenotipe kosong adalah warning dengan dampak "Perlu penanganan lebih lanjut". Varian dengan data genotipe hilang adalah warning dengan dampak "Dapat ditangani pada tahap QC" (`GENOTYPE_MISSING_CALLS`, dihitung dari `.bed` dan VCF). Tes: `test_missing_phenotype_values_is_warning_not_invalid`, `test_missing_genotype_calls_is_warning_handled_by_qc`, `test_sample_id_mismatch_is_error_but_partial_is_warning`.

## 9. Konfigurasi dan pemilihan metode

Respons `GET /config/presets` dan `POST /config/check` tampil sebagai "string" karena tidak punya skema respons. Sekarang semua endpoint punya skema (lihat butir 25). `presets` mengirim daftar `domains` (nilai, label, deskripsi) untuk dropdown domain riset yang belum ada di frontend, ditambah nilai parameter tiap domain. Bila `dataset_id` dikirim, domain milik dataset ditandai. `config/check` mengembalikan `valid`, `errors`, `warnings`, konfigurasi yang sudah dinormalisasi, dan estimasi waktu. Contoh isinya ada di Swagger. Tes: `test_presets_returns_domain_choices_for_frontend_dropdown`, `test_config_check_response_is_fully_structured`.

## 10. Menjalankan analisis

use_pca dan pca_components. Aturannya: `pca_components` hanya berarti bila `use_pca = true`, nilainya hanya 5, 10, atau 20 (sesuai kesepakatan diskusi, bukan 1), dan bawaannya 5. Bila `use_pca = false` dan `pca_components` terkirim, nilainya diabaikan dan jawaban memberi warning `PCA_COMPONENTS_IGNORED`, nilai tersimpan menjadi null. Bila `use_pca = true` dan jumlahnya kosong dipakai 5 dengan warning `PCA_COMPONENTS_DEFAULT`. Catatan lama bahwa jumlah komponen harus lebih kecil dari jumlah sampel dikurangi 1 telah digantikan: backend kini menerima permintaan 5/10/20 dan melaporkan hasil aktual sebagai complete/partial/unavailable sesuai output PCA.

Kovariat. Daftar `covariates` hanya berisi kolom dari data fenotipe. Pilihan di catatan (Age, Sex, Age + Sex, BMI, tanpa kovariat tambahan) semuanya diterima, termasuk teks dropdown seperti "Age + Sex". Komponen utama PCA berasal dari genotipe dan diatur terpisah lewat `use_pca` dan `pca_components`. Setiap respons konfigurasi memuat `covariate_sources` yang menunjukkan keduanya: `{"phenotype_columns": ["Age", "Sex"], "principal_components": 10}`. Pada tahap GWAS keduanya digabung menjadi satu berkas pengontrol, dan tes memeriksa isi berkas itu.

Execution mode. Mode auto dan staged memakai empat tahap yang sama (`qc`, `pca`, `gwas`, `save`) dengan keadaan `pending`, `running`, `done`, `skipped`, atau `failed`. Tes menjalankan keduanya dengan konfigurasi yang sama dan membandingkan: urutan tahap, status akhir, dan hasil analisis (varian terbaik, p-value, effect size, jumlah varian) identik. Tes: `test_staged_and_auto_give_identical_stages_and_results`.

## 11. PCA, pemilihan fenotipe, dan model (perlu diputuskan tim, tanya PM)

Pemilihan fenotipe. `GET /datasets/{id}/traits` mengirim daftar trait yang bisa dianalisis, lengkap dengan tipe, jumlah nilai, jumlah kasus dan kontrol (untuk trait biner), dan model yang cocok. Frontend memakainya untuk dropdown fenotipe sebelum Step 3.

Model disesuaikan dengan aturan masing-masing. `GET /config/models` menjelaskan tiga model: regresi linear (trait kuantitatif, PLINK 2), regresi logistik (trait biner, minimal 5 kasus dan 5 kontrol, warning di bawah 20), dan LMM (trait kuantitatif, GEMMA, kinship, PCA tambahan boleh tetapi biasanya tidak perlu). Semua model menguji satu trait per analisis. Pilihan manual yang tidak cocok dengan tipe trait ditolak (`MODEL_TRAIT_MISMATCH`), dan `auto` memilih model dari tipe trait. Syarat khusus tiap model diperiksa saat konfigurasi, termasuk keberadaan program GEMMA.

PCA dan kovariat dipisah sesuai konsep di catatan. `POST /analyses/{id}/pca` hanya menghitung komponen utama dari genotipe. Kovariat fenotipe diatur lewat endpoint tersendiri, `PUT /analyses/{id}/covariates`. Pemilihan model dan penjalanan analisis asosiasi dilakukan di `POST /analyses/{id}/run`, yang memakai komponen utama dan kovariat sekaligus. PCA tidak menghitung usia, jenis kelamin, atau BMI. Tes: `test_pcs_and_covariates_are_both_used_but_come_from_different_sources`, `test_logistic_needs_enough_cases_and_controls`.

## 12. Retry

Retry hanya untuk status `failed`. Status lain (`queued`, `running`, `awaiting`, `done`) dijawab 409 `NOT_RETRYABLE` dengan `details.retryable_statuses = ["failed"]`. Jawaban sukses menjelaskan hasilnya: `scheduled` (true bila berhasil dijadwalkan), `status`, `attempt` (nomor percobaan), `retry_from` (dari awal atau dari tahap mana), dan `message`. Jumlah percobaan juga tampil di status dan detail analisis. Tes: `test_retry_only_for_failed_and_reports_scheduling`, `test_retry_rejected_for_every_status_except_failed`.

## 13. Log

Endpoint log punya `offset` dan `limit` (bawaan 200, paling banyak 1000). Jawabannya berisi `logs`, `offset`, `limit`, `next_offset`, `has_more`, ditambah `finished` dan `waiting`. Frontend meminta berulang dengan `offset = next_offset` selama `has_more` true, dan sesekali selama analisis berjalan. Baris yang masih setengah ditulis tidak dikirim sampai lengkap. Tes memastikan membaca halaman demi halaman menghasilkan log yang sama persis dengan membaca sekaligus. Tes: `test_log_pagination_covers_whole_log_without_gaps`.

## 14. Perbedaan endpoint status dan detail

`GET /analyses/{id}/status` ringan, untuk polling progress bar: status, tahap, empat tahap beserta keadaannya, persen, estimasi sisa waktu, galat, dan penanda `can_continue` dan `can_retry`. `GET /analyses/{id}` lengkap, untuk halaman detail: konfigurasi yang dipakai (`trait_type`, `method`, `qc`, `pca`, `number_of_pcs`, kovariat, ambang), ringkasan progres, jumlah percobaan, `created_at`, `updated_at`, `finished_at`. Penjelasan perbedaannya ditulis di Swagger masing-masing endpoint, dan bentuk detail mengikuti contoh di catatan. Tes: `test_status_is_light_and_detail_is_full`.

## 15. Post-processing dan aturan threshold

Aturan lima metode: genome_wide memakai 5e-8 dan suggestive memakai 1e-5 (tetap), bonferroni memakai 0,05 dibagi jumlah varian yang benar-benar diuji, fdr memakai Benjamini-Hochberg dengan nilai q (bawaan 0,05), dan custom memakai nilai dari pengguna (wajib, bila kosong dijawab 422 `THRESHOLD_REQUIRED`).

Soal "apakah threshold boleh dikirim untuk metode yang sudah punya ambang bawaan": keputusan sementara, nilainya diabaikan, bukan ditolak. Jawaban menandai `ignored_threshold = true` dan menulis penjelasan di `notes`, jadi contoh `threshold: 1` di Swagger tidak lagi menyesatkan dan tidak mengubah arti metode. `threshold` hanya dipakai oleh fdr (sebagai q) dan custom (sebagai batas p). Tes: `test_threshold_is_ignored_for_methods_with_builtin_value`, `test_custom_needs_threshold_and_uses_it`.

## 16. Results Summary

Berisi total varian yang dianalisis (`n_variants_tested`), jumlah varian signifikan, varian dengan p-value terbaik (`best_variant`), p-value terkecil, serta metode dan ambang yang sedang dipakai (`significance`), ditambah lambda GC, model, trait, dan jumlah sampel. Tes: `test_summary_content`.

## 17. Manhattan plot dan pengurangan titik

Terbukti benar. Saya menjalankan kode lama dengan 4.000 varian dan ambang 0,01: ada 50 varian signifikan, tetapi hanya 16 yang tampil di Manhattan, karena yang dipertahankan hanya varian dengan p-value kecil dari 1e-3. Sekarang semua varian signifikan menurut aturan post-processing yang berlaku selalu ikut tergambar, baru varian dengan p-value kecil, lalu sampel merata dari sisanya. Jawaban menyebut `significant_total`, `significant_displayed`, dan `all_significant_displayed`. Bila varian signifikan sendiri melebihi batas titik, yang tampil adalah yang p-value-nya terkecil dan `all_significant_displayed` bernilai false. Tes: `test_manhattan_keeps_every_significant_variant_when_thinning`.

## 18 dan 22. QQ plot, observed, expected, dan lambda

Jawaban QQ memuat `observed_p` (p-value teramati, urut naik), `expected_p` (p-value harapan, `(i + 0,5) / n`), versi -log10 keduanya untuk digambar, `lambda_gc`, dan `definitions` yang menjelaskan observed, expected, lambda GC, dan cara membaca grafik. Lambda GC adalah median chi-kuadrat teramati dibagi 0,4549 (median chi-kuadrat dengan 1 derajat bebas), dengan chi-kuadrat tiap varian diperoleh dari p-value-nya. Ujung paling signifikan dan titik terakhir selalu ikut walau titik dikurangi. Tes dihitung tangan: p-value 0,5 pada semua varian memberi lambda 1,0. Tes: `test_qq_has_expected_observed_and_lambda_with_definitions`, `test_lambda_is_one_when_no_inflation_and_high_when_inflated`.

## 19. Tabel Significant Variants mengikuti post-processing terakhir

Hanya satu fungsi (`apply_significance`) yang mengisi penanda signifikan, dalam satu perintah database dan satu commit, lalu menyimpan aturan yang berlaku. Semua endpoint hasil (summary, top, manhattan, significant) membaca aturan yang sama dan menampilkannya di jawaban, lengkap dengan waktu penerapan. Tabel tanpa parameter `threshold` ditandai `source = saved_postprocess`. Dengan parameter `threshold`, tabel dibuat dari batas itu saja (`source = query_parameter`) dan tidak mengubah yang tersimpan. Tes: `test_significant_table_follows_latest_postprocess`, `test_all_result_endpoints_report_same_rule`.

## 20. Top Variants

Diurutkan dari p-value terkecil, tidak dibatasi pada varian signifikan. Ini ditulis di jawaban (`sorted_by`, `order`, `only_significant = false`) dan di Swagger, sehingga kandidat terbaik tetap tampil walau tidak ada yang melewati ambang. Nilai p-value yang sama diurutkan dengan nomor hasil, jadi urutannya tidak berubah antar permintaan. Tes: `test_top_is_sorted_by_smallest_p_and_not_limited_to_significant`.

## 21. Parameter threshold pada postprocess

Genome-wide dan suggestive otomatis dari metode. Bonferroni memakai jumlah varian yang diuji (varian yang lolos QC dan punya p-value), dan angkanya tampil sebagai `n_tests` beserta penjelasan "bukan jumlah varian pada berkas unggahan". FDR dijelaskan sebagai metode yang mengubah p-value menjadi q-value (p-value terkoreksi), dan varian signifikan bila q-value kurang dari atau sama dengan q. Tabel Top dan Significant menampilkan `q_value` tiap varian saat metode aktif adalah FDR, dan nilainya dicocokkan dengan hitungan Benjamini-Hochberg independen di tes. Custom wajib diisi pengguna. Tes: `test_bonferroni_uses_number_of_variants_actually_tested`, `test_fdr_matches_independent_benjamini_hochberg`.

## 23. Export

Ekspor dikirim sebagai berkas sungguhan dengan `Content-Type` sesuai (`text/csv`, `text/tab-separated-values`, `application/json`, `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`), header `Content-Disposition: attachment` berisi nama berkas, dan `X-Content-Type-Options: nosniff`. Swagger menuliskan keempatnya sebagai berkas biner, bukan JSON. Excel dibuka ulang di tes untuk memastikan berkasnya valid. Tes: `test_export_returns_files_with_proper_mime_and_headers`, `test_export_contents_are_readable`.

## 24. Config export

Bentuk berkasnya sekarang terdefinisi (`ConfigExportOut`): `schema_version`, `exported_at`, `analysis_id`, `status`, `model_used`, `dataset`, `configuration` (seluruh parameter), `significance_applied`, `software_versions`, dan `duration_seconds`. Versi perangkat lunak yang dicatat: GenoSphere API, versi alur kerja, PLINK 2, GEMMA (bila LMM dipakai), dan Python. Versi diambil dari program yang benar-benar dijalankan saat analisis. Tes: `test_config_export_records_software_versions`.

## 25. Respons "string" harus diperjelas

Sebelumnya 30 dari 33 endpoint tidak punya skema respons, itulah yang tampil sebagai "string" di Swagger. Sekarang semua endpoint punya `response_model` dengan keterangan arti tiap kolom, dan beberapa dilengkapi contoh isi. Endpoint unduhan ditulis sebagai berkas. Satu tes menelusuri seluruh dokumentasi dan gagal bila ada endpoint yang jawaban suksesnya tidak terdefinisi. Tes: `test_every_gwas_endpoint_documents_its_success_response`.

## Catatan penutup

Catatan analis menyebut bahwa butir-butirnya perlu diverifikasi, bukan berarti pasti salah. Hasil verifikasinya: butir 1, 2, 4, 9, 13, 17, dan 25 terbukti benar di kode sebelumnya. Butir 6 sudah ditangani sebagian dan sekarang dilengkapi status `incomplete`. Butir lain berupa penyempurnaan aturan dan kejelasan respons.
