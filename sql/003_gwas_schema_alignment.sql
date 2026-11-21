-- GenoSphere-GWAS | Migrasi 003: penyelarasan skema untuk mesin GWAS.
-- Aman dijalankan berulang (idempotent). Hanya MENAMBAH kolom/indeks dan MELEBARKAN tipe; tidak ada
-- tabel, kolom, atau relasi yang dihapus/diganti nama, sehingga kode dan data lama tetap berlaku.
-- Jalankan:  psql -U postgres -h localhost -d gwas -f sql/003_gwas_schema_alignment.sql
--
-- (1) gwas_result.p_value & effect_size: NUMERIC(15,10) -> DOUBLE PRECISION.
--     ALASAN (terbukti): NUMERIC(15,10) hanya menyimpan 10 angka di belakang koma, sehingga p-value
--     1e-12 atau 3.2e-20 tersimpan sebagai 0 dan SNP paling signifikan hilang dari Manhattan Plot
--     (-log10(0) tak hingga). Nilai lama dikonversi tanpa kehilangan data.
-- (2) gwas_result: tambah se dan p_value_adjusted (SDP 4.3.3 Tabel 12) untuk Bonferroni/FDR (FR-17).
-- (3) gwas_analysis: tambah kolom pencatatan sesi (SDP 4.3.3 Tabel 11, SKPL NFR-07 reproduksibilitas,
--     FR-16 status/error, FR-19 riwayat). Semua nullable kecuali created_at (berdefault NOW()).
-- (4) Indeks gwas_result(analysis_id): hampir semua query hasil/Manhattan/ekspor memfilter per analisis.

DO $$
BEGIN
    IF to_regclass('public.gwas_result') IS NULL OR to_regclass('public.gwas_analysis') IS NULL THEN
        RAISE EXCEPTION 'Tabel gwas_result/gwas_analysis belum ada. Jalankan schema_snapshot_before_003.sql dulu (database kosong).';
    END IF;

    -- (1) tipe numerik
    IF (SELECT data_type FROM information_schema.columns
        WHERE table_schema='public' AND table_name='gwas_result' AND column_name='p_value') = 'numeric' THEN
        ALTER TABLE public.gwas_result ALTER COLUMN p_value TYPE double precision USING p_value::double precision;
    END IF;
    IF (SELECT data_type FROM information_schema.columns
        WHERE table_schema='public' AND table_name='gwas_result' AND column_name='effect_size') = 'numeric' THEN
        ALTER TABLE public.gwas_result ALTER COLUMN effect_size TYPE double precision USING effect_size::double precision;
    END IF;
END
$$;

-- (2)
ALTER TABLE public.gwas_result
    ADD COLUMN IF NOT EXISTS se double precision,
    ADD COLUMN IF NOT EXISTS p_value_adjusted double precision;

-- (3)
ALTER TABLE public.gwas_analysis
    ADD COLUMN IF NOT EXISTS target_trait        varchar(100),
    ADD COLUMN IF NOT EXISTS genotype_file_path  varchar(255),
    ADD COLUMN IF NOT EXISTS phenotype_file_path varchar(255),
    ADD COLUMN IF NOT EXISTS params              jsonb,
    ADD COLUMN IF NOT EXISTS error_message       text,
    ADD COLUMN IF NOT EXISTS pipeline_version    varchar(100),
    ADD COLUMN IF NOT EXISTS created_at          timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS finished_at         timestamptz;

-- (4)
CREATE INDEX IF NOT EXISTS idx_gwas_result_analysis_id ON public.gwas_result (analysis_id);
