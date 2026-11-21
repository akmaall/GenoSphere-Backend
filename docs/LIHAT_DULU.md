> Dokumen ini merujuk struktur integrasi versi sebelumnya. Untuk API backend terbaru, prioritaskan `03_KONTRAK_API_UNTUK_FRONTEND.md` dan `REVISI_BACKEND_2026-11.md`.

# GenoSphere-GWAS: catatan versi backend gabungan sebelumnya (historis)

Dasar: kode `backend.zip` Anda, ditambah fitur terbaik dari dua revisi (lihat `backend/docs/01_PERBANDINGAN_REVISI_DAN_KEPUTUSAN.md`).

1. `pip install -r requirements.txt` (menambah `openpyxl` untuk ekspor Excel).
2. `psql -U postgres -h localhost -d gwas -f sql/004_user_profile.sql` (satu kali; aman diulang; 001-003 sudah Anda jalankan).
3. Salin `.env.example` menjadi `.env` bila belum ada. **`.env` Anda tidak disertakan** (berisi rahasia). Tambahkan baris `CORS_ORIGIN_REGEX` dari `.env.example` bila perlu.
4. `python -m pytest -q`, lalu `uvicorn app.main:app --reload --port 8000`, cek `http://localhost:8000/health`.
5. Frontend: salin isi `frontend-integration/` (lihat `backend/docs/03_INTEGRASI_FRONTEND.md`).
