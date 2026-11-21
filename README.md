# GenoSphere-GWAS Backend: autentikasi, pipeline GWAS, dan interpretasi statistik opsional

> **Dokumentasi:** baca `docs/03_KONTRAK_API_UNTUK_FRONTEND.md`, `docs/03_INTEGRASI_FRONTEND.md`, dan `docs/REVISI_BACKEND_2026-11.md`.
> Untuk database yang sudah memakai skema versi lama, jalankan migrasi yang belum diterapkan secara berurutan, termasuk `sql/005_gwas_interpretation.sql`. Instalasi baru dapat memakai `sql/schema.sql`. Jangan menjalankan migrasi lama secara acak pada database produksi; cocokkan dulu dengan riwayat skema yang sudah diterapkan.

FastAPI + PostgreSQL + JWT (OAuth2 password flow, bearer token).

## Menjalankan (WSL Ubuntu)

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # lalu isi POSTGRES_PASSWORD dan SECRET_KEY (openssl rand -hex 32)
psql -U postgres -h localhost -d gwas -f sql/001_create_users.sql
psql -U postgres -h localhost -d gwas -f sql/002_link_gwas_analysis_user.sql   # setelah skema ERD (gwas_analysis) ada
uvicorn app.main:app --reload --port 8000
```

Swagger UI: http://localhost:8000/docs (tombol **Authorize** memakai /api/login).

Jika `psql -d GWAS` menjawab *database "GWAS" does not exist*, database dibuat tanpa tanda kutip sehingga namanya `gwas` (huruf kecil). Isi `POSTGRES_DB=gwas` di `.env` dan pakai `-d gwas`.

## Endpoint

| Method | Path | Keterangan |
|---|---|---|
| POST | /api/register | JSON: `full_name`/`fullName`, `email`, `username` (opsional; bila kosong dibuat dari email), `password`, `confirm_password`/`confirmPassword`, `institution`, `bio` (opsional). 201 + data user |
| POST | /api/login | **form-urlencoded**: `username` (email atau username), `password`. 200 + `access_token` |
| POST | /api/forgot-password | JSON: `email`. Selalu 202 dengan pesan yang sama (email terdaftar atau tidak). Tautan dikirim lewat email |
| POST | /api/reset-password | JSON: `token`, `password`, `confirm_password`. 200 bila berhasil. Token berlaku 30 menit dan hanya bisa dipakai sekali |
| GET | /api/me | Wajib header `Authorization: Bearer <token>`. Memuat `institution`, `bio` |
| PATCH | /api/me | JSON: `full_name`/`fullName`, `institution`, `bio` (hanya yang dikirim diubah) |
| GET | /health | Cek API dan koneksi database |

## Kode error (`detail.code`)

| Status | Code | Kapan |
|---|---|---|
| 401 | INVALID_CREDENTIALS | email/username atau password salah |
| 401 | TOKEN_MISSING | header Authorization tidak ada |
| 401 | TOKEN_EXPIRED | token lewat masa berlaku, frontend arahkan ke login |
| 401 | TOKEN_INVALID | token rusak, dimodifikasi, atau user sudah tidak ada |
| 400 | RESET_TOKEN_INVALID / RESET_TOKEN_EXPIRED | tautan pemulihan rusak, sudah dipakai, atau kedaluwarsa |
| 403 | USER_INACTIVE | akun dinonaktifkan |
| 409 | EMAIL_ALREADY_REGISTERED / USERNAME_ALREADY_TAKEN | data sudah dipakai |
| 404 | RESOURCE_NOT_FOUND | data bukan milik akun yang login (lihat `ensure_owner`) |
| 422 | VALIDATION_ERROR | input tidak memenuhi aturan; rinciannya di `detail.errors[]` |

Contoh 422 (bentuknya sama dengan error lain, pesan berbahasa Indonesia, nilai yang dikirim tidak dipantulkan kembali):

```json
{"detail": {"code": "VALIDATION_ERROR",
            "message": "Data yang dikirim belum valid. Periksa kembali isian Anda.",
            "errors": [{"field": "password", "message": "Kata sandi minimal 8 karakter."}]}}
```

## Melindungi endpoint lain

```python
from app.deps import get_current_user
@router.get("/api/analyses")
def list_analyses(user: User = Depends(get_current_user)): ...
```

Pemisahan data antarpengguna (SKPL BR-06): saring query dengan `GwasAnalysis.user_id == user.user_id`, atau untuk satu baris panggil `ensure_owner(analysis.user_id, user)` (dari `app.deps`), yang menjawab 404 bila bukan pemilik.

## Testing

```bash
pytest -q
```

Sebagian tes integrasi membutuhkan executable PLINK2/GEMMA atau PostgreSQL yang sesuai. Jumlah lulus/gagal/skip yang benar untuk lingkungan saat ini dicatat pada `docs/REVISI_BACKEND_2026-11.md`; jangan menganggap seluruh tes integrasi akan berjalan hanya dengan SQLite.

## Pemulihan kata sandi

Tautan dikirim ke `FRONTEND_URL/reset-password?token=...`, jadi frontend membutuhkan satu halaman `reset-password` yang membaca `token` dari URL lalu memanggil `POST /api/reset-password` (halaman ini belum ada di LKP 6, hanya halaman "Kirim Tautan Pemulihan"). Tanpa `SMTP_HOST`, tautan hanya dicetak di log uvicorn (cukup untuk pengembangan; jangan dipakai di produksi).

## Catatan

- **gwas_analysis.user_id (SKPL BR-06).** `sql/002_...` menautkan analisis ke pemiliknya, tetapi ERD final di LKP 6 belum memuat kolom ini. Jalankan hanya setelah SA menyetujui; 001 saja sudah cukup untuk login/register.
- **Peran Administrator dan kolaborator** (SKPL NFR-03/AC-N3) belum ada di ERD final maupun wireframe, sehingga belum dibangun.
- **WebSocket progres (Step 7).** Browser tidak bisa mengirim header `Authorization` pada WebSocket; saat endpoint itu dibuat, token perlu dikirim lewat query string atau pesan pertama.


## Modul GWAS

Unggah data, validasi, konfigurasi, eksekusi pipeline (QC, PCA, PLINK 2 / GEMMA), hasil, dan ekspor sudah tersedia di `/api/gwas/...`.
Penjelasan alur melalui Swagger dan contoh integrasi frontend ada di **`docs/PANDUAN_MODUL_GWAS.md`** serta kontrak API. Untuk database existing, terapkan migrasi yang belum dijalankan sesuai kondisi skema; untuk instalasi baru, gunakan `sql/schema.sql`.

## Endpoint tambahan modul GWAS

| Method | Path | Keterangan |
|---|---|---|
| POST | /api/login/json | Login JSON `{identifier, password}` (form OAuth2 `/api/login` tetap ada) |
| GET | /api/gwas/config/options | Opsi dropdown (label UI <-> nilai API) |
| POST | /api/gwas/analyses/{id}/pca | Mode staged, Step 6: PCA + kovariat (menerima `"5 PC"`, `"Usia, Jenis Kelamin"`) |
| POST | /api/gwas/analyses/{id}/run | Mode staged, Step 7: pilih model, jalankan GWAS |
| DELETE | /api/gwas/datasets/{id}/files/{slot} | Hapus satu berkas dataset (tombol batal Step 1) |
| DELETE | /api/gwas/datasets/{id} | Hapus dataset + analisis + berkas |

Upload menerima satu berkas pada field multipart `file` per request. Untuk PLINK BED/BIM/FAM, unggah ketiga berkas secara berurutan dengan `dataset_id` yang sama. Endpoint tambahan untuk Data Usage Summary, ekspor data sumber Manhattan/QQ/PCA, ekspor QC, dan interpretasi AI dijelaskan pada kontrak API.


## Pembaruan skema database

Untuk backend existing yang telah menerapkan migrasi sebelumnya sampai skema GWAS dan profil pengguna, jalankan migrasi berikut satu kali:

```bash
psql -U postgres -d gwas -f sql/005_gwas_interpretation.sql
```

Migrasi ini hanya menambahkan tabel `gwas_interpretation` untuk antrean/hasil interpretasi statistik AI; tabel dan data GWAS yang sudah ada tidak dihapus atau diubah. `sql/schema.sql` juga memuat definisi tabel tersebut untuk instalasi baru.

## Interpretasi statistik AI (opsional)

Isi `AI_PROVIDER`, `AI_API_BASE_URL`, `AI_MODEL`, dan `AI_API_KEY` pada environment server untuk mengaktifkan interpretasi AI. API key hanya dibaca backend dan tidak pernah dikirim ke frontend. Bila key/provider tidak tersedia, status interpretasi menjadi `unavailable` dan hasil GWAS tetap berstatus `done`.


## Batas unggahan dan sumber daya

Batas per berkas (`MAX_UPLOAD_MB`), total dataset (`MAX_DATASET_UPLOAD_MB`), cadangan disk, estimasi RAM, dan concurrency dapat diatur melalui environment. Backend juga mendeteksi kapasitas host/cgroup bila tersedia. Estimasi resource adalah heuristik untuk admission control, bukan jaminan kebutuhan pasti PLINK2/GEMMA. Lihat `.env.example` dan `docs/REVISI_BACKEND_2026-11.md`.
