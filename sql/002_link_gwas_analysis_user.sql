-- GenoSphere-GWAS | Migrasi 002: tautkan gwas_analysis.user_id ke users.user_id
-- Dasar: SDP 4.3.3 (gwas_analysis.user_id INT, "referensi ke modul akun"), Proposal (tabel GWAS Analysis memuat user_id),
--        SKPL BR-06 (pengguna hanya melihat/mengelola/mengekspor hasil milik akunnya sendiri).
-- Aman dijalankan berulang (idempotent) dan tidak mengubah kolom lain di gwas_analysis.
-- Jalankan SETELAH 001:  psql -U postgres -h localhost -d gwas -f sql/002_link_gwas_analysis_user.sql
--
-- Jika ALTER di bawah gagal dengan "violates foreign key constraint", berarti ada baris uji lama dengan
-- user_id yang belum ada di users. Bersihkan dulu data uji:  UPDATE gwas_analysis SET user_id = NULL;

DO $$
BEGIN
    IF to_regclass('public.gwas_analysis') IS NULL THEN
        RAISE NOTICE 'Tabel gwas_analysis belum ada, migrasi 002 dilewati. Jalankan ulang setelah skema ERD dibuat.';
        RETURN;
    END IF;

    ALTER TABLE public.gwas_analysis ADD COLUMN IF NOT EXISTS user_id INTEGER;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.gwas_analysis'::regclass AND conname = 'gwas_analysis_user_id_fkey'
    ) THEN
        ALTER TABLE public.gwas_analysis
            ADD CONSTRAINT gwas_analysis_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users (user_id);
    END IF;

    -- Hampir semua query hasil/riwayat akan memfilter berdasarkan pemilik (BR-06).
    CREATE INDEX IF NOT EXISTS idx_gwas_analysis_user_id ON public.gwas_analysis (user_id);
END
$$;
