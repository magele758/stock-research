"""MCP contract tests: 21 tools (13 V2.0 + 8 V3.0), McpEnvelope validity, strict <= 8,192-byte responses,
async fast-accept, Bearer guard, stdio transport and the Agent Home registration dry run."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services" / "quant-api" / "tests"))  # shared hermetic fakes

import anyio
import fakes
from mcp.client import Client
from quant_api import deps, jobs
from quant_api.catalog import MCP_TOOL_NAMES
from quant_core.schemas import McpEnvelope, McpErrorCode
from quant_mcp import envelope as E
from quant_mcp.server import _REGISTRY, BearerAuthMiddleware, build_http_app, build_server
from test_api import install_v3_stubs  # hermetic V3 stubs shared with the REST tests

ORCH = None  # FakeOrchestrator of the running test (set by the `env` fixture)
MAX = 8192
V3_TOOLS = [
    "quant_glossary_lookup", "quant_formal_verify", "quant_regime_analysis", "quant_capacity_analysis",
    "quant_dispatch_research", "quant_get_research_study", "quant_list_research_studies",
    "quant_publish_research_finding",
]
# tool / path words that must never appear (read-only research surface)
FORBIDDEN = re.compile(r"order|broker|execut|trade|position|account|buy|sell|ctp|qmt|place", re.IGNORECASE)

# Minimal valid arguments per tool.
CALLS: dict[str, dict] = {
    "quant_data_health": {},
    "quant_query_pit_panel": {"universe_id": "CSI500", "preview_limit": 500},
    "quant_universe_constituents": {"universe_id": "CSI500", "include_symbols": True},
    "quant_eval_factor": {"factor_id": "alpha_vp_corr_20d"},
    "quant_factor_correlation": {"factor_ids": ["alpha_vp_corr_20d", "alpha_mom_resid_60d"]},
    "quant_submit_factor_hypothesis": {"factor_id": "mcp_f1", "hypothesis": "h", "expression": "-close.pct_change(5)"},
    "quant_run_cpcv_gate": {"experiment_id": "EXP-0427", "trial_count_m": 8},
    "quant_list_experiments": {"limit": 20},
    "quant_barra_exposure": {"experiment_id": "EXP-0427"},
    "quant_optimize_portfolio": {"experiment_id": "EXP-0427", "constraints": {"max_stock_weight": 0.03}},
    "quant_run_backtest": {"experiment_id": "EXP-0427"},
    "quant_attribution_tearsheet": {},  # filled after the backtest ran
    "quant_shadow_drift_check": {},
    # --- V3.0 ---
    "quant_glossary_lookup": {"query_or_term_id": "rank_ic", "context_values": {"rank_ic_mean": 0.031}},
    "quant_formal_verify": {"factor_id": "alpha_vp_corr_20d"},
    "quant_regime_analysis": {"factor_id": "alpha_vp_corr_20d"},
    "quant_capacity_analysis": {"experiment_id": "EXP-0427"},
    "quant_dispatch_research": {"title": "VP corr v3", "hypothesis": "volume-price decoupling",
                                "expression": "-rolling_corr(ret, dvol, 30)", "factor_id": "vp30", "async_mode": False},
    "quant_get_research_study": {},  # filled after the dispatch ran
    "quant_list_research_studies": {"view": "studies"},
    "quant_publish_research_finding": {"candidate_expression": "-close.pct_change(7)", "study_id": None,
                                       "worker_claimed_metrics": {"rank_ic_mean": 0.9}},
}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("QUANT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("QUANT_DATA_ROOT", str(tmp_path / "quant-lake"))
    store = jobs.StateStore(tmp_path / "state" / "api.db")
    jobs.set_store(store)
    mgr = jobs.JobManager(store)
    jobs.set_manager(mgr)
    deps.set_overrides(lake=fakes.FakeLake(tmp_path), engines=fakes.make_engines())
    global ORCH
    ORCH = install_v3_stubs(monkeypatch)
    yield tmp_path
    mgr.shutdown()
    deps.reset()
    jobs.set_store(None)
    jobs.set_manager(None)


def call(name: str, args: dict | None = None) -> tuple[str, McpEnvelope]:
    async def go():
        async with Client(build_server()) as c:
            return await c.call_tool(name, args or {})

    res = anyio.run(go)
    assert len(res.content) == 1, "exactly one text block (no duplicated structured payload)"
    assert res.structured_content is None
    text = res.content[0].text
    return text, McpEnvelope.model_validate_json(text)


# ---------------------------------------------------------------------------


def test_exactly_21_quant_tools_registered_with_schemas(env):
    async def go():
        async with Client(build_server()) as c:
            return (await c.list_tools()).tools

    tools = anyio.run(go)
    names = [t.name for t in tools]
    assert sorted(names) == sorted(MCP_TOOL_NAMES) and len(names) == 21 == len(set(names))
    assert all(n.startswith("quant_") for n in names)
    for t in tools:
        assert t.input_schema["type"] == "object" and t.description
    assert not [n for n in names if FORBIDDEN.search(n)]
    # no forbidden words in any argument name either
    assert not [(t.name, k) for t in tools for k in t.input_schema.get("properties", {}) if FORBIDDEN.search(k)]
    assert [n for n, _, _ in _REGISTRY] == MCP_TOOL_NAMES  # registry / catalog 100% in sync, same order
    assert MCP_TOOL_NAMES[13:] == V3_TOOLS
    props = {t.name: t.input_schema["properties"] for t in tools}
    assert {"query_or_term_id", "category", "context_values"} <= set(props["quant_glossary_lookup"])
    assert {"factor_id", "expression", "experiment_id", "universe_id", "trial_count_m", "bootstrap_b", "snapshot_id",
            "async_mode", "job_id"} <= set(props["quant_formal_verify"])
    assert {"title", "hypothesis", "kind", "parent_study_id", "dispatch_mode", "target_cluster", "math_derivation",
            "allow_duplicate", "async_mode", "job_id"} <= set(props["quant_dispatch_research"])
    assert {"candidate_expression", "worker_cluster_id", "worker_claimed_metrics", "notes"} <= set(
        props["quant_publish_research_finding"])


def test_all_tools_return_valid_envelope_within_8kib(env):
    # run the backtest first (job id needed by the tearsheet)
    text, bt = call("quant_run_backtest", CALLS["quant_run_backtest"])
    assert bt.status in {"ok", "accepted"} and len(text.encode()) <= MAX
    if bt.status == "accepted":
        for _ in range(100):
            _, bt = call("quant_run_backtest", {"job_id": bt.job_id})
            if bt.status != "accepted":
                break
            time.sleep(0.1)
    assert bt.status == "ok" and bt.summary["run_id"] == "BT-TEST-1"
    CALLS["quant_attribution_tearsheet"] = {"run_id": "BT-TEST-1"}
    # V3 research tools need a study id: dispatch one synchronously, then read it back
    _, disp = call("quant_dispatch_research", CALLS["quant_dispatch_research"])
    assert disp.status == "ok" and disp.summary["verdict"] == "VERIFIED", disp
    CALLS["quant_get_research_study"] = {"study_id": disp.summary["study_id"]}
    CALLS["quant_publish_research_finding"]["study_id"] = disp.summary["study_id"]

    sizes: dict[str, int] = {}
    for name in MCP_TOOL_NAMES:
        text, envl = call(name, CALLS[name])
        size = len(text.encode("utf-8"))
        sizes[name] = size
        assert size <= MAX, f"{name} response is {size} bytes"
        assert envl.schema_version == "2.0" and envl.request_id.startswith("req_")
        assert envl.status in {"ok", "degraded", "accepted"}, f"{name}: {text[:300]}"
        assert envl.error is None
        if name not in {"quant_list_experiments", "quant_glossary_lookup"}:  # glossary needs no snapshot
            assert envl.snapshot_id == fakes.SNAP_ID, name
            assert envl.provenance and envl.provenance.snapshot_id == fakes.SNAP_ID
        assert json.loads(text)  # strictly valid JSON
    assert max(sizes.values()) <= MAX and len(sizes) == 21


def test_heavy_results_go_to_artifacts(env):
    for name in ["quant_eval_factor", "quant_barra_exposure", "quant_shadow_drift_check", "quant_data_health"]:
        _, envl = call(name, CALLS[name])
        assert envl.artifact is not None and envl.artifact.uri.startswith("quant-artifact://"), name
        assert len(envl.artifact.sha256) == 64


def test_pit_panel_preview_is_capped_at_20_rows(env):
    _, envl = call("quant_query_pit_panel", {"preview_limit": 1000})
    assert envl.preview is not None and len(envl.preview) <= 20
    assert envl.summary["rows"] > 20 and envl.artifact.format == "parquet"
    assert {"count", "null_pct", "p05", "p50", "p95"} <= set(envl.summary["stats"]["close"])
    _, small = call("quant_query_pit_panel", {"preview_limit": 3, "symbols": [fakes.SYMBOLS[0]]})
    assert len(small.preview) == 3 and {r["symbol"] for r in small.preview} == {fakes.SYMBOLS[0]}


@pytest.mark.parametrize(
    "name,args",
    [
        ("quant_run_backtest", {"experiment_id": "EXP-0427"}),
        ("quant_run_cpcv_gate", {"experiment_id": "EXP-0427", "trial_count_m": 8}),
        ("quant_submit_factor_hypothesis", {"factor_id": "af", "hypothesis": "h", "expression": "-close.pct_change(7)"}),
    ],
)
def test_long_tools_async_mode_accepts_fast_then_completes(env, name, args):
    t0 = time.perf_counter()
    text, envl = call(name, {**args, "async_mode": True})
    assert time.perf_counter() - t0 < 1.0, "async accept must be < 1s"
    assert envl.status == "accepted" and envl.job_id and envl.job_state in {"queued", "running", "completed"}
    assert len(text.encode()) <= MAX
    for _ in range(200):
        _, polled = call(name, {**args, "job_id": envl.job_id})
        if polled.status != "accepted":
            break
        time.sleep(0.1)
    assert polled.status == "ok" and polled.job_state == "completed" and polled.job_id == envl.job_id


def test_attribution_via_job_id_and_not_found(env):
    _, acc = call("quant_run_backtest", {"experiment_id": "EXP-0427", "async_mode": True})
    for _ in range(200):
        _, done = call("quant_run_backtest", {"job_id": acc.job_id})
        if done.status != "accepted":
            break
        time.sleep(0.1)
    _, att = call("quant_attribution_tearsheet", {"job_id": acc.job_id})
    assert att.status == "ok" and "attribution" in att.summary and "friction" in att.summary
    _, missing = call("quant_attribution_tearsheet", {"run_id": "NOPE"})
    assert missing.status == "error" and missing.error.code == McpErrorCode.NOT_FOUND
    _, bad = call("quant_attribution_tearsheet", {})
    assert bad.error.code == McpErrorCode.INVALID_ARGUMENT


def test_errors_are_enveloped_not_raised(env):
    _, e1 = call("quant_eval_factor", {"factor_id": "x", "expression": "close.shift(-5)"})
    assert e1.status == "error" and e1.error.code == McpErrorCode.INVALID_ARGUMENT
    _, e2 = call("quant_run_cpcv_gate", {"experiment_id": "EXP-NOPE", "async_mode": False})
    assert e2.status == "error"
    _, e3 = call("quant_optimize_portfolio", {"constraints": {"bogus": 1}})
    assert e3.error.code == McpErrorCode.INVALID_ARGUMENT
    _, e4 = call("quant_universe_constituents", {"universe_id": "NOPE"})
    assert e4.error.code == McpErrorCode.NOT_FOUND
    _, e5 = call("quant_run_backtest", {"engine": "nonsense"})
    assert e5.error.code == McpErrorCode.INVALID_ARGUMENT


def test_no_data_returns_data_stale_with_make_seed_hint(tmp_path, monkeypatch):
    monkeypatch.setenv("QUANT_STATE_DIR", str(tmp_path / "state"))
    jobs.set_store(jobs.StateStore(tmp_path / "state" / "api.db"))
    deps.set_overrides(lake=fakes.FakeLake(tmp_path, with_snapshot=False), engines=fakes.make_engines())
    try:
        for name in ("quant_data_health", "quant_eval_factor", "quant_shadow_drift_check"):
            _, envl = call(name, CALLS[name])
            assert envl.status == "error" and envl.error.code == McpErrorCode.DATA_STALE
            assert "make seed" in envl.error.message
    finally:
        deps.reset()
        jobs.set_store(None)


# --- V3.0 tools -------------------------------------------------------------------------------------


def test_v3_glossary_lookup_by_id_keyword_and_without_snapshot(env, tmp_path):
    _, by_id = call("quant_glossary_lookup", {"query_or_term_id": "rank_ic", "context_values": {"rank_ic_mean": 0.031}})
    assert by_id.status == "ok" and by_id.summary["term_id"] == "rank_ic" and by_id.artifact is not None
    assert "0.031" in "".join(by_id.summary["bot_prompt"])  # context rendered into the Bot prompt
    assert by_id.summary["institutional_threshold"].startswith(">= 0.03")
    _, kw = call("quant_glossary_lookup", {"query_or_term_id": "deflated", "category": "validation"})
    assert kw.summary["term_id"] == "dsr"
    _, alias = call("quant_glossary_lookup", {"query_or_term_id": "秩相关"})
    assert alias.summary["term_id"] == "rank_ic" and alias.summary["other_matches"] == []
    _, miss = call("quant_glossary_lookup", {"query_or_term_id": "zzz-nothing"})
    assert miss.status == "error" and miss.error.code == McpErrorCode.NOT_FOUND
    _, empty = call("quant_glossary_lookup", {"query_or_term_id": "  "})
    assert empty.error.code == McpErrorCode.INVALID_ARGUMENT
    # works on an empty data lake (no snapshot dependency)
    deps.set_overrides(lake=fakes.FakeLake(tmp_path, with_snapshot=False), engines=fakes.make_engines())
    _, offline = call("quant_glossary_lookup", {"query_or_term_id": "dsr"})
    assert offline.status == "ok" and offline.snapshot_id is None


def test_v3_formal_verify_sync_async_and_failed_proof(env):
    t0 = time.perf_counter()
    text, acc = call("quant_formal_verify", {"factor_id": "alpha_mom_resid_60d", "async_mode": True})
    assert time.perf_counter() - t0 < 1.0 and acc.status == "accepted" and acc.job_id
    for _ in range(200):
        _, done = call("quant_formal_verify", {"factor_id": "alpha_mom_resid_60d", "job_id": acc.job_id})
        if done.status != "accepted":
            break
        time.sleep(0.1)
    assert done.status == "ok" and done.summary["passed"] is True and len(done.summary["checks"]) == 8
    assert done.artifact is not None and done.summary["proof_sha256"]
    _, sync = call("quant_formal_verify", {"factor_id": "peek", "expression": "close.shift(-3)"})
    assert sync.status == "ok" and sync.summary["passed"] is False
    assert sync.summary["blocking_failures"] == ["T1.PREFIX_INVARIANCE"]
    _, nf = call("quant_formal_verify", {"factor_id": "no_such_factor"})
    assert nf.status == "error" and nf.error.code == McpErrorCode.NOT_FOUND
    _, via_exp = call("quant_formal_verify", {"experiment_id": "EXP-0427"})  # factor resolved from the experiment
    assert via_exp.status == "ok"


def test_v3_regime_and_capacity_summaries(env):
    _, ok = call("quant_regime_analysis", {"factor_id": "alpha_vp_corr_20d"})
    assert ok.status == "ok" and ok.summary["single_dim_dominance_flag"] is False and ok.preview
    _, dom = call("quant_regime_analysis", {"factor_id": "alpha_mom_resid_60d", "dimensions": ["market_regime"]})
    assert dom.status == "degraded" and dom.summary["dominant_bucket"] == "BULL"
    assert dom.summary["warning_summary"] == "DIMENSION_DOMINANCE" and dom.artifact is not None
    _, cap = call("quant_capacity_analysis", {"experiment_id": "EXP-0427", "aum_grid_cny": [1e7, 1e8, 1e9]})
    assert cap.status == "ok" and len(cap.preview) == 3 and cap.summary["half_alpha_aum_cny"] == 5e8
    assert "uncalibrated" in cap.summary["note"]
    _, nf = call("quant_capacity_analysis", {"experiment_id": "EXP-NOPE"})
    assert nf.error.code == McpErrorCode.NOT_FOUND
    _, bad = call("quant_regime_analysis", {"factor_id": "alpha_vp_corr_20d", "dimensions": ["not_a_dim"]})
    assert bad.status == "ok" or bad.error is not None  # engine decides; never an unhandled exception


def test_v3_dispatch_async_accept_then_poll_study(env):
    args = {"title": "async study", "hypothesis": "h", "expression": "-rolling_corr(ret, dvol, 45)", "factor_id": "vp45"}
    t0 = time.perf_counter()
    text, acc = call("quant_dispatch_research", args)  # async_mode defaults to True
    assert time.perf_counter() - t0 < 1.0 and len(text.encode()) <= MAX
    assert acc.status == "accepted" and acc.job_state in {"queued", "running"} and acc.summary["study_id"]
    sid = acc.summary["study_id"]
    assert acc.job_id and sid in acc.summary["message"]
    for _ in range(200):
        _, st = call("quant_get_research_study", {"study_id": sid, "include_events_tail": 3})
        if st.summary["status"] in {"COMPLETED", "FALSIFIED", "FAILED"}:
            break
        time.sleep(0.1)
    assert st.status == "ok" and st.summary["verdict"] == "VERIFIED" and st.summary["proofs"] == "8/8"
    assert len(st.summary["events_tail"]) == 3 and st.artifact is not None
    _, none_tail = call("quant_get_research_study", {"study_id": sid, "include_events_tail": 0})
    assert none_tail.summary["events_tail"] == []
    _, nf = call("quant_get_research_study", {"study_id": "NOPE"})
    assert nf.status == "error" and nf.error.code == McpErrorCode.NOT_FOUND
    # the orchestrator owns its job ids: a study id works as the poll handle via `job_id`
    _, by_handle = call("quant_dispatch_research", {"job_id": sid})
    assert by_handle.summary["study_id"] == sid and by_handle.status == "ok"
    _, no_handle = call("quant_dispatch_research", {"job_id": "job_unknown"})
    assert no_handle.status == "error" and no_handle.error.code == McpErrorCode.NOT_FOUND
    _, again = call("quant_dispatch_research", {**args, "expression": "-rolling_corr(ret, dvol, 46)"})
    assert again.status == "accepted"


def test_v3_dispatch_sync_tombstone_block_and_override(env):
    _, listing = call("quant_list_research_studies", {"view": "studies"})  # seeds the golden ledger incl. one tombstone
    assert listing.summary["total"] == 3
    base = {"title": "peek again", "hypothesis": "h", "expression": "close.shift(-3)", "async_mode": False}
    _, blocked = call("quant_dispatch_research", base)
    assert blocked.status == "error" and blocked.error.code == McpErrorCode.DUPLICATE_TOMBSTONE
    assert blocked.summary["tombstone_warnings"]
    _, forced = call("quant_dispatch_research", {**base, "allow_duplicate": True})
    assert forced.status == "ok" and forced.summary["verdict"] == "FALSIFIED"  # proof failed -> falsified
    assert forced.summary["blocking_failures"] == ["T1.PREFIX_INVARIANCE"]
    _, bad_kind = call("quant_dispatch_research", {**base, "kind": "bogus_kind"})
    assert bad_kind.error.code == McpErrorCode.INVALID_ARGUMENT
    _, no_expr = call("quant_dispatch_research", {"title": "x"})
    assert no_expr.error.code == McpErrorCode.INVALID_ARGUMENT
    _, deriv = call("quant_dispatch_research", {
        "title": "with math", "expression": "-rolling_corr(ret, dvol, 50)", "async_mode": False,
        "math_derivation": {"economic_hypothesis": "decoupling signals informed flow"}})
    assert deriv.status == "ok"
    _, bad_deriv = call("quant_dispatch_research", {
        "title": "bad math", "expression": "-rolling_corr(ret, dvol, 51)", "math_derivation": {"nope": 1}})
    assert bad_deriv.error.code == McpErrorCode.INVALID_ARGUMENT


def test_v3_list_views_and_8kib_guard(env):
    call("quant_list_research_studies", {"view": "studies"})  # seeds the golden ledger first
    for i in range(60):  # many studies with long titles must still fit the envelope budget
        ORCH._new("T" * 300 + f" {i}", "COMPLETED", "VERIFIED", expr=f"-close.pct_change({i + 2})")
    text, big = call("quant_list_research_studies", {"view": "studies", "limit": 100})
    assert len(text.encode()) <= MAX and big.status == "ok" and big.summary["total"] >= 60
    assert len(big.preview or []) <= 20
    _, lin = call("quant_list_research_studies", {"view": "lineage", "parent_study_id": "STUDY-GOLD-ROOT"})
    assert lin.summary["view"] == "lineage" and lin.summary["total"] == 1
    _, vd = call("quant_list_research_studies", {"view": "studies", "verdict": "FALSIFIED"})
    assert vd.summary["total"] == 1
    _, tomb = call("quant_list_research_studies", {"view": "tombstones", "query": "peek"})
    assert tomb.summary["count"] == 1 and tomb.preview[0]["code"] == "LOOKAHEAD_PREFIX_VIOLATION"
    _, bl0 = call("quant_list_research_studies", {"view": "baselines"})
    assert bl0.status == "ok" and bl0.summary["count"] == 0
    _, cl = call("quant_list_research_studies", {"view": "clusters"})
    assert cl.preview[0]["id"] == "engine_sandbox"
    _, bad = call("quant_list_research_studies", {"view": "orders"})
    assert bad.status == "error" and bad.error.code == McpErrorCode.INVALID_ARGUMENT


def test_v3_publish_is_zero_trust_and_opens_study_when_missing(env):
    claimed = {"rank_ic_mean": 0.99, "icir_ann": 9.9}
    _, pub = call("quant_publish_research_finding", {
        "candidate_expression": "close.shift(-3)", "worker_cluster_id": "ext-1", "worker_claimed_metrics": claimed,
        "title": "too good to be true"})  # no study_id -> an external_publish study is opened first
    assert pub.status == "ok" and pub.summary["verdict"] == "FALSIFIED" and pub.summary["formal_passed"] is False
    assert pub.summary["claimed_metrics"] == claimed and "server-side" in pub.summary["zero_trust"]
    study_id, req = ORCH.publish_calls[-1]
    assert req.worker_claimed_metrics == claimed and req.snapshot_id == fakes.SNAP_ID
    assert ORCH.studies[study_id].dispatch_mode == "external_publish" and ORCH.studies[study_id].target_cluster == "ext-1"

    _, good = call("quant_publish_research_finding", {
        "candidate_expression": "-close.pct_change(7)", "study_id": study_id,
        "math_derivation": {"economic_hypothesis": "short-term reversal"}})
    assert good.summary["verdict"] == "VERIFIED" and good.summary["study_id"] == study_id
    _, nf = call("quant_publish_research_finding", {"candidate_expression": "x", "study_id": "NOPE"})
    assert nf.error.code == McpErrorCode.NOT_FOUND


def test_v3_publish_proof_failed_error_code(env, monkeypatch):
    _, d = call("quant_dispatch_research", {"title": "t", "expression": "-close.pct_change(5)", "async_mode": False})
    sid = d.summary["study_id"]
    monkeypatch.setattr(ORCH, "publish_finding", lambda study_id, req: ORCH.studies[study_id].model_copy(
        update={"verdict": "VERIFIED", "formal_proof": None}))
    _, e = call("quant_publish_research_finding", {"candidate_expression": "-close.pct_change(9)", "study_id": sid})
    assert e.status == "error" and e.error.code == McpErrorCode.PROOF_FAILED


def test_v3_orchestrator_missing_is_clean_error_envelope(env, monkeypatch):
    monkeypatch.setitem(sys.modules, "quant_api.orchestrator", None)
    monkeypatch.setitem(sys.modules, "quant_core.validation.formal", None)
    _, e = call("quant_list_research_studies", {})
    assert e.status == "error" and e.error.code == McpErrorCode.CLUSTER_UNAVAILABLE
    _, f = call("quant_formal_verify", {"factor_id": "alpha_vp_corr_20d"})
    assert f.status == "error" and "not importable" in f.error.message
    _, ok = call("quant_data_health")  # V2.0 tools keep working
    assert ok.status == "ok"


def test_v3_tools_have_no_forbidden_words_in_any_response(env):
    _, d = call("quant_dispatch_research", CALLS["quant_dispatch_research"])
    sid = d.summary["study_id"]
    for name, args in [
        ("quant_glossary_lookup", {"query_or_term_id": "rank_ic"}), ("quant_formal_verify", {"factor_id": "alpha_vp_corr_20d"}),
        ("quant_regime_analysis", {}), ("quant_capacity_analysis", {}), ("quant_get_research_study", {"study_id": sid}),
        ("quant_list_research_studies", {"view": "clusters"}),
    ]:
        text, _ = call(name, args)
        assert len(text.encode()) <= MAX
        assert not re.search(r"broker_connected\"\s*:\s*true", text, re.IGNORECASE), name


def test_tool_call_stats_feed_api_catalog(env):
    from quant_api import service

    call("quant_data_health")
    call("quant_data_health")
    call("quant_eval_factor", {"factor_id": "x", "expression": "close.shift(-5)"})
    tools = {t["name"]: t for t in service.mcp_catalog()["tools"]}
    assert tools["quant_data_health"]["calls"] == 2 and tools["quant_data_health"]["errors"] == 0
    assert tools["quant_eval_factor"]["calls"] == 1 and tools["quant_eval_factor"]["errors"] == 1
    # V3.0 tools are recorded in `tool_calls` too (ok + error)
    call("quant_glossary_lookup", {"query_or_term_id": "dsr"})
    call("quant_glossary_lookup", {"query_or_term_id": "zzz-nothing"})
    call("quant_list_research_studies", {"view": "clusters"})
    tools = {t["name"]: t for t in service.mcp_catalog()["tools"]}
    assert len(tools) == 21
    assert tools["quant_glossary_lookup"]["calls"] == 2 and tools["quant_glossary_lookup"]["errors"] == 1
    assert tools["quant_list_research_studies"]["calls"] == 1 and tools["quant_list_research_studies"]["avg_ms"] is not None


# --- envelope size guard ------------------------------------------------------------------------


def test_fit_enforces_budget_for_oversized_payloads(env):
    huge = {"rows": [{"k": "v" * 200, "i": i} for i in range(5000)], "text": "x" * 500_000,
            **{f"key{i}": "y" * 200 for i in range(200)}}
    for n_preview in (0, 5, 20, 200):
        envl = E.ok_envelope("req_t", snapshot_id="s", as_of="2026-01-01", summary=huge,
                             preview=[{"a": "b" * 500, "i": i} for i in range(n_preview)] or None)
        text = E.to_text(envl, huge)
        assert len(text.encode()) <= MAX
        back = McpEnvelope.model_validate_json(text)
        assert back.artifact is not None, "oversized payload must be spilled to an artifact"


def test_compact_rounds_and_truncates():
    out = E.compact({"a": 1.123456789, "b": list(range(100)), "c": "z" * 1000, "d": float("nan")})
    assert out["a"] == 1.123457 and len(out["b"]) == 21 and len(out["c"]) <= 240 and out["d"] is None


# --- transports / auth ---------------------------------------------------------------------------


def test_bearer_middleware_rejects_missing_or_wrong_token():
    import httpx

    async def go():
        async def inner(scope, receive, send):  # stand-in app: 200 for anything that passes the guard
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        app = BearerAuthMiddleware(inner, "s3cret")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            assert (await c.post("/mcp")).status_code == 401
            assert (await c.post("/mcp", headers={"Authorization": "Bearer nope"})).status_code == 401
            assert (await c.post("/mcp", headers={"Authorization": "Basic s3cret"})).status_code == 401
            ok = await c.post("/mcp", headers={"Authorization": "Bearer s3cret"})
            assert ok.status_code == 200
            assert "www-authenticate" in (await c.post("/mcp")).headers

    anyio.run(go)


def test_http_app_wraps_only_when_token_configured(monkeypatch):
    monkeypatch.delenv("QUANT_MCP_BEARER_TOKEN", raising=False)
    assert not isinstance(build_http_app(build_server()), BearerAuthMiddleware)
    monkeypatch.setenv("QUANT_MCP_BEARER_TOKEN", "tok")
    assert isinstance(build_http_app(build_server()), BearerAuthMiddleware)


def test_stdio_transport_lists_21_tools(tmp_path):
    from mcp.client.stdio import StdioServerParameters

    paths = [ROOT / "packages/quant-core/src", ROOT / "services/quant-api/src", ROOT / "services/quant-mcp/src"]
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "quant_mcp.server", "--transport", "stdio"],
        env={**os.environ, "PYTHONPATH": os.pathsep.join(map(str, paths)),
             "QUANT_STATE_DIR": str(tmp_path / "state"), "QUANT_DATA_ROOT": str(tmp_path / "quant-lake")},
    )

    async def go():
        with anyio.fail_after(60):
            async with Client(params) as c:
                tools = (await c.list_tools()).tools
                res = await c.call_tool("quant_data_health", {})
                return [t.name for t in tools], res.content[0].text

    names, text = anyio.run(go)
    assert sorted(names) == sorted(MCP_TOOL_NAMES)
    envl = McpEnvelope.model_validate_json(text)  # empty lake -> clean DATA_STALE envelope, not a crash
    assert envl.status == "error" and envl.error.code == McpErrorCode.DATA_STALE


def test_streamable_http_server_boots_and_serves_tools(tmp_path):
    """Real subprocess on an ephemeral port with Bearer auth: 401 without token, tools/list with token."""
    import socket

    import httpx

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    paths = [ROOT / "packages/quant-core/src", ROOT / "services/quant-api/src", ROOT / "services/quant-mcp/src"]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(map(str, paths)), "QUANT_MCP_BEARER_TOKEN": "tok-123",
           "QUANT_STATE_DIR": str(tmp_path / "state"), "QUANT_DATA_ROOT": str(tmp_path / "quant-lake")}
    proc = subprocess.Popen([sys.executable, "-m", "quant_mcp.server", "--transport", "streamable-http",
                             "--host", "127.0.0.1", "--port", str(port)], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        url = f"http://127.0.0.1:{port}/mcp"
        for _ in range(100):
            try:
                r = httpx.post(url, json={}, timeout=1)
                break
            except httpx.TransportError:
                time.sleep(0.2)
        else:
            raise AssertionError("server did not start")
        assert r.status_code == 401

        async def go():
            import httpx2  # the MCP SDK's own HTTP client flavour
            from mcp.client.streamable_http import streamable_http_client

            async with httpx2.AsyncClient(headers={"Authorization": "Bearer tok-123"}) as http:
                async with Client(streamable_http_client(url, http_client=http)) as c:
                    return [t.name for t in (await c.list_tools()).tools]

        names = anyio.run(go)
        assert sorted(names) == sorted(MCP_TOOL_NAMES)
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_register_agent_home_dry_run_exits_zero():
    r = subprocess.run(["node", str(ROOT / "scripts" / "register_agent_home.mjs"), "--dry-run"],
                       capture_output=True, text=True, timeout=60, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    assert "RAW_AGENT_MCP_EXPAND_HTTP=true" in r.stdout and "RAW_AGENT_MCP_HTTP=" in r.stdout
    assert "quant-lead" in r.stdout and "SKILL.md" in r.stdout
    assert (ROOT / "services/quant-mcp/skills/quant-lead/SKILL.md").exists()
    j = subprocess.run(["node", str(ROOT / "scripts" / "register_agent_home.mjs"), "--dry-run", "--json"],
                       capture_output=True, text=True, timeout=60, cwd=ROOT)
    report = json.loads(j.stdout)
    assert report["mode"] == "dry-run" and report["skillExists"] is True
    entry = json.loads(report["env"]["RAW_AGENT_MCP_HTTP"])[0]
    assert entry == {"url": "http://127.0.0.1:8101/mcp", "transport": "streamable-http", "bearerEnv": "QUANT_MCP_BEARER_TOKEN"}
    desc = report["plannedRequests"][1]["body"]["description"]
    assert len(desc) <= 2000
    assert all(t in desc for t in V3_TOOLS), "Bot description must advertise every V3 tool"
    assert "INSTITUTIONAL_GATES" in desc and "零荐股" in desc
    assert "/Users/" not in desc and not re.search(r"(?<![\w.])/(home|root|var|tmp)/", desc)  # no absolute local paths
    assert "services/quant-mcp/skills/quant-lead/SKILL.md" in desc


def test_register_agent_home_source_has_no_personal_paths_or_keys():
    src = (ROOT / "scripts" / "register_agent_home.mjs").read_text(encoding="utf-8")
    assert "/Users/" not in src and not re.search(r"/home/\w+", src)
    assert not re.search(r"\b(?!127\.0\.0\.1\b|0\.0\.0\.0\b)\d{1,3}(\.\d{1,3}){3}\b", src)
    assert not re.search(r"sk-[A-Za-z0-9]{16,}", src)


def test_skill_md_documents_v3_gates_and_workflows():
    skill = (ROOT / "services/quant-mcp/skills/quant-lead/SKILL.md").read_text(encoding="utf-8")
    for heading in ("专业术语与实时指标深度解读", "数学推演与形式化证明铁律", "开发集群调度研究与零信任复算写回",
                    "研究血缘树与墓碑库查重", "INSTITUTIONAL_GATES"):
        assert heading in skill, heading
    for tool in MCP_TOOL_NAMES[13:]:
        assert tool in skill, tool
    from quant_core.schemas import INSTITUTIONAL_GATES as G

    assert f"{G['rank_ic_min']:.2f}" in skill and f"{G['icir_ann_min']:.2f}" in skill and f"{G['nw_t_stat_min']:.2f}" in skill
    assert "/Users/" not in skill and not re.search(r"/home/\w+", skill) and ".ts.net" not in skill
