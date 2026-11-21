"""Resource-aware limits for upload and GWAS preflight.

All estimates are planning heuristics, not guarantees about PLINK2/GEMMA internals. Hard
limits remain administrator-configurable and are bounded by the resources detectable on
this host. When a resource cannot be detected, configured conservative caps are used.
"""
from __future__ import annotations

import math
import os
import shutil
from pathlib import Path
from typing import Any

from app.config import get_settings

_MIB = 1024 * 1024
_GIB = 1024 * _MIB


def _read_int(path: str) -> int | None:
    try:
        value = Path(path).read_text(encoding="ascii").strip()
        if value.lower() == "max":
            return None
        number = int(value)
        return number if number > 0 else None
    except (OSError, ValueError):
        return None


def _proc_memory() -> tuple[int | None, int | None]:
    total = available = None
    try:
        fields = {}
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            key, raw = line.split(":", 1)
            fields[key] = int(raw.strip().split()[0]) * 1024
        total = fields.get("MemTotal")
        available = fields.get("MemAvailable")
    except (OSError, ValueError, IndexError):
        pass
    # Respect cgroup limits where present (common in Docker/Kubernetes).
    limit = _read_int("/sys/fs/cgroup/memory.max") or _read_int("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    usage = _read_int("/sys/fs/cgroup/memory.current") or _read_int("/sys/fs/cgroup/memory/memory.usage_in_bytes")
    if limit and limit < 1 << 60:
        total = min(total, limit) if total else limit
        cgroup_available = max(0, limit - (usage or 0))
        available = min(available, cgroup_available) if available is not None else cgroup_available
    return total, available


def _effective_cpu_count() -> int | None:
    detected = os.cpu_count()
    try:
        quota, period = Path("/sys/fs/cgroup/cpu.max").read_text(encoding="ascii").split()[:2]
        if quota != "max":
            detected = min(detected or int(quota), max(1, math.ceil(int(quota) / int(period))))
    except (OSError, ValueError, ZeroDivisionError):
        try:
            quota = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text())
            period = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text())
            if quota > 0 and period > 0:
                detected = min(detected or quota, max(1, math.ceil(quota / period)))
        except (OSError, ValueError, ZeroDivisionError):
            pass
    return detected if detected and detected > 0 else None


def resource_snapshot(storage_path: str | Path | None = None) -> dict[str, int | None]:
    """Return current CPU, memory and disk capacity without requiring psutil."""
    settings = get_settings()
    root = Path(storage_path or settings.storage_dir)
    try:
        root.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(root)
        disk_total, disk_free = usage.total, usage.free
    except OSError:
        disk_total = disk_free = None
    memory_total, memory_available = _proc_memory()
    return {
        "cpu_cores": _effective_cpu_count(),
        "memory_total_bytes": memory_total,
        "memory_available_bytes": memory_available,
        "disk_total_bytes": disk_total,
        "disk_free_bytes": disk_free,
    }


def current_dataset_bytes(dataset_dir: Path, exclude_slots: set[str] | None = None) -> int:
    """Count current upload files only; analysis outputs are accounted for separately."""
    excluded = exclude_slots or set()
    total = 0
    if dataset_dir.exists():
        for child in dataset_dir.iterdir():
            if child.is_file() and child.name not in excluded and child.name not in {"files.json", "validation.json"}:
                try:
                    total += child.stat().st_size
                except OSError:
                    continue
    return total


def upload_budget(dataset_dir: Path, replacing_slots: set[str] | None = None) -> dict[str, int | None]:
    """Calculate per-file and remaining dataset upload budget from configured caps and free disk."""
    settings = get_settings()
    snapshot = resource_snapshot(settings.storage_dir)
    reserve = max(0, settings.upload_disk_reserve_mb) * _MIB
    disk_budget = max(0, snapshot["disk_free_bytes"] - reserve) if snapshot["disk_free_bytes"] is not None else None
    per_file_cap = max(0, settings.max_upload_mb) * _MIB
    dataset_limit = max(0, settings.max_dataset_upload_mb) * _MIB
    existing = current_dataset_bytes(dataset_dir, replacing_slots)
    quota_remaining = max(0, dataset_limit - existing)
    dataset_remaining = min(quota_remaining, disk_budget) if disk_budget is not None else quota_remaining
    per_file_cap = min(per_file_cap, dataset_remaining)
    return {
        "per_file_bytes": per_file_cap,
        "dataset_remaining_bytes": dataset_remaining,
        "dataset_existing_bytes": existing,
        "dataset_limit_bytes": dataset_limit,
        "dataset_quota_remaining_bytes": quota_remaining,
        "disk_budget_bytes": disk_budget,
        "disk_free_bytes": snapshot["disk_free_bytes"],
        "disk_reserve_bytes": reserve,
    }


def estimate_analysis_resources(
    *, dataset_bytes: int, n_samples: int, n_variants: int, use_pca: bool
) -> dict[str, int]:
    """Conservative workload estimate for admission decisions and run metadata.

    PLINK2 streams genotype data; the estimate uses input size plus a bounded quadratic
    sample workspace for PCA. It is deliberately labelled as an estimate in API output.
    """
    base_memory = max(256 * _MIB, min(2 * _GIB, dataset_bytes * 3))
    pca_workspace = (n_samples * n_samples * 8 * 4) if use_pca else 0
    # PCA fallback memory estimate is separately exposed; its implementation uses a Gram matrix.
    fallback_memory = pca_workspace + max(0, n_samples) * 128 * 8 if use_pca else 0
    disk = max(256 * _MIB, dataset_bytes * 3)
    return {
        "estimated_memory_bytes": int(base_memory + pca_workspace),
        "estimated_disk_bytes": int(disk),
        "pca_fallback_memory_bytes": int(fallback_memory),
        "genotype_cells": int(max(0, n_samples) * max(0, n_variants)),
    }


def effective_concurrency(snapshot: dict[str, int | None] | None = None) -> int:
    """Adapt the per-host job cap to detected CPU/RAM, while respecting admin's ceiling."""
    settings = get_settings()
    snap = snapshot or resource_snapshot(settings.storage_dir)
    cpu = snap.get("cpu_cores") or settings.max_concurrent_jobs
    available = snap.get("memory_available_bytes")
    reserve = settings.analysis_memory_reserve_mb * _MIB
    per_job = settings.analysis_memory_per_job_mb * _MIB
    if available is None:
        memory_slots = max(1, settings.max_concurrent_jobs // 2) if settings.max_concurrent_jobs > 1 else 1
    else:
        memory_slots = max(1, (max(0, available - reserve) // max(_MIB, per_job)))
    return max(1, min(settings.max_concurrent_jobs, cpu, int(memory_slots)))


def preflight_analysis(
    *, dataset_dir: Path, n_samples: int, n_variants: int, use_pca: bool, queued_or_running: int = 0
) -> dict[str, Any]:
    """Assess disk/RAM availability before queueing a GWAS run."""
    settings = get_settings()
    snapshot = resource_snapshot(settings.storage_dir)
    dataset_bytes = current_dataset_bytes(dataset_dir)
    estimate = estimate_analysis_resources(dataset_bytes=dataset_bytes, n_samples=n_samples,
                                           n_variants=n_variants, use_pca=use_pca)
    warnings: list[dict[str, str]] = []
    disk_free = snapshot.get("disk_free_bytes")
    disk_reserve = settings.analysis_disk_reserve_mb * _MIB
    if disk_free is not None and disk_free - disk_reserve < estimate["estimated_disk_bytes"]:
        return {"accepted": False, "code": "INSUFFICIENT_DISK_SPACE", "message": "Ruang disk kosong tidak cukup untuk memperkirakan kebutuhan file sementara dan hasil analisis.",
                "snapshot": snapshot, "estimate": estimate, "warnings": warnings,
                "effective_concurrency": effective_concurrency(snapshot), "queued_or_running": queued_or_running}
    memory_available = snapshot.get("memory_available_bytes")
    if memory_available is not None:
        required = estimate["estimated_memory_bytes"] + settings.analysis_memory_reserve_mb * _MIB
        if memory_available < required:
            return {"accepted": False, "code": "INSUFFICIENT_MEMORY", "message": "Memori tersedia lebih rendah daripada estimasi konservatif kebutuhan analisis dan cadangan sistem.",
                    "snapshot": snapshot, "estimate": estimate, "warnings": warnings,
                    "effective_concurrency": effective_concurrency(snapshot), "queued_or_running": queued_or_running}
    else:
        warnings.append({"code": "MEMORY_UNDETECTED", "message": "Kapasitas RAM tidak dapat dideteksi; batas konfigurasi aman digunakan."})
    if snapshot.get("disk_free_bytes") is None:
        warnings.append({"code": "DISK_UNDETECTED", "message": "Kapasitas disk tidak dapat dideteksi; hanya batas konfigurasi yang digunakan."})
    if queued_or_running >= effective_concurrency(snapshot):
        warnings.append({"code": "RESOURCE_QUEUE", "message": "Jumlah pekerjaan aktif/antrean telah mencapai concurrency adaptif; pekerjaan ini akan menunggu slot."})
    if use_pca and estimate["pca_fallback_memory_bytes"] > (memory_available or settings.pca_fallback_memory_limit_mb * _MIB):
        warnings.append({"code": "PCA_FALLBACK_MEMORY_LIMIT", "message": "Fallback PCA Python dapat dibatasi oleh estimasi memori; PLINK2 tetap menjadi metode utama."})
    return {"accepted": True, "snapshot": snapshot, "estimate": estimate, "warnings": warnings,
            "effective_concurrency": effective_concurrency(snapshot), "queued_or_running": queued_or_running}


def check_pca_fallback_memory(n_samples: int, n_variants: int) -> tuple[bool, int, int]:
    """Return (allowed, estimated_bytes, available_budget_bytes) for streaming PCA fallback."""
    settings = get_settings()
    snapshot = resource_snapshot(settings.storage_dir)
    available = snapshot.get("memory_available_bytes")
    configured_budget = settings.pca_fallback_memory_limit_mb * _MIB
    detected_budget = int(available * settings.pca_fallback_memory_fraction) if available is not None else configured_budget
    budget = min(configured_budget, detected_budget) if configured_budget > 0 else detected_budget
    estimated = int(max(1, n_samples) ** 2 * 8 * 4 + max(1, n_samples) * 128 * 8)
    return estimated <= budget, estimated, max(0, budget)
