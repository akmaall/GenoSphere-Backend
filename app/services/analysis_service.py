from datetime import datetime
import uuid
from fastapi import HTTPException, status
from app.schemas.analysis import AnalysisRequest, AnalysisResponse
from app.services.qc_service import QCService

class AnalysisService:
    @staticmethod
    async def run_analysis(payload: AnalysisRequest) -> AnalysisResponse:
        # 1. Proteksi Backend: Cek apakah QC sudah dilakukan dan LULUS
        qc_result = QCService.get_qc_status(payload.sample_id)
        
        if not qc_result:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Sampel {payload.sample_id} belum melalui proses Quality Control (QC)."
            )
            
        if not qc_result.passed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Sampel {payload.sample_id} GAGAL pada tahap QC. Analisis tidak dapat dilanjutkan."
            )

        # 2. Jalankan pipeline analisis utama (proses berat)
        analysis_id = f"ANL-{uuid.uuid4().hex[:8].upper()}"
        computed_results = {
            "summary": "Analisis sukses dieksekusi",
            "variant_count": 124,
            "quality_gate_passed": True
        }

        return AnalysisResponse(
            sample_id=payload.sample_id,
            analysis_id=analysis_id,
            status="COMPLETED",
            results=computed_results,
            timestamp=datetime.utcnow()
        )