"""Tes khusus PostgreSQL. Dilewati otomatis bila TEST_POSTGRES_URL tidak diisi.

Contoh menjalankan (database harus kosong atau boleh dihapus isinya, JANGAN pakai database kerja Anda):

    TEST_POSTGRES_URL=postgresql+psycopg2://postgres:password@localhost:5432/gwas_test pytest tests/test_postgres.py

Yang diuji: skrip sql/schema.sql cocok dengan model Python, p-value sangat kecil tidak dibulatkan, dan perhitungan
q-value Benjamini-Hochberg, Manhattan, serta QQ memberi hasil yang sama di PostgreSQL seperti di SQLite.
"""
import math
import os
import random
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, insert, text
from sqlalchemy.orm import sessionmaker

from app.models import Accession, GwasAnalysis, GwasResult, Snp
from app.services import results as R

URL = os.environ.get("TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_POSTGRES_URL tidak diisi")
SCHEMA = Path(__file__).resolve().parent.parent / "sql" / "schema.sql"


@pytest.fixture()
def pg():
    engine = create_engine(URL)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute(SCHEMA.read_text(encoding="utf-8"))
        raw.commit()
    finally:
        raw.close()
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()
    engine.dispose()


def _analysis(db, pvalues):
    acc = Accession(sample_code="pg", genotype_file_path="u1/ds_1/genotype")
    db.add(acc)
    db.flush()
    a = GwasAnalysis(accession_id=acc.accession_id, status="done", model_used="Linear Regression")
    db.add(a)
    db.flush()
    n = len(pvalues)
    db.execute(insert(Snp), [{"snp_id": i + 1, "rsid": f"rs{i}", "chromosome": str(1 + i % 3), "position": 1000 + i, "ref_allele": "A", "alt_allele": "G"} for i in range(n)])
    db.execute(insert(GwasResult), [{"analysis_id": a.analysis_id, "snp_id": i + 1, "p_value": p, "effect_size": 0.1, "is_significant": False} for i, p in enumerate(pvalues)])
    db.commit()
    return a.analysis_id


def test_schema_sql_matches_python_models(pg):
    from app.database import Base
    insp = inspect(pg.get_bind())
    for table in Base.metadata.sorted_tables:
        assert insp.has_table(table.name), table.name
        assert {c.name for c in table.columns} <= {c["name"] for c in insp.get_columns(table.name)}, table.name
    cols = {c["name"]: str(c["type"]) for c in insp.get_columns("gwas_result")}
    assert "DOUBLE" in cols["p_value"] and "DOUBLE" in cols["effect_size"]


def test_schema_sql_can_run_twice(pg):
    raw = pg.get_bind().raw_connection()
    with raw.cursor() as cur:
        cur.execute(SCHEMA.read_text(encoding="utf-8"))
    raw.commit()
    raw.close()


def test_tiny_p_values_are_not_rounded_to_zero(pg):
    aid = _analysis(pg, [1e-30, 3e-15, 0.5])
    got = sorted(p for (p,) in pg.execute(text("SELECT p_value FROM gwas_result WHERE analysis_id = :a"), {"a": aid}))
    assert got == [1e-30, 3e-15, 0.5]


def test_benjamini_hochberg_in_postgres_matches_reference(pg):
    rnd = random.Random(21)
    ps = [rnd.random() ** 3 for _ in range(500)]
    aid = _analysis(pg, ps)
    n = len(ps)
    order = sorted(range(n), key=lambda i: (ps[i], i + 1))
    raw = [ps[i] * n / (k + 1) for k, i in enumerate(order)]
    adj, run = [0.0] * n, 1.0
    for k in range(n - 1, -1, -1):
        run = min(run, raw[k])
        adj[k] = min(1.0, run)
    q = R.bh_qvalues(pg, aid, 10, 25)
    assert sorted(q) == list(range(11, 36))
    for rank, val in q.items():
        assert math.isclose(val, adj[rank - 1], rel_tol=1e-9)
    rule = R.apply_significance(pg, aid, "fdr", 0.05)
    assert rule["n_significant"] == sum(1 for a in adj if a <= 0.05)


def test_results_queries_work_on_postgres(pg):
    rnd = random.Random(2)
    ps = [rnd.random() for _ in range(3000)]
    aid = _analysis(pg, ps)
    R.apply_significance(pg, aid, "custom", 0.01)
    rule = R.build_rule(pg, aid, "custom", 0.01)
    m = R.manhattan(pg, aid, 1000, rule)
    assert m["all_significant_displayed"] is True and m["displayed_variants"] <= 1000
    q = R.qq(pg, aid, 1000)
    assert q["observed_p"] == sorted(q["observed_p"]) and q["displayed_variants"] <= 1000 and 0.5 < q["lambda_gc"] < 1.5
    assert R.summary(pg, aid, rule, False)["n_variants_tested"] == 3000
    assert len(R.top(pg, aid, 5, rule, False)["items"]) == 5
    assert R.significant(pg, aid, 10, 0, None, rule, False)["total"] == sum(1 for p in ps if p <= 0.01)
