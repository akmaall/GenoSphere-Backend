from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

# Make the backend project root importable when executed directly.
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import database
from app.models import GwasInterpretation
from app.services.ai_interpretation import PROMPT_VERSION
from app.services.ai_jobs import enqueue_interpretation

ANALYSIS_ID = 44
MAX_WAIT_SECONDS = 420
TERMINAL_STATUSES = {"done", "failed", "unavailable", "stale"}


def serialize(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def read_row():
    db = database.SessionLocal()
    try:
        return db.scalar(
            select(GwasInterpretation).where(
                GwasInterpretation.analysis_id == ANALYSIS_ID
            )
        )
    finally:
        db.close()


def backup_previous_result(row):
    report_dir = Path("tools/reports")
    report_dir.mkdir(parents=True, exist_ok=True)

    fields = [
        "analysis_id",
        "status",
        "attempts",
        "provider",
        "model",
        "prompt_version",
        "schema_version",
        "result_json",
        "token_usage",
        "cost_usd",
        "input_hash",
        "error_code",
        "error_message",
        "queued_at",
        "started_at",
        "finished_at",
    ]

    snapshot = {
        field: getattr(row, field, None)
        for field in fields
    }

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = report_dir / (
        f"analysis44_ai_before_v8_{timestamp}.json"
    )

    path.write_text(
        json.dumps(
            snapshot,
            ensure_ascii=False,
            indent=2,
            default=serialize,
        ),
        encoding="utf-8",
    )

    return path


def main():
    print("=" * 65)
    print("GENOSPHERE AI QUEUE INTEGRATION TEST")
    print("=" * 65)

    print("Analysis ID:", ANALYSIS_ID)
    print("Current prompt version:", PROMPT_VERSION)

    if not PROMPT_VERSION.startswith(
        "statistical-narrative-v8"
    ):
        raise RuntimeError(
            "Source code belum menggunakan prompt v8."
        )

    row = read_row()

    if row is None:
        raise RuntimeError(
            "Record GwasInterpretation Analysis 44 tidak ditemukan."
        )

    print("\n========== PREVIOUS DATABASE STATE ==========")
    print("Status:", row.status)
    print("Prompt:", row.prompt_version)
    print("Attempts:", row.attempts)
    print("Has previous result:", row.result_json is not None)

    if (
        row.status == "done"
        and row.prompt_version != PROMPT_VERSION
    ):
        backup_path = backup_previous_result(row)
        print("[OK] Previous result backed up:", backup_path)

    print("\n========== ENQUEUE AI JOB ==========")

    response = enqueue_interpretation(ANALYSIS_ID)
    print("Enqueue response:", response)

    if response.get("status") == "unavailable":
        raise RuntimeError(
            f"AI job tidak dapat diantrekan: {response}"
        )

    if response.get("status") == "done":
        print(
            "[INFO] Hasil dengan prompt version saat ini "
            "sudah tersedia."
        )
        return

    started = time.monotonic()
    last_report = None

    print("\n========== MONITOR WORKER ==========")

    while time.monotonic() - started < MAX_WAIT_SECONDS:
        row = read_row()

        if row is None:
            raise RuntimeError("Record AI hilang.")

        elapsed = time.monotonic() - started

        if row.status != last_report:
            print(
                f"[{elapsed:.1f}s] "
                f"Status: {row.status} | "
                f"Attempts: {row.attempts}"
            )
            last_report = row.status

        if row.status in TERMINAL_STATUSES:
            break

        time.sleep(5)

    else:
        print("[WARNING] Monitoring mencapai batas waktu.")

    elapsed = time.monotonic() - started
    row = read_row()

    print("\n========== FINAL DATABASE STATE ==========")
    print("Status:", row.status)
    print("Prompt version:", row.prompt_version)
    print("Expected version:", PROMPT_VERSION)
    print("Attempts:", row.attempts)
    print("Provider:", row.provider)
    print("Model:", row.model)
    print("Error code:", row.error_code)
    print("Error message:", row.error_message)
    print("Finished at:", row.finished_at)
    print("Elapsed:", f"{elapsed:.2f} seconds")

    if isinstance(row.token_usage, dict):
        print("Token usage:", row.token_usage)

    if isinstance(row.result_json, dict):
        print("Result keys:", list(row.result_json.keys()))

    success = (
        row.status == "done"
        and row.prompt_version == PROMPT_VERSION
        and isinstance(row.result_json, dict)
    )

    if success:
        print("\n[SUCCESS] AI QUEUE INTEGRATION TEST PASSED.")
    else:
        print("\n[FAIL] AI QUEUE INTEGRATION TEST NOT PASSED.")


if __name__ == "__main__":
    main()
