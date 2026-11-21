from pydantic import BaseModel, Field
from typing import Any, Optional, List, Literal

class AnalysisExecutionRequest(BaseModel):
    analysis_id: int
    trait_name: str
    trait_type: str = "quantitative"  # "quantitative" atau "binary"
    model: str = "linear"            # "linear", "logistic", atau "lmm"
    use_pca: bool = True
    pca_components: Optional[Literal[5, 10, 20]] = Field(default=5, description="Pilihan: 5, 10, atau 20 PC")
    covariates: Optional[List[str]] = []
    significance_threshold: Optional[float] = 5e-8

class LegacyAnalysisRunOut(BaseModel):
    analysis_id: int
    status: Literal["queued"]
    model: str
    message: str


class LegacyAnalysisStatusOut(BaseModel):
    analysis_id: int
    status: str
    stage: str | None = None
    percent: int | None = None
    message: str | None = None
    error: dict[str, Any] | None = None


class LegacyAnalysisSummaryOut(BaseModel):
    model: str
    n_variants_tested: int
    n_significant: int
    best_variant: dict[str, Any] | None = None
    smallest_p_value: float | None = None
    lambda_gc: float | None = None
    reference_thresholds: dict[str, float]
