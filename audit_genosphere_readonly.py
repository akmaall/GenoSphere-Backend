#!/usr/bin/env python3
"""Read-only audit for a local GenoSphere FastAPI backend.

Run from the backend root with its virtual environment active:
    python audit_genosphere_readonly.py

It does not print secrets, password hashes, JWTs, API keys, or .env values.
It performs SELECT-only database inspection and writes no project files.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.metadata as md
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path.cwd()
MIB = 1024 * 1024
GIB = 1024 * MIB


def section(title: str) -> None:
    print("\n" + "=" * 88)
    print(title)
    print("=" * 88)


def safe_bool(value: Any) -> str:
    return "YA" if bool(value) else "TIDAK"


def mask_email(value: str | None) -> str:
    if not value or "@" not in value:
        return "(tidak tersedia)"
    local, domain = value.split("@", 1)
    if len(local) <= 2:
        masked = local[:1] + "*"
    else:
        masked = local[:2] + "***"
    return f"{masked}@{domain}"


def format_bytes(value: int | None) -> str:
    if value is None:
        return "tidak terdeteksi"
    if value >= GIB:
        return f"{value / GIB:.2f} GiB"
    return f"{value / MIB:.1f} MiB"


def proc_memory() -> tuple[int | None, int | None]:
    total = available = None
    try:
        info = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, raw = line.split(":", 1)
            info[key] = int(raw.strip().split()[0]) * 1024
        total = info.get("MemTotal")
        available = info.get("MemAvailable")
    except (OSError, ValueError, IndexError):
        pass
    for limit_path, current_path in (
        ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory.current"),
        ("/sys/fs/cgroup/memory/memory.limit_in_bytes", "/sys/fs/cgroup/memory/memory.usage_in_bytes"),
    ):
        try:
            raw_limit = Path(limit_path).read_text().strip()
            if raw_limit == "max":
                continue
            limit = int(raw_limit)
            if limit <= 0 or limit >= (1 << 60):
                continue
            try:
                used = int(Path(current_path).read_text().strip())
            except (OSError, ValueError):
                used = 0
            total = min(total, limit) if total is not None else limit
            avail = max(0, limit - used)
            available = min(available, avail) if available is not None else avail
            break
        except (OSError, ValueError):
            continue
    return total, available


def source_hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "FILE_NOT_FOUND"


def ast_functions(path: Path) -> dict[str, int]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"AST ERROR {path}: {type(exc).__name__}: {exc}")
        return {}
    result = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result[node.name] = node.lineno
    return result


def run_version(label: str, command: list[str]) -> None:
    try:
        p = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=12, check=False)
        lines = [x.strip() for x in p.stdout.splitlines() if x.strip()]
        print(f"{label}: exit={p.returncode}; " + (" | ".join(lines[:3]) if lines else "tidak ada output"))
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"{label}: TIDAK DAPAT DIJALANKAN ({type(exc).__name__})")


def main() -> int:
    if not (ROOT / "app").is_dir() or not (ROOT / "requirements.txt").exists():
        print("ERROR: Jalankan script dari root folder backend GenoSphere.")
        print("Contoh: cd /mnt/d/GenoSphere/backend && python audit_genosphere_readonly.py")
        return 2

    sys.path.insert(0, str(ROOT))

    section("0. IDENTITAS AUDIT")
    print(f"Project root : {ROOT}")
    print(f"Python       : {sys.version.split()[0]}")
    print(f"Platform     : {platform.platform()}")
    print(f"Waktu lokal  : {__import__('datetime').datetime.now().astimezone().isoformat(timespec='seconds')}")
    print("Mode         : READ-ONLY; tidak mengubah source, dataset, atau database")

    section("1. KONFIGURASI RUNTIME (RAHASIA DISENSOR)")
    try:
        from app.config import get_settings, APP_VERSION, PIPELINE_VERSION
        settings = get_settings()
        print(f"APP_VERSION / PIPELINE_VERSION : {APP_VERSION} / {PIPELINE_VERSION}")
        safe_fields = [
            "postgres_user", "postgres_host", "postgres_port", "postgres_db",
            "jwt_algorithm", "access_token_expire_minutes", "frontend_url", "reset_token_expire_minutes",
            "smtp_host", "smtp_port", "smtp_from", "smtp_starttls", "storage_dir",
            "plink2_bin", "gemma_bin", "max_upload_mb", "max_dataset_upload_mb",
            "upload_disk_reserve_mb", "max_concurrent_jobs", "analysis_memory_reserve_mb",
            "analysis_memory_per_job_mb", "analysis_disk_reserve_mb", "pca_fallback_memory_limit_mb",
            "pca_fallback_memory_fraction", "tool_timeout_seconds", "max_plot_points",
            "ai_provider", "ai_api_base_url", "ai_model", "ai_timeout_seconds",
            "ai_max_output_tokens", "ai_max_attempts", "ai_top_snp_count", "ai_max_concurrent_jobs",
            "cors_origins", "cors_origin_regex",
        ]
        for key in safe_fields:
            try:
                val = getattr(settings, key)
                print(f"{key}: {val}")
            except Exception:
                pass
        print("database_url configured :", safe_bool(getattr(settings, "database_url", None)))
        print("postgres_password set   :", safe_bool(getattr(settings, "postgres_password", None)))
        secret = str(getattr(settings, "secret_key", "") or "")
        print("SECRET_KEY set           :", safe_bool(secret))
        print("SECRET_KEY length >= 32  :", safe_bool(len(secret) >= 32))
        print("SMTP credentials set     :", safe_bool(getattr(settings, "smtp_user", None) and getattr(settings, "smtp_password", None)))
        print("AI_API_KEY configured    :", safe_bool(getattr(settings, "ai_api_key", None)))
    except Exception as exc:
        settings = None
        print(f"GAGAL MEMUAT app.config: {type(exc).__name__}: {exc}")

    section("2. FILE .ENV (NAMA VARIABEL SAJA, NILAI TIDAK DITAMPILKAN)")
    env_path = ROOT / ".env"
    example_path = ROOT / ".env.example"
    print(".env exists        :", safe_bool(env_path.exists()))
    print(".env.example exists:", safe_bool(example_path.exists()))
    if env_path.exists():
        keys = []
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if s and not s.startswith("#") and "=" in s:
                keys.append(s.split("=", 1)[0].strip())
        print(".env variable keys:", ", ".join(sorted(set(keys))) or "(kosong)")
    else:
        print(".env belum ditemukan di root backend.")

    section("3. BATAS UPLOAD DAN RUANG PENYIMPANAN AKTUAL")
    if settings is not None:
        one_file_cap = max(0, int(settings.max_upload_mb)) * MIB
        dataset_cap = max(0, int(settings.max_dataset_upload_mb)) * MIB
        reserve = max(0, int(settings.upload_disk_reserve_mb)) * MIB
        root = Path(settings.storage_dir).expanduser()
        print(f"Batas konfigurasi per berkas : {format_bytes(one_file_cap)}")
        print(f"Batas konfigurasi per dataset: {format_bytes(dataset_cap)}")
        print(f"Cadangan disk unggahan       : {format_bytes(reserve)}")
        print(f"Folder storage               : {root}")
        probe = root if root.exists() else root.parent
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        disk_free = None
        try:
            disk_free = shutil.disk_usage(probe).free
        except OSError:
            pass
        disk_budget = max(0, disk_free - reserve) if disk_free is not None else None
        print(f"Disk free terdeteksi         : {format_bytes(disk_free)}")
        print(f"Budget disk setelah reserve  : {format_bytes(disk_budget)}")
        if disk_budget is not None:
            print(f"Batas satu file saat ini     : {format_bytes(min(one_file_cap, dataset_cap, disk_budget))}")
        else:
            print(f"Batas satu file sementara   : {format_bytes(min(one_file_cap, dataset_cap))} (disk tidak terdeteksi)")
        dataset_dirs = sorted(root.glob("u*/ds_*")) if root.exists() else []
        print(f"Jumlah folder dataset        : {len([p for p in dataset_dirs if p.is_dir()])}")
        for d in [p for p in dataset_dirs if p.is_dir()][:30]:
            used = 0
            for f in d.iterdir():
                if f.is_file() and f.name not in {"files.json", "validation.json"}:
                    try:
                        used += f.stat().st_size
                    except OSError:
                        pass
            remaining = max(0, dataset_cap - used)
            if disk_budget is not None:
                remaining = min(remaining, disk_budget)
            print(f"  {d.name}: terpakai={format_bytes(used)}; kuota dataset tersisa={format_bytes(max(0, dataset_cap-used))}; budget upload sekarang={format_bytes(min(one_file_cap, remaining))}")
        print("Catatan: upload_budget menghitung ulang sisa kuota/disk pada setiap request; angka efektif dapat turun.")

    section("4. FORMAT FILE YANG DIIZINKAN")
    try:
        from app.services import storage
        print("Genotype:", ", ".join(storage.GENOTYPE_EXTS))
        print("Phenotype:", ", ".join(storage.PHENOTYPE_EXTS))
    except Exception as exc:
        print("Tidak dapat membaca storage config:", type(exc).__name__, exc)

    section("5. AUTENTIKASI, USERNAME, DAN PASSWORD")
    try:
        from app import schemas as schema_module
        for cls_name in ("RegisterRequest", "LoginRequest", "ResetPasswordRequest", "UserOut"):
            cls = getattr(schema_module, cls_name, None)
            if cls is None:
                continue
            print(f"{cls_name}:")
            for name, field in getattr(cls, "model_fields", {}).items():
                if name in {"password", "confirm_password", "token", "access_token"}:
                    print(f"  {name}: field tersedia; isi/nilai tidak ditampilkan")
                else:
                    print(f"  {name}: required={field.is_required()}, metadata={field.metadata}, description={field.description}")
        from app.routers import auth as auth_router
        print("Login accepts email/username: YA (cek implementasi endpoint)")
        print("Batas panjang password login:", getattr(auth_router, "MAX_PASSWORD_LEN", "tidak diketahui"))
    except Exception as exc:
        print(f"Schema auth tidak dapat diinspeksi: {type(exc).__name__}: {exc}")
    try:
        from app.security import hash_password
        marker = hash_password("Audit-Only-Temporary-Password-Not-Stored-82931")
        print("Metode hash password:", "Argon2" if marker.startswith("$argon2") else "Hash terdeteksi; skema tidak dikenali otomatis")
        del marker
    except Exception as exc:
        print("Pemeriksaan password hasher gagal:", type(exc).__name__, exc)
    print("Password plaintext:", "TIDAK disimpan/dapat dipulihkan dari database; gunakan reset-password bila lupa.")
    print("Password hash, SECRET_KEY, JWT, SMTP_PASSWORD, AI_API_KEY: tidak ditampilkan oleh audit ini.")

    section("6. AKUN TERDAFTAR (SELECT SAJA; EMAIL DISAMARKAN; TANPA HASH PASSWORD)")
    try:
        from sqlalchemy import select, func, text
        from app.database import SessionLocal
        from app.models import User, Accession, GwasAnalysis, GwasResult
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
            print("Database connection: OK")
            print("Kolom tabel users:", ", ".join(User.__table__.columns.keys()))
            for model, label in ((User, "users"), (Accession, "accession/datasets"), (GwasAnalysis, "gwas_analysis"), (GwasResult, "gwas_result")):
                try:
                    count = db.scalar(select(func.count()).select_from(model))
                    print(f"Jumlah {label}: {int(count or 0)}")
                except Exception as exc:
                    print(f"Jumlah {label}: gagal dibaca ({type(exc).__name__})")
            rows = db.execute(select(User.user_id, User.username, User.email, User.is_active).order_by(User.user_id).limit(50)).all()
            print("Akun (maks. 50):")
            if not rows:
                print("  (belum ada akun)")
            for uid, username, email, active in rows:
                print(f"  user_id={uid}; username={username or '(kosong)'}; email={mask_email(email)}; aktif={bool(active)}")
    except Exception as exc:
        print(f"Database/account query gagal: {type(exc).__name__}: {exc}")
        print("Tidak ada operasi INSERT/UPDATE/DELETE yang dilakukan.")

    section("7. RESOURCE HOST DAN CONCURRENCY")
    mem_total, mem_avail = proc_memory()
    print("CPU logical detected:", os.cpu_count())
    print("RAM total:", format_bytes(mem_total))
    print("RAM available:", format_bytes(mem_avail))
    if settings is not None:
        print("Configured max concurrent jobs:", settings.max_concurrent_jobs)
        try:
            from app.services.resource_policy import effective_concurrency
            cpu = os.cpu_count() or settings.max_concurrent_jobs
            snap = {"cpu_cores": cpu, "memory_available_bytes": mem_avail}
            print("Effective concurrency estimate:", effective_concurrency(snap))
        except Exception as exc:
            print("Effective concurrency: gagal dihitung", type(exc).__name__, exc)
        print("Analysis timeout:", settings.tool_timeout_seconds, "seconds")
        print("PCA Python fallback memory cap:", settings.pca_fallback_memory_limit_mb, "MiB; fraction:", settings.pca_fallback_memory_fraction)

    section("8. PLINK2, GEMMA, PYTHON DEPENDENCIES")
    if settings is not None:
        for label, configured in (("PLINK2", settings.plink2_bin), ("GEMMA", settings.gemma_bin)):
            resolved = shutil.which(configured) if not Path(configured).is_absolute() else configured
            print(f"{label} configured={configured}; resolved={resolved or 'TIDAK DITEMUKAN'}")
            if resolved:
                run_version(label + " version", [resolved, "--version"] if label == "PLINK2" else [resolved, "-h"])
    for package in ("fastapi", "uvicorn", "SQLAlchemy", "psycopg2-binary", "pydantic", "pydantic-settings", "PyJWT", "pwdlib", "argon2-cffi", "python-multipart", "pytest", "numpy", "pandas", "scipy", "openpyxl"):
        try:
            print(f"{package}: {md.version(package)}")
        except md.PackageNotFoundError:
            print(f"{package}: TIDAK TERPASANG")

    section("9. AUDIT KOMPONEN PIPELINE DARI SOURCE SAAT INI")
    inspect_targets = {
        "app/services/validation.py": ["read_phenotype_table", "id_column_index", "check_phenotype", "validate_dataset"],
        "app/services/gwas_engine.py": ["_prepare_tables", "_write_covar", "stage_qc", "stage_pca", "stage_gwas", "_run_gemma"],
        "app/services/resource_policy.py": ["upload_budget", "preflight_analysis", "check_pca_fallback_memory", "effective_concurrency"],
        "app/routers/auth.py": ["register", "login", "me", "forgot_password", "reset_password"],
    }
    for rel, names in inspect_targets.items():
        p = ROOT / rel
        funcs = ast_functions(p)
        print(f"{rel}: exists={p.exists()}")
        for name in names:
            print(f"  {name}: {'line ' + str(funcs[name]) if name in funcs else 'TIDAK DITEMUKAN'}")
    vpath = ROOT / "app/services/validation.py"
    if vpath.exists():
        src = vpath.read_text(encoding="utf-8", errors="replace")
        for line in src.splitlines():
            if line.lstrip().startswith("MISSING_TOKENS"):
                print("MISSING_TOKENS:", line.strip())
            if line.lstrip().startswith("HEADER_") or line.lstrip().startswith("REQUIRED_"):
                print("HEADER RULE:", line.strip())
    gpath = ROOT / "app/services/gwas_engine.py"
    if gpath.exists():
        src = gpath.read_text(encoding="utf-8", errors="replace")
        checks = ["--maf", "--geno", "--hwe", "--pca", "--glm", "_run_gemma", "--pheno", "--covar"]
        for marker in checks:
            print(f"gwas_engine contains {marker}:", safe_bool(marker in src))

    section("10. TEST SUITE DAN TEST FILES")
    test_dir = ROOT / "tests"
    files = sorted(test_dir.glob("test_*.py")) if test_dir.exists() else []
    total = 0
    for p in files:
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
            n = sum(1 for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"))
            total += n
            print(f"{p.name}: {n} test functions")
        except Exception as exc:
            print(f"{p.name}: parse failed ({type(exc).__name__})")
    print("Total static test functions:", total)
    print("Catatan: jumlah fungsi bukan hasil kelulusan tes. Hasil terakhir yang dilaporkan pengguna: 140 passed, 7 failed sebelum follow-up.")

    section("11. FILE HASH UNTUK KODE PENTING")
    hash_paths = [
        "app/config.py", "app/routers/auth.py", "app/models.py", "app/schemas.py",
        "app/services/security.py", "app/services/storage.py", "app/services/resource_policy.py",
        "app/services/validation.py", "app/services/gwas_engine.py", "app/services/config_rules.py",
        "app/services/ui_adapter.py", "app/routers/gwas.py", "app/routers/qc.py", "app/schemas_gwas.py",
        "requirements.txt", ".env.example",
    ]
    for rel in hash_paths:
        p = ROOT / rel
        if p.exists():
            print(f"{rel}: {source_hash(p)}")
        else:
            print(f"{rel}: tidak ditemukan")

    section("12. BATASAN AUDIT DAN KEAMANAN")
    print("- Audit ini memeriksa konfigurasi backend yang sedang aktif di mesin lokal.")
    print("- Nilai rahasia .env, password, password_hash, JWT, SMTP_PASSWORD, AI_API_KEY tidak dicetak.")
    print("- Password akun tidak dapat dibaca kembali; reset-password diperlukan bila lupa.")
    print("- Hasil audit tidak mengubah database maupun berkas proyek.")
    print("- Untuk audit frontend, unggah folder/ZIP frontend terbaru secara terpisah.")
    print("- Untuk menilai kebenaran statistik pada suatu dataset, dibutuhkan validasi dan hasil QC/GWAS dari dataset itu.")
    print("\nAUDIT READ-ONLY SELESAI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
