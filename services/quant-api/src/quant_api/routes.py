"""`/api/v1/*` routes. Read-only research surface: no broker / order routes exist."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Header, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from quant_core.schemas import (
    AsyncJobRecord,
    BacktestRequest,
    BacktestResult,
    BarraExposureRequest,
    BarraExposureResult,
    CapacityAnalysisRequest,
    CapacityFrontierResult,
    CpcvRequest,
    CpcvResult,
    DataHealthReport,
    DatasetSnapshot,
    ExperimentRecord,
    FactorCorrRequest,
    FactorCorrResult,
    FactorEvalRequest,
    FactorEvalResult,
    FactorHypothesisRequest,
    FactorHypothesisResult,
    FormalProofResult,
    FormalVerifyRequest,
    GlossaryListResponse,
    GlossaryTerm,
    PortfolioOptRequest,
    PortfolioOptResult,
    PublishFindingRequest,
    RegimeAnalysisRequest,
    RegimeAnalysisResult,
    ResearchDispatchRequest,
    ResearchDispatchResponse,
    ResearchStudyDetail,
    ShadowDriftRequest,
    ShadowDriftResult,
)

from . import service
from .jobs import get_manager

router = APIRouter(prefix="/api/v1")


class SyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["tushare", "baostock", "edgar"]
    mode: Literal["check", "incremental"] = "check"


class TombstoneCreateRequest(BaseModel):
    """Record a falsified expression in the tombstone library (dedupe guard for future studies)."""

    model_config = ConfigDict(extra="forbid")

    factor_id: str
    expression: str
    universe_id: str = "CSI500"
    snapshot_id: str | None = None
    failure_code: str
    failure_metrics: dict[str, Any] = Field(default_factory=dict)
    lesson_summary: str = ""
    study_id: str | None = None
    created_by: str = "quant-lead"


# -- Q1 ----------------------------------------------------------------------


@router.get("/health", tags=["data"])
def health() -> dict[str, Any]:
    return service.health()


@router.get("/data/health", response_model=DataHealthReport, tags=["data"])
def data_health(snapshot_id: str | None = None) -> DataHealthReport:
    return service.data_health(snapshot_id)


@router.get("/data/snapshots", response_model=list[DatasetSnapshot], tags=["data"])
def data_snapshots() -> list[DatasetSnapshot]:
    return service.snapshots()


@router.get("/data/universe/{universe_id}", tags=["data"])
def data_universe(
    universe_id: str, as_of_date: date | None = None, snapshot_id: str | None = None
) -> dict[str, Any]:
    return service.universe(universe_id, as_of_date, snapshot_id)


@router.post("/data/sync", tags=["data"])
def data_sync(req: SyncRequest) -> dict[str, Any]:
    return service.data_sync(req.provider, req.mode)


# -- Q2 ----------------------------------------------------------------------


@router.get("/factors/catalog", tags=["factors"])
def factors_catalog() -> dict[str, Any]:
    return service.factor_catalog()


@router.post("/factors/evaluate", response_model=FactorEvalResult, tags=["factors"])
def factors_evaluate(req: FactorEvalRequest) -> FactorEvalResult:
    return service.eval_factor(req)


@router.post("/factors/correlation", response_model=FactorCorrResult, tags=["factors"])
def factors_correlation(req: FactorCorrRequest) -> FactorCorrResult:
    return service.factor_corr(req)


@router.post(
    "/factors/hypothesis",
    response_model=FactorHypothesisResult | AsyncJobRecord,
    tags=["factors"],
)
def factors_hypothesis(
    req: FactorHypothesisRequest, async_mode: bool = Query(False)
) -> FactorHypothesisResult | AsyncJobRecord:
    if async_mode:
        return service.enqueue_hypothesis(req)
    rec = service.submit_hypothesis(req, evaluate=True)
    return FactorHypothesisResult.model_validate(rec["result"])


# -- Q3 ----------------------------------------------------------------------


@router.get("/experiments", response_model=list[ExperimentRecord], tags=["validation"])
def experiments(limit: int = Query(20, ge=1, le=500)) -> list[ExperimentRecord]:
    return service.list_experiments(limit)


@router.post("/validation/cpcv", response_model=CpcvResult | AsyncJobRecord, tags=["validation"])
def validation_cpcv(
    req: CpcvRequest, async_mode: bool = Query(False), idempotency_key: str | None = Header(None)
) -> CpcvResult | AsyncJobRecord:
    if async_mode:
        return service.enqueue_cpcv(req, idempotency_key)
    return service.run_cpcv(req)


# -- Q4 ----------------------------------------------------------------------


@router.post("/risk/barra-exposure", response_model=BarraExposureResult, tags=["risk"])
def risk_barra(req: BarraExposureRequest) -> BarraExposureResult:
    return service.barra(req)


@router.post("/portfolio/optimize", response_model=PortfolioOptResult, tags=["risk"])
def portfolio_optimize(req: PortfolioOptRequest) -> PortfolioOptResult:
    return service.optimize(req)


# -- Q5 ----------------------------------------------------------------------


@router.post("/backtest/run", response_model=BacktestResult | AsyncJobRecord, tags=["backtest"])
def backtest_run(
    req: BacktestRequest, async_mode: bool = Query(False), idempotency_key: str | None = Header(None)
) -> BacktestResult | AsyncJobRecord:
    if async_mode:
        return service.enqueue_backtest(req, idempotency_key)
    return service.run_backtest(req)


@router.get("/backtest/{run_id}", response_model=BacktestResult, tags=["backtest"])
def backtest_get(run_id: str) -> BacktestResult:
    return service.get_backtest(run_id)


# -- Q6 ----------------------------------------------------------------------


@router.get("/shadow/drift", response_model=ShadowDriftResult, tags=["shadow"])
def shadow_drift(
    shadow_portfolio_id: str = "SHADOW-CSI500-MAIN",
    universe_id: str = "CSI500",
    window_days: int = Query(60, ge=5, le=500),
    snapshot_id: str | None = None,
) -> ShadowDriftResult:
    return service.shadow(
        ShadowDriftRequest(
            shadow_portfolio_id=shadow_portfolio_id,
            universe_id=universe_id,
            window_days=window_days,
            snapshot_id=snapshot_id,
        )
    )


# -- Q7 jobs / catalog ---------------------------------------------------------


@router.get("/jobs/{job_id}", response_model=AsyncJobRecord, tags=["jobs"])
def jobs_get(job_id: str) -> AsyncJobRecord:
    return service.get_job(job_id)


@router.get("/jobs/{job_id}/events", tags=["jobs"])
def jobs_events(job_id: str) -> StreamingResponse:
    service.get_job(job_id)  # 404 early
    return StreamingResponse(
        get_manager().sse_events(job_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/mcp/catalog", tags=["mcp"])
def mcp_catalog() -> dict[str, Any]:
    return service.mcp_catalog()


# -- V3.0 Q8 glossary (no snapshot dependency) -----------------------------------------------------


@router.get("/glossary", response_model=GlossaryListResponse, tags=["glossary"])
def glossary_list(
    category: str | None = Query(None), domain: str | None = Query(None), q: str | None = Query(None)
) -> GlossaryListResponse:
    return service.glossary_list(category, domain, q)


@router.get("/glossary/{term_id}", response_model=GlossaryTerm, tags=["glossary"])
def glossary_get(term_id: str) -> GlossaryTerm:
    return service.glossary_get(term_id)


# -- V3.0 formal verification / regime / capacity ----------------------------------------------------


@router.post(
    "/validation/formal-verify",
    response_model=FormalProofResult | AsyncJobRecord,
    tags=["validation"],
)
def validation_formal_verify(
    req: FormalVerifyRequest, async_mode: bool = Query(False)
) -> FormalProofResult | AsyncJobRecord:
    if async_mode:
        return service.enqueue_formal_verify(req)
    return service.formal_verify(req)


@router.post("/factors/regime-analysis", response_model=RegimeAnalysisResult, tags=["factors"])
def factors_regime_analysis(req: RegimeAnalysisRequest) -> RegimeAnalysisResult:
    return service.regime_analysis(req)


@router.post("/portfolio/capacity", response_model=CapacityFrontierResult, tags=["risk"])
def portfolio_capacity(req: CapacityAnalysisRequest) -> CapacityFrontierResult:
    return service.capacity(req)


# -- V3.0 Q7 research ledger / cluster orchestrator ----------------------------------------------------


@router.get("/research/studies", tags=["research"])
def research_studies(
    status: str | None = Query(None),
    verdict: str | None = Query(None),
    family_id: str | None = Query(None),
    parent_study_id: str | None = Query(None),
    q: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    return service.research_list_studies(status, verdict, family_id, parent_study_id, q, limit)


@router.post("/research/dispatch", response_model=ResearchDispatchResponse, tags=["research"])
@router.post("/research/studies", response_model=ResearchDispatchResponse, tags=["research"])
def research_dispatch(
    req: ResearchDispatchRequest,
    async_mode: bool = Query(True),
    idempotency_key: str | None = Header(None),
) -> ResearchDispatchResponse:
    return service.research_dispatch(req, async_mode, idempotency_key)


@router.get("/research/studies/{study_id}", response_model=ResearchStudyDetail, tags=["research"])
def research_study_get(study_id: str) -> ResearchStudyDetail:
    return service.research_get_study(study_id)


@router.get("/research/studies/{study_id}/events", tags=["research"])
def research_study_events(
    study_id: str, after_seq: int = Query(0, ge=0), last_event_id: str | None = Header(None)
) -> StreamingResponse:
    service.research_get_study(study_id)  # 404 early
    if last_event_id and last_event_id.strip().isdigit():
        after_seq = max(after_seq, int(last_event_id.strip()))
    return StreamingResponse(
        service.research_event_stream(study_id, after_seq),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/research/studies/{study_id}/memo", tags=["research"])
def research_study_memo(study_id: str) -> dict[str, Any]:
    return service.research_memo(study_id)


@router.post("/research/studies/{study_id}/publish", response_model=ResearchStudyDetail, tags=["research"])
def research_study_publish(study_id: str, req: PublishFindingRequest) -> ResearchStudyDetail:
    return service.research_publish(study_id, req)


@router.get("/research/baselines", tags=["research"])
def research_baselines_get(snapshot_id: str | None = Query(None), universe_id: str = Query("CSI500")) -> dict[str, Any]:
    return service.research_baselines(snapshot_id, universe_id)


@router.post("/research/baselines", tags=["research"])
def research_baselines_build(snapshot_id: str | None = Query(None), universe_id: str = Query("CSI500")) -> dict[str, Any]:
    return service.research_baselines(snapshot_id, universe_id, build=True)


@router.get("/research/tombstones", tags=["research"])
def research_tombstones_get(
    q: str | None = Query(None), expr_hash: str | None = Query(None), limit: int = Query(50, ge=1, le=500)
) -> dict[str, Any]:
    return service.research_tombstones(q, expr_hash, limit)


@router.post("/research/tombstones", tags=["research"])
def research_tombstones_add(req: TombstoneCreateRequest) -> dict[str, Any]:
    return service.research_add_tombstone(**req.model_dump())


@router.get("/research/clusters", tags=["research"])
def research_clusters(probe_health: bool = Query(False)) -> dict[str, Any]:
    return service.research_clusters(probe_health)
