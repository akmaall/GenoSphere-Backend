"""Pengaturan aplikasi.

Semua nilai dibaca dari variabel lingkungan atau berkas .env di folder backend. Tidak ada
kata sandi atau kunci rahasia yang ditulis di dalam kode. Berkas .env.example memberi contoh isinya.
"""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

# Satu tempat untuk nomor versi. Dipakai oleh /health, Swagger, dan berkas ekspor konfigurasi.
APP_VERSION = "1.0.0"
# Dicatat pada setiap analisis agar hasilnya bisa ditelusuri ke versi alur kerja yang sama.
PIPELINE_VERSION = "genosphere-pipeline-1.0"


class Settings(BaseSettings):
    """Semua pengaturan aplikasi. Nilainya dibaca dari variabel lingkungan atau berkas .env."""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database. DATABASE_URL (opsional) menimpa kelima nilai di bawahnya, terutama dipakai saat pengujian.
    database_url: str | None = None
    postgres_user: str = "postgres"
    postgres_password: str = ""
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "gwas"

    # Token login (JWT). SECRET_KEY wajib diisi minimal 32 karakter; tanpa itu aplikasi menolak menyala.
    secret_key: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60

    # Pemulihan kata sandi. Tautan di email mengarah ke FRONTEND_URL/reset-password?token=...
    frontend_url: str = "http://localhost:3000"
    reset_token_expire_minutes: int = 30
    # Bila SMTP_HOST kosong (mode pengembangan), tautan pemulihan hanya dicetak di log server.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "no-reply@genosphere.local"
    smtp_starttls: bool = True

    # Penyimpanan berkas dan alat analisis.
    storage_dir: str = "uploads"           # folder berkas pengguna dan hasil analisis (jangan di-commit)
    plink2_bin: str = "plink2"             # nama atau lokasi lengkap program PLINK 2
    gemma_bin: str = "gemma"               # nama atau lokasi lengkap program GEMMA
    max_upload_mb: int = 2048              # batas ukuran satu berkas unggahan (MiB)
    max_dataset_upload_mb: int = 6144      # batas total dataset, meliputi seluruh berkas genotipe + fenotipe
    upload_disk_reserve_mb: int = 1024     # ruang disk yang tidak boleh dipakai unggahan
    max_concurrent_jobs: int = 2           # batas administrasi maksimum analisis aktif
    analysis_memory_reserve_mb: int = 512  # RAM yang disisakan untuk OS/API/database
    analysis_memory_per_job_mb: int = 2048 # estimasi RAM dasar per job; disesuaikan dengan kapasitas aktual
    analysis_disk_reserve_mb: int = 1024   # ruang disk yang disisakan untuk sistem
    pca_fallback_memory_limit_mb: int = 1024
    pca_fallback_memory_fraction: float = 0.35
    tool_timeout_seconds: int = 6 * 3600   # batas waktu satu perintah PLINK/GEMMA sebelum dihentikan
    max_plot_points: int = 100_000         # batas titik grafik Manhattan dan QQ sebelum disederhanakan

    # Interpretasi statistik AI (opsional; GWAS tetap berfungsi tanpa provider/API key).
    ai_provider: str = "openai_compatible"
    ai_api_base_url: str = "https://api.openai.com/v1"
    ai_api_key: str | None = None
    ai_model: str = "gpt-4.1-mini"
    ai_timeout_seconds: int = 240
    ai_job_deadline_seconds: int = 360
    ai_max_output_tokens: int = 512
    ai_max_attempts: int = 1
    ai_top_snp_count: int = 50
    ai_max_concurrent_jobs: int = 2

    # CORS: alamat frontend yang boleh memanggil API ini. Di .env ditulis sebagai daftar JSON.
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    # Pola tambahan supaya Next.js yang pindah ke port 3001, 3002, dst. tetap diizinkan.
    # Kosongkan (CORS_ORIGIN_REGEX=) di server produksi.
    cors_origin_regex: str | None = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"

    app_version: str = APP_VERSION

    @property
    def sqlalchemy_url(self) -> str | URL:
        """Alamat koneksi database untuk SQLAlchemy. Memakai DATABASE_URL bila diisi, bila tidak, disusun dari kelima nilai POSTGRES_*."""
        if self.database_url:
            return self.database_url
        # URL.create menangani karakter khusus di kata sandi (@, :, /, dan sebagainya).
        return URL.create(
            "postgresql+psycopg2",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    """Mengembalikan satu objek pengaturan yang dipakai bersama. Dibaca sekali saja lalu disimpan."""
    return Settings()
