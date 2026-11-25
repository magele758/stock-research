"""ClusterResearchOrchestrator: 9-state dual-mode research orchestration, SSE & zero-trust re-verification."""

from __future__ import annotations

import json
import threading
import time
import traceback
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from quant_core.lake.registry import QuantRegistry
from quant_core.schemas import (
    AsyncJobRecord,
    ClusterAdapterType,
    ClusterNodeStatus,
    DimensionBaselineRecord,
    FalsifiedTombstoneRecord,
    PublishFindingRequest,
    ResearchDispatchRequest,
    ResearchDispatchResponse,
    ResearchStudyDetail,
    ResearchStudySummary,
    StudyEventRecord,
)

from quant_api import deps, jobs
from quant_api.orchestrator.adapters import (
    AdapterSecurityError,
    ClusterAdapter,
    WorkerTaskEnvelope,
    get_adapter,
)
from quant_api.orchestrator.cluster_config import (
    get_cluster_spec,
    list_cluster_statuses,
)
from quant_api.orchestrator.memo import render_ic_memo
from quant_api.orchestrator.sandbox import (
    ensure_dimension_baselines,
    run_deterministic_verification_pipeline,
)

TERMINAL_STUDY_STATUSES = frozenset({"COMPLETED", "FALSIFIED", "FAILED"})

VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    "DRAFT": frozenset({"QUEUED", "FAILED"}),
    "QUEUED": frozenset({"DISPATCHED", "FALSIFIED", "FAILED"}),
    "DISPATCHED": frozenset({"DEVELOPING", "FAILED"}),
    "DEVELOPING": frozenset({"VERIFYING", "FAILED"}),
    "VERIFYING": frozenset({"DIAGNOSING", "FALSIFIED", "FAILED"}),
    "DIAGNOSING": frozenset({"COMPLETED", "FALSIFIED", "FAILED"}),
    "FAILED": frozenset({"QUEUED"}),
    "COMPLETED": frozenset(),
    "FALSIFIED": frozenset(),
}


def _sse_frame(ev: StudyEventRecord) -> str:
    payload = json.loads(ev.model_dump_json())
    return f"id: {ev.seq}\nevent: {ev.event_type}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


class ClusterResearchOrchestrator:
    """Dual-mode institutional research orchestrator with 9-state machine, SSE, and zero-trust proof gate."""

    def __init__(
        self,
        *,
        registry: QuantRegistry | None = None,
        max_workers: int = 4,
        adapter_overrides: dict[str, ClusterAdapter] | None = None,
    ) -> None:
        self._explicit_registry = registry
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="quant-research")
        self._adapter_overrides: dict[str, ClusterAdapter] = dict(adapter_overrides or {})
        self._slot_lock = threading.Lock()
        self._active_slots: dict[str, int] = {}
        self._queued_slots: dict[str, int] = {}
        self._fallback_registry_lock = threading.Lock()
        self._fallback_registry: QuantRegistry | None = None

    def get_registry(self) -> QuantRegistry:
        """Resolve the active ``QuantRegistry`` (preferring ``lake.registry`` when it is a real QuantRegistry)."""
        if self._explicit_registry is not None:
            return self._explicit_registry
        try:
            lake = deps.get_lake()
            reg = getattr(lake, "registry", None)
            if isinstance(reg, QuantRegistry):
                return reg
        except deps.ServiceUnavailable:
            pass
        with self._fallback_registry_lock:
            target = deps.state_dir() / "quant_registry.db"
            if self._fallback_registry is None or self._fallback_registry.db_path != target:
                self._fallback_registry = QuantRegistry(db_path=target)
            return self._fallback_registry

    def set_adapter_override(self, adapter_type: ClusterAdapterType | str, adapter: ClusterAdapter) -> None:
        key = adapter_type.value if isinstance(adapter_type, ClusterAdapterType) else str(adapter_type)
        self._adapter_overrides[key] = adapter

    def _resolve_adapter(self, adapter_type: ClusterAdapterType) -> ClusterAdapter:
        if adapter_type.value in self._adapter_overrides:
            return self._adapter_overrides[adapter_type.value]
        return get_adapter(adapter_type)

    # ------------------------------------------------------------------
    # State Machine Transitions
    # ------------------------------------------------------------------

    def transition(
        self,
        study_id: str,
        new_status: str,
        *,
        stage: str,
        progress_pct: int,
        message: str,
        payload: dict[str, Any] | None = None,
        actor: str = "orchestrator",
    ) -> StudyEventRecord:
        reg = self.get_registry()
        cur = reg.get_study(study_id, include_lineage=False, include_events=False, include_notes=False)
        if cur is None:
            raise deps.NotFound(f"research study {study_id!r} not found")
        allowed = VALID_TRANSITIONS.get(cur.status, frozenset())
        if new_status != cur.status and new_status not in allowed:
            raise ValueError(f"Invalid study state transition: {cur.status} -> {new_status}")
        reg.update_study(study_id, status=new_status, stage=stage)
        return reg.append_study_event(
            study_id,
            stage=stage,
            event_type="state_change",
            progress_pct=progress_pct,
            message=message,
            payload={"from_status": cur.status, "to_status": new_status, **(payload or {})},
            actor=actor,
        )

    def _emit_event(
        self,
        study_id: str,
        stage: str,
        event_type: str,
        progress_pct: int,
        message: str,
        payload: dict[str, Any] | None = None,
        actor: str = "orchestrator",
    ) -> StudyEventRecord:
        return self.get_registry().append_study_event(
            study_id,
            stage=stage,
            event_type=event_type,
            progress_pct=progress_pct,
            message=message,
            payload=payload,
            actor=actor,
        )

    # ------------------------------------------------------------------
    # Cluster Inventory, Baselines, Tombstones & Query APIs
    # ------------------------------------------------------------------

    def list_clusters(self, *, probe_health: bool = False) -> list[ClusterNodeStatus]:
        with self._slot_lock:
            active = dict(self._active_slots)
            queued = dict(self._queued_slots)
        return list_cluster_statuses(active_counts=active, queue_counts=queued, probe_health=probe_health)

    def list_studies(
        self,
        *,
        status: str | None = None,
        verdict: str | None = None,
        snapshot_id: str | None = None,
        family_id: str | None = None,
        parent_study_id: str | None = None,
        tag: str | None = None,
        q: str | None = None,
        limit: int = 50,
    ) -> list[ResearchStudySummary]:
        return self.get_registry().list_studies(
            status=status,
            verdict=verdict,
            snapshot_id=snapshot_id,
            family_id=family_id,
            parent_study_id=parent_study_id,
            tag=tag,
            q=q,
            limit=limit,
        )

    def get_study(
        self,
        study_id: str,
        *,
        include_lineage: bool = True,
        include_events: bool = True,
        include_notes: bool = True,
    ) -> ResearchStudyDetail:
        detail = self.get_registry().get_study(
            study_id,
            include_lineage=include_lineage,
            include_events=include_events,
            include_notes=include_notes,
        )
        if detail is None:
            raise deps.NotFound(f"research study {study_id!r} not found")
        return detail

    def render_memo(self, study_id: str) -> str:
        return render_ic_memo(self.get_study(study_id))

    def export_memo(self, study_id: str) -> dict[str, Any]:
        detail = self.get_study(study_id)
        return {
            "study_id": study_id,
            "filename": f"IC-Memo-{study_id}-{detail.snapshot_id}.md",
            "markdown": render_ic_memo(detail),
        }

    def ensure_golden_studies(
        self,
        snapshot_id: str | None = None,
        universe_id: str = "CSI500",
    ) -> dict[str, Any]:
        from quant_api.orchestrator.seeder import ensure_golden_studies as _seed

        return _seed(snapshot_id=snapshot_id, universe_id=universe_id, orchestrator=self)

    def build_or_get_baselines(
        self,
        *,
        snapshot_id: str | None = None,
        universe_id: str = "CSI500",
        force_rebuild: bool = False,
    ) -> list[DimensionBaselineRecord]:
        snap = deps.require_snapshot(snapshot_id)
        lake = deps.get_lake()
        return ensure_dimension_baselines(
            self.get_registry(),
            lake,
            snap.snapshot_id,
            universe_id=universe_id,
            force_rebuild=force_rebuild,
        )

    def list_baselines(
        self,
        *,
        snapshot_id: str | None = None,
        universe_id: str = "CSI500",
    ) -> list[DimensionBaselineRecord]:
        return self.build_or_get_baselines(
            snapshot_id=snapshot_id,
            universe_id=universe_id,
            force_rebuild=False,
        )

    def build_baselines(
        self,
        *,
        snapshot_id: str | None = None,
        universe_id: str = "CSI500",
    ) -> list[DimensionBaselineRecord]:
        return self.build_or_get_baselines(
            snapshot_id=snapshot_id,
            universe_id=universe_id,
            force_rebuild=True,
        )

    def list_tombstones(
        self,
        *,
        expr_hash: str | None = None,
        failure_code: str | None = None,
        snapshot_id: str | None = None,
        q: str | None = None,
        limit: int = 50,
    ) -> list[FalsifiedTombstoneRecord]:
        return self.get_registry().list_tombstones(
            expr_hash=expr_hash,
            failure_code=failure_code,
            snapshot_id=snapshot_id,
            q=q,
            limit=limit,
        )

    def register_tombstone(
        self,
        *,
        factor_id: str,
        expression: str,
        universe_id: str = "CSI500",
        snapshot_id: str | None = None,
        failure_code: str = "MANUAL_FALSIFIED",
        lesson_summary: str = "",
        study_id: str | None = None,
        failure_metrics: dict[str, Any] | None = None,
        created_by: str = "researcher",
    ) -> FalsifiedTombstoneRecord:
        snap = deps.require_snapshot(snapshot_id)
        return self.get_registry().save_tombstone(
            study_id=study_id,
            factor_id=factor_id,
            expression=expression,
            universe_id=universe_id,
            snapshot_id=snap.snapshot_id,
            failure_code=failure_code,
            failure_metrics=failure_metrics,
            lesson_summary=lesson_summary or f"Falsified factor {factor_id} ({failure_code})",
            created_by=created_by,
        )

    def add_tombstone(
        self,
        *,
        factor_id: str,
        expression: str,
        universe_id: str = "CSI500",
        snapshot_id: str | None = None,
        failure_code: str = "MANUAL_FALSIFIED",
        lesson_summary: str = "",
        study_id: str | None = None,
        failure_metrics: dict[str, Any] | None = None,
        created_by: str = "quant-lead",
    ) -> FalsifiedTombstoneRecord:
        return self.register_tombstone(
            factor_id=factor_id,
            expression=expression,
            universe_id=universe_id,
            snapshot_id=snapshot_id,
            failure_code=failure_code,
            lesson_summary=lesson_summary,
            study_id=study_id,
            failure_metrics=failure_metrics,
            created_by=created_by,
        )

    # ------------------------------------------------------------------
    # Dispatch Research (Mode 1 engine_sandbox & Mode 2 cluster_dev_worker)
    # ------------------------------------------------------------------

    def dispatch_research(
        self,
        req: ResearchDispatchRequest,
        *,
        async_mode: bool = True,
        study_id: str | None = None,
    ) -> ResearchDispatchResponse:
        """Create and dispatch a research study with pre-flight 3-tier tombstone scan."""
        snap = deps.require_snapshot(req.snapshot_id)
        reg = self.get_registry()

        if req.idempotency_key:
            existing = reg.get_study_by_idempotency_key(req.idempotency_key)
            if existing is not None:
                job_rec = jobs.get_manager().get(existing.job_id) if existing.job_id else None
                return ResearchDispatchResponse(
                    study=ResearchStudySummary.model_validate(existing.model_dump(include=set(ResearchStudySummary.model_fields))),
                    job=job_rec,
                    detail=existing if existing.status in TERMINAL_STUDY_STATUSES else None,
                    tombstone_warnings=existing.tombstone_warnings,
                    duplicate_blocked=(existing.status == "FALSIFIED" and bool(existing.tombstone_warnings)),
                )

        # Validate cluster specification when using Mode 2
        cluster_spec = get_cluster_spec(req.target_cluster)
        if req.dispatch_mode == "cluster_dev_worker":
            if cluster_spec is None:
                raise deps.InvalidArgument(f"Unknown target_cluster: {req.target_cluster!r}")
            if not cluster_spec.enabled:
                raise deps.ServiceUnavailable(
                    f"Target cluster {req.target_cluster!r} is disabled/offline",
                    code="CLUSTER_UNAVAILABLE",
                    retryable=True,
                )

        # Pre-dispatch 3-layer tombstone deduplication
        dup_scan = reg.check_tombstone_duplicates(
            expression=req.expression,
            hypothesis=req.hypothesis,
            title=req.title,
            factor_id=req.factor_id,
        )
        tomb_warnings: list[dict[str, Any]] = list(dup_scan["matches"])

        # Create in DRAFT, immediately transition to QUEUED
        detail = reg.create_study(
            study_id=study_id,
            title=req.title,
            snapshot_id=snap.snapshot_id,
            hypothesis=req.hypothesis,
            parent_study_id=req.parent_study_id,
            family_id=req.family_id,
            kind=req.kind,
            topic_tags=req.topic_tags,
            status="DRAFT",
            verdict="PENDING",
            stage="HYPOTHESIS",
            dispatch_mode=req.dispatch_mode,
            target_cluster=req.target_cluster,
            universe_id=req.universe_id,
            idempotency_key=req.idempotency_key,
            factor_id=req.factor_id or "alpha_vp_corr_20d",
            expression=req.expression,
            hypothesis_extra={
                "preprocess": req.preprocess.model_dump(mode="json"),
                "constraints": req.constraints.model_dump(mode="json") if req.constraints else None,
                "dimensions": list(req.dimensions),
                "trial_count_m": req.trial_count_m,
                "bootstrap_b": req.bootstrap_b,
                "allow_duplicate": req.allow_duplicate,
                "override_rationale": req.override_rationale,
            },
            math_derivation=req.math_derivation,
            tombstone_warnings=tomb_warnings,
            created_by=req.created_by,
        )
        sid = detail.study_id
        self._emit_event(sid, "HYPOTHESIS", "state_change", 2, f"Study {sid} created in DRAFT", {"status": "DRAFT"})
        self.transition(sid, "QUEUED", stage="HYPOTHESIS", progress_pct=5, message="Queued after snapshot lock")

        if tomb_warnings:
            self._emit_event(
                sid,
                "HYPOTHESIS",
                "warning",
                6,
                f"Tombstone scan matched {len(tomb_warnings)} prior falsified record(s)",
                {"exact_hit": dup_scan["exact_hit"], "ast_hit": dup_scan["ast_hit"], "matches": tomb_warnings[:3]},
            )

        # Tier-1 exact duplicate block unless allow_duplicate=True
        if dup_scan["exact_hit"] and not req.allow_duplicate:
            self.transition(
                sid,
                "FALSIFIED",
                stage="PUBLISHED",
                progress_pct=100,
                message="Blocked by exact falsified tombstone match (set allow_duplicate=true to override)",
            )
            blocked = reg.finalize_study(
                sid,
                status="FALSIFIED",
                verdict="FALSIFIED",
                metrics_summary={"duplicate_tombstone_blocked": True},
                auto_tombstone=False,
            )
            self._emit_event(
                sid,
                "PUBLISHED",
                "terminal",
                100,
                f"Study {sid} short-circuited by exact tombstone match",
                {"status": "FALSIFIED", "verdict": "FALSIFIED", "duplicate_blocked": True},
            )
            blocked = reg.get_study(sid) or blocked
            return ResearchDispatchResponse(
                study=ResearchStudySummary.model_validate(blocked.model_dump(include=set(ResearchStudySummary.model_fields))),
                job=None,
                detail=blocked,
                tombstone_warnings=tomb_warnings,
                duplicate_blocked=True,
            )

        # External publish mode opens a QUEUED study ready for publish_finding() zero-trust verification
        if req.dispatch_mode == "external_publish":
            queued_detail = reg.get_study(sid)
            assert queued_detail is not None
            return ResearchDispatchResponse(
                study=ResearchStudySummary.model_validate(
                    queued_detail.model_dump(include=set(ResearchStudySummary.model_fields))
                ),
                job=None,
                detail=queued_detail,
                tombstone_warnings=tomb_warnings,
                duplicate_blocked=False,
            )

        effective_m = req.trial_count_m + (5 if dup_scan["ast_hit"] else 0)
        if async_mode:
            with self._slot_lock:
                self._queued_slots[req.target_cluster] = self._queued_slots.get(req.target_cluster, 0) + 1

            def _job_work(progress: jobs.ProgressFn) -> dict[str, Any]:
                progress(10)
                final_detail = self._execute_study(sid, req, snap, effective_m)
                progress(99)
                return {
                    "summary": {
                        "study_id": final_detail.study_id,
                        "status": final_detail.status,
                        "verdict": final_detail.verdict,
                        "proofs_passed": final_detail.proofs_passed,
                        "proofs_total": final_detail.proofs_total,
                    },
                    "artifact_uri": final_detail.artifact_uri,
                }

            job_rec: AsyncJobRecord = jobs.get_manager().submit(
                "research_study",
                snap.snapshot_id,
                _job_work,
                idempotency_key=f"study:{sid}",
            )
            updated = reg.update_study(sid, job_id=job_rec.job_id)
            return ResearchDispatchResponse(
                study=ResearchStudySummary.model_validate(updated.model_dump(include=set(ResearchStudySummary.model_fields))),
                job=job_rec,
                detail=None,
                tombstone_warnings=tomb_warnings,
                duplicate_blocked=False,
            )

        # Synchronous execution
        final_detail = self._execute_study(sid, req, snap, effective_m)
        return ResearchDispatchResponse(
            study=ResearchStudySummary.model_validate(final_detail.model_dump(include=set(ResearchStudySummary.model_fields))),
            job=None,
            detail=final_detail,
            tombstone_warnings=tomb_warnings,
            duplicate_blocked=False,
        )

    def dispatch_study(
        self,
        req: ResearchDispatchRequest,
        *,
        async_mode: bool = True,
        study_id: str | None = None,
    ) -> ResearchDispatchResponse:
        """Alias for ``dispatch_research`` used by REST/MCP service wiring."""
        return self.dispatch_research(req, async_mode=async_mode, study_id=study_id)

    def _execute_study(
        self,
        study_id: str,
        req: ResearchDispatchRequest,
        snap: Any,
        effective_m: int,
    ) -> ResearchStudyDetail:
        reg = self.get_registry()
        cluster_id = req.target_cluster
        with self._slot_lock:
            if self._queued_slots.get(cluster_id, 0) > 0:
                self._queued_slots[cluster_id] -= 1
            self._active_slots[cluster_id] = self._active_slots.get(cluster_id, 0) + 1

        try:
            self.transition(
                study_id,
                "DISPATCHED",
                stage="MATH",
                progress_pct=12,
                message=f"Dispatched to {cluster_id} ({req.dispatch_mode})",
                payload={"cluster_id": cluster_id, "dispatch_mode": req.dispatch_mode},
            )
            self.transition(
                study_id,
                "DEVELOPING",
                stage="MATH",
                progress_pct=18,
                message="Preparing isolated execution & candidate expression",
            )

            candidate_expr = req.expression
            candidate_fid = req.factor_id or "alpha_vp_corr_20d"
            worker_claimed: dict[str, Any] | None = None

            if req.dispatch_mode == "cluster_dev_worker":
                spec = get_cluster_spec(cluster_id)
                if spec is None:
                    raise deps.InvalidArgument(f"Cluster {cluster_id!r} not found")
                adapter = self._resolve_adapter(spec.adapter)
                task = WorkerTaskEnvelope(
                    study_id=study_id,
                    command_template=spec.allowed_command_templates[0],
                    snapshot_id=snap.snapshot_id,
                    universe_id=req.universe_id,
                    title=req.title,
                    hypothesis=req.hypothesis,
                    factor_id=candidate_fid,
                    expression=candidate_expr,
                    timeout_sec=spec.default_timeout_sec,
                )

                def _on_adapter_event(ev: dict[str, Any]) -> None:
                    self._emit_event(
                        study_id,
                        str(ev.get("stage", "DEVELOPING")),
                        str(ev.get("event_type", "progress")),
                        int(ev.get("progress_pct", 22)),
                        str(ev.get("message", "worker heartbeat")),
                        ev,
                        actor=cluster_id,
                    )

                bundle = adapter.launch(spec, task, on_event=_on_adapter_event)
                if not bundle.succeeded:
                    raise RuntimeError(bundle.error or f"Cluster worker {cluster_id} failed (exit={bundle.exit_code})")
                if bundle.candidate_expression:
                    candidate_expr = bundle.candidate_expression
                if bundle.candidate_factor_id:
                    candidate_fid = bundle.candidate_factor_id
                if bundle.worker_claimed_metrics:
                    worker_claimed = dict(bundle.worker_claimed_metrics)

            lake = deps.get_lake()

            def _emit(stage: str, ev_type: str, pct: int, msg: str, payload: dict[str, Any] | None) -> None:
                self._emit_event(study_id, stage, ev_type, pct, msg, payload)

            def _move(status: str, stage: str, pct: int, msg: str) -> None:
                self.transition(study_id, status, stage=stage, progress_pct=pct, message=msg)

            return run_deterministic_verification_pipeline(
                study_id=study_id,
                registry=reg,
                lake=lake,
                snapshot=snap,
                universe_id=req.universe_id,
                factor_id=candidate_fid,
                expression=candidate_expr,
                preprocess=req.preprocess,
                constraints=req.constraints,
                dimensions=req.dimensions,
                trial_count_m=effective_m,
                bootstrap_b=req.bootstrap_b,
                worker_claimed_metrics=worker_claimed,
                emit=_emit,
                transition_status=_move,
            )
        except Exception as exc:  # noqa: BLE001
            err_msg = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
            cur = reg.get_study(study_id, include_lineage=False, include_events=False, include_notes=False)
            if cur is not None and cur.status not in TERMINAL_STUDY_STATUSES:
                self.transition(
                    study_id,
                    "FAILED",
                    stage=cur.stage,
                    progress_pct=100,
                    message=f"Infrastructure/execution failure: {err_msg}",
                    payload={"error": err_msg, "retryable": not isinstance(exc, AdapterSecurityError)},
                )
                reg.finalize_study(
                    study_id,
                    status="FAILED",
                    verdict="ERROR",
                    metrics_summary={"error": err_msg, "retryable": not isinstance(exc, AdapterSecurityError)},
                    auto_tombstone=False,
                )
                self._emit_event(
                    study_id,
                    "PUBLISHED",
                    "terminal",
                    100,
                    f"Study {study_id} FAILED: {err_msg}",
                    {"status": "FAILED", "verdict": "ERROR", "error": err_msg},
                )
            failed_detail = reg.get_study(study_id)
            assert failed_detail is not None
            return failed_detail
        finally:
            with self._slot_lock:
                if self._active_slots.get(cluster_id, 0) > 0:
                    self._active_slots[cluster_id] -= 1

    # ------------------------------------------------------------------
    # Zero-Trust External Finding Verification & Publish
    # ------------------------------------------------------------------

    def publish_finding(
        self,
        study_id: str | None,
        req: PublishFindingRequest,
    ) -> ResearchStudyDetail:
        """Accept external worker findings and FORCE full local FormalVerificationEngine recomputation.

        External ``worker_claimed_metrics`` are NEVER trusted: they are quarantined under
        ``metrics_summary['worker_claimed_metrics']`` and compared against deterministic
        re-computation in ``metrics_summary['claimed_vs_verified_diff']``.
        """
        snap = deps.require_snapshot(req.snapshot_id)
        reg = self.get_registry()
        lake = deps.get_lake()

        existing = reg.get_study(study_id, include_lineage=False, include_events=False, include_notes=False) if study_id else None
        if existing is None:
            created = reg.create_study(
                study_id=study_id,
                title=req.title or f"External Finding · {req.factor_id or req.worker_cluster_id}",
                snapshot_id=snap.snapshot_id,
                hypothesis=req.math_derivation.economic_hypothesis if req.math_derivation else req.notes or "External worker candidate finding",
                status="DRAFT",
                verdict="PENDING",
                stage="HYPOTHESIS",
                dispatch_mode="external_publish",
                target_cluster=req.worker_cluster_id,
                universe_id=req.universe_id,
                factor_id=req.factor_id or "external_candidate",
                expression=req.candidate_expression,
                math_derivation=req.math_derivation,
                created_by=req.worker_cluster_id,
            )
            sid = created.study_id
            self.transition(sid, "QUEUED", stage="HYPOTHESIS", progress_pct=5, message="External finding received")
            self.transition(sid, "DISPATCHED", stage="MATH", progress_pct=10, message=f"Source worker: {req.worker_cluster_id}")
            self.transition(sid, "DEVELOPING", stage="MATH", progress_pct=15, message="Quarantining untrusted external claims")
        else:
            sid = existing.study_id
            if existing.status == "DRAFT":
                self.transition(sid, "QUEUED", stage="HYPOTHESIS", progress_pct=5, message="External finding attached")
                self.transition(sid, "DISPATCHED", stage="MATH", progress_pct=10, message=f"Source worker: {req.worker_cluster_id}")
                self.transition(sid, "DEVELOPING", stage="MATH", progress_pct=15, message="External candidate ready for verification")
            elif existing.status == "QUEUED":
                self.transition(sid, "DISPATCHED", stage="MATH", progress_pct=10, message=f"Source worker: {req.worker_cluster_id}")
                self.transition(sid, "DEVELOPING", stage="MATH", progress_pct=15, message="External candidate ready for verification")
            elif existing.status == "DISPATCHED":
                self.transition(sid, "DEVELOPING", stage="MATH", progress_pct=15, message="External candidate ready for verification")
            elif existing.status == "FAILED":
                self.transition(sid, "QUEUED", stage="HYPOTHESIS", progress_pct=5, message="Retrying failed study for external publish")
                self.transition(sid, "DISPATCHED", stage="MATH", progress_pct=10, message=f"Source worker: {req.worker_cluster_id}")
                self.transition(sid, "DEVELOPING", stage="MATH", progress_pct=15, message="External candidate ready for verification")
            elif existing.status in TERMINAL_STUDY_STATUSES:
                reg.update_study(sid, status="DEVELOPING", verdict="PENDING", stage="MATH")
                self._emit_event(
                    sid,
                    "MATH",
                    "state_change",
                    15,
                    f"Re-opening {sid} for zero-trust external finding re-verification",
                    {"from_status": existing.status, "to_status": "DEVELOPING"},
                )
            if req.math_derivation is not None:
                reg.update_study(sid, math_derivation=req.math_derivation)

        if req.notes:
            reg.add_research_note(sid, author=req.worker_cluster_id, content_md=req.notes, note_type="rationale")

        def _emit(stage: str, ev_type: str, pct: int, msg: str, payload: dict[str, Any] | None) -> None:
            self._emit_event(sid, stage, ev_type, pct, msg, payload)

        def _move(status: str, stage: str, pct: int, msg: str) -> None:
            self.transition(sid, status, stage=stage, progress_pct=pct, message=msg)

        return run_deterministic_verification_pipeline(
            study_id=sid,
            registry=reg,
            lake=lake,
            snapshot=snap,
            universe_id=req.universe_id,
            factor_id=req.factor_id or "external_candidate",
            expression=req.candidate_expression,
            worker_claimed_metrics=dict(req.worker_claimed_metrics),
            emit=_emit,
            transition_status=_move,
        )

    # ------------------------------------------------------------------
    # SSE Event Stream (with Last-Event-ID / after_seq resume)
    # ------------------------------------------------------------------

    def stream_events(
        self,
        study_id: str,
        *,
        after_seq: int = 0,
        replay_only: bool = False,
        poll_interval: float = 0.1,
        max_seconds: float = 120.0,
    ) -> Iterator[str]:
        """Yield SSE frames for ``study_id`` starting strictly after ``after_seq``."""
        reg = self.get_registry()
        if reg.get_study(study_id, include_lineage=False, include_events=False, include_notes=False) is None:
            yield f"event: error\ndata: {json.dumps({'study_id': study_id, 'error': 'study not found'})}\n\n"
            return

        cursor = int(after_seq)
        deadline = time.monotonic() + max_seconds
        while True:
            events = reg.list_study_events(study_id, after_seq=cursor)
            saw_terminal = False
            for ev in events:
                yield _sse_frame(ev)
                cursor = ev.seq
                if ev.event_type == "terminal":
                    saw_terminal = True
            if replay_only or saw_terminal:
                return
            study = reg.get_study(study_id, include_lineage=False, include_events=False, include_notes=False)
            if study is not None and study.status in TERMINAL_STUDY_STATUSES:
                # Drain any final events written just before terminal status
                for ev in reg.list_study_events(study_id, after_seq=cursor):
                    yield _sse_frame(ev)
                    cursor = ev.seq
                return
            if time.monotonic() >= deadline:
                yield f"event: timeout\ndata: {json.dumps({'study_id': study_id, 'last_seq': cursor})}\n\n"
                return
            time.sleep(poll_interval)

    def wait_study(self, study_id: str, timeout: float = 30.0, poll: float = 0.05) -> ResearchStudyDetail:
        deadline = time.monotonic() + timeout
        detail = self.get_study(study_id)
        while detail.status not in TERMINAL_STUDY_STATUSES and time.monotonic() < deadline:
            time.sleep(poll)
            detail = self.get_study(study_id)
        return detail

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


_ORCH_LOCK = threading.Lock()
_ORCHESTRATOR: ClusterResearchOrchestrator | None = None


def get_orchestrator() -> ClusterResearchOrchestrator:
    global _ORCHESTRATOR
    with _ORCH_LOCK:
        if _ORCHESTRATOR is None:
            _ORCHESTRATOR = ClusterResearchOrchestrator()
        return _ORCHESTRATOR


def set_orchestrator(orch: ClusterResearchOrchestrator | None) -> None:
    global _ORCHESTRATOR
    with _ORCH_LOCK:
        _ORCHESTRATOR = orch


__all__ = [
    "TERMINAL_STUDY_STATUSES",
    "VALID_TRANSITIONS",
    "ClusterResearchOrchestrator",
    "get_orchestrator",
    "set_orchestrator",
]
