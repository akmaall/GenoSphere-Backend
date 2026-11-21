from pydantic import BaseModel, Field
from typing import Any, Literal, Optional

class QCExecutionRequest(BaseModel):
    dataset_id: int
    skip_qc: bool = False
    maf: float = Field(default=0.01, ge=0.0, le=0.5)
    call_rate: float = Field(default=0.95, ge=0.0, le=1.0)
    hwe_p: Optional[float] = Field(default=1e-6, ge=0.0, le=1.0)

class QCExecutionOut(BaseModel):
    analysis_id: int
    dataset_id: int
    status: Literal["queued"]
    message: str
    qc_config: QCExecutionRequest


