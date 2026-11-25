"""QuantLab Pro V3.0 Dual-Mode Cluster Research Orchestrator package."""

from __future__ import annotations

from quant_api.orchestrator.adapters import (
    COMMAND_TEMPLATE_REGISTRY,
    AdapterSecurityError,
    ClusterAdapter,
    HerdrHcAdapter,
    LocalSubprocessAdapter,
    SshRunnerAdapter,
    TemplateNotAllowedError,
    WorkerOutputBundle,
    WorkerTaskEnvelope,
    build_sanitized_env,
    get_adapter,
    prepare_sandbox_dir,
)
from quant_api.orchestrator.cluster_config import (
    default_cluster_specs,
    get_cluster_spec,
    list_cluster_statuses,
    load_cluster_specs,
    probe_cluster_status,
)
from quant_api.orchestrator.engine import (
    TERMINAL_STUDY_STATUSES,
    VALID_TRANSITIONS,
    ClusterResearchOrchestrator,
    get_orchestrator,
    set_orchestrator,
)
from quant_api.orchestrator.memo import render_ic_memo
from quant_api.orchestrator.sandbox import (
    ensure_dimension_baselines,
    reset_v3_engine_overrides,
    run_deterministic_verification_pipeline,
    set_v3_engine_overrides,
)
from quant_api.orchestrator.seeder import ensure_golden_studies

__all__ = [
    "COMMAND_TEMPLATE_REGISTRY",
    "TERMINAL_STUDY_STATUSES",
    "VALID_TRANSITIONS",
    "AdapterSecurityError",
    "ClusterAdapter",
    "ClusterResearchOrchestrator",
    "HerdrHcAdapter",
    "LocalSubprocessAdapter",
    "SshRunnerAdapter",
    "TemplateNotAllowedError",
    "WorkerOutputBundle",
    "WorkerTaskEnvelope",
    "build_sanitized_env",
    "default_cluster_specs",
    "ensure_dimension_baselines",
    "ensure_golden_studies",
    "get_adapter",
    "get_cluster_spec",
    "get_orchestrator",
    "list_cluster_statuses",
    "load_cluster_specs",
    "prepare_sandbox_dir",
    "probe_cluster_status",
    "render_ic_memo",
    "reset_v3_engine_overrides",
    "run_deterministic_verification_pipeline",
    "set_orchestrator",
    "set_v3_engine_overrides",
]
