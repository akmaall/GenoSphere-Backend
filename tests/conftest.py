"""Pengaturan bersama untuk semua tes.

Tes memakai SQLite di memori (cepat, tanpa PostgreSQL) dan folder penyimpanan sementara, jadi tidak menyentuh data asli.
Pengujian khusus PostgreSQL ada di test_postgres.py dan hanya jalan bila TEST_POSTGRES_URL diisi.
"""
import os
import shutil
import tempfile

# Harus diatur SEBELUM aplikasi diimpor, karena pengaturan dibaca saat impor.
_STORAGE = tempfile.mkdtemp(prefix="genosphere-test-")
os.environ["SECRET_KEY"] = "kunci-rahasia-khusus-pengujian-minimal-32-karakter"
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["STORAGE_DIR"] = _STORAGE
os.environ["AI_API_KEY"] = ""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.database as db_module
from app.database import Base, get_db

# Engine tes global. StaticPool dan check_same_thread=False membuat semua thread memakai satu database memori yang sama,
# termasuk thread tugas latar belakang yang menjalankan analisis.
test_engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
TestingSession = sessionmaker(bind=test_engine, autoflush=False, expire_on_commit=False)
db_module.engine = test_engine
db_module.SessionLocal = TestingSession

from app.main import app  # noqa: E402  (diimpor setelah engine diganti)


@pytest.fixture(autouse=True)
def setup_database():
    """Membuat tabel baru dan folder penyimpanan kosong sebelum setiap tes."""
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    shutil.rmtree(_STORAGE, ignore_errors=True)
    os.makedirs(_STORAGE, exist_ok=True)
    yield


@pytest.fixture()
def client():
    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def pytest_sessionfinish(session, exitstatus):
    try:
        from app.services import ai_jobs
        executor = ai_jobs._executor

        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
            ai_jobs._executor = None
    finally:
        shutil.rmtree(_STORAGE, ignore_errors=True)
