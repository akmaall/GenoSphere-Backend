"""Pengelola analisis di latar belakang (UC-03 langkah 3 sampai 5; NFR-01, NFR-07, NFR-08; FR-16).

Fungsinya menjalankan alur GWAS TANPA membuat permintaan pengguna menunggu, dan menjaga agar satu analisis yang gagal
tidak menjatuhkan server. Yang diurus di sini:

  - status di database. Alurnya queued, running, lalu done atau failed (sesuai SDP). Mode bertahap menambah satu
    status, awaiting, yaitu satu tahap sudah selesai dan sistem menunggu langkah berikutnya dari pengguna.
  - berkas progress.json (status, tahap, persen, estimasi sisa waktu) dan analysis.log, yang dibaca oleh polling REST dan WebSocket
  - batas jumlah analisis yang jalan bersamaan (max_concurrent_jobs), sisanya antre dengan status queued
  - params.json dan run_meta.json (parameter, versi program, waktu) supaya hasil bisa ditelusuri ulang (NFR-07)
  - pemulihan: analisis yang masih running saat server mati ditandai failed ketika server hidup lagi

Tahapan yang SAMA untuk mode auto dan staged (catatan analis no. 10):

    qc  ->  pca  ->  gwas  ->  save
    Tiap tahap berstatus pending, running, done, skipped (PCA tidak dipakai), atau failed.

  Mode auto   : keempat tahap berjalan berurutan dalam satu job, tanpa jeda.
  Mode staged : job berhenti (status awaiting) setelah qc dan setelah pca. Pengguna lalu memanggil tahap berikutnya.
                Tahap gwas dan save berjalan bersama dalam satu job terakhir.
  Hasil akhirnya, urutan tahapnya, dan isi progress.json sama untuk kedua mode. Yang berbeda hanya ada tidaknya jeda.
"""
import json
import logging
import os
import shutil
import threading
import time
import traceback
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select, text

from app import database
from app.config import APP_VERSION, PIPELINE_VERSION, get_settings
from app.models import Accession, GwasAnalysis, GwasResult
from app.services import results as R
from app.services.resource_policy import effective_concurrency
from app.services import storage
from app.services.config_rules import MODEL_LABEL
from app.services.gwas_engine import PipelineError, RunContext, import_results, run_pipeline
from app.services.tools import ToolError

logger = logging.getLogger("genosphere.jobs")
_slots: threading.BoundedSemaphore | None = None
_slots_lock = threading.Lock()

STAGES = ("qc", "pca", "gwas", "save")
STAGE_LABEL = {"qc": "Quality Control", "pca": "PCA", "gwas": "Analisis asosiasi", "save": "Menyimpan hasil"}
# Nama tahap internal mesin (input, qc, pca, gwas, import) dipetakan ke tahap yang tampil di layar.
_ENGINE_TO_STAGE = {"input": "qc", "qc": "qc", "pca": "pca", "gwas": "gwas", "import": "save"}


def _get_slots() -> threading.BoundedSemaphore:
    """Pembatas jumlah analisis yang boleh jalan bersamaan (max_concurrent_jobs). Dibuat saat pertama dipakai."""
    global _slots
    with _slots_lock:
        if _slots is None:
            _slots = threading.BoundedSemaphore(max(1, effective_concurrency()))
        return _slots


@contextmanager
def _acquire_job_slot():
    """Adaptive per-process semaphore plus PostgreSQL advisory lock across workers.

    On SQLite/test databases, only the process-local semaphore is available. On PostgreSQL,
    a shared lock slot enforces the administrator's global ceiling across API workers.
    """
    local = _get_slots()
    local.acquire()
    connection = None
    lock_slot = None
    try:
        if database.engine.dialect.name == "postgresql":
            global_cap = max(1, effective_concurrency())
            namespace = 1196576841  # stable application-specific lock namespace
            while lock_slot is None:
                candidate = database.engine.connect()
                try:
                    for slot in range(global_cap):
                        acquired = candidate.execute(
                            text("SELECT pg_try_advisory_lock(:namespace, :slot)"),
                            {"namespace": namespace, "slot": slot + 1}).scalar()
                        if acquired:
                            connection, lock_slot = candidate, slot + 1
                            break
                except Exception:
                    candidate.close()
                    logger.exception("Advisory lock PostgreSQL gagal; memakai semaphore lokal sebagai fallback")
                    break
                if lock_slot is None:
                    candidate.close()
                    time.sleep(1)
        yield
    finally:
        if connection is not None and lock_slot is not None:
            try:
                connection.execute(text("SELECT pg_advisory_unlock(:namespace, :slot)"),
                                   {"namespace": 1196576841, "slot": lock_slot})
            except Exception:
                logger.exception("Gagal melepas advisory lock untuk job")
            finally:
                connection.close()
        local.release()


def _now() -> str:
    """Waktu sekarang (UTC) dalam format ISO 8601."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------- progress.json ----------------------------
def write_progress(rdir: Path, **fields) -> None:
    """Menggabungkan `fields` ke progress.json secara atomik, jadi aman dibaca saat sedang ditulis."""
    path = rdir / "progress.json"
    data = read_progress(rdir, with_eta=False)
    data.update(fields, updated_at=_now())
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def read_progress(rdir: Path, with_eta: bool = True) -> dict:
    """Membaca progress.json. Untuk analisis yang sedang berjalan, estimasi sisa waktu ikut dihitung."""
    try:
        data = json.loads((rdir / "progress.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if with_eta and data.get("state") == "running" and data.get("started_ts"):
        elapsed = max(0.0, time.time() - data["started_ts"])
        pct = data.get("percent", 0)
        data["elapsed_seconds"] = int(elapsed)
        # Perkiraan kasar secara linear dari persen progres (NFR-01 / AC-N1: estimasi waktu atau progress bar).
        data["estimated_remaining_seconds"] = int(elapsed * (100 - pct) / pct) if pct >= 10 else None
    return data


def _set_stage(rdir: Path, name: str, state: str) -> None:
    """Mengubah keadaan satu tahap (pending, running, done, skipped, failed) pada progress.json."""
    stages = read_progress(rdir, with_eta=False).get("stages") or {s: "pending" for s in STAGES}
    stages[name] = state
    write_progress(rdir, stages=stages)


def stage_list(progress: dict) -> list[dict]:
    """Daftar tahap berurutan untuk ditampilkan, selalu lengkap walau job belum jalan."""
    st = progress.get("stages") or {}
    return [{"name": s, "label": STAGE_LABEL[s], "state": st.get(s, "pending")} for s in STAGES]


def run_dir_for(db, analysis: GwasAnalysis) -> Path:
    """Folder keluaran sebuah analisis."""
    acc = db.get(Accession, analysis.accession_id)
    return storage.run_dir(storage.dataset_dir(storage.dataset_folder(acc.genotype_file_path)), analysis.analysis_id)


def prepare_run(rdir: Path, config: dict, user_id: int) -> None:
    """Dipanggil saat analisis dibuat: menyimpan parameter (params.json) dan status awal 'queued'."""
    (rdir / "params.json").write_text(json.dumps({"config": config, "user_id": user_id, "created_at": _now()},
                                                 ensure_ascii=False, indent=2), encoding="utf-8")
    write_progress(rdir, state="queued", stage="queued", percent=0, message="Menunggu giliran dijalankan", error=None,
                   stages={s: "pending" for s in STAGES}, attempts=1, created_at=_now())


def mark_retry(rdir: Path) -> int:
    """Dipanggil saat pengguna mengulang analisis yang gagal. Mengembalikan nomor percobaan yang baru."""
    attempts = int(read_progress(rdir, with_eta=False).get("attempts", 1)) + 1
    write_progress(rdir, state="queued", message="Menunggu giliran dijalankan (percobaan ulang)", error=None,
                   finished_at=None, attempts=attempts)
    return attempts


def _clean_previous_outputs(rdir: Path) -> None:
    """Pada percobaan ulang dari awal: hapus keluaran lama agar tidak tercampur. params.json, log, dan progress dipertahankan."""
    for item in rdir.iterdir():
        if item.name in ("params.json", "analysis.log", "progress.json"):
            continue
        shutil.rmtree(item) if item.is_dir() else item.unlink()


# Rencana satu job. "all" = mode auto (sekali jalan). "qc", "pca", "gwas" = tahap pada mode staged.
PLAN_STAGES = {"all": ("qc", "pca", "gwas"), "qc": ("qc",), "pca": ("pca",), "gwas": ("gwas",)}
# Setelah tahap ini selesai pada mode staged, job berhenti di status "awaiting" dan menunjuk langkah berikutnya.
AFTER_STAGE = {"qc": (35, "pca", "QC selesai. Tentukan komponen PCA dan kovariat."),
               "pca": (50, "gwas", "PCA selesai. Pilih model lalu jalankan analisis asosiasi.")}


# ---------------------------- job utama ----------------------------
def run_analysis_job(analysis_id: int, plan: str = "all") -> None:
    """Pintu masuk job (dipanggil lewat BackgroundTasks). Tidak pernah melempar error ke luar (NFR-08).

    plan: "all" (QC, PCA, GWAS, simpan dalam satu jalan) atau salah satu tahap mode staged: "qc", "pca", "gwas".
    """
    db = database.SessionLocal()
    rdir = None
    try:
        analysis = db.get(GwasAnalysis, analysis_id)
        rdir = run_dir_for(db, analysis)
        cfg = json.loads((rdir / "params.json").read_text(encoding="utf-8"))["config"]
        with _acquire_job_slot():                   # antre; PostgreSQL memakai lock lintas worker
            if plan in ("all", "qc"):               # tahap pertama: mulai dari nol
                _clean_previous_outputs(rdir)
                write_progress(rdir, stages={s: "pending" for s in STAGES})
            analysis.status = "running"
            db.commit()
            started = time.time()
            first_stage = {"all": "input", "qc": "input", "pca": "pca", "gwas": "gwas"}[plan]
            write_progress(rdir, state="running", stage=first_stage, percent=1, message="Analisis dimulai", error=None, plan=plan,
                           next_step=None, retry_plan=None, started_ts=started, started_at=_now(), finished_at=None)

            def on_progress(stage: str, pct: int, msg: str) -> None:
                """Dipanggil mesin analisis setiap progres berubah: menulis progress.json, menandai tahap yang berjalan, dan mencatat log."""
                current = _ENGINE_TO_STAGE.get(stage, stage)
                write_progress(rdir, stage=stage, percent=pct, message=msg)
                _set_stage(rdir, current, "running")
                ctx.log(stage, msg)

            ctx = RunContext(storage.dataset_dir(storage.dataset_folder(db.get(Accession, analysis.accession_id).genotype_file_path)),
                             rdir, rdir / "analysis.log", on_progress)
            current_stage = PLAN_STAGES[plan][0]
            try:
                out: dict = {}
                for st in PLAN_STAGES[plan]:
                    current_stage = st
                    _set_stage(rdir, st, "running")
                    if st == "pca" and not cfg["use_pca"]:
                        run_pipeline(cfg, ctx, ("pca",))       # hanya membersihkan sisa PCA lama
                        _set_stage(rdir, "pca", "skipped")
                        continue
                    result = run_pipeline(cfg, ctx, (st,))
                    if st == "gwas":
                        out = result
                    _set_stage(rdir, st, "done")
                if plan in AFTER_STAGE:             # mode staged: berhenti dan menunggu keputusan pengguna
                    pct, nxt, msg = AFTER_STAGE[plan]
                    analysis.status = "awaiting"
                    db.commit()
                    ctx.log(plan, msg)
                    write_progress(rdir, state="awaiting", stage=plan, percent=pct, message=msg, next_step=nxt)
                    return
                _save_results(db, analysis, analysis_id, cfg, out, rdir, ctx, started)
            except (PipelineError, ToolError) as e:
                _fail(db, analysis, rdir, e.code, e.message, e.hint, plan, current_stage)
            except Exception:  # kegagalan tak terduga: tetap terisolasi, rincian teknis hanya masuk log
                with open(rdir / "analysis.log", "a", encoding="utf-8") as f:
                    f.write(traceback.format_exc())
                logger.exception("Analisis %s gagal", analysis_id)
                _fail(db, analysis, rdir, "INTERNAL_ERROR", "Terjadi kesalahan tak terduga saat menjalankan analisis.",
                      "Coba ulang. Bila berulang, laporkan ke administrator beserta nomor analisis.", plan, current_stage)
    except Exception:
        logger.exception("Job analisis %s tidak dapat dimulai", analysis_id)
    finally:
        db.close()


def _save_results(db, analysis: GwasAnalysis, analysis_id: int, cfg: dict, out: dict, rdir: Path, ctx: RunContext, started: float) -> None:
    """Tahap terakhir: simpan hasil ke database, tandai varian signifikan, tulis catatan jalannya analisis, lalu tandai done."""
    _set_stage(rdir, "save", "running")
    est = max(1, out["n_variants_tested_input"])
    ctx.progress("import", 80, "Menyimpan hasil ke database")
    db.execute(delete(GwasResult).where(GwasResult.analysis_id == analysis_id))   # aman untuk percobaan ulang
    db.commit()
    stats = import_results(
        db, analysis_id, out["parser"](out["results_file"]),
        on_progress=lambda n: write_progress(rdir, stage="import", percent=80 + min(18, int(18 * n / est)),
                                             message=f"Menyimpan hasil: {n} varian"))
    # Penandaan signifikan lewat satu jalur yang sama dengan endpoint post-processing.
    rule = R.apply_significance(db, analysis_id, cfg["significance_method"], cfg["significance_threshold"])
    R.save_rule(rdir, rule)
    finished_at = _now()
    validation_path = ctx.ddir / "validation.json"
    try:
        validation_report = json.loads(validation_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        validation_report = {}
    genotype_info = validation_report.get("genotype", {})
    phenotype_info = validation_report.get("phenotype", {})
    matching = validation_report.get("matching", {})
    uploaded_files = storage.list_files(ctx.ddir)
    total_uploaded_bytes = sum(int(item.get("size_bytes", 0)) for item in uploaded_files)
    qc = out["qc_summary"]
    pca = out.get("pca_summary") or {}
    sample_accounting = out.get("sample_accounting") or {}
    data_usage = {
        "schema_version": "1.0",
        "uploaded": {"files": uploaded_files, "total_size_bytes": total_uploaded_bytes,
                     "genotype_format": genotype_info.get("format"), "genotype_samples": matching.get("genotype_samples", genotype_info.get("n_samples")),
                     "variants_initial": genotype_info.get("n_variants"), "phenotype_samples": phenotype_info.get("n_samples"),
                     "matched_samples": matching.get("matched"), "unmatched_genotype_samples": matching.get("unmatched_genotype"),
                     "unmatched_phenotype_samples": matching.get("unmatched_phenotype")},
        "preprocessing": {**sample_accounting, "samples_after_qc_keep": qc.get("samples_after")},
        "qc": {"variants_before": qc.get("variants_before"), "variants_after": qc.get("variants_after"),
               "variants_removed": qc.get("variants_removed"), "variants_removed_percent": qc.get("variants_removed_percent"),
               "samples_before": qc.get("samples_before"), "samples_after": qc.get("samples_after"),
               "samples_removed": qc.get("samples_removed"), "parameters": qc.get("parameters"),
               "removed_by_filter": qc.get("removed_by_filter"), "metrics_before": qc.get("metrics_before"),
               "metrics_after": qc.get("metrics_after"), "warnings": qc.get("warnings")},
        "pca": pca,
        "association": {"model": MODEL_LABEL[out["model"]], "samples_in_model": out["n_samples"],
                        "snps_tested": stats["n_results"], "results_saved": stats["n_results"],
                        "significant_snps": rule["n_significant"]},
        "summary": {"n_samples_analyzed": out["n_samples"], "n_variants_tested": stats["n_results"],
                    "n_significant": rule["n_significant"], "status": "done"},
        "timing": {"started_at": datetime.fromtimestamp(started, timezone.utc).isoformat(timespec="seconds"),
                   "finished_at": finished_at, "duration_seconds": round(time.time() - started, 3)},
        "software_versions": {**out["versions"], "genosphere_api": APP_VERSION, "pipeline": PIPELINE_VERSION},
        "outputs": {"result_rows": stats["n_results"], "qc_summary": (rdir / "qc_summary.json").exists(),
                    "pca_coordinates": (rdir / "pca.eigenvec").exists(), "association_source": out["results_file"].name},
    }
    meta = {"config": cfg, "model": MODEL_LABEL[out["model"]], "n_samples": out["n_samples"], "qc_summary": qc,
            "pca_summary": pca, "sample_accounting": sample_accounting,
            "versions": data_usage["software_versions"], "n_results": stats["n_results"],
            "n_significant": rule["n_significant"], "significance": rule,
            "finished_at": finished_at, "duration_seconds": round(time.time() - started, 3),
            "started_at": data_usage["timing"]["started_at"]}
    (rdir / "data_usage_summary.json").write_text(json.dumps(data_usage, ensure_ascii=False, indent=2), encoding="utf-8")
    (rdir / "run_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    analysis.status = "done"
    db.commit()
    # AI interpretation is deliberately enqueued after GWAS status/results commit.
    try:
        from app.services.ai_jobs import enqueue_interpretation
        enqueue_interpretation(analysis_id)
    except Exception:
        logger.exception("Interpretasi AI untuk analisis %s gagal masuk antrean; hasil GWAS tetap done", analysis_id)
    _set_stage(rdir, "save", "done")
    ctx.log("done", f"Selesai: {stats['n_results']} varian diuji, {rule['n_significant']} signifikan")
    write_progress(rdir, state="done", stage="done", percent=100, message="Analisis selesai", finished_at=_now(), next_step=None)


def _fail(db, analysis: GwasAnalysis, rdir: Path, code: str, message: str, hint: str, plan: str = "all", stage: str | None = None) -> None:
    """Menandai analisis gagal: status di database, catatan di log, tahap yang gagal, dan rencana percobaan ulang."""
    db.rollback()
    analysis.status = "failed"
    db.commit()
    with open(rdir / "analysis.log", "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] [ERROR] {code}: {message}\n")
    if stage:
        _set_stage(rdir, stage, "failed")
    # retry_plan menyimpan tahap yang gagal, supaya "coba ulang" pada mode staged hanya mengulang tahap itu.
    write_progress(rdir, state="failed", message=message, finished_at=_now(), retry_plan=plan,
                   error={"code": code, "message": message, "hint": hint})


def recover_interrupted_jobs() -> int:
    """Dipanggil saat server menyala: analisis yang masih 'queued' atau 'running' (server sempat mati) ditandai failed."""
    try:
        db = database.SessionLocal()
    except Exception:
        return 0
    n = 0
    try:
        for a in db.scalars(select(GwasAnalysis).where(GwasAnalysis.status.in_(["queued", "running"]))).all():
            a.status = "failed"
            try:
                rd = run_dir_for(db, a)
                write_progress(rd, state="failed", message="Analisis terhenti karena server dimulai ulang.",
                               finished_at=_now(), retry_plan=read_progress(rd, with_eta=False).get("plan", "all"),
                               error={"code": "INTERRUPTED", "message": "Analisis terhenti karena server dimulai ulang.",
                                      "hint": "Gunakan tombol coba ulang."})
            except Exception:
                pass
            n += 1
        db.commit()
    except Exception:   # misalnya tabel belum ada saat pengujian
        db.rollback()
    finally:
        db.close()
    return n
