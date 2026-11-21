-- GenoSphere-GWAS | Migrasi 003: kesiapan tabel hasil untuk modul GWAS
-- Jalankan SETELAH 001 dan 002:  psql -U postgres -h localhost -d gwas -f sql/003_gwas_module.sql
-- Aman dijalankan berulang (idempotent).
--
-- 1) WAJIB: gwas_result.p_value dan effect_size pada ERD LKP 6 bertipe NUMERIC(15,10). Tipe ini hanya menyimpan
--    10 angka di belakang koma, sehingga p-value < 1e-10 (justru hasil GWAS terkuat) tersimpan sebagai 0.
--    SDP 4.3.3 sendiri mendefinisikan keduanya FLOAT. Diubah ke DOUBLE PRECISION (nilai lama tidak berubah).
-- 2) Indeks: hanya mempercepat query (Manhattan, tabel signifikan, ekspor); tidak mengubah struktur ERD.
DO $$
BEGIN
    IF to_regclass('public.gwas_result') IS NOT NULL THEN
        ALTER TABLE public.gwas_result ALTER COLUMN p_value     TYPE DOUBLE PRECISION USING p_value::double precision;
        ALTER TABLE public.gwas_result ALTER COLUMN effect_size TYPE DOUBLE PRECISION USING effect_size::double precision;
        CREATE INDEX IF NOT EXISTS idx_gwas_result_analysis   ON public.gwas_result (analysis_id);
        CREATE INDEX IF NOT EXISTS idx_gwas_result_analysis_p ON public.gwas_result (analysis_id, p_value);
    ELSE
        RAISE NOTICE 'Tabel gwas_result belum ada; bagian 1 dan indeks hasil dilewati.';
    END IF;

    IF to_regclass('public.snp') IS NOT NULL THEN
        CREATE INDEX IF NOT EXISTS idx_snp_chrom_pos ON public.snp (chromosome, position);
    END IF;
    IF to_regclass('public.phenotype') IS NOT NULL THEN
        CREATE INDEX IF NOT EXISTS idx_phenotype_accession ON public.phenotype (accession_id);
    END IF;
END
$$;
