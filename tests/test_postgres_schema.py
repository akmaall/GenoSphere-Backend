"""Uji integrasi dengan PostgreSQL SUNGGUHAN. Dilewati kecuali TEST_POSTGRES_URL diisi.

PERINGATAN: tes ini MENGHAPUS dan membuat ulang schema public. Karena itu hanya mau berjalan pada database
yang namanya berakhiran '_test'. Contoh:
    createdb -U postgres -h localhost gwas_test
    TEST_POSTGRES_URL='postgresql+psycopg2://postgres:PASSWORD@localhost:5432/gwas_test' pytest tests/test_postgres_schema.py -q
"""
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.database import Base
from app.models import Accession, GwasAnalysis, GwasResult, Snp, User

URL = os.environ.get("TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_POSTGRES_URL tidak diisi")
SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


@pytest.fixture(scope="module")
def engine():
    assert make_url(URL).database.endswith("_test"), "Database uji harus berakhiran _test (tes ini menghapus schema public)"
    eng = create_engine(URL)
    raw = eng.raw_connection()  # koneksi DBAPI sendiri: snapshot mengosongkan search_path pada sesinya
    cur = raw.cursor()
    cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    cur.execute((SQL_DIR / "schema_snapshot_before_003.sql").read_text())
    raw.commit()
    raw.close()
    eng.dispose()  # buang koneksi berpool yang search_path-nya sudah kosong

    eng = create_engine(URL)
    raw = eng.raw_connection()
    cur = raw.cursor()
    # Jalur database tim (LKP 8): snapshot -> 003_gwas_module -> 004_user_profile; 004 dijalankan dua kali (idempoten).
    cur.execute((SQL_DIR / "003_gwas_module.sql").read_text())
    migration = (SQL_DIR / "004_user_profile.sql").read_text()
    cur.execute(migration)
    cur.execute(migration)
    raw.commit()
    raw.close()
    yield eng
    eng.dispose()


def test_orm_columns_match_real_database(engine):
    insp = inspect(engine)
    for name, table in Base.metadata.tables.items():
        assert {c["name"] for c in insp.get_columns(name)} == {c.name for c in table.columns}, name


def test_extra_columns_from_alignment_variant_do_not_break_orm(engine):
    """Tim yang menjalankan 003_gwas_schema_alignment.sql punya kolom ekstra (params, se, dst.): ORM tetap harus cocok (subset)."""
    raw = engine.raw_connection()
    raw.cursor().execute((SQL_DIR / "003_gwas_schema_alignment.sql").read_text())
    raw.commit()
    raw.close()
    insp = inspect(engine)
    for name, table in Base.metadata.tables.items():
        assert {c.name for c in table.columns} <= {c["name"] for c in insp.get_columns(name)}, name


def test_tiny_p_values_survive_in_postgres(engine):
    with Session(engine) as db:
        u = User(full_name="Uji", email="pg@x.id", password_hash="h")
        a, s = Accession(sample_code="IND1"), Snp(rsid="rs1", chromosome="1", position=1)
        db.add_all([u, a, s])
        db.flush()
        an = GwasAnalysis(accession_id=a.accession_id, user_id=u.user_id)
        db.add(an)
        db.flush()
        db.add_all([GwasResult(analysis_id=an.analysis_id, snp_id=s.snp_id, p_value=p) for p in (5e-8, 1e-12, 3.2e-20)])
        db.commit()
        got = sorted(r[0] for r in db.execute(text("SELECT p_value FROM gwas_result")))
        assert got == [3.2e-20, 1e-12, 5e-8]
        db.execute(text("DELETE FROM accession"))  # ON DELETE CASCADE ke analisis dan hasil tetap bekerja
        db.commit()
        assert db.execute(text("SELECT count(*) FROM gwas_result")).scalar() == 0
