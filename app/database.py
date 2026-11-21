"""Koneksi database (SQLAlchemy). `get_db` memberi satu sesi database untuk satu permintaan lalu menutupnya setelah selesai."""
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

engine = create_engine(get_settings().sqlalchemy_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """Kelas dasar semua model tabel (lihat models.py)."""
    pass


def get_db() -> Iterator[Session]:
    """Memberi satu sesi database untuk satu permintaan, lalu menutupnya setelah permintaan selesai."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
