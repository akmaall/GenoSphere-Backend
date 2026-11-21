import json
from pathlib import Path
from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, ensure_owner
from app.errors import APIError
from app.models import Accession, GwasAnalysis, User
from app.schemas.qc import QCExecutionOut, QCExecutionRequest
from app.schemas_gwas import QcSummaryOut
from app.services import jobs, storage, validation

router = APIRouter(prefix="/api/v1/qc", tags=["03. Step 2: Quality Control (QC)"])

def _read_json(path: Path, missing_code: str, missing_msg: str):
    if not path.exists():
        raise APIError(409, missing_code, missing_msg)
    return json.loads(path.read_text(encoding="utf-8"))

def _get_dataset(db: Session, dataset_id: int, user: User) -> Accession:
    acc = db.get(Accession, dataset_id)
    ensure_owner(storage.prefix_owner(acc.genotype_file_path) if acc else None, user)
    return acc

@router.post("", response_model=QCExecutionOut, status_code=202, summary="Jalankan Quality Control (QC)")
def execute_qc(
    body: QCExecutionRequest, 
    background: BackgroundTasks, 
    current_user: User = Depends(get_current_user), 
    db: Session = Depends(get_db)
):
    acc = _get_dataset(db, body.dataset_id, current_user)
    
    # Ambil trait default dari hasil validasi dataset
    ddir = storage.dataset_dir(storage.dataset_prefix(current_user.user_id, acc.accession_id))
    report = validation.load_report(ddir) or {}
    traits = (report.get("phenotype") or {}).get("traits", [])
    default_trait = traits[0]["name"] if traits else None
    default_trait_type = traits[0]["type"] if traits else None

    analysis = GwasAnalysis(
        user_id=current_user.user_id, 
        accession_id=acc.accession_id, 
        analysis_mode="manual",
        qc_skipped=body.skip_qc, 
        pca_used=False, 
        model_used="Pending",
        status="queued"
    )
    db.add(analysis)
    db.commit()

    qc_data = {
        "dataset_id": body.dataset_id,
        "trait_name": default_trait,
        "trait_type": default_trait_type,
        "qc": {
            "skip_qc": body.skip_qc,
            "maf": body.maf,
            "call_rate": body.call_rate,
            "hwe_p": body.hwe_p
        },
        "execution_mode": "staged"
    }

    rdir = jobs.run_dir_for(db, analysis)
    jobs.prepare_run(rdir, qc_data, current_user.user_id)
    jobs.write_progress(rdir, execution_mode="staged")
    
    background.add_task(jobs.run_analysis_job, analysis.analysis_id, "qc")
    
    return {
        "analysis_id": analysis.analysis_id,
        "dataset_id": acc.accession_id,
        "status": "queued",
        "message": "Quality Control (QC) sedang diproses di latar belakang.",
        "qc_config": body.model_dump()
    }

@router.get("/{analysis_id}/summary", response_model=QcSummaryOut, summary="Dapatkan Ringkasan Hasil QC", tags=["03. Step 2: Quality Control (QC)"])
def get_qc_summary(analysis_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    a = db.get(GwasAnalysis, analysis_id)
    ensure_owner(a.user_id if a else None, current_user)
    rdir = jobs.run_dir_for(db, a)
    return _read_json(rdir / "qc_summary.json", "STAGE_NOT_READY", "Tahap Quality Control belum selesai.")