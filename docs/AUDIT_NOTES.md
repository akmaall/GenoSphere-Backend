# Catatan audit sumber (14 November 2026)

Berdasarkan output terminal yang diberikan:

| Komponen | Temuan | Keputusan |
|---|---|---|
| OS | Ubuntu 24.04.3 LTS dalam WSL2 | Target awal laptop lain sebaiknya sama |
| Arsitektur | x86_64 | Binary bundle hanya ditujukan untuk x86_64 |
| Python | 3.12.12 | Buat ulang `.venv` dari requirements di target |
| PLINK2 | `/usr/local/bin/plink2`, static ELF x86-64, build `2.0.0-b.1-devLM` | Salin binary ke `tools/bin/plink2`; simpan versi + checksum |
| GEMMA | `/usr/local/bin/gemma`, static ELF x86-64, v0.98.5 | Salin binary ke `tools/bin/gemma`; simpan versi + checksum |
| Ollama | `/usr/local/bin/ollama`, v0.40.16, service aktif | Salin binary beserta `lib/ollama`; jangan hanya salin executable |
| Model | `qwen3:4b`, ukuran terlapor 2.5 GB | Salin seluruh model store (manifest + blob), bukan hanya nama model |

## Hal yang belum dibuktikan oleh output audit

- Lokasi aktual model store Ollama dan kelengkapan blob Qwen.
- Kelengkapan direktori runtime Ollama (`lib/ollama`).
- Dependensi database PostgreSQL, apakah lokal/remote/container, versi, schema dan data.
- Isi lengkap `requirements.txt` dan apakah semua wheel dapat diperoleh untuk offline install.
- Dependensi aplikasi lain seperti R, Java, Docker, Node.js, Redis, atau tools tambahan yang mungkin digunakan source code.
- Kompatibilitas binary pada laptop lain dengan versi glibc/driver GPU yang berbeda.
- Apakah binary ELF dapat dieksekusi langsung dari mount DrvFs `/mnt/d` pada konfigurasi WSL laptop tujuan.

Jangan menganggap `ldd` tanpa pesan `not found` sebagai bukti portabilitas lintas semua distro. Itu hanya mengindikasikan dependensi dinamis yang diperiksa dapat ditemukan pada mesin sumber saat audit dilakukan.
