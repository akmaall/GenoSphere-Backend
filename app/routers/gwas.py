"""Endpoint modul GWAS: /api/gwas/..."""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal, TypeAlias

import jwt
from fastapi import (APIRouter, BackgroundTasks, Depends, File, Form, Query, Response, UploadFile, WebSocket,
                     WebSocketDisconnect)
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import database
from app.config import get_settings
from app.database import get_db
from app.deps import ensure_owner, get_current_user
from app.errors import APIError, ErrorResponse
from app.models import Accession, GwasAnalysis, GwasInterpretation, GwasResult, Phenotype, User
from app.schemas_gwas import (AIInterpretationOut, AIInterpretationRetryOut, DataUsageOut, AnalysisCreatedOut, AnalysisDetailOut, AnalysisListOut, AnalysisStatusOut, ConfigCheckOut,
                              ConfigExportOut, CovariatesRequest, DatasetDetailOut, DatasetListOut,
                              DeleteDatasetOut, DeleteFileOut, LogOut, ManhattanOut, ModelsOut, OptionsOut,
                              ParametersOut, PcaOut, PcaRequest, PostprocessOut, PostprocessRequest, PresetsOut,
                              QcSummaryOut, QQOut, RetryOut, RunRequest, SignificantOut, StageOut, SummaryOut,
                              TopOut, TraitsOut, UploadOut, ValidateOut)
from app.security import decode_access_token
from app.services import ai_jobs, jobs, results, significance, storage, ui_adapter, validation
from app.services.config_rules import (FRONTEND_OPTIONS, MODEL_CATALOG, MODEL_LABEL, PARAMETER_DOCS,
                                       PRESETS, AnalysisConfig, check_config, models_for_trait)
from app.services.tools import ToolError, resolve_binary
from app.services import resource_policy

_COMMON = {401: {"model": ErrorResponse, "description": "Belum login atau token tidak berlaku."},
           404: {"model": ErrorResponse, "description": "Data tidak ditemukan atau bukan milik Anda."}}

# Router utama GWAS tanpa tag generik "GWAS" agar me-refer pada tags_metadata main.py
router = APIRouter(prefix="/api/gwas", responses=_COMMON)

CurrentUser: TypeAlias = Annotated[User, Depends(get_current_user)]
DbSession: TypeAlias = Annotated[Session, Depends(get_db)]

# ==========================================
# HELPER FUNCTIONS
# ==========================================

def _get_dataset(db: Session, dataset_id: int, user: User) -> Accession:
    acc = db.get(Accession, dataset_id)
    ensure_owner(storage.prefix_owner(acc.genotype_file_path) if acc else None, user)
    return acc

def _get_analysis(db: Session, analysis_id: int, user: User) -> GwasAnalysis:
    a = db.get(GwasAnalysis, analysis_id)
    ensure_owner(a.user_id if a else None, user)
    return a

def _require_done(a: GwasAnalysis) -> None:
    if a.status != "done":
        raise APIError(409, "ANALYSIS_NOT_FINISHED",
                       f"Analisis belum selesai (status: {a.status}). Hasil baru dapat dilihat atau diekspor setelah analisis selesai.")

def _require_stage(a: GwasAnalysis, rdir: Path, allowed: tuple[str, ...]) -> dict:
    """Memastikan analisis berjalan pada mode staged dan sedang menunggu salah satu langkah yang diizinkan."""
    prog = jobs.read_progress(rdir)
    if _cfg_of(rdir).get("execution_mode") != "staged":
        raise APIError(409, "NOT_STAGED", "Analisis ini berjalan pada mode auto, jadi tahapnya tidak bisa dilanjutkan manual.")
    if a.status != "awaiting" or prog.get("next_step") not in allowed:
        raise APIError(409, "WRONG_STAGE", f"Langkah ini belum atau tidak dapat dijalankan (status: {a.status}, langkah berikutnya: {prog.get('next_step') or '-'}).",
                       details={"status": a.status, "next_step": prog.get("next_step"), "allowed_next_steps": list(allowed)})
    return prog

def _revalidate(db: Session, user: User, a: GwasAnalysis, rdir: Path, changes: dict) -> tuple[dict, list]:
    """Menggabungkan perubahan ke konfigurasi tersimpan, memeriksanya lagi, lalu menyimpannya."""
    acc = db.get(Accession, a.accession_id)
    ddir = _ddir(user, acc)
    merged = AnalysisConfig(**{**_cfg_of(rdir), **changes})
    st = validation.dataset_status(ddir)["status"]
    data, errors, warnings = check_config(merged, validation.current_valid_report(ddir), acc.source_domain, merged.model_fields_set, st)
    if errors:
        raise APIError(422, "CONFIG_INVALID", "Pengaturan belum dapat dijalankan.", errors=errors)
    params = json.loads((rdir / "params.json").read_text(encoding="utf-8"))
    params["config"] = data
    (rdir / "params.json").write_text(json.dumps(params, ensure_ascii=False, indent=2), encoding="utf-8")
    a.pca_used, a.model_used = data["use_pca"], MODEL_LABEL[data["model"]]
    return data, warnings

def _is_logistic(a: GwasAnalysis) -> bool:
    return a.model_used == MODEL_LABEL["logistic"]

def _rdir(db: Session, a: GwasAnalysis) -> Path:
    return jobs.run_dir_for(db, a)

def _ddir(user: User, acc: Accession) -> Path:
    return storage.dataset_dir(storage.dataset_prefix(user.user_id, acc.accession_id))

def _analyses_of(db: Session, dataset_id: int) -> list[GwasAnalysis]:
    return list(db.scalars(select(GwasAnalysis).where(GwasAnalysis.accession_id == dataset_id)).all())

def _cfg_of(rdir: Path) -> dict:
    try:
        return json.loads((rdir / "params.json").read_text(encoding="utf-8")).get("config", {})
    except (OSError, ValueError):
        return {}

def _read_json(path: Path, missing_code: str, missing_msg: str):
    if not path.exists():
        raise APIError(409, missing_code, missing_msg)
    return json.loads(path.read_text(encoding="utf-8"))

def _dataset_out(db: Session, acc: Accession, ddir: Path) -> dict:
    comp = validation.completeness(ddir)
    st = validation.dataset_status(ddir)
    return {"dataset_id": acc.accession_id, "name": acc.sample_code, "organism": acc.organism, "source_domain": acc.source_domain,
            "files": storage.list_files(ddir), "ready_for_validation": comp["ready_for_validation"], "missing": comp["missing"],
            "genotype_complete": comp["genotype_complete"], "phenotype_present": comp["phenotype_present"],
            "validation_status": st["status"], "validation_label": st["label"], "can_proceed": st["can_proceed"],
            "n_analyses": len(_analyses_of(db, acc.accession_id))}

def _upload_many(db: Session, user: User, uploads: list[UploadFile], kind: Literal["genotype", "phenotype"], dataset_id: int | None,
                 organism: str | None, source_domain: str | None, name: str | None) -> dict:
    if not uploads:
        raise APIError(400, "NO_FILE", "Tidak ada berkas yang dikirim.", hint="Kirim satu berkas lewat kolom form bernama `file`.")
    exts = storage.GENOTYPE_EXTS if kind == "genotype" else storage.PHENOTYPE_EXTS
    label = "genotipe" if kind == "genotype" else "fenotipe"
    planned = []
    for up in uploads:
        ext = storage.match_ext(up.filename, exts)
        if ext is None:
            raise APIError(400, f"INVALID_{kind.upper()}_FORMAT",
                           f"Format file {label} tidak valid ({up.filename}). Ekstensi yang didukung: {', '.join(exts)}")
        planned.append((up, ext))
    if kind == "phenotype" and len(planned) > 1:
        raise APIError(400, "TOO_MANY_FILES", "Hanya satu berkas fenotipe yang boleh diunggah per dataset.")
    slots = [f"{kind}{ext}" for _, ext in planned]
    if len(set(slots)) != len(slots):
        raise APIError(400, "DUPLICATE_FILE", "Ada dua berkas dengan jenis yang sama pada satu unggahan (misalnya dua file .bed).")

    request_exts = {ext for _, ext in planned}

    if kind == "genotype":
        request_vcf = request_exts & {".vcf", ".vcf.gz"}
        request_plink = request_exts & {".bed", ".bim", ".fam"}

        if request_vcf and request_plink:
            raise APIError(
                400,
                "MIXED_GENOTYPE_FORMAT",
                "Jangan mencampurkan format VCF dan PLINK dalam satu permintaan."
            )

        if len(request_vcf) > 1:
            raise APIError(
                400,
                "MULTIPLE_VCF_FORMATS",
                "Pilih hanya satu format VCF: .vcf atau .vcf.gz."
            )

    if source_domain is not None and source_domain.lower() not in storage.DOMAINS:
        raise APIError(400, "INVALID_DOMAIN", f"Domain riset tidak dikenal. Pilihan: {', '.join(storage.DOMAINS)}.")

    if dataset_id is None:
        first = planned[0][0].filename
        acc = Accession(sample_code=(name or first or "dataset")[:100], genotype_file_path=None)
        db.add(acc)
        db.flush()
        acc.genotype_file_path = f"{storage.dataset_prefix(user.user_id, acc.accession_id)}/genotype"
    else:
        acc = _get_dataset(db, dataset_id, user)
        if _analyses_of(db, acc.accession_id):
            raise APIError(409, "DATASET_IN_USE", "Dataset ini sudah dipakai analisis, jadi berkasnya tidak dapat diubah.",
                           hint="Buat dataset baru dengan unggahan tanpa dataset_id.")
        if name:
            acc.sample_code = name[:100]
    if organism:
        acc.organism = organism[:100]
    if source_domain:
        acc.source_domain = source_domain.lower()

    ddir = storage.dataset_dir(storage.dataset_prefix(user.user_id, acc.accession_id))

    if kind == "genotype":
        existing_exts = (
            set(validation.find_genotype_files(ddir))
            if ddir.exists()
            else set()
        )

        combined_exts = existing_exts | request_exts
        combined_vcf = combined_exts & {".vcf", ".vcf.gz"}
        combined_plink = combined_exts & {".bed", ".bim", ".fam"}

        if combined_vcf and combined_plink:
            raise APIError(
                400,
                "MIXED_GENOTYPE_FORMAT",
                "Dataset sudah berisi format genotipe yang berbeda. Hapus format lama terlebih dahulu atau buat dataset baru."
            )

        if len(combined_vcf) > 1:
            raise APIError(
                400,
                "MULTIPLE_VCF_FORMATS",
                "Dataset hanya boleh menggunakan .vcf atau .vcf.gz."
            )

    saved = []
    replacing = set(slots)
    if kind == "phenotype":
        replacing.update(f"phenotype{ext}" for ext in storage.PHENOTYPE_EXTS)
    budget = resource_policy.upload_budget(ddir, replacing_slots=replacing)
    remaining = int(budget["dataset_remaining_bytes"] or 0)
    if remaining <= 0:
        raise APIError(413, "DATASET_QUOTA_EXCEEDED", "Kuota total unggahan dataset sudah habis.",
                       hint="Hapus berkas yang tidak diperlukan atau sesuaikan MAX_DATASET_UPLOAD_MB oleh administrator.")
    for (up, ext), slot in zip(planned, slots):
        per_file_limit = min(int(budget["per_file_bytes"] or 0), remaining)
        if per_file_limit <= 0:
            raise APIError(413, "DATASET_QUOTA_EXCEEDED", "Sisa kuota dataset tidak mencukupi untuk unggahan berikutnya.")
        admin_file_cap = get_settings().max_upload_mb * 1024 * 1024
        too_large_code = "FILE_TOO_LARGE" if per_file_limit >= admin_file_cap else "DATASET_QUOTA_EXCEEDED"
        size = storage.save_upload(up, ddir / slot, max_bytes=per_file_limit, too_large_code=too_large_code)


        remaining -= size
        storage.record_file(ddir, slot, up.filename, size)
        if kind == "phenotype":
            for other in storage.PHENOTYPE_EXTS:
                if other != ext:
                    storage.remove_slot(ddir, f"phenotype{other}")
        saved.append(storage.file_entry(ddir, slot))
    (ddir / "validation.json").unlink(missing_ok=True)
    db.commit()
    state = _dataset_out(db, acc, ddir)
    nxt = ("Dataset sudah lengkap. Panggil POST /api/gwas/datasets/{dataset_id}/validate."
           if state["ready_for_validation"] else
           "Dataset belum lengkap. Unggah berkas yang masih kurang dengan dataset_id yang sama (lihat kolom `missing`).")
    return {"status": "success", "dataset_id": acc.accession_id, "files": saved, "dataset_files": state["files"],
            "message": f"File {label} berhasil diunggah" if len(saved) == 1 else f"{len(saved)} file {label} berhasil diunggah",
            "ready_for_validation": state["ready_for_validation"], "missing": state["missing"],
            "validation_status": state["validation_status"], "validation_label": state["validation_label"],
            "resource_limits": {"per_file_limit_bytes": budget["per_file_bytes"],
                                "dataset_remaining_before_bytes": budget["dataset_remaining_bytes"],
                                "dataset_remaining_after_bytes": remaining,
                                "disk_free_bytes": budget["disk_free_bytes"],
                                "is_estimate": True}, "next": nxt}

_UPLOAD_ERRORS = {400: {"model": ErrorResponse, "description": "Format atau jumlah berkas tidak sesuai."},
                  409: {"model": ErrorResponse, "description": "Dataset sudah dipakai analisis."},
                  413: {"model": ErrorResponse, "description": "Ukuran berkas melebihi batas."}}

# ==========================================
# 02. Step 1: Input & Validasi Data
# ==========================================

@router.post("/upload-phenotype", response_model=UploadOut, summary="Unggah berkas fenotipe (.csv, .tsv, .txt, .pheno)",
             tags=["02. Step 1: Input & Validasi Data"], responses=_UPLOAD_ERRORS)
def upload_phenotype(
    user: CurrentUser, db: DbSession,
    file: UploadFile = File(..., description="Berkas fenotipe (.csv, .tsv, .txt, .pheno). Hanya SATU berkas per dataset."),
    dataset_id: Annotated[int | None, Form(description="Isi bila berkas ini menambah dataset yang sudah ada. Kosong membuat dataset baru.")] = None,
    organism: Annotated[str | None, Form()] = None,
    source_domain: Annotated[str | None, Form(description="kesehatan, farmasi, agrikultur, atau peternakan.")] = None,
    name: Annotated[str | None, Form(description="Nama dataset yang tampil di layar.")] = None,
):
    return _upload_many(db, user, [file], "phenotype", dataset_id, organism, source_domain, name)


@router.post(
    "/upload-genotype",
    response_model=UploadOut,
    summary="Unggah satu atau beberapa berkas genotipe (.bed/.bim/.fam, .vcf, .vcf.gz)",
    tags=["02. Step 1: Input & Validasi Data"],
    responses=_UPLOAD_ERRORS,
)
def upload_genotype(
    user: CurrentUser,
    db: DbSession,
    file: list[UploadFile] = File(
        ...,
        description=(
            "PLINK: pilih .bed, .bim, dan .fam sekaligus. "
            "Berkas PLINK juga tetap dapat diunggah bertahap menggunakan dataset_id yang sama. "
            "VCF: pilih satu .vcf atau .vcf.gz. Jangan mencampurkan VCF dan PLINK."
        ),
    ),
    dataset_id: Annotated[
        int | None,
        Form(description="Isi bila berkas ini menambah dataset yang sudah ada. Kosong membuat dataset baru."),
    ] = None,
    organism: Annotated[str | None, Form()] = None,
    source_domain: Annotated[
        str | None,
        Form(description="kesehatan, farmasi, agrikultur, atau peternakan."),
    ] = None,
    name: Annotated[
        str | None,
        Form(description="Nama dataset yang tampil di layar."),
    ] = None,
):
    return _upload_many(
        db, user, file, "genotype",
        dataset_id, organism, source_domain, name
    )

@router.delete("/datasets/{dataset_id}/files/{slot}", response_model=DeleteFileOut, summary="Hapus satu berkas dataset",
               tags=["02. Step 1: Input & Validasi Data"], responses={409: {"model": ErrorResponse, "description": "Dataset sudah dipakai analisis."}})
def delete_dataset_file(dataset_id: int, slot: str, user: CurrentUser, db: DbSession):
    acc = _get_dataset(db, dataset_id, user)
    if not storage.SLOT_RE.match(slot):
        raise APIError(400, "INVALID_SLOT", "Nama berkas tidak dikenal.")
    if _analyses_of(db, dataset_id):
        raise APIError(409, "DATASET_IN_USE", "Dataset sudah dipakai analisis, jadi berkasnya tidak dapat diubah.",
                       hint="Hapus dataset (dengan konfirmasi) atau buat dataset baru.")
    ddir = _ddir(user, acc)
    if not storage.remove_slot(ddir, slot):
        raise APIError(404, "FILE_NOT_FOUND", "Berkas tidak ditemukan pada dataset ini.")
    state = _dataset_out(db, acc, ddir)
    return {"deleted": slot, "dataset": state,
            "message": f"Berkas {slot} dihapus. Status dataset diperbarui menjadi: {state['validation_label']}."}

@router.delete("/datasets/{dataset_id}", response_model=DeleteDatasetOut, summary="Hapus dataset beserta seluruh analisis dan berkasnya",
               tags=["02. Step 1: Input & Validasi Data"], responses={409: {"model": ErrorResponse, "description": "Butuh konfirmasi (confirm=true) atau masih ada analisis berjalan."}})
def delete_dataset(dataset_id: int, user: CurrentUser, db: DbSession,
                   confirm: Annotated[bool, Query(description="Wajib true agar penghapusan benar-benar dilakukan.")] = False):
    acc = _get_dataset(db, dataset_id, user)
    analyses = _analyses_of(db, dataset_id)
    ddir = _ddir(user, acc)
    if any(a.status in ("queued", "running") for a in analyses):
        raise APIError(409, "ANALYSIS_RUNNING", "Masih ada analisis yang berjalan pada dataset ini. Tunggu sampai selesai.")
    ids = [a.analysis_id for a in analyses]
    files = storage.list_files(ddir)
    if not confirm:
        n_results = db.scalar(select(func.count()).select_from(GwasResult).where(GwasResult.analysis_id.in_(ids))) if ids else 0
        raise APIError(409, "CONFIRMATION_REQUIRED",
                       "Menghapus dataset akan menghapus semua berkas dan hasil analisisnya, dan tidak bisa dibatalkan. Kirim ulang dengan confirm=true bila Anda yakin.",
                       details={"will_delete": {"dataset": acc.sample_code, "files": [f["slot"] for f in files], "n_files": len(files),
                                                "n_analyses": len(ids), "n_result_rows": int(n_results or 0)}})
    if ids:
        db.execute(delete(GwasResult).where(GwasResult.analysis_id.in_(ids)))
        db.execute(delete(GwasAnalysis).where(GwasAnalysis.analysis_id.in_(ids)))
    db.execute(delete(Phenotype).where(Phenotype.accession_id == dataset_id))
    db.delete(acc)
    db.commit()
    storage.remove_dataset_dir(ddir)
    return {"deleted": dataset_id, "analyses_deleted": len(ids), "files_deleted": len(files),
            "message": "Dataset beserta seluruh berkas dan hasil analisisnya telah dihapus."}

@router.get("/datasets", response_model=DatasetListOut, summary="Daftar dataset milik saya",
            tags=["02. Step 1: Input & Validasi Data"])
def list_datasets(user: CurrentUser, db: DbSession):
    rows = db.scalars(select(Accession).where(Accession.genotype_file_path.like(f"u{user.user_id}/ds_%"))
                      .order_by(Accession.accession_id.desc())).all()
    return {"items": [_dataset_out(db, acc, _ddir(user, acc)) for acc in rows]}

@router.get("/datasets/{dataset_id}", response_model=DatasetDetailOut, summary="Detail dataset dan hasil validasi terakhir",
            tags=["02. Step 1: Input & Validasi Data"])
def get_dataset(dataset_id: int, user: CurrentUser, db: DbSession):
    acc = _get_dataset(db, dataset_id, user)
    ddir = _ddir(user, acc)
    return {**_dataset_out(db, acc, ddir), "validation": validation.load_report(ddir)}

@router.post("/datasets/{dataset_id}/validate", response_model=ValidateOut, summary="Validasi format, kelengkapan, dan kecocokan Sample ID",
             tags=["02. Step 1: Input & Validasi Data"])
def validate_dataset(dataset_id: int, user: CurrentUser, db: DbSession):
    acc = _get_dataset(db, dataset_id, user)
    ddir = _ddir(user, acc)
    report = validation.validate_dataset(ddir)
    db.execute(delete(Phenotype).where(Phenotype.accession_id == acc.accession_id))
    for t in report["phenotype"].get("traits", []):
        db.add(Phenotype(accession_id=acc.accession_id, trait_name=t["name"][:100], trait_type=t["type"], trait_value=t["summary"][:100]))
    db.commit()
    return {**_dataset_out(db, acc, ddir), **report}

@router.get("/datasets/{dataset_id}/traits", response_model=TraitsOut, summary="Pilihan fenotipe (trait) dan model yang cocok",
            tags=["02. Step 1: Input & Validasi Data"], responses={409: {"model": ErrorResponse, "description": "Dataset belum tervalidasi."}})
def dataset_traits(dataset_id: int, user: CurrentUser, db: DbSession):
    acc = _get_dataset(db, dataset_id, user)
    ddir = _ddir(user, acc)
    report = validation.current_valid_report(ddir)
    if report is None or report["status"] != "valid":
        st = validation.dataset_status(ddir)
        raise APIError(409, "DATASET_NOT_VALIDATED", f"Dataset belum bisa dipakai (status: {st['label']}).",
                       hint="Lengkapi berkas dan jalankan validasi (Step 2) terlebih dahulu.")
    traits = []
    for t in report["phenotype"]["traits"]:
        models = models_for_trait(t["type"])
        traits.append({"name": t["name"], "type": t["type"], "n_valid": t["n_valid"], "n_missing": t["n_missing"], "summary": t["summary"],
                       "n_cases": t.get("n_cases"), "n_controls": t.get("n_controls"), "models": models,
                       "recommended_model": next(m["value"] for m in models if m["recommended"])})
    candidates = [{"name": c, "kind": "numeric"} for c in report["phenotype"]["numeric_columns"]]
    candidates += [{"name": c["name"], "kind": "categorical", "levels": c["levels"]} for c in report["phenotype"]["categorical_columns"]]
    return {"dataset_id": acc.accession_id, "traits": traits, "covariate_candidates": candidates,
            "note": "Satu analisis menguji satu trait. Untuk menguji trait lain, buat analisis baru."}

# ==========================================
# 03. Step 2: Quality Control (QC)
# ==========================================

@router.get("/analyses/{analysis_id}/qc-summary", response_model=QcSummaryOut, summary="Ringkasan hasil QC",
            tags=["03. Step 2: Quality Control (QC)"], responses={409: {"model": ErrorResponse, "description": "Tahap QC belum selesai."}})
def qc_summary(analysis_id: int, user: CurrentUser, db: DbSession):
    a = _get_analysis(db, analysis_id, user)
    return _read_json(_rdir(db, a) / "qc_summary.json", "STAGE_NOT_READY", "Tahap Quality Control belum selesai.")


@router.get("/analyses/{analysis_id}/data-usage", response_model=DataUsageOut,
            summary="Ringkasan penggunaan data dari upload hingga hasil GWAS",
            tags=["06. Step 5: Hasil, Visualisasi & Ekspor"])
def data_usage_summary(analysis_id: int, user: CurrentUser, db: DbSession):
    a = _get_analysis(db, analysis_id, user)
    _require_done(a)
    return _read_json(_rdir(db, a) / "data_usage_summary.json", "DATA_USAGE_NOT_READY", "Data Usage Summary belum tersedia.")


@router.get("/analyses/{analysis_id}/data-usage/export", summary="Unduh Data Usage Summary (JSON/CSV)",
            tags=["06. Step 5: Hasil, Visualisasi & Ekspor"],
            responses={200: {"description": "Data Usage Summary JSON atau CSV.", "content": {
                "application/json": {"schema": {"type": "object", "properties": {
                    "uploaded": {"type": "object"}, "preprocessing": {"type": "object"}, "qc": {"type": "object"},
                    "pca": {"type": "object"}, "association": {"type": "object"}, "summary": {"type": "object"}}}},
                "text/csv": {"schema": {"type": "string", "format": "binary"}}}}})
def export_data_usage(analysis_id: int, user: CurrentUser, db: DbSession,
                      format: Annotated[Literal["json", "csv"], Query()] = "json"):
    a = _get_analysis(db, analysis_id, user)
    _require_done(a)
    payload = _read_json(_rdir(db, a) / "data_usage_summary.json", "DATA_USAGE_NOT_READY", "Data Usage Summary belum tersedia.")
    filename = f"gwas_analysis_{analysis_id}_data_usage.{format}"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"', "X-Content-Type-Options": "nosniff"}
    if format == "json":
        return Response(content=json.dumps(payload, ensure_ascii=False, indent=2), media_type="application/json", headers=headers)
    import csv, io
    def flatten(value, prefix=""):
        if isinstance(value, dict):
            for key, item in value.items():
                yield from flatten(item, f"{prefix}.{key}" if prefix else key)
        elif isinstance(value, list):
            for i, item in enumerate(value):
                yield from flatten(item, f"{prefix}[{i}]")
        else:
            yield prefix, value
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["field", "value"])
    for key, value in flatten(payload):
        writer.writerow([key, value])
    return Response(content=buf.getvalue(), media_type="text/csv; charset=utf-8", headers=headers)


@router.get("/analyses/{analysis_id}/qc-summary/export", summary="Unduh laporan QC (JSON/CSV)",
            tags=["06. Step 5: Hasil, Visualisasi & Ekspor"],
            responses={200: {"description": "Laporan QC JSON atau CSV.", "content": {
                "application/json": {"schema": {"$ref": "#/components/schemas/QcSummaryOut"}},
                "text/csv": {"schema": {"type": "string", "format": "binary"}}}}})
def export_qc_summary(analysis_id: int, user: CurrentUser, db: DbSession,
                      format: Annotated[Literal["json", "csv"], Query()] = "json"):
    a = _get_analysis(db, analysis_id, user)
    _require_done(a)
    payload = _read_json(_rdir(db, a) / "qc_summary.json", "STAGE_NOT_READY", "Laporan QC belum tersedia.")
    filename = f"gwas_analysis_{analysis_id}_qc.{format}"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"', "X-Content-Type-Options": "nosniff"}
    if format == "json":
        return Response(content=json.dumps(payload, ensure_ascii=False, indent=2), media_type="application/json", headers=headers)
    import csv, io
    buf = io.StringIO(); writer = csv.writer(buf, lineterminator="\n"); writer.writerow(["field", "value"])
    def walk(obj, prefix=""):
        if isinstance(obj, dict):
            for k, v in obj.items(): yield from walk(v, f"{prefix}.{k}" if prefix else k)
        elif isinstance(obj, list):
            for i, v in enumerate(obj): yield from walk(v, f"{prefix}[{i}]")
        else: yield prefix, obj
    for row in walk(payload): writer.writerow(row)
    return Response(content=buf.getvalue(), media_type="text/csv; charset=utf-8", headers=headers)

# ==========================================
# 04. Step 3: PCA & Kovariat
# ==========================================

@router.post("/analyses/{analysis_id}/pca", response_model=StageOut, status_code=202, summary="Mode staged, Step 6: jalankan PCA",
             tags=["04. Step 3: PCA & Kovariat"],
             responses={409: {"model": ErrorResponse, "description": "Bukan saatnya langkah ini (WRONG_STAGE) atau bukan mode staged."},
                        422: {"model": ErrorResponse}})
def stage_pca(analysis_id: int, body: PcaRequest, background: BackgroundTasks, response: Response, user: CurrentUser, db: DbSession):
    a = _get_analysis(db, analysis_id, user)
    rdir = _rdir(db, a)
    _require_stage(a, rdir, ("pca", "gwas"))
    data, warnings = _revalidate(db, user, a, rdir, {"use_pca": body.use_pca, "pca_components": body.pca_components})
    if not data["use_pca"]:
        for old in rdir.glob("pca.*"):
            old.unlink()
        db.commit()
        jobs._set_stage(rdir, "pca", "skipped")
        msg = "PCA tidak dipakai. Pilih model lalu jalankan analisis."
        jobs.write_progress(rdir, state="awaiting", stage="pca", percent=50, next_step="gwas", message=msg)
        response.status_code = 200
        return {"analysis_id": a.analysis_id, "status": "awaiting", "next_step": "gwas", "message": msg, "warnings": warnings, "config": data}
    a.status = "queued"
    db.commit()
    jobs.write_progress(rdir, state="queued", stage="queued", percent=35, message="Menunggu giliran dijalankan", error=None, next_step=None)
    background.add_task(jobs.run_analysis_job, a.analysis_id, "pca")
    return {"analysis_id": a.analysis_id, "status": "queued", "next_step": None, "warnings": warnings, "config": data,
            "message": f"PCA dengan {data['pca_components']} komponen dijadwalkan."}

@router.put("/analyses/{analysis_id}/covariates", response_model=StageOut, summary="Mode staged: tetapkan kovariat dari data fenotipe",
            tags=["04. Step 3: PCA & Kovariat"], responses={409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def stage_covariates(analysis_id: int, body: CovariatesRequest, user: CurrentUser, db: DbSession):
    a = _get_analysis(db, analysis_id, user)
    rdir = _rdir(db, a)
    _require_stage(a, rdir, ("pca", "gwas"))
    data, warnings = _revalidate(db, user, a, rdir, {"covariates": body.covariates})
    db.commit()
    nxt = jobs.read_progress(rdir, with_eta=False).get("next_step")
    return {"analysis_id": a.analysis_id, "status": a.status, "next_step": nxt, "warnings": warnings, "config": data,
            "message": "Kovariat disimpan: " + (", ".join(data["covariates"]) if data["covariates"] else "tanpa kovariat tambahan") + "."}

@router.get("/analyses/{analysis_id}/pca", response_model=PcaOut, summary="Koordinat PCA untuk scatter plot",
            tags=["04. Step 3: PCA & Kovariat"], responses={409: {"model": ErrorResponse, "description": "Tahap PCA belum selesai."}})
def pca_points(analysis_id: int, user: CurrentUser, db: DbSession):
    a = _get_analysis(db, analysis_id, user)
    if not a.pca_used:
        raise APIError(404, "PCA_NOT_USED", "Analisis ini dijalankan tanpa PCA.")
    rdir = _rdir(db, a)
    f = rdir / "pca.eigenvec"
    if not f.exists():
        raise APIError(409, "STAGE_NOT_READY", "Tahap PCA belum selesai.")
    lines = f.read_text(encoding="utf-8").strip().splitlines()
    comps = lines[0].lstrip("#").split()[2:]
    trait = {}
    if (rdir / "pheno.txt").exists():
        trait = {ln.split("\t")[1]: ln.split("\t")[2] for ln in (rdir / "pheno.txt").read_text(encoding="utf-8").splitlines()[1:]}
    eig = [float(x) for x in (rdir / "pca.eigenval").read_text(encoding="utf-8").split()] if (rdir / "pca.eigenval").exists() else []
    pca_meta = _read_json(rdir / "pca_summary.json", "PCA_UNAVAILABLE", "Metadata PCA tidak ditemukan.") if (rdir / "pca_summary.json").exists() else {}
    return {"components": comps, "eigenvalues": eig,
            "requested_components": pca_meta.get("requested_components", len(comps)),
            "actual_components": len(comps), "status": pca_meta.get("status", "complete"),
            "warnings": pca_meta.get("warnings", []),
            "variance_explained": pca_meta.get("variance_explained", []),
            "cumulative_variance_explained": pca_meta.get("cumulative_variance_explained", []),
            "points": [{"sample_id": ln.split()[1], "trait": trait.get(ln.split()[1]), "pcs": [float(x) for x in ln.split()[2:]]} for ln in lines[1:] if ln.strip()]}

# ==========================================
# 05. Step 4: Analisis GWAS & Post-Processing
# ==========================================

@router.post("/analyses", response_model=AnalysisCreatedOut, status_code=202, summary="Konfirmasi konfigurasi dan jalankan di latar belakang",
             tags=["05. Step 4: Analisis GWAS & Post-Processing"],
             responses={422: {"model": ErrorResponse, "description": "Konfigurasi belum bisa dijalankan (CONFIG_INVALID)."}})
def create_analysis(cfg: AnalysisConfig, background: BackgroundTasks, user: CurrentUser, db: DbSession):
    acc, ddir, data, errors, warnings = _check(db, user, cfg)
    if errors:
        raise APIError(422, "CONFIG_INVALID", "Konfigurasi belum dapat dijalankan. Perbaiki hal berikut.", errors=errors)
    report = validation.current_valid_report(ddir) or {}
    queued_count = db.scalar(select(func.count()).select_from(GwasAnalysis).where(GwasAnalysis.status.in_(["queued", "running"]))) or 0
    preflight = resource_policy.preflight_analysis(
        dataset_dir=ddir, n_samples=int((report.get("matching") or {}).get("matched", 0)),
        n_variants=int((report.get("genotype") or {}).get("n_variants", 0)),
        use_pca=bool(data.get("use_pca")), queued_or_running=int(queued_count))
    if not preflight.get("accepted"):
        raise APIError(503, preflight.get("code", "RESOURCE_LIMIT"), preflight.get("message", "Resource server tidak mencukupi."),
                       details={"snapshot": preflight.get("snapshot"), "estimate": preflight.get("estimate"),
                                "effective_concurrency": preflight.get("effective_concurrency")},
                       hint="Kurangi ukuran dataset atau minta administrator menambah kapasitas server/menyesuaikan batas konfigurasi.")
    warnings.extend(preflight.get("warnings", []))
    analysis = GwasAnalysis(user_id=user.user_id, accession_id=acc.accession_id, analysis_mode=data["analysis_mode"],
                            qc_skipped=data["qc"]["skip_qc"], pca_used=data["use_pca"], model_used=MODEL_LABEL[data["model"]], status="queued")
    db.add(analysis)
    db.commit()
    rdir = _rdir(db, analysis)
    jobs.prepare_run(rdir, data, user.user_id)
    jobs.write_progress(rdir, execution_mode=data["execution_mode"])
    background.add_task(jobs.run_analysis_job, analysis.analysis_id, "qc" if data["execution_mode"] == "staged" else "all")
    return {"analysis_id": analysis.analysis_id, "status": "queued", "model": analysis.model_used, "warnings": warnings,
            "execution_mode": data["execution_mode"], "next_step": "pca" if data["execution_mode"] == "staged" else None,
            "config": data, "resource_preflight": preflight}

@router.post("/analyses/{analysis_id}/run", response_model=StageOut, status_code=202, summary="Mode staged, Step 7: pilih model dan jalankan analisis asosiasi",
             tags=["05. Step 4: Analisis GWAS & Post-Processing"],
             responses={409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}})
def stage_run(analysis_id: int, body: RunRequest, background: BackgroundTasks, user: CurrentUser, db: DbSession):
    a = _get_analysis(db, analysis_id, user)
    rdir = _rdir(db, a)
    _require_stage(a, rdir, ("gwas",))
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    data, warnings = _revalidate(db, user, a, rdir, changes)
    a.status = "queued"
    db.commit()
    jobs.write_progress(rdir, state="queued", stage="queued", percent=50, message="Menunggu giliran dijalankan", error=None, next_step=None)
    background.add_task(jobs.run_analysis_job, a.analysis_id, "gwas")
    return {"analysis_id": a.analysis_id, "status": "queued", "next_step": None, "warnings": warnings, "config": data,
            "message": f"Analisis asosiasi ({MODEL_LABEL[data['model']]}) dijadwalkan."}

@router.post("/analyses/{analysis_id}/postprocess", response_model=PostprocessOut, summary="Terapkan ambang signifikansi",
             tags=["05. Step 4: Analisis GWAS & Post-Processing"],
             responses={409: {"model": ErrorResponse, "description": "Analisis belum selesai."},
                        422: {"model": ErrorResponse, "description": "Kombinasi metode dan threshold tidak sah."}})
def postprocess(analysis_id: int, body: PostprocessRequest, user: CurrentUser, db: DbSession):
    a = _get_analysis(db, analysis_id, user)
    _require_done(a)
    try:
        rule = results.apply_significance(db, analysis_id, body.method, body.threshold)
    except ValueError as e:
        code = "THRESHOLD_REQUIRED" if body.method == "custom" and body.threshold is None else "SIGNIFICANCE_INVALID"
        raise APIError(422, code, str(e))
    results.save_rule(_rdir(db, a), rule)
    ai_row = db.scalar(select(GwasInterpretation).where(GwasInterpretation.analysis_id == analysis_id))
    if ai_row is not None and ai_row.status in {"queued", "running", "done"}:
        ai_row.status = "stale"
        ai_row.error_code = "AI_INPUT_CHANGED"
        ai_row.error_message = "Aturan signifikansi GWAS berubah setelah payload interpretasi dibuat; interpretasi perlu dijalankan ulang."
        ai_row.finished_at = datetime.now(timezone.utc)
        db.commit()
    return {"analysis_id": analysis_id, "significance": rule,
            "message": f"Ambang signifikansi diterapkan. {rule['n_significant']:,} dari {rule['n_tests']:,} varian dinyatakan signifikan."}

@router.post("/analyses/{analysis_id}/retry", response_model=RetryOut, status_code=202, summary="Coba ulang analisis yang gagal",
             tags=["05. Step 4: Analisis GWAS & Post-Processing"],
             responses={409: {"model": ErrorResponse, "description": "Status analisis tidak mengizinkan percobaan ulang (NOT_RETRYABLE)."}})
def retry_analysis(analysis_id: int, background: BackgroundTasks, user: CurrentUser, db: DbSession):
    a = _get_analysis(db, analysis_id, user)
    if a.status != "failed":
        raise APIError(409, "NOT_RETRYABLE", f"Hanya analisis berstatus failed yang dapat diulang (status saat ini: {a.status}).",
                       details={"status": a.status, "retryable_statuses": ["failed"]})
    rdir = _rdir(db, a)
    plan = jobs.read_progress(rdir, with_eta=False).get("retry_plan") or "all"
    if plan not in jobs.PLAN_STAGES:
        plan = "all"
    a.status = "queued"
    db.commit()
    attempt = jobs.mark_retry(rdir)
    background.add_task(jobs.run_analysis_job, a.analysis_id, plan)
    where = "dari awal" if plan == "all" else f"dari tahap {plan}"
    return {"analysis_id": a.analysis_id, "scheduled": True, "status": "queued", "attempt": attempt, "retry_from": plan,
            "message": f"Percobaan ulang dijadwalkan {where}. Pantau lewat /status."}

# ==========================================
# 06. Step 5: Hasil, Visualisasi & Ekspor
# ==========================================

def _rule(db: Session, a: GwasAnalysis) -> dict | None:
    return results.load_rule(_rdir(db, a))

@router.get("/analyses/{analysis_id}/results/summary", response_model=SummaryOut, summary="Ringkasan hasil GWAS",
            tags=["06. Step 5: Hasil, Visualisasi & Ekspor"],
            responses={409: {"model": ErrorResponse, "description": "Analisis belum selesai."}})
def results_summary(analysis_id: int, user: CurrentUser, db: DbSession):
    a = _get_analysis(db, analysis_id, user)
    _require_done(a)
    rdir = _rdir(db, a)
    meta = json.loads((rdir / "run_meta.json").read_text(encoding="utf-8")) if (rdir / "run_meta.json").exists() else {}
    return {"analysis_id": analysis_id, "model_used": a.model_used, "trait_name": _cfg_of(rdir).get("trait_name"), "n_samples": meta.get("n_samples"),
            "significance": _rule(db, a), **results.summary(db, analysis_id, _rule(db, a), _is_logistic(a))}

@router.get("/analyses/{analysis_id}/results/manhattan", response_model=ManhattanOut, summary="Data Manhattan plot",
            tags=["06. Step 5: Hasil, Visualisasi & Ekspor"],
            responses={409: {"model": ErrorResponse, "description": "Analisis belum selesai."}})
def results_manhattan(analysis_id: int, user: CurrentUser, db: DbSession,
                      max_points: Annotated[int | None, Query(ge=1000, le=2_000_000)] = None,
                      format: Annotated[Literal["columns", "rows"], Query()] = "columns"):
    a = _get_analysis(db, analysis_id, user)
    _require_done(a)
    return results.manhattan(db, analysis_id, max_points or get_settings().max_plot_points, _rule(db, a), as_rows=(format == "rows"))

@router.get("/analyses/{analysis_id}/results/qq", response_model=QQOut, summary="Data QQ plot dan lambda genomic inflation",
            tags=["06. Step 5: Hasil, Visualisasi & Ekspor"],
            responses={409: {"model": ErrorResponse, "description": "Analisis belum selesai."}})
def results_qq(analysis_id: int, user: CurrentUser, db: DbSession,
               max_points: Annotated[int | None, Query(ge=1000, le=2_000_000)] = None):
    a = _get_analysis(db, analysis_id, user)
    _require_done(a)
    return results.qq(db, analysis_id, max_points or get_settings().max_plot_points)


@router.get("/analyses/{analysis_id}/results/{plot}/export", summary="Unduh data sumber Manhattan/QQ lengkap",
            tags=["06. Step 5: Hasil, Visualisasi & Ekspor"],
            responses={200: {"description": "Sumber plot lengkap (CSV, TSV, atau JSON), bukan data display yang di-downsample.", "content": {
                "application/json": {"schema": {"type": "object", "properties": {"metadata": {"type": "object"}, "items": {"type": "array", "items": {"type": "object"}}}}},
                "text/csv": {"schema": {"type": "string", "format": "binary"}},
                "text/tab-separated-values": {"schema": {"type": "string", "format": "binary"}}}}})
def export_plot_source(analysis_id: int, plot: Literal["manhattan", "qq"], user: CurrentUser, db: DbSession,
                       format: Annotated[Literal["csv", "tsv", "json"], Query()] = "csv"):
    a = _get_analysis(db, analysis_id, user)
    _require_done(a)
    filename = f"gwas_analysis_{analysis_id}_{plot}_complete.{format}"
    media = {"csv": "text/csv; charset=utf-8", "tsv": "text/tab-separated-values; charset=utf-8", "json": "application/json"}[format]
    headers = {"Content-Disposition": f'attachment; filename="{filename}"', "X-Content-Type-Options": "nosniff",
               "X-Data-Scope": "complete_source", "X-Downsampled": "false"}
    def stream():
        session = database.SessionLocal()
        try:
            yield from results.export_plot_source(session, analysis_id, plot, format)
        finally:
            session.close()
    return StreamingResponse(stream(), media_type=media, headers=headers)


@router.get("/analyses/{analysis_id}/pca/export", summary="Unduh koordinat dan metadata PCA",
            tags=["06. Step 5: Hasil, Visualisasi & Ekspor"],
            responses={200: {"description": "Koordinat PCA dan metadata (CSV, TSV, atau JSON).", "content": {
                "application/json": {"schema": {"type": "object", "properties": {"metadata": {"type": "object"}, "columns": {"type": "array"}, "points": {"type": "array", "items": {"type": "object"}}}}},
                "text/csv": {"schema": {"type": "string", "format": "binary"}},
                "text/tab-separated-values": {"schema": {"type": "string", "format": "binary"}}}}})
def export_pca(analysis_id: int, user: CurrentUser, db: DbSession,
               format: Annotated[Literal["csv", "tsv", "json"], Query()] = "csv"):
    a = _get_analysis(db, analysis_id, user)
    _require_done(a)
    rdir = _rdir(db, a)
    summary = _read_json(rdir / "pca_summary.json", "PCA_NOT_USED", "PCA tidak tersedia pada analisis ini.")
    pca_file = rdir / "pca.eigenvec"
    if summary.get("status") == "not_used" or not pca_file.exists():
        raise APIError(404, "PCA_NOT_AVAILABLE", "Analisis ini tidak menghasilkan koordinat PCA.")
    filename = f"gwas_analysis_{analysis_id}_pca.{format}"
    media = {"csv": "text/csv; charset=utf-8", "tsv": "text/tab-separated-values; charset=utf-8", "json": "application/json"}[format]
    headers = {"Content-Disposition": f'attachment; filename="{filename}"', "X-Content-Type-Options": "nosniff"}
    def stream():
        import csv, io
        with pca_file.open("r", encoding="utf-8") as handle:
            header = handle.readline().lstrip("#").split()
            if format == "json":
                meta = {k: summary.get(k) for k in ("requested_components", "actual_components", "status", "warnings", "eigenvalues", "variance_explained", "cumulative_variance_explained", "samples_used", "variants_after_qc")}
                yield '{"metadata":' + json.dumps(meta, ensure_ascii=False) + ',"columns":' + json.dumps(header) + ',"points":['
                first = True
                for line in handle:
                    cols = line.split()
                    if len(cols) >= 2:
                        item = {"FID": cols[0], "IID": cols[1], **{header[i]: float(cols[i]) for i in range(2, min(len(cols), len(header)))}}
                        yield ("" if first else ",") + json.dumps(item, ensure_ascii=False)
                        first = False
                yield "]}"
            else:
                delim = "," if format == "csv" else "\t"
                yield "# PCA metadata: " + json.dumps(summary, ensure_ascii=False, separators=(",", ":")) + "\n"
                yield delim.join(header) + "\n"
                for line in handle:
                    cols = line.split()
                    if cols:
                        buf = io.StringIO(); csv.writer(buf, delimiter=delim, lineterminator="\n").writerow(cols)
                        yield buf.getvalue()
    return StreamingResponse(stream(), media_type=media, headers=headers)

@router.get("/analyses/{analysis_id}/results/significant", response_model=SignificantOut, summary="Tabel varian signifikan",
            tags=["06. Step 5: Hasil, Visualisasi & Ekspor"],
            responses={409: {"model": ErrorResponse, "description": "Analisis belum selesai."}})
def results_significant(analysis_id: int, user: CurrentUser, db: DbSession,
                        limit: Annotated[int, Query(ge=1, le=1000)] = 100, offset: Annotated[int, Query(ge=0)] = 0,
                        threshold: Annotated[float | None, Query(gt=0.0, le=1.0)] = None):
    a = _get_analysis(db, analysis_id, user)
    _require_done(a)
    return results.significant(db, analysis_id, limit, offset, threshold, _rule(db, a), _is_logistic(a))

@router.get("/analyses/{analysis_id}/results/top", response_model=TopOut, summary="Varian terbaik (tabel Top SNP)",
            tags=["06. Step 5: Hasil, Visualisasi & Ekspor"],
            responses={409: {"model": ErrorResponse, "description": "Analisis belum selesai."}})
def results_top(analysis_id: int, user: CurrentUser, db: DbSession, limit: Annotated[int, Query(ge=1, le=200)] = 10):
    a = _get_analysis(db, analysis_id, user)
    _require_done(a)
    return results.top(db, analysis_id, limit, _rule(db, a), _is_logistic(a))

@router.get("/analyses/{analysis_id}/interpretation", response_model=AIInterpretationOut,
            summary="Status dan hasil interpretasi statistik AI",
            tags=["06. Step 5: Hasil, Visualisasi & Ekspor"])
def get_ai_interpretation(analysis_id: int, user: CurrentUser, db: DbSession):
    a = _get_analysis(db, analysis_id, user)
    _require_done(a)
    row = db.scalar(select(GwasInterpretation).where(GwasInterpretation.analysis_id == analysis_id))
    if row is None:
        return {"analysis_id": analysis_id, "status": "unavailable", "error_code": "AI_NOT_QUEUED",
                "message": "Pekerjaan interpretasi AI belum dibuat."}
    return {"analysis_id": analysis_id, "status": row.status, "provider": row.provider, "model": row.model,
            "prompt_version": row.prompt_version, "schema_version": row.schema_version,
            "attempts": row.attempts, "input_hash": row.input_hash, "result": row.result_json,
            "error_code": row.error_code, "error_message": row.error_message, "token_usage": row.token_usage,
            "queued_at": row.queued_at, "started_at": row.started_at, "finished_at": row.finished_at}


@router.get("/analyses/{analysis_id}/interpretation/export", summary="Unduh hasil interpretasi statistik AI (JSON)",
            tags=["06. Step 5: Hasil, Visualisasi & Ekspor"],
            responses={200: {"description": "Berkas hasil interpretasi statistik AI.",
                             "content": {"application/json": {"schema": {"type": "string", "format": "binary"}}}},
                       404: {"model": ErrorResponse, "description": "Hasil AI belum tersedia atau analisis bukan milik pengguna."}})
def export_ai_interpretation(analysis_id: int, user: CurrentUser, db: DbSession,
                             format: Literal["json"] = "json"):
    a = _get_analysis(db, analysis_id, user)
    _require_done(a)
    row = db.scalar(select(GwasInterpretation).where(GwasInterpretation.analysis_id == analysis_id))
    if row is None or row.status != "done" or row.result_json is None:
        raise APIError(404, "AI_RESULT_NOT_AVAILABLE", "Hasil interpretasi AI belum tersedia untuk diunduh.")
    payload = {
        "analysis_id": analysis_id,
        "status": row.status,
        "provider": row.provider,
        "model": row.model,
        "prompt_version": row.prompt_version,
        "schema_version": row.schema_version,
        "input_hash": row.input_hash,
        "attempts": row.attempts,
        "token_usage": row.token_usage,
        "result": row.result_json,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }
    filename = f"gwas_analysis_{analysis_id}_ai_interpretation.{format}"
    return Response(content=json.dumps(payload, ensure_ascii=False, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"',
                             "X-Content-Type-Options": "nosniff"})


@router.post("/analyses/{analysis_id}/interpretation/retry", response_model=AIInterpretationRetryOut, status_code=202,
             summary="Jadwalkan ulang interpretasi AI yang gagal/tidak tersedia",
             tags=["06. Step 5: Hasil, Visualisasi & Ekspor"])
def retry_ai_interpretation(analysis_id: int, user: CurrentUser, db: DbSession):
    a = _get_analysis(db, analysis_id, user)
    _require_done(a)
    row = db.scalar(select(GwasInterpretation).where(GwasInterpretation.analysis_id == analysis_id))
    prompt_is_outdated = bool(
        row and row.status == "done" and row.prompt_version != ai_jobs.PROMPT_VERSION
    )
    if row and row.status not in {"failed", "unavailable", "stale"} and not prompt_is_outdated:
        raise APIError(409, "AI_NOT_RETRYABLE", f"Interpretasi berstatus {row.status} dan tidak dapat diulang.",
                       details={"retryable_statuses": ["failed", "unavailable", "stale"],
                                "prompt_version_current": ai_jobs.PROMPT_VERSION,
                                "prompt_version_saved": row.prompt_version})
    result = ai_jobs.enqueue_interpretation(analysis_id)
    return {"analysis_id": analysis_id, **result, "message": "Permintaan interpretasi diproses pada antrean AI terpisah; status GWAS tetap done."}


_EXPORT_DOC = {
    200: {"description": "Berkas hasil analisis.",
          "headers": {"Content-Disposition": {"schema": {"type": "string"}}},
          "content": {mime.split(";")[0]: {"schema": {"type": "string", "format": "binary"}} for mime in results.EXPORT_FORMATS.values()}},
    409: {"model": ErrorResponse, "description": "Analisis belum selesai."},
}

@router.get("/analyses/{analysis_id}/export", response_class=Response, summary="Unduh hasil analisis (CSV, TSV, JSON, atau Excel)",
            tags=["06. Step 5: Hasil, Visualisasi & Ekspor"], responses=_EXPORT_DOC)
def export_results(analysis_id: int, user: CurrentUser, db: DbSession, only_significant: bool = False,
                   format: Annotated[str, Query()] = "csv"):
    a = _get_analysis(db, analysis_id, user)
    _require_done(a)
    fmt = ui_adapter.parse_export_format(format)
    if fmt not in results.EXPORT_FORMATS:
        raise APIError(400, "INVALID_EXPORT_FORMAT", f"Format ekspor '{format}' tidak didukung. Pilihan: {', '.join(results.EXPORT_FORMATS)}.")
    logistic = _is_logistic(a)
    name = f"gwas_analysis_{analysis_id}{'_significant' if only_significant else ''}.{fmt}"
    headers = {"Content-Disposition": f'attachment; filename="{name}"', "X-Content-Type-Options": "nosniff"}

    if fmt == "xlsx":
        def xlsx_stream():
            session = database.SessionLocal()
            try:
                yield from results.export_xlsx_chunks(session, analysis_id, only_significant, logistic)
            finally:
                session.close()
        return StreamingResponse(xlsx_stream(), media_type=results.EXPORT_FORMATS[fmt], headers=headers)

    def stream():
        s = database.SessionLocal()
        try:
            gen = (results.export_json_chunks(s, analysis_id, only_significant, logistic) if fmt == "json"
                   else results.export_text_lines(s, analysis_id, only_significant, logistic, "," if fmt == "csv" else "\t"))
            buf, size = [], 0
            for line in gen:
                buf.append(line)
                size += len(line)
                if size >= 65536:
                    yield "".join(buf)
                    buf, size = [], 0
            if buf:
                yield "".join(buf)
        finally:
            s.close()

    return StreamingResponse(stream(), media_type=results.EXPORT_FORMATS[fmt], headers=headers)

CONFIG_EXPORT_VERSION = "1.0"

@router.get("/analyses/{analysis_id}/config/export", response_model=ConfigExportOut, summary="Unduh konfigurasi dan versi perangkat lunak (JSON)",
            tags=["06. Step 5: Hasil, Visualisasi & Ekspor"])
def export_config(analysis_id: int, user: CurrentUser, db: DbSession):
    a = _get_analysis(db, analysis_id, user)
    rdir = _rdir(db, a)
    params = _read_json(rdir / "params.json", "NO_PARAMS", "Parameter analisis tidak ditemukan.")
    meta = json.loads((rdir / "run_meta.json").read_text(encoding="utf-8")) if (rdir / "run_meta.json").exists() else {}
    acc = db.get(Accession, a.accession_id)
    report = validation.load_report(_ddir(user, acc)) or {}
    body = {"schema_version": CONFIG_EXPORT_VERSION, "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "analysis_id": a.analysis_id, "status": a.status, "model_used": a.model_used,
            "dataset": {"dataset_id": acc.accession_id, "name": acc.sample_code, "organism": acc.organism, "source_domain": acc.source_domain,
                        "n_samples_matched": (report.get("matching") or {}).get("matched"),
                        "n_variants": (report.get("genotype") or {}).get("n_variants")},
            "configuration": params["config"], "significance_applied": _rule(db, a),
            "software_versions": meta.get("versions"), "duration_seconds": meta.get("duration_seconds")}
    text = ConfigExportOut(**body).model_dump_json(indent=2)
    return Response(content=text, media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="gwas_analysis_{analysis_id}_config.json"', "X-Content-Type-Options": "nosniff"})

# ==========================================
# 07. Konfigurasi & Riwayat
# ==========================================

@router.get("/config/parameters", response_model=ParametersOut, summary="Penjelasan setiap parameter",
            tags=["07. Konfigurasi & Riwayat"])
def config_parameters(user: CurrentUser):
    return {"parameters": PARAMETER_DOCS, "thresholds": {k: v["description"] for k, v in significance.METHODS.items()}}

@router.get("/config/presets", response_model=PresetsOut, summary="Domain riset dan preset Guided Mode",
            tags=["07. Konfigurasi & Riwayat"])
def config_presets(user: CurrentUser, db: DbSession,
                   dataset_id: Annotated[int | None, Query(description="Bila diisi, domain milik dataset itu ditandai.")] = None):
    recorded = None
    if dataset_id is not None:
        recorded = (_get_dataset(db, dataset_id, user).source_domain or "").lower()
    domains = [{"value": k, "label": k.capitalize(), "description": v["deskripsi"],
                **({"is_recorded_domain": k == recorded} if dataset_id is not None else {})} for k, v in PRESETS.items()]
    return {"domains": domains, "presets": PRESETS}

@router.get("/config/models", response_model=ModelsOut, summary="Daftar model GWAS beserta syarat masing-masing",
            tags=["07. Konfigurasi & Riwayat"])
def config_models(user: CurrentUser):
    return {"models": list(MODEL_CATALOG.values()), "auto_choice": {"binary": "logistic", "quantitative": "linear"},
            "note": "model = auto memilih dari tipe trait. Pilihan manual harus cocok dengan tipe trait."}

@router.get("/config/options", response_model=OptionsOut, summary="Isi dropdown di Step 3, 4, 6, 8, dan 10",
            tags=["07. Konfigurasi & Riwayat"])
def config_options(user: CurrentUser):
    methods = [{"value": k, "label": v["label"], "description": v["description"], "uses_threshold": v["uses_threshold"],
                "threshold_meaning": v.get("threshold_meaning")} for k, v in significance.METHODS.items()]
    return {"options": {**FRONTEND_OPTIONS, "significance_methods": methods}}

def _check(db: Session, user: User, cfg: AnalysisConfig):
    acc = _get_dataset(db, cfg.dataset_id, user)
    ddir = _ddir(user, acc)
    st = validation.dataset_status(ddir)["status"]
    data, errors, warnings = check_config(cfg, validation.current_valid_report(ddir), acc.source_domain, cfg.model_fields_set, st)
    if not errors and data["model"] == "lmm":
        try:
            resolve_binary(get_settings().gemma_bin)
        except ToolError as e:
            errors.append({"code": "TOOL_NOT_AVAILABLE", "message": "Model LMM memerlukan program GEMMA, tetapi GEMMA tidak ditemukan di server.",
                           "hint": e.hint})
    return acc, ddir, data, errors, warnings

@router.post("/config/check", response_model=ConfigCheckOut, summary="Periksa konfigurasi tanpa menjalankan analisis",
             tags=["07. Konfigurasi & Riwayat"])
def config_check(cfg: AnalysisConfig, user: CurrentUser, db: DbSession):
    acc, ddir, data, errors, warnings = _check(db, user, cfg)
    out = {"valid": not errors, "errors": errors, "warnings": warnings, "config": data, "estimate": None}
    report = validation.current_valid_report(ddir)
    if report and report["status"] == "valid":
        out["estimate"] = ui_adapter.estimate_runtime(report["matching"]["matched"], report["genotype"].get("n_variants") or 0,
                                                      data["model"], data["use_pca"])
    return out

def _analysis_item(db: Session, a: GwasAnalysis) -> dict:
    rdir = _rdir(db, a)
    try:
        params = json.loads((rdir / "params.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        params = {}
    cfg = params.get("config", {})
    return {"analysis_id": a.analysis_id, "dataset_id": a.accession_id, "status": a.status, "analysis_mode": a.analysis_mode,
            "model_used": a.model_used, "qc_skipped": a.qc_skipped, "pca_used": a.pca_used, "trait_name": cfg.get("trait_name"),
            "execution_mode": cfg.get("execution_mode", "auto"), "created_at": params.get("created_at"),
            "finished_at": jobs.read_progress(rdir, with_eta=False).get("finished_at")}

@router.get("/analyses", response_model=AnalysisListOut, summary="Riwayat analisis milik saya",
            tags=["07. Konfigurasi & Riwayat"])
def list_analyses(user: CurrentUser, db: DbSession):
    rows = db.scalars(select(GwasAnalysis).where(GwasAnalysis.user_id == user.user_id).order_by(GwasAnalysis.analysis_id.desc())).all()
    return {"items": [_analysis_item(db, a) for a in rows]}

@router.get("/analyses/{analysis_id}", response_model=AnalysisDetailOut, summary="Detail lengkap analisis (konfigurasi, progres, waktu)",
            tags=["07. Konfigurasi & Riwayat"])
def get_analysis(analysis_id: int, user: CurrentUser, db: DbSession):
    a = _get_analysis(db, analysis_id, user)
    rdir = _rdir(db, a)
    params = _read_json(rdir / "params.json", "NO_PARAMS", "Parameter analisis tidak ditemukan.")
    cfg, prog = params["config"], jobs.read_progress(rdir, with_eta=False)
    configuration = {
        "trait_name": cfg.get("trait_name"), "trait_type": cfg.get("trait_type"), "method": cfg.get("model"),
        "qc": not cfg["qc"]["skip_qc"], "qc_parameters": cfg["qc"], "pca": cfg["use_pca"], "number_of_pcs": cfg.get("pca_components") if cfg["use_pca"] else None,
        "covariates": cfg.get("covariates", []), "covariate_sources": cfg.get("covariate_sources"),
        "significance_method": cfg.get("significance_method"), "significance_threshold": cfg.get("significance_threshold"),
        "analysis_mode": cfg.get("analysis_mode"), "execution_mode": cfg.get("execution_mode", "auto")}
    return {"analysis_id": a.analysis_id, "dataset_id": a.accession_id, "status": a.status, "model_used": a.model_used,
            "configuration": configuration,
            "progress": {"percent": prog.get("percent", 0), "stage": prog.get("stage"), "message": prog.get("message")},
            "attempts": prog.get("attempts", 1), "result_available": a.status == "done",
            "created_at": params.get("created_at"), "updated_at": prog.get("updated_at"), "finished_at": prog.get("finished_at")}

@router.get("/analyses/{analysis_id}/status", response_model=AnalysisStatusOut, summary="Status ringan untuk polling (progress bar)",
            tags=["07. Konfigurasi & Riwayat"])
def analysis_status(analysis_id: int, user: CurrentUser, db: DbSession):
    a = _get_analysis(db, analysis_id, user)
    rdir = _rdir(db, a)
    p = jobs.read_progress(rdir)
    return {"analysis_id": a.analysis_id, "status": a.status, "stage": p.get("stage"), "stages": jobs.stage_list(p),
            "percent": p.get("percent", 0), "message": p.get("message"), "elapsed_seconds": p.get("elapsed_seconds"),
            "estimated_remaining_seconds": p.get("estimated_remaining_seconds"), "error": p.get("error"),
            "started_at": p.get("started_at"), "finished_at": p.get("finished_at"), "updated_at": p.get("updated_at"),
            "next_step": p.get("next_step"), "execution_mode": _cfg_of(rdir).get("execution_mode", "auto"),
            "attempts": p.get("attempts", 1), "can_continue": a.status == "awaiting", "can_retry": a.status == "failed"}

@router.get("/analyses/{analysis_id}/log", response_model=LogOut, summary="Log eksekusi, dibaca bertahap",
            tags=["07. Konfigurasi & Riwayat"])
def analysis_log(analysis_id: int, user: CurrentUser, db: DbSession,
                 offset: Annotated[int, Query(ge=0, description="Posisi awal pembacaan.")] = 0,
                 limit: Annotated[int, Query(ge=1, le=1000, description="Jumlah baris log maksimal.")] = 200):
    a = _get_analysis(db, analysis_id, user)
    path = _rdir(db, a) / "analysis.log"
    finished = a.status in ("done", "failed")
    logs: list[str] = []
    pos, size = offset, 0
    if path.exists():
        size = path.stat().st_size
        pos = min(offset, size)
        with open(path, "rb") as f:
            f.seek(pos)
            while len(logs) < limit:
                line = f.readline()
                if not line:
                    break
                if not line.endswith(b"\n") and not finished:
                    break
                pos += len(line)
                logs.append(line.decode("utf-8", errors="replace").rstrip("\r\n"))
    return {"logs": logs, "offset": offset, "limit": limit, "next_offset": pos, "has_more": len(logs) == limit and pos < size,
            "finished": finished, "waiting": a.status == "awaiting"}

@router.websocket("/analyses/{analysis_id}/ws")
async def analysis_stream(websocket: WebSocket, analysis_id: int, token: str = Query(...)):
    try:
        user_id = decode_access_token(token)
    except jwt.InvalidTokenError:
        await websocket.close(code=4401)
        return
    db = database.SessionLocal()
    try:
        user = db.get(User, user_id)
        a = db.get(GwasAnalysis, analysis_id)
        if user is None or not user.is_active or a is None or a.user_id != user_id:
            await websocket.close(code=4404)
            return
        rdir = _rdir(db, a)
    finally:
        db.close()

    await websocket.accept()
    offset = 0
    try:
        while True:
            progress = jobs.read_progress(rdir)
            log = rdir / "analysis.log"
            if log.exists():
                with open(log, "rb") as f:
                    f.seek(offset)
                    chunk = f.read(200_000)
                offset += len(chunk)
                lines = chunk.decode("utf-8", errors="replace").splitlines()
                if lines:
                    await websocket.send_json({"type": "log", "lines": lines})
            await websocket.send_json({"type": "progress", **progress, "stages": jobs.stage_list(progress)})
            if progress.get("state") in ("done", "failed", "awaiting"):
                await websocket.close()
                return
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        return