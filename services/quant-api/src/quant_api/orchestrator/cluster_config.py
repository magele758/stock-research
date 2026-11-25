"""Cluster worker inventory loader and safe health probing.

Reads cluster definitions from ``QUANT_CLUSTER_WORKERS`` (JSON array) or
``config/cluster_workers.json`` relative to repo root, falling back to safe
built-in defaults with zero hardcoded IPs, credentials, or personal paths.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from quant_core.schemas import ClusterAdapterType, ClusterNodeStatus, ClusterWorkerSpec

from quant_api import deps


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def default_cluster_specs() -> list[ClusterWorkerSpec]:
    """Return built-in safe default cluster worker inventory."""
    ssh_host = os.environ.get("QUANT_SSH_CLUSTER_HOST", "").strip()
    herdr_enabled_env = os.environ.get("QUANT_CLUSTER_HERDR_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
    return [
        ClusterWorkerSpec(
            cluster_id="engine_sandbox",
            display_name="Deterministic Engine Sandbox",
            adapter=ClusterAdapterType.LOCAL_SUBPROCESS,
            host="localhost",
            agent_cli="python",
            capabilities=["polars", "cvxpy", "formal_verify", "event_backtest", "regime_analysis", "capacity"],
            max_concurrency=4,
            default_timeout_sec=300,
            max_timeout_sec=1200,
            workspace_root="data/sandboxes",
            lake_mount_mode="read_only",
            allowed_command_templates=["run_study_script", "run_engine_sandbox"],
            enabled=True,
        ),
        ClusterWorkerSpec(
            cluster_id="ubuntu1_cluster",
            display_name="Primary Research Compute Cluster",
            adapter=ClusterAdapterType.HERDR_HC,
            host="primary-compute-node",
            agent_cli="claude",
            capabilities=["herdr_hc", "polars", "cvxpy", "factor_dev", "multi_factor"],
            max_concurrency=4,
            default_timeout_sec=600,
            max_timeout_sec=1800,
            workspace_root="data/sandboxes",
            lake_mount_mode="read_only",
            allowed_command_templates=["agent_factor_dev", "run_study_script"],
            enabled=herdr_enabled_env,
        ),
        ClusterWorkerSpec(
            cluster_id="herdr_agent",
            display_name="Remote Research Agent Runner",
            adapter=ClusterAdapterType.SSH_RUNNER if ssh_host else ClusterAdapterType.HERDR_HC,
            host=ssh_host or "remote-research-node",
            agent_cli="codex",
            capabilities=["ssh_runner", "herdr_hc", "factor_dev", "gpu_research"],
            max_concurrency=2,
            default_timeout_sec=600,
            max_timeout_sec=1800,
            workspace_root="data/sandboxes",
            lake_mount_mode="read_only",
            allowed_command_templates=["run_study_script", "agent_factor_dev"],
            enabled=bool(ssh_host),
        ),
    ]


def load_cluster_specs() -> list[ClusterWorkerSpec]:
    """Load cluster specs from ``QUANT_CLUSTER_WORKERS``, config file, or safe defaults."""
    raw_env = os.environ.get("QUANT_CLUSTER_WORKERS", "").strip()
    if raw_env:
        parsed = json.loads(raw_env)
        if not isinstance(parsed, list):
            raise ValueError("QUANT_CLUSTER_WORKERS must be a JSON array of ClusterWorkerSpec objects")
        return [ClusterWorkerSpec.model_validate(item) for item in parsed]

    cfg_path: Path = deps.repo_root() / "config" / "cluster_workers.json"
    if cfg_path.is_file():
        parsed = json.loads(cfg_path.read_text(encoding="utf-8"))
        if isinstance(parsed, list):
            return [ClusterWorkerSpec.model_validate(item) for item in parsed]

    return default_cluster_specs()


def get_cluster_spec(cluster_id: str) -> ClusterWorkerSpec | None:
    for spec in load_cluster_specs():
        if spec.cluster_id == cluster_id:
            return spec
    return None


def probe_cluster_status(
    spec: ClusterWorkerSpec,
    *,
    active_slots: int = 0,
    queue_depth: int = 0,
    probe_health: bool = False,
) -> ClusterNodeStatus:
    """Compute a sanitized node status without leaking credentials, real IPs, or local home paths."""
    status = "offline"
    detail = ""
    if not spec.enabled:
        status = "offline"
        detail = "cluster disabled in configuration"
    elif spec.adapter == ClusterAdapterType.LOCAL_SUBPROCESS:
        status = "online"
        detail = "in-process & isolated subprocess sandbox ready"
    elif spec.adapter == ClusterAdapterType.HERDR_HC:
        hc_override = os.environ.get("QUANT_HC_BINARY", "").strip()
        hc_found = bool(hc_override and Path(hc_override).exists()) or bool(shutil.which("hc"))
        if hc_found:
            status = "online"
            detail = "herdr-core (hc) CLI detected"
        else:
            status = "offline"
            detail = "herdr-core (hc) CLI not found on PATH"
    elif spec.adapter == ClusterAdapterType.SSH_RUNNER:
        ssh_found = bool(shutil.which("ssh"))
        has_host = bool(spec.host and not spec.host.startswith("remote-research-"))
        if ssh_found and has_host:
            status = "online" if not probe_health else "degraded"
            detail = "allowlisted ssh_runner configured"
        else:
            status = "offline"
            detail = "ssh_runner host not configured via environment"

    return ClusterNodeStatus(
        cluster_id=spec.cluster_id,
        display_name=spec.display_name,
        adapter=spec.adapter.value,
        agent_cli=spec.agent_cli,
        status=status,  # type: ignore[arg-type]
        slots_busy=max(0, int(active_slots)),
        slots_total=max(1, int(spec.max_concurrency)),
        queue_depth=max(0, int(queue_depth)),
        capabilities=list(spec.capabilities),
        last_heartbeat=_now_iso(),
        detail=detail,
    )


def list_cluster_statuses(
    *,
    active_counts: dict[str, int] | None = None,
    queue_counts: dict[str, int] | None = None,
    probe_health: bool = False,
) -> list[ClusterNodeStatus]:
    active = active_counts or {}
    queued = queue_counts or {}
    return [
        probe_cluster_status(
            spec,
            active_slots=active.get(spec.cluster_id, 0),
            queue_depth=queued.get(spec.cluster_id, 0),
            probe_health=probe_health,
        )
        for spec in load_cluster_specs()
    ]


__all__ = [
    "default_cluster_specs",
    "get_cluster_spec",
    "list_cluster_statuses",
    "load_cluster_specs",
    "probe_cluster_status",
]
