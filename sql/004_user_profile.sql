-- GenoSphere-GWAS | Migrasi 004: kolom profil pengguna untuk halaman /profile frontend
-- (Nama Lengkap, Username, Email, Institusi, Bio / Bidang Penelitian; LKP 8 minggu 8).
-- Aman dijalankan berulang. Jalankan SETELAH 001:  psql -U postgres -h localhost -d gwas -f sql/004_user_profile.sql
ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS institution VARCHAR(150),
    ADD COLUMN IF NOT EXISTS bio         TEXT;
