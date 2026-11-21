-- GenoSphere-GWAS: skema database lengkap (PostgreSQL 16).
--
-- Cara memakai, dari folder backend:
--     psql -U postgres -d gwas -f sql/schema.sql
--
-- Skrip ini AMAN DIJALANKAN BERULANG: tabel yang sudah ada tidak dibuat ulang dan datanya tidak dihapus.
-- Untuk memulai dari nol, kosongkan dulu database-nya:
--     DROP SCHEMA public CASCADE; CREATE SCHEMA public;
--
-- Tabel analisis mengikuti ERD tim (LKP 9, Gambar 1): accession, phenotype, snp, gwas_analysis, gwas_result.
-- Satu tabel tambahan, users, menyimpan akun untuk login.

-- Akun pengguna -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    user_id        SERIAL PRIMARY KEY,
    full_name      VARCHAR(100) NOT NULL,
    email          VARCHAR(255) NOT NULL UNIQUE,
    username       VARCHAR(50)  UNIQUE,
    password_hash  VARCHAR(255) NOT NULL,        -- hash argon2, bukan kata sandi asli
    is_active      BOOLEAN      NOT NULL DEFAULT TRUE,
    institution    VARCHAR(150),                 -- halaman profil: Institusi
    bio            TEXT,                         -- halaman profil: Bio / Bidang Penelitian
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- Dataset (satu baris = satu kohort sampel yang diunggah) --------------------------------
CREATE TABLE IF NOT EXISTS accession (
    accession_id        SERIAL PRIMARY KEY,
    sample_code         VARCHAR(100),            -- nama dataset yang tampil di layar
    organism            VARCHAR(100),
    source_domain       VARCHAR(100),            -- kesehatan | farmasi | agrikultur | peternakan
    genotype_file_path  VARCHAR(255)             -- mis. u3/ds_12/genotype ("u3" = pemilik: pengguna nomor 3)
);

-- Ringkasan trait tiap dataset (hasil validasi) -------------------------------------------
CREATE TABLE IF NOT EXISTS phenotype (
    phenotype_id  SERIAL PRIMARY KEY,
    accession_id  INTEGER NOT NULL REFERENCES accession(accession_id) ON DELETE CASCADE,
    trait_name    VARCHAR(100),
    trait_type    VARCHAR(50),                   -- binary | quantitative
    trait_value   VARCHAR(100)                   -- ringkasan singkat, mis. "n=120; mean=3.2; sd=1.1"
);

-- Sesi analisis --------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gwas_analysis (
    analysis_id    SERIAL PRIMARY KEY,
    user_id        INTEGER REFERENCES users(user_id),                      -- pemilik analisis (BR-06)
    accession_id   INTEGER NOT NULL REFERENCES accession(accession_id) ON DELETE CASCADE,
    analysis_mode  VARCHAR(50),                  -- manual | guided
    qc_skipped     BOOLEAN DEFAULT FALSE,
    pca_used       BOOLEAN DEFAULT FALSE,
    model_used     VARCHAR(50),                  -- Linear Regression | Logistic Regression | LMM (GEMMA)
    status         VARCHAR(50)                   -- queued | running | awaiting | done | failed
);

-- Daftar varian: satu varian unik disimpan sekali, dipakai ulang oleh banyak analisis ---------
CREATE TABLE IF NOT EXISTS snp (
    snp_id      SERIAL PRIMARY KEY,
    rsid        VARCHAR(100),
    chromosome  VARCHAR(50),
    position    INTEGER,
    ref_allele  VARCHAR(10),
    alt_allele  VARCHAR(10)
);

-- Hasil uji asosiasi per varian ------------------------------------------------------------
-- p_value dan effect_size memakai DOUBLE PRECISION, bukan NUMERIC(15,10), supaya p-value sekecil 1e-30
-- tidak dibulatkan menjadi 0.
CREATE TABLE IF NOT EXISTS gwas_result (
    result_id       SERIAL PRIMARY KEY,
    analysis_id     INTEGER NOT NULL REFERENCES gwas_analysis(analysis_id) ON DELETE CASCADE,
    snp_id          INTEGER NOT NULL REFERENCES snp(snp_id) ON DELETE CASCADE,
    p_value         DOUBLE PRECISION,
    effect_size     DOUBLE PRECISION,            -- BETA (linear, LMM) atau ln(odds ratio) (logistik)
    is_significant  BOOLEAN DEFAULT FALSE
);

-- Indeks: mempercepat pencarian varian, pengurutan p-value, dan Manhattan plot ----------------------
CREATE INDEX IF NOT EXISTS idx_snp_locus            ON snp (chromosome, position);
CREATE INDEX IF NOT EXISTS idx_result_analysis_p    ON gwas_result (analysis_id, p_value, result_id);
CREATE INDEX IF NOT EXISTS idx_result_analysis_sig  ON gwas_result (analysis_id, is_significant);
CREATE INDEX IF NOT EXISTS idx_result_snp           ON gwas_result (snp_id);
CREATE INDEX IF NOT EXISTS idx_analysis_user        ON gwas_analysis (user_id);
CREATE INDEX IF NOT EXISTS idx_analysis_accession   ON gwas_analysis (accession_id);
CREATE INDEX IF NOT EXISTS idx_phenotype_accession  ON phenotype (accession_id);


-- Interpretasi AI statistik (opsional, terisolasi dari status GWAS) -----------------------
CREATE TABLE IF NOT EXISTS gwas_interpretation (
    interpretation_id SERIAL PRIMARY KEY,
    analysis_id INTEGER NOT NULL UNIQUE REFERENCES gwas_analysis(analysis_id) ON DELETE CASCADE,
    status VARCHAR(24) NOT NULL DEFAULT 'queued',
    provider VARCHAR(80),
    model VARCHAR(120),
    prompt_version VARCHAR(100) NOT NULL DEFAULT 'statistical-v1',
    schema_version VARCHAR(40) NOT NULL DEFAULT '1.0',
    input_hash VARCHAR(64),
    result_json JSONB,
    error_code VARCHAR(80),
    error_message TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    token_usage JSONB,
    cost_usd DOUBLE PRECISION,
    queued_at TIMESTAMPTZ DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_gwas_interpretation_status ON gwas_interpretation(status, queued_at);
