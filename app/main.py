from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from pydantic import BaseModel, Field
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.openapi.utils import get_openapi
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.errors import (
    ErrorResponse, http_error_handler, unhandled_error_handler, validation_error_handler,
)
from app.routers import analysis, auth, gwas, qc
from app.services.jobs import recover_interrupted_jobs

settings = get_settings()

class HealthOut(BaseModel):
    status: str
    database: str
    version: str | None = None
    database_schema: str | None = Field(None, alias="schema")
    missing: list[str] | None = None
    hint: str | None = None

@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Saat server hidup: analisis yang masih "queued/running" (server sempat mati) ditandai failed agar bisa di-retry.
    recover_interrupted_jobs()
    try:
        from app.services.ai_jobs import recover_interpretation_jobs
        recover_interpretation_jobs()
    except Exception:
        # AI merupakan layanan opsional dan tidak boleh menghalangi startup GWAS.
        pass
    yield

tags_metadata = [
    {
        "name": "01. Autentikasi & Akun",
        "description": "Endpoint login JWT, pendaftaran pengguna, dan pengoperasian profil akun."
    },
    {
        "name": "02. Step 1: Input & Validasi Data",
        "description": "Pengunggahan berkas genotipe (.bed/.bim/.fam atau .vcf) dan fenotipe (.csv/.tsv), serta validasi kecocokan sampel ID."
    },
    {
        "name": "03. Step 2: Quality Control (QC)",
        "description": "Filter Missingness, HWE, MAF, dan Kekerabatan (KING) beserta ringkasan laporan statistik QC."
    },
    {
        "name": "04. Step 3: PCA & Kovariat",
        "description": "Kalkulasi Principal Component Analysis (eigenvectors/eigenvalues), koordinat scatter plot, dan penetapan kovariat."
    },
    {
        "name": "05. Step 4: Analisis GWAS & Post-Processing",
        "description": "Eksekusi uji asosiasi genomik (Linear, Logistic, LMM GEMMA) dan penyesuaian threshold signifikansi Bonferroni/FDR."
    },
    {
        "name": "06. Step 5: Hasil, Visualisasi & Ekspor",
        "description": "Data visualisasi Manhattan Plot, QQ Plot, Lambda GC, tabel SNP signifikan, dan ekspor file (.csv, .xlsx, .json)."
    },
    {
        "name": "07. Konfigurasi & Riwayat",
        "description": "Pilihan dropdown UI, parameter presets, log eksekusi, polling status, dan riwayat analisis pengguna."
    },
    {
        "name": "08. Sistem & Health Check",
        "description": "Pengecekan status server dan verifikasi skema database PostgreSQL."
    }
]

app = FastAPI(
    title="GenoSphere-GWAS API",
    version=settings.app_version,
    openapi_tags=tags_metadata,
    lifespan=lifespan
)

# Middleware CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=settings.cors_origin_regex or None,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Exception handlers: validation, HTTP errors (including router 404/405), and unexpected errors.
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(StarletteHTTPException, http_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)

# Include Router
app.include_router(auth.router)
app.include_router(gwas.router)
app.include_router(qc.router)
app.include_router(analysis.router)

@app.get("/", tags=["08. Sistem & Health Check"], include_in_schema=False)
def root():
    return {"message": "GenoSphere API Service Running"}

@app.get("/health", response_model=HealthOut, tags=["08. Sistem & Health Check"], summary="Pengecekan Kesehatan Server & Skema Database", responses={503: {"model": HealthOut, "description": "Database tidak dapat dijangkau."}})
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(status_code=503, content={"status": "degraded", "database": "unreachable"})
    
    from app.database import Base
    import app.models  # noqa: F401
    insp = inspect(db.get_bind())
    gaps: list[str] = []
    for table in Base.metadata.sorted_tables:
        if not insp.has_table(table.name):
            gaps.append(f"{table.name} (tabel belum ada)")
            continue
        existing = {c["name"] for c in insp.get_columns(table.name)}
        gaps += [f"{table.name}.{c.name}" for c in table.columns if c.name not in existing]

    body = {"status": "ok", "database": "connected", "version": settings.app_version, "schema": "ok"}
    if gaps:
        body.update(schema="needs_migration", missing=gaps, hint="Jalankan skrip migrasi SQL database.")
    return body

def custom_openapi():
    """Document the actual unified API error envelope instead of FastAPI's default 422 schema."""
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        tags=app.openapi_tags,
    )
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    # Kompatibilitas Swagger UI untuk array UploadFile pada OpenAPI 3.1.
    # Swagger UI 5.x belum merender contentMediaType pada item array
    # sebagai pemilih file. Ubah item tersebut menjadi format binary.
    def _patch_binary_upload_array_items(node):
        if isinstance(node, dict):
            if node.get("type") == "array":
                items = node.get("items")
                if (
                    isinstance(items, dict)
                    and items.get("contentMediaType") == "application/octet-stream"
                ):
                    items.pop("contentMediaType", None)
                    items["format"] = "binary"

            for value in node.values():
                _patch_binary_upload_array_items(value)

        elif isinstance(node, list):
            for item in node:
                _patch_binary_upload_array_items(item)

    _patch_binary_upload_array_items(schema)

    # ErrorResponse is referenced by router response declarations. Keep a defensive
    # fallback for routes that have no explicit error response model.
    if "ErrorResponse" not in components:
        components["ErrorResponse"] = ErrorResponse.model_json_schema(ref_template="#/components/schemas/{model}")
    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            if not isinstance(operation, dict) or "responses" not in operation:
                continue
            responses = operation["responses"]
            if "422" in responses:
                responses["422"] = {
                    "description": "Data permintaan tidak memenuhi validasi.",
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                        }
                    },
                }
    # No endpoint should refer to the framework's default validation model now.
    components.pop("HTTPValidationError", None)
    components.pop("ValidationError", None)
    app.openapi_schema = schema
    return app.openapi_schema


app.openapi = custom_openapi
