"""Format kesalahan tunggal untuk seluruh API.

Setiap kesalahan, dari modul mana pun dan dengan status apa pun, dikirim dengan bentuk yang sama:

    {"detail": {"code": "KODE_SINGKAT", "message": "Pesan untuk pengguna.", "errors": [...]}}

`code` dipakai frontend untuk menentukan tindakan (misalnya TOKEN_EXPIRED berarti arahkan ke halaman login).
`message` boleh langsung ditampilkan ke pengguna. `errors` hanya ada bila ada rincian per kolom isian.

Bentuk ini juga yang ditulis di dokumentasi Swagger (lihat custom_openapi di main.py), sehingga apa yang
tertulis di dokumentasi sama dengan apa yang benar-benar dikirim server.
"""
import logging

from fastapi import HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("genosphere.errors")


class FieldError(BaseModel):
    """Satu kesalahan pada satu kolom isian."""
    field: str | None = Field(None, description="Nama kolom isian yang salah, mis. confirm_password. Kosong bila bukan kesalahan kolom.")
    message: str = Field(description="Penjelasan kesalahan dalam bahasa Indonesia.")


class ErrorDetail(BaseModel):
    """Isi bagian `detail` pada semua respons kesalahan."""
    code: str = Field(description="Kode singkat untuk logika frontend, mis. VALIDATION_ERROR atau TOKEN_EXPIRED.")
    message: str = Field(description="Pesan yang boleh ditampilkan langsung ke pengguna.")
    hint: str | None = Field(None, description="Saran cara memperbaiki, bila ada.")
    errors: list[FieldError] | list[dict] | None = Field(None, description="Rincian per kolom (422) atau per masalah (konfigurasi).")
    details: dict | None = Field(None, description="Data tambahan yang membantu frontend, misalnya daftar yang akan terhapus pada konfirmasi hapus.")


class ErrorResponse(BaseModel):
    """Bentuk semua respons kesalahan."""

    detail: ErrorDetail

    model_config = {
        "json_schema_extra": {
            "example": {
                "detail": {
                    "code": "VALIDATION_ERROR",
                    "message": "Data yang dikirim belum valid. Periksa kembali isian Anda.",
                    "errors": [{"field": "confirm_password", "message": "Konfirmasi kata sandi tidak sama dengan kata sandi."}],
                }
            }
        }
    }


class APIError(HTTPException):
    """Kesalahan terstruktur yang sengaja dilempar oleh kode kita sendiri.

    Contoh: raise APIError(404, "RESOURCE_NOT_FOUND", "Data tidak ditemukan.")
    """

    def __init__(self, status_code: int, code: str, message: str, *, hint: str | None = None,
                 errors: list | None = None, details: dict | None = None, bearer: bool = False):
        """Menyusun kesalahan terstruktur. `bearer=True` menambahkan header WWW-Authenticate untuk jawaban 401."""
        detail: dict = {"code": code, "message": message}
        if hint:
            detail["hint"] = hint
        if errors is not None:
            detail["errors"] = errors
        if details is not None:
            detail["details"] = details
        headers = {"WWW-Authenticate": "Bearer"} if bearer else None
        super().__init__(status_code=status_code, detail=detail, headers=headers)


# Nama kolom yang ramah dibaca, dipakai untuk menyusun pesan "X wajib diisi".
_FIELD_LABELS = {
    "full_name": "Nama lengkap", "fullName": "Nama lengkap",
    "email": "Email", "username": "Username", "password": "Kata sandi",
    "confirm_password": "Konfirmasi kata sandi", "confirmPassword": "Konfirmasi kata sandi",
    "institution": "Institusi", "bio": "Bio", "token": "Token pemulihan", "identifier": "Email / Username",
    # modul GWAS
    "dataset_id": "Dataset", "trait_name": "Nama trait", "trait_type": "Tipe trait", "model": "Model GWAS",
    "analysis_mode": "Mode analisis", "domain": "Domain riset", "maf": "Ambang MAF", "call_rate": "Call rate",
    "hwe_p": "Ambang p-value HWE", "pca_components": "Jumlah komponen PCA", "n_components": "Jumlah komponen PCA",
    "covariates": "Kovariat", "significance_threshold": "Ambang signifikansi", "threshold": "Ambang signifikansi",
    "method": "Metode ambang", "limit": "Batas jumlah", "offset": "Posisi awal",
}


def _field_message(label: str, err: dict) -> str:
    """Menerjemahkan satu kesalahan Pydantic menjadi kalimat Indonesia yang mudah dipahami."""
    kind, ctx = err.get("type", ""), err.get("ctx", {})
    if kind == "missing":
        return f"{label} wajib diisi."
    if kind == "string_too_short":
        return f"{label} minimal {ctx.get('min_length')} karakter."
    if kind == "string_too_long":
        return f"{label} maksimal {ctx.get('max_length')} karakter."
    if kind == "string_pattern_mismatch":
        return f"{label} hanya boleh berisi huruf, angka, titik, garis bawah, dan tanda hubung."
    if kind in ("greater_than_equal", "greater_than"):
        return f"{label} harus lebih besar{' atau sama dengan' if kind.endswith('equal') else ' dari'} {ctx.get('ge', ctx.get('gt'))}."
    if kind in ("less_than_equal", "less_than"):
        return f"{label} harus lebih kecil{' atau sama dengan' if kind.endswith('equal') else ' dari'} {ctx.get('le', ctx.get('lt'))}."
    if kind == "literal_error":
        options = str(ctx.get("expected", "")).replace("'", "").replace(" or ", ", ")   # "'a', 'b' or 'c'" menjadi "a, b, c"
        return f"{label} harus salah satu dari: {options}."
    if kind in ("int_parsing", "float_parsing", "int_from_float", "int_type", "float_type"):
        return f"{label} harus berupa angka."
    if kind in ("bool_parsing", "bool_type"):
        return f"{label} harus berupa true atau false."
    if kind in ("list_type", "too_long", "too_short"):
        return f"{label} tidak sesuai ketentuan (jumlah atau bentuk daftar)."
    if kind in ("json_invalid", "model_attributes_type", "dict_type"):
        return "Isi permintaan bukan JSON yang valid."
    if err.get("loc", ())[-1:] == ("email",):
        return "Format email tidak valid."
    if kind == "value_error":  # pesan dari validator buatan sendiri, sudah berbahasa Indonesia
        return str(err.get("msg", "")).removeprefix("Value error, ")
    return f"{label} tidak valid."


def build_validation_errors(exc_errors: list[dict], labels_override: dict | None = None) -> list[dict]:
    """Mengubah daftar kesalahan Pydantic menjadi daftar {field, message} berbahasa Indonesia untuk respons 422."""
    labels = {**_FIELD_LABELS, **(labels_override or {})}
    out = []
    for err in exc_errors:
        loc = err.get("loc", ())
        # loc[0] adalah asal data ("body", "query", "path"). Sisanya adalah nama kolom, bisa bersarang (qc.maf).
        field = ".".join(str(x) for x in loc[1:]) if len(loc) > 1 else None
        label = labels.get(str(loc[-1]) if len(loc) > 1 else "", "Isian")
        out.append({"field": field, "message": _field_message(label, err)})
    return out


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Respons 422. Sengaja tidak memuat nilai `input` bawaan FastAPI karena itu akan memantulkan
    kata sandi yang dikirim pengguna kembali di isi respons."""
    labels = {"username": "Email / Username"} if request.url.path.endswith("/login") else None
    body = {"detail": {"code": "VALIDATION_ERROR",
                       "message": "Data yang dikirim belum valid. Periksa kembali isian Anda.",
                       "errors": build_validation_errors(exc.errors(), labels)}}
    return JSONResponse(status_code=422, content=jsonable_encoder(body))


_STATUS_TEXT = {
    400: ("BAD_REQUEST", "Permintaan tidak dapat diproses."),
    401: ("UNAUTHORIZED", "Anda belum login atau sesi tidak valid."),
    403: ("FORBIDDEN", "Anda tidak memiliki izin untuk tindakan ini."),
    404: ("NOT_FOUND", "Alamat atau data yang diminta tidak ditemukan."),
    405: ("METHOD_NOT_ALLOWED", "Metode HTTP ini tidak didukung untuk alamat tersebut."),
    409: ("CONFLICT", "Permintaan bertabrakan dengan keadaan data saat ini."),
    413: ("PAYLOAD_TOO_LARGE", "Data yang dikirim terlalu besar."),
    415: ("UNSUPPORTED_MEDIA_TYPE", "Jenis isi permintaan tidak didukung."),
}


async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Semua HTTPException ikut dibungkus bentuk yang sama.

    Kesalahan bawaan Starlette (alamat tidak ada, metode salah) aslinya berbentuk {"detail": "teks"}.
    Di sini diubah supaya frontend cukup membaca satu bentuk saja.
    """
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        content = {"detail": exc.detail}
    else:
        code, message = _STATUS_TEXT.get(exc.status_code, ("ERROR", "Terjadi kesalahan pada permintaan."))
        content = {"detail": {"code": code, "message": message}}
    return JSONResponse(status_code=exc.status_code, content=jsonable_encoder(content), headers=getattr(exc, "headers", None))


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Kesalahan tak terduga (bug). Rinciannya hanya masuk log server, pengguna cukup menerima pesan umum."""
    logger.exception("Kesalahan tak terduga pada %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": {
        "code": "INTERNAL_ERROR", "message": "Terjadi kesalahan di server. Coba lagi, atau hubungi administrator bila berulang."}})
