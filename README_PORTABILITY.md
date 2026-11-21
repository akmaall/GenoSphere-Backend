# GenoSphere Backend: Portable Tools & First-Run Setup

Dokumen ini menjelaskan cara menyiapkan folder `backend/` agar tool bioinformatika dan model AI yang dibutuhkan proyek ikut terbawa saat folder dipindahkan.

## 1. Target portabilitas yang didukung

Bundle awal dibuat dari lingkungan:

- WSL2
- Ubuntu 24.04 LTS
- Linux `x86_64`
- Python 3.12
- PLINK2 (binary Linux x86-64)
- GEMMA 0.98.5
- Ollama 0.40.16
- Model Ollama `qwen3:1.7b`

Laptop tujuan minimal harus memiliki WSL2 + distro Linux yang kompatibel (disarankan Ubuntu 24.04 x86-64). Folder proyek tidak dapat menggantikan WSL2, kernel Linux, driver GPU, atau library sistem dasar. Binary Linux tidak bisa dijalankan langsung dari Windows tanpa lingkungan Linux/WSL.

## 2. Struktur folder yang disarankan

```text
backend/
├── app/
├── scripts/
│   ├── package_tools.sh
│   ├── build_wheelhouse.sh
│   ├── bootstrap.sh
│   ├── check_environment.sh
│   ├── start_ollama.sh
│   └── stop_ollama.sh
├── tools/
│   ├── bin/
│   │   ├── plink2
│   │   └── gemma
│   ├── ollama/
│   │   ├── bin/ollama
│   │   ├── lib/ollama/       # runtime libraries/runners Ollama
│   │   └── models/           # blobs + manifests, termasuk qwen3:1.7b
│   ├── python/wheelhouse/    # paket Python untuk instalasi offline
│   ├── reports/
│   └── SHA256SUMS
├── requirements.txt
├── .env.example
├── .env                   # konfigurasi lokal; jangan commit/share secrets
├── .venv/                 # dibuat ulang di tiap laptop; jangan dipindahkan
└── README_PORTABILITY.md
```

Jangan mengandalkan `.venv` dari laptop lama. Virtual environment biasanya berisi path absolut dan native wheels yang terikat pada OS/Python/arsitektur. Pindahkan `requirements.txt` dan, bila perlu, wheelhouse; buat ulang `.venv` di laptop tujuan.

**Catatan untuk `/mnt/d`:** output audit Anda menunjukkan mount DrvFs tanpa opsi `metadata`. Secara default WSL tidak menyimpan permission Linux pada file Windows; hal ini dapat membuat bit executable untuk binary/script tidak konsisten. Uji `tools/bin/plink2 --version` dan `tools/ollama/bin/ollama -v` setelah packaging. Jika muncul `Permission denied`, konfigurasi DrvFs `metadata` pada `/etc/wsl.conf` dapat diperlukan, lalu restart WSL. Jangan menimpa pengaturan `wsl.conf` yang sudah ada tanpa memeriksanya terlebih dahulu. Dokumentasi Microsoft menjelaskan bahwa metadata DrvFs mendukung permission Linux dan default-nya nonaktif.

## 3. Persiapan di laptop sumber (laptop Anda)

Jalankan dari WSL Ubuntu:

```bash
cd /mnt/d/GenoSphere/backend
bash scripts/package_tools.sh
bash scripts/build_wheelhouse.sh
bash scripts/check_environment.sh
```

`package_tools.sh` menyalin PLINK2, GEMMA, binary dan runtime Ollama, serta model store yang ditemukan pada lokasi umum. Skrip tidak menghapus sumber asli. Setelah selesai, pastikan `tools/reports/source_manifest.txt` mencatat Qwen3 1.7B dan `tools/ollama/models/manifests/` berisi manifest `qwen3/1.7b`.

Apabila model berada di lokasi khusus, jalankan dengan path eksplisit:

```bash
OLLAMA_MODELS="/lokasi/model/ollama" bash scripts/package_tools.sh
```

Wheelhouse dibuat dari `requirements.txt` dan cocok untuk versi Python/platform sumber. Bila target berbeda versi Python/arsitektur, wheelhouse perlu dibuat ulang untuk target tersebut.

## 4. Persiapan di laptop tujuan

1. Pasang/aktifkan WSL2 dan Ubuntu 24.04 x86-64.
2. Pastikan Python 3, `python3-venv`, `python3-pip`, `curl`, dan library sistem yang dibutuhkan tersedia.
3. Pindahkan seluruh folder `GenoSphere` ke lokasi yang diinginkan.
4. Dari WSL, masuk ke folder backend dan jalankan:

```bash
cd /mnt/d/GenoSphere/backend
bash scripts/check_environment.sh
bash scripts/bootstrap.sh --offline
bash scripts/start_ollama.sh
bash scripts/check_environment.sh
```

Gunakan `bash scripts/bootstrap.sh` tanpa `--offline` bila wheelhouse tidak lengkap dan laptop tujuan memiliki internet.

## 5. Prasyarat sistem yang tidak boleh diasumsikan ikut terbawa

| Komponen | Cara penanganan |
|---|---|
| WSL2 + Ubuntu | Harus tersedia pada Windows laptop tujuan. Tidak disediakan oleh folder backend. |
| Python 3 + venv | Sediakan pada Ubuntu; `.venv` dibuat ulang lewat `bootstrap.sh`. |
| Library dasar Linux (glibc, libstdc++, OpenMP, dll.) | Harus kompatibel dengan distro target. Jangan menyalin library inti OS secara manual. |
| Driver GPU | Dikelola pada host/WSL. Opsional untuk percepatan AI; CPU dapat lebih lambat. |
| PostgreSQL | Belum bisa dipastikan dari audit yang dikirim. Bila proyek memerlukannya, tentukan apakah server lokal, Docker, atau server eksternal; siapkan schema/migrations dan backup data secara terpisah. |
| Environment `.env` | Buat dari `.env.example`; isi URL database, secret, dan konfigurasi lokal pada tiap laptop. Jangan memasukkan secret ke repository/zip. |
| Folder data pengguna | Pindahkan secara eksplisit dan verifikasi hak akses serta checksum. |

Jika `requirements.txt` mengandung package yang memerlukan kompilasi native, wheelhouse mungkin memerlukan compiler/header tambahan pada OS target. Skrip bootstrap akan menampilkan error pip yang sebenarnya; jangan menyamarkan error tersebut.

## 6. Menjalankan Ollama dari bundle proyek

`start_ollama.sh` menjalankan binary yang ada di `tools/ollama/bin/ollama` dengan:

- `OLLAMA_MODELS=<backend>/tools/ollama/models`
- `OLLAMA_HOST=127.0.0.1:11434`

Agar tidak salah memakai model dari instalasi global, skrip menolak start jika port 11434 sudah dipakai. Hentikan service Ollama sistem yang lama terlebih dahulu bila diperlukan. Proses yang dimulai skrip disimpan di `.run/ollama.pid`; hentikan dengan `bash scripts/stop_ollama.sh`.

## 7. Integrasi backend

Backend sebaiknya tidak memanggil `plink2`, `gemma`, atau `ollama` berdasarkan asumsi bahwa tool sudah masuk `PATH`. Gunakan path absolut yang dihitung dari direktori proyek, misalnya:

- `Path(__file__).resolve().parents[...] / "tools" / "bin" / "plink2"`
- `Path(__file__).resolve().parents[...] / "tools" / "bin" / "gemma"`
- Ollama API: `http://127.0.0.1:11434`
- Model: `qwen3:1.7b`

Path final perlu disesuaikan dengan lokasi file konfigurasi/runner di source code GenoSphere. Jangan hardcode `/mnt/d/GenoSphere` karena laptop tujuan dapat menyimpan proyek di direktori berbeda.

## 8. Integritas dan ukuran

`tools/SHA256SUMS` memeriksa apakah file bundle berubah/rusak setelah dipindahkan. Setelah proses packaging di laptop sumber, simpan output `du -sh tools` sebagai acuan ukuran. Model dan runtime Ollama dapat membuat folder `tools/` berukuran beberapa GB.

Jika memakai Git, jangan commit `.env`, `.venv`, atau `.run`. Folder `tools/` dalam kasus ini memang harus ikut dibawa saat Anda memindahkan folder proyek secara langsung, tetapi model/runtime berukuran besar dapat melampaui batas hosting Git biasa. Untuk pengiriman antarlaptop, gunakan arsip/penyimpanan file yang mampu menampung folder `tools/` secara utuh atau Git LFS sesuai kebijakan proyek.

## 9. Batasan saat ini

Skrip ini menyiapkan dan memvalidasi bundle tool yang terlihat dalam audit. Ini belum membuktikan bahwa seluruh aplikasi GenoSphere siap dijalankan: pemeriksaan dependensi Python penuh, database, migrasi, konfigurasi `.env`, pipeline PLINK2/GEMMA, izin file di DrvFs `/mnt/d`, dan integrasi runner aplikasi tetap harus diuji pada project sebenarnya.
