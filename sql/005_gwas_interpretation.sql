-- Menambahkan antrean dan hasil interpretasi statistik AI tanpa mengubah tabel analisis GWAS.
CREATE TABLE IF NOT EXISTS gwas_interpretation (
    interpretation_id SERIAL PRIMARY KEY,
    analysis_id INTEGER NOT NULL UNIQUE REFERENCES gwas_analysis(analysis_id) ON DELETE CASCADE,
    status VARCHAR(24) NOT NULL DEFAULT 'queued',
    provider VARCHAR(80), model VARCHAR(120),
    prompt_version VARCHAR(100) NOT NULL DEFAULT 'statistical-v1',
    schema_version VARCHAR(40) NOT NULL DEFAULT '1.0',
    input_hash VARCHAR(64), result_json JSONB,
    error_code VARCHAR(80), error_message TEXT, attempts INTEGER NOT NULL DEFAULT 0,
    token_usage JSONB, cost_usd DOUBLE PRECISION,
    queued_at TIMESTAMPTZ DEFAULT now(), started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_gwas_interpretation_status ON gwas_interpretation(status, queued_at);
