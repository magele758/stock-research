"""MCP contract tests: 13 tools, McpEnvelope validity, strict <= 8,192-byte responses, async fast-accept,
Bearer guard, stdio transport and the Agent Home registration dry run."""

from __future__ import annotations

import json
import os
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
from quant_mcp.server import BearerAuthMiddleware, build_http_app, build_server

MAX = 8192

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


def test_exactly_13_quant_tools_registered_with_schemas(env):
    async def go():
        async with Client(build_server()) as c:
            return (await c.list_tools()).tools

    tools = anyio.run(go)
    names = [t.name for t in tools]
    assert sorted(names) == sorted(MCP_TOOL_NAMES) and len(names) == 13
    assert all(n.startswith("quant_") for n in names)
    for t in tools:
        assert t.input_schema["type"] == "object" and t.description
    bad = ("order", "broker", "trade", "buy", "sell", "place")
    assert not [n for n in names if any(b in n.replace("backtest", "") for b in bad)]


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

    sizes: dict[str, int] = {}
    for name in MCP_TOOL_NAMES:
        text, envl = call(name, CALLS[name])
        size = len(text.encode("utf-8"))
        sizes[name] = size
        assert size <= MAX, f"{name} response is {size} bytes"
        assert envl.schema_version == "2.0" and envl.request_id.startswith("req_")
        assert envl.status in {"ok", "degraded", "accepted"}, f"{name}: {text[:300]}"
        assert envl.error is None
        if name not in {"quant_list_experiments"}:
            assert envl.snapshot_id == fakes.SNAP_ID, name
            assert envl.provenance and envl.provenance.snapshot_id == fakes.SNAP_ID
        assert json.loads(text)  # strictly valid JSON
    assert max(sizes.values()) <= MAX


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


def test_tool_call_stats_feed_api_catalog(env):
    from quant_api import service

    call("quant_data_health")
    call("quant_data_health")
    call("quant_eval_factor", {"factor_id": "x", "expression": "close.shift(-5)"})
    tools = {t["name"]: t for t in service.mcp_catalog()["tools"]}
    assert tools["quant_data_health"]["calls"] == 2 and tools["quant_data_health"]["errors"] == 0
    assert tools["quant_eval_factor"]["calls"] == 1 and tools["quant_eval_factor"]["errors"] == 1


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


def test_stdio_transport_lists_13_tools(tmp_path):
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
    assert len(report["plannedRequests"][1]["body"]["description"]) <= 2000
