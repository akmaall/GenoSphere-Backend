"""Persistent AI interpretation queue, isolated from the main GWAS worker."""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Lock

import httpx
from sqlalchemy import select, update

from app import database
from app.config import get_settings
from app.models import GwasAnalysis, GwasInterpretation
from app.services.ai_interpretation import (
    AIResponseValidationError,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    build_ai_payload,
    get_provider,
    payload_hash,
    validate_interpretation,
)

logger = logging.getLogger("genosphere.ai")
_executor: ThreadPoolExecutor | None = None
_executor_lock = Lock()


def _now():
    return datetime.now(timezone.utc)


def _executor_instance():
    global _executor
    if _executor is None:
        with _executor_lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(
                    max_workers=max(1, get_settings().ai_max_concurrent_jobs),
                    thread_name_prefix="gwas-ai",
                )
    return _executor


def enqueue_interpretation(analysis_id: int) -> dict:
    """Queue or manually retry an AI job without changing GWAS status."""
    settings = get_settings()
    db = database.SessionLocal()
    try:
        analysis = db.get(GwasAnalysis, analysis_id)
        if analysis is None or analysis.status != "done":
            return {"status": "unavailable", "error_code": "GWAS_NOT_DONE"}

        row = db.scalar(
            select(GwasInterpretation).where(GwasInterpretation.analysis_id == analysis_id)
        )
        if row and row.status in {"queued", "running"}:
            return {"status": row.status}
        if row and row.status == "done" and row.prompt_version == PROMPT_VERSION:
            return {"status": "done"}

        now = _now()
        if row is None:
            row = GwasInterpretation(
                analysis_id=analysis_id,
                status="queued",
                provider=settings.ai_provider,
                model=settings.ai_model,
                prompt_version=PROMPT_VERSION,
                schema_version=SCHEMA_VERSION,
                attempts=0,
                queued_at=now,
            )
            db.add(row)
        else:
            # Keep attempts cumulative for audit history; manual retry is a new queued execution.
            row.status = "queued"
            row.error_code = None
            row.error_message = None
            row.result_json = None
            row.token_usage = None
            row.cost_usd = None
            row.input_hash = None
            row.provider = settings.ai_provider
            row.model = settings.ai_model
            row.prompt_version = PROMPT_VERSION
            row.schema_version = SCHEMA_VERSION
            row.queued_at = now
            row.started_at = None
            row.finished_at = None

        if not settings.ai_api_key:
            row.status = "unavailable"
            row.error_code = "AI_NOT_CONFIGURED"
            row.error_message = "Provider AI belum dikonfigurasi; hasil GWAS tetap tersedia."
            row.finished_at = now
            db.commit()
            return {"status": "unavailable", "error_code": row.error_code}

        if settings.ai_provider != "openai_compatible":
            row.status = "unavailable"
            row.error_code = "AI_PROVIDER_UNSUPPORTED"
            row.error_message = "Adapter provider yang dikonfigurasi belum tersedia."
            row.finished_at = now
            db.commit()
            return {"status": "unavailable", "error_code": row.error_code}

        db.commit()
        _executor_instance().submit(_run, analysis_id)
        return {"status": "queued"}
    except Exception:
        db.rollback()
        logger.exception("Tidak dapat mengantre interpretasi AI %s", analysis_id)
        return {"status": "unavailable", "error_code": "AI_QUEUE_ERROR"}
    finally:
        db.close()


def _classify_error(exc: Exception) -> tuple[str, str]:
    message = str(exc).strip()
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)) or "timeout" in type(exc).__name__.lower() or message == "AI_TIMEOUT":
        code = "AI_TIMEOUT"
        detail = "Provider AI melewati batas waktu respons."
    elif message == "AI_SCHEMA_MODE_UNSUPPORTED":
        code = "AI_SCHEMA_UNSUPPORTED"
        detail = "Ollama tidak menerima response_format JSON Schema pada endpoint ini. Periksa kompatibilitas API Ollama."
    elif message in {"AI_OUTPUT_TRUNCATED", "AI_INVALID_JSON", "AI_EMPTY_CONTENT", "AI_INVALID_RESPONSE"}:
        code = message
        detail = {
            "AI_OUTPUT_TRUNCATED": "Output AI mencapai batas token sebelum JSON selesai.",
            "AI_INVALID_JSON": "Provider AI tidak menghasilkan JSON yang valid.",
            "AI_EMPTY_CONTENT": "Provider AI mengembalikan konten kosong.",
            "AI_INVALID_RESPONSE": "Format respons provider AI tidak dikenali.",
        }[message]
    elif message.startswith("AI_HTTP_"):
        code = "AI_PROVIDER_ERROR"
        detail = message
    elif message in {"AI_CONNECTION_ERROR", "AI_NOT_CONFIGURED", "AI_PROVIDER_UNSUPPORTED"}:
        code = message
        detail = message
    else:
        code = "AI_PROVIDER_ERROR"
        detail = message[:240] if message else "Tidak ada detail tambahan."
    return code, detail[:500]

def _run(analysis_id: int) -> None:
    db = database.SessionLocal()
    row = None
    try:
        claimed = db.execute(
            update(GwasInterpretation)
            .where(
                GwasInterpretation.analysis_id == analysis_id,
                GwasInterpretation.status == "queued",
            )
            .values(
                status="running",
                attempts=GwasInterpretation.attempts + 1,
                started_at=_now(),
                finished_at=None,
                error_code=None,
                error_message=None,
            )
        )
        db.commit()
        if not claimed.rowcount:
            return

        row = db.scalar(
            select(GwasInterpretation).where(GwasInterpretation.analysis_id == analysis_id)
        )
        analysis = db.get(GwasAnalysis, analysis_id)
        if row is None or analysis is None or analysis.status != "done":
            raise RuntimeError("GWAS_NOT_DONE")

        settings = get_settings()
        # Queue waiting time is excluded: deadline begins when this worker claims the job.
        job_deadline_seconds = max(
            60.0,
            float(getattr(settings, "ai_job_deadline_seconds", 360)),
        )
        deadline = time.monotonic() + job_deadline_seconds

        payload = build_ai_payload(db, analysis)
        row.input_hash = payload_hash(payload)
        db.commit()

        if not settings.ai_api_key:
            row.status = "unavailable"
            row.error_code = "AI_NOT_CONFIGURED"
            row.error_message = "Provider AI tidak dikonfigurasi; hasil GWAS tetap tersedia."
            row.finished_at = _now()
            db.commit()
            return

        result = None
        usage = None
        last_error: tuple[str, str] | None = None
        max_attempts = max(1, int(settings.ai_max_attempts))

        for attempt in range(max_attempts):
            if attempt > 0:
                row.attempts += 1
                db.commit()

            remaining = deadline - time.monotonic() - 10.0
            if remaining <= 1:
                last_error = (
                    "AI_DEADLINE_EXCEEDED",
                    "Batas total waktu pekerjaan interpretasi AI telah habis.",
                )
                break

            request_timeout = min(float(settings.ai_timeout_seconds), remaining)
            try:
                result, usage = get_provider(settings.ai_provider).generate(
                    payload,
                    timeout_seconds=request_timeout,
                )
                result = validate_interpretation(result, payload)
                last_error = None
                break
            except AIResponseValidationError as exc:
                last_error = ("AI_RESPONSE_INVALID", str(exc)[:500])
                logger.warning("AI response schema invalid for analysis %s: %s", analysis_id, last_error[1])
                break
            except Exception as exc:
                last_error = _classify_error(exc)
                logger.warning(
                    "AI attempt %s/%s gagal untuk analysis %s: %s",
                    attempt + 1,
                    max_attempts,
                    analysis_id,
                    last_error[0],
                )
                if attempt + 1 < max_attempts:
                    backoff = min(8, 2 ** attempt)
                    if time.monotonic() + backoff + 10 >= deadline:
                        break
                    time.sleep(backoff)

        db.refresh(row)
        if row.status != "running":
            return

        current_payload = build_ai_payload(db, analysis)
        if payload_hash(current_payload) != row.input_hash:
            row.status = "stale"
            row.error_code = "AI_INPUT_CHANGED"
            row.error_message = "Data statistik berubah saat interpretasi berjalan; hasil provider dibuang."
            row.finished_at = _now()
        elif last_error:
            row.status = "failed"
            row.error_code, row.error_message = last_error
            row.finished_at = _now()
        else:
            row.status = "done"
            row.result_json = result
            row.token_usage = usage
            if isinstance(usage, dict):
                raw_cost = usage.get("cost_usd", usage.get("cost"))
                try:
                    row.cost_usd = float(raw_cost) if raw_cost is not None else None
                except (TypeError, ValueError):
                    row.cost_usd = None
            row.error_code = None
            row.error_message = None
            row.finished_at = _now()
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Worker interpretasi AI gagal untuk analisis %s", analysis_id)
        try:
            row = db.scalar(
                select(GwasInterpretation).where(GwasInterpretation.analysis_id == analysis_id)
            )
            if row and row.status == "running":
                row.status = "failed"
                row.error_code = "AI_WORKER_ERROR"
                row.error_message = "Worker interpretasi mengalami kesalahan internal. Periksa log backend."
                row.finished_at = _now()
                db.commit()
        except Exception:
            db.rollback()
    finally:
        db.close()


def recover_interpretation_jobs() -> int:
    """Requeue AI tasks interrupted by an API process restart."""
    db = database.SessionLocal()
    try:
        db.execute(
            update(GwasInterpretation)
            .where(GwasInterpretation.status == "running")
            .values(
                status="queued",
                error_code="AI_WORKER_INTERRUPTED",
                error_message="Worker AI terhenti saat server dimulai ulang.",
                started_at=None,
            )
        )
        ids = list(
            db.scalars(
                select(GwasInterpretation.analysis_id).where(GwasInterpretation.status == "queued")
            ).all()
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Gagal memulihkan AI jobs")
        return 0
    finally:
        db.close()

    for analysis_id in ids:
        _executor_instance().submit(_run, analysis_id)
    return len(ids)
