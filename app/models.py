"""Pemetaan tabel database ke kelas Python (SQLAlchemy).

Tabel analisis mengikuti ERD tim (LKP 9, Gambar 1): accession, phenotype, snp, gwas_analysis, gwas_result.
Satu tabel tambahan, `users`, menyimpan akun untuk login. gwas_analysis.user_id menghubungkan analisis ke
pemiliknya (SDP 4.3.3 dan aturan bisnis BR-06). Skrip pembuat tabel yang sama ada di sql/schema.sql.
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, JSON, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    """Akun pengguna. Namanya `users` karena kata `user` dicadangkan oleh PostgreSQL."""

    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    full_name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String(50), unique=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    institution: Mapped[str | None] = mapped_column(String(150))   # halaman profil: Institusi
    bio: Mapped[str | None] = mapped_column(Text)                  # halaman profil: Bio / Bidang Penelitian
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Accession(Base):
    """Satu baris mewakili satu dataset yang diunggah pengguna (kohort sampel beserta berkas genotipenya).

    genotype_file_path berisi lokasi folder dataset relatif terhadap folder penyimpanan, contohnya
    "u3/ds_12/genotype". Awalan "u3/" menandai pemilik dataset (pengguna nomor 3). Cara ini dipakai karena
    ERD tidak punya kolom accession.user_id, dan kepemilikan tetap wajib ditegakkan (BR-06).
    """

    __tablename__ = "accession"

    accession_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sample_code: Mapped[str | None] = mapped_column(String(100))    # nama dataset yang tampil di layar
    organism: Mapped[str | None] = mapped_column(String(100))       # spesies, mis. manusia atau padi
    source_domain: Mapped[str | None] = mapped_column(String(100))  # kesehatan | farmasi | agrikultur | peternakan
    genotype_file_path: Mapped[str | None] = mapped_column(String(255))


class Phenotype(Base):
    """Ringkasan trait hasil validasi: satu baris untuk setiap kolom trait di berkas fenotipe.

    Nilai fenotipe per sampel tetap berada di berkas, bukan di tabel ini, karena ERD tidak punya kolom
    sampel pada tabel phenotype.
    """

    __tablename__ = "phenotype"

    phenotype_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    accession_id: Mapped[int] = mapped_column(Integer, ForeignKey("accession.accession_id", ondelete="CASCADE"), nullable=False)
    trait_name: Mapped[str | None] = mapped_column(String(100))
    trait_type: Mapped[str | None] = mapped_column(String(50))     # "binary" atau "quantitative"
    trait_value: Mapped[str | None] = mapped_column(String(100))   # ringkasan singkat, mis. "n=120; mean=3.2; sd=1.1"


class GwasAnalysis(Base):
    """Satu sesi analisis GWAS.

    status: queued (antre) -> running (berjalan) -> done (selesai) atau failed (gagal).
    Mode bertahap menambah satu status, awaiting, yaitu satu tahap sudah selesai dan sistem menunggu
    langkah berikutnya dari pengguna.
    """

    __tablename__ = "gwas_analysis"

    analysis_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.user_id"))   # pemilik analisis
    accession_id: Mapped[int] = mapped_column(Integer, ForeignKey("accession.accession_id", ondelete="CASCADE"), nullable=False)
    analysis_mode: Mapped[str | None] = mapped_column(String(50))   # "manual" atau "guided"
    qc_skipped: Mapped[bool | None] = mapped_column(Boolean, default=False)
    pca_used: Mapped[bool | None] = mapped_column(Boolean, default=False)
    model_used: Mapped[str | None] = mapped_column(String(50))      # Linear Regression | Logistic Regression | LMM (GEMMA)
    status: Mapped[str | None] = mapped_column(String(50))          # queued | running | awaiting | done | failed


class Snp(Base):
    """Daftar varian. Satu varian unik (kromosom, posisi, alel) disimpan sekali lalu dipakai ulang oleh banyak analisis."""

    __tablename__ = "snp"

    snp_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rsid: Mapped[str | None] = mapped_column(String(100))
    chromosome: Mapped[str | None] = mapped_column(String(50))
    position: Mapped[int | None] = mapped_column(Integer)
    ref_allele: Mapped[str | None] = mapped_column(String(10))
    alt_allele: Mapped[str | None] = mapped_column(String(10))


class GwasResult(Base):
    """Hasil uji asosiasi satu varian pada satu analisis.

    effect_size berisi BETA untuk regresi linear dan LMM, atau ln(odds ratio) untuk regresi logistik.
    p_value dan effect_size harus bertipe DOUBLE PRECISION agar p-value sekecil 1e-30 tidak dibulatkan
    menjadi 0 (tipe NUMERIC akan melakukannya). sql/schema.sql sudah memakai tipe yang benar.
    """

    __tablename__ = "gwas_result"

    result_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    analysis_id: Mapped[int] = mapped_column(Integer, ForeignKey("gwas_analysis.analysis_id", ondelete="CASCADE"), nullable=False)
    snp_id: Mapped[int] = mapped_column(Integer, ForeignKey("snp.snp_id", ondelete="CASCADE"), nullable=False)
    p_value: Mapped[float | None] = mapped_column(Float)
    effect_size: Mapped[float | None] = mapped_column(Float)
    is_significant: Mapped[bool | None] = mapped_column(Boolean, default=False)


class GwasInterpretation(Base):
    """Hasil interpretasi statistik eksternal yang terpisah dari status analisis GWAS."""
    __tablename__ = "gwas_interpretation"
    __table_args__ = (UniqueConstraint("analysis_id", name="uq_gwas_interpretation_analysis"),)

    interpretation_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    analysis_id: Mapped[int] = mapped_column(Integer, ForeignKey("gwas_analysis.analysis_id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    provider: Mapped[str | None] = mapped_column(String(80))
    model: Mapped[str | None] = mapped_column(String(120))
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False, default="statistical-v1")
    schema_version: Mapped[str] = mapped_column(String(40), nullable=False, default="1.0")
    input_hash: Mapped[str | None] = mapped_column(String(64))
    result_json: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_usage: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    cost_usd: Mapped[float | None] = mapped_column(Float)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
