"""Pengelolaan executable PLINK2 dan GEMMA secara portabel."""

import os
import shutil
import subprocess
from pathlib import Path

from app.config import get_settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ToolError(Exception):
    def __init__(self, code: str, message: str, hint: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint


def resolve_binary(name: str) -> str:
    """Prioritaskan binary proyek, kemudian gunakan PATH sebagai fallback."""

    raw = Path(name).expanduser()

    if raw.is_absolute():
        candidates = [raw]
    else:
        candidates = [PROJECT_ROOT / raw]

    for candidate in candidates:
        try:
            candidate = candidate.resolve()
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        except OSError:
            continue

    found = shutil.which(name)
    if found:
        return str(Path(found).resolve())

    raise ToolError(
        "TOOL_NOT_FOUND",
        f"Program '{name}' tidak ditemukan.",
        "Periksa tools/bin, permission executable, dan konfigurasi PLINK2_BIN/GEMMA_BIN."
    )


def tail(path: Path, n: int = 12) -> str:
    try:
        return "\n".join(
            path.read_text(errors="replace").splitlines()[-n:]
        )
    except OSError:
        return ""


def run_tool(label: str, cmd: list[str], cwd: Path, log_path: Path) -> None:
    timeout = get_settings().tool_timeout_seconds

    with open(log_path, "ab") as log:
        log.write(f"\n$ {' '.join(cmd)}\n".encode())
        log.flush()

        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=log,
            stderr=subprocess.STDOUT
        )

        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise ToolError(
                "TOOL_TIMEOUT",
                f"Tahap {label} dihentikan karena melebihi {timeout // 60} menit.",
                "Gunakan dataset lebih kecil atau naikkan TOOL_TIMEOUT_SECONDS."
            )

    if code != 0:
        if code in (-9, 137):
            raise ToolError(
                "OUT_OF_MEMORY",
                f"Tahap {label} dihentikan karena server kehabisan memori.",
                "Kurangi ukuran dataset atau tingkatkan kapasitas RAM."
            )

        raise ToolError(
            "TOOL_FAILED",
            f"Tahap {label} gagal (kode keluar {code}).",
            "Periksa log analisis."
        )


def tool_version(binary: str, args: list[str]) -> str:
    try:
        out = subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=20
        )
        return (out.stdout or out.stderr).strip().splitlines()[0]
    except Exception:
        return "tidak diketahui"
