# Keputusan yang perlu dikonfirmasi tim

Beberapa aturan belum tertulis di dokumen proyek, jadi saya memilih nilai sementara yang masuk akal. Semuanya bisa diubah di tempat yang disebutkan. Sebaiknya dibahas dengan Pram (Project Manager) dan Nakhwah (System Analyst) sebelum dipakai di demo.

| No | Keputusan sementara | Alasan | Diubah di |
|---|---|---|---|
| 1 | Minimal 10 sampel yang cocok antara genotipe dan fenotipe, di bawah itu error. Di bawah 50 sampel diberi warning. | BR-03 butuh batas minimum, angkanya belum ditetapkan. | `MIN_SAMPLES` dan `SMALL_SAMPLE_WARNING` di `services/validation.py` |
| 2 | Regresi logistik butuh minimal 5 kasus dan 5 kontrol, di bawah itu ditolak. Di bawah 20 diberi warning. | Dengan kelompok yang terlalu kecil hasil regresi tidak stabil. | `MIN_PER_CLASS` dan `WARN_PER_CLASS` di `services/config_rules.py` |
| 3 | Jumlah komponen PCA hanya 5, 10, atau 20, bawaan 5. | Disepakati pada diskusi kebutuhan (catatan analis butir 11). | `PCA_CHOICES` dan `DEFAULT_PCA` di `services/config_rules.py` |
| 4 | Bila `use_pca = false`, `pca_components` diabaikan dengan warning, tidak ditolak. | Formulir frontend bisa menyisakan angka lama. Menolaknya akan membingungkan pengguna. | `check_config` di `services/config_rules.py` |
| 5 | Pada genome_wide, suggestive, dan bonferroni, `threshold` yang terkirim diabaikan dengan keterangan, tidak ditolak. | Catatan butir 15 meminta diputuskan. Mengabaikan lebih ramah bagi frontend, dan respons tetap memberi tahu. Bila tim lebih suka menolak, ubah `check_rule`. | `services/significance.py` |
| 6 | Bonferroni memakai tingkat signifikansi 0,05 yang tetap. Nilai q pada FDR boleh 0 sampai 0,5, bawaan 0,05. | Nilai umum yang dipakai pada GWAS. | `services/significance.py` |
| 7 | Preset Guided Mode per domain (MAF, call rate, HWE, PCA). | Dokumen belum menetapkan angkanya. Nilai sekarang mengikuti kebiasaan umum dan contoh di LKP 6. | `PRESETS` di `services/config_rules.py` |
| 8 | Sampel dengan nilai trait kosong dikeluarkan dari analisis trait itu, dan data yang kosong hanya menghasilkan warning. | Praktik umum (complete-case). Catatan butir 8 menyebut kondisi ini sebagai warning. | `validation.py` dan `gwas_engine.py` |
| 9 | Genotipe VCF dan PLINK sekaligus pada satu dataset ditolak. | Agar jelas berkas mana yang dianalisis. | `completeness` di `services/validation.py` |
| 10 | Dataset yang sudah dipakai analisis tidak bisa diubah berkasnya satu per satu, hanya dihapus utuh dengan konfirmasi. | Menjaga hasil analisis tetap bisa ditelusuri ke data aslinya. | `routers/gwas.py` |
| 11 | Status analisis memakai huruf kecil (`failed`, `running`), sama dengan SDP. Contoh di catatan memakai huruf besar. | Frontend yang sudah dibuat memakai huruf kecil. | `models.py` dan `services/jobs.py` |
| 12 | Kolom kategori (misalnya jenis kelamin) boleh jadi kovariat bila jumlah kategorinya 2 sampai 10. | Kolom teks dengan terlalu banyak kategori kemungkinan besar kode sampel, bukan kovariat. | `MAX_CATEGORY_LEVELS` di `services/validation.py` |
| 13 | Alamat `/api/login/json` dipertahankan tersembunyi. | Supaya frontend yang sudah memanggilnya tidak rusak. Boleh dihapus setelah frontend pindah ke `/api/login`. | `routers/auth.py` |
| 14 | Batas titik grafik 100.000. | Cukup untuk grafik tetap ringan di browser. | `MAX_PLOT_POINTS` di `.env` |
| 15 | ERD tidak diubah: tidak ada kolom `se` dan `p_value_adjusted` pada `gwas_result`. | LKP 9 menyatakan skema final. q-value FDR dan p Bonferroni dihitung saat diminta. Bila tim ingin menyimpannya, SDP 4.3.3 sudah mencantumkan kedua kolom itu. | `models.py` dan `sql/schema.sql` |

Pertanyaan yang sebaiknya dijawab Pram atau PM lebih dulu: butir 1 (batas sampel), butir 2 (batas kasus dan kontrol), dan butir 7 (preset domain), karena angka-angkanya akan terlihat langsung oleh pengguna.
