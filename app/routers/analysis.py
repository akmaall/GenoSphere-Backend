import json
from pathlib import Path
from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, ensure_owner
from app.errors import APIError
from app.models import GwasAnalysis, User
from app.schemas.analysis import (AnalysisExecutionRequest, LegacyAnalysisRunOut,
                                  LegacyAnalysisStatusOut, LegacyAnalysisSummaryOut)
from app.services import jobs, results
from app.services.config_rules import MODEL_LABEL

router = APIRouter(prefix="/api/v1/analysis", tags=["05. Step 4: Analisis GWAS & Post-Processing"])

def _get_analysis(db: Session, analysis_id: int, user: User) -> GwasAnalysis:
    a = db.get(GwasAnalysis, analysis_id)
    ensure_owner(a.user_id if a else None, user)
    return a

def _require_qc_passed(db: Session, a: GwasAnalysis) -> Path:
    rdir = jobs.run_dir_for(db, a)
    qc_file = rdir / "qc_summary.json"
    if not qc_file.exists():
        raise APIError(409, "QC_NOT_COMPLETED", "QC belum dijalankan atau belum selesai untuk sampel ini.")
    return rdir

@router.post("/run", response_model=LegacyAnalysisRunOut, status_code=202, summary="Jalankan Analisis Utama (GWAS & PCA)")
def run_main_analysis(
    body: AnalysisExecutionRequest, 
    background: BackgroundTasks, 
    current_user: User = Depends(get_current_user), 
    db: Session = Depends(get_db)
):
    """
    Menjalankan Analisis Utama (PCA & GWAS) berdasarkan hasil QC yang telah selesai.
    """
    a = _get_analysis(db, body.analysis_id, current_user)
    rdir = _require_qc_passed(db, a)

    if a.status in ("queued", "running"):
        raise APIError(409, "ALREADY_RUNNING", "Analisis sedang berjalan.")

    a.pca_used = body.use_pca
    a.model_used = MODEL_LABEL.get(body.model, body.model)
    a.status = "queued"
    db.commit()

    params_path = rdir / "params.json"
    existing_params = json.loads(params_path.read_text(encoding="utf-8")) if params_path.exists() else {}
    existing_config = existing_params.get("config", {})
    
    existing_config.update({
        "trait_name": body.trait_name,
        "trait_type": body.trait_type,
        "model": body.model,
        "use_pca": body.use_pca,
        "pca_components": body.pca_components,
        "covariates": body.covariates,
        "significance_threshold": body.significance_threshold
    })
    existing_params["config"] = existing_config
    params_path.write_text(json.dumps(existing_params, indent=2), encoding="utf-8")

    jobs.write_progress(rdir, state="queued", stage="queued", percent=50, message="Menunggu giliran eksekusi analisis.", error=None)
    
    plan = "all" if body.use_pca else "gwas"
    background.add_task(jobs.run_analysis_job, a.analysis_id, plan)

    return {
        "analysis_id": a.analysis_id,
        "status": "queued",
        "model": a.model_used,
        "message": "Proses Analisis Utama berhasil dijadwalkan dengan parameter baru."
    }

@router.get("/{analysis_id}/status", response_model=LegacyAnalysisStatusOut, summary="Cek Status Progres Analisis")
def get_analysis_status(analysis_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    a = _get_analysis(db, analysis_id, current_user)
    rdir = jobs.run_dir_for(db, a)
    p = jobs.read_progress(rdir)
    return {
        "analysis_id": a.analysis_id,
        "status": a.status,
        "stage": p.get("stage"),
        "percent": p.get("percent"),
        "message": p.get("message"),
        "error": p.get("error")
    }

@router.get("/{analysis_id}/results/summary", response_model=LegacyAnalysisSummaryOut, summary="Hasil Ringkasan Analisis")
def get_analysis_summary(analysis_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    a = _get_analysis(db, analysis_id, current_user)
    if a.status != "done":
        raise APIError(409, "ANALYSIS_NOT_FINISHED", "Analisis belum selesai.")
    return {"model": a.model_used, **results.summary(db, analysis_id, a.model_used == MODEL_LABEL.get("logistic"))}