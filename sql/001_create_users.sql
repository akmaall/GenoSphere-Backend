-- GenoSphere-GWAS | Migrasi 001: tabel akun untuk autentikasi (/api/register, /api/login)
-- Jalankan di database GWAS:  psql -U postgres -h localhost -d gwas -f sql/001_create_users.sql

CREATE TABLE IF NOT EXISTS users (
    user_id       SERIAL        PRIMARY KEY,
    full_name     VARCHAR(100)  NOT NULL,
    email         VARCHAR(255)  NOT NULL UNIQUE,
    username      VARCHAR(50)   UNIQUE,
    password_hash VARCHAR(255)  NOT NULL,
    is_active     BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    CONSTRAINT users_email_lowercase    CHECK (email = lower(email)),
    CONSTRAINT users_username_lowercase CHECK (username IS NULL OR username = lower(username))
);

-- Penautan ke gwas_analysis.user_id ada di 002_link_gwas_analysis_user.sql
