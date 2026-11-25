"""REST contract tests for services/quant-api (hermetic: FakeLake + engine stubs; real lake for the no-data case)."""

from __future__ import annotations

import re
import time
from pathlib import Path

import fakes
import pytest
from fastapi.testclient import TestClient
from quant_api import deps, jobs
from quant_api.catalog import MCP_TOOL_NAMES
from quant_api.main import app

EXPECTED_ROUTES = {
    ("GET", "/api/v1/health"),
    ("GET", "/api/v1/data/health"),
    ("GET", "/api/v1/data/snapshots"),
    ("GET", "/api/v1/data/universe/{universe_id}"),
    ("POST", "/api/v1/data/sync"),
    ("GET", "/api/v1/factors/catalog"),
    ("POST", "/api/v1/factors/evaluate"),
    ("POST", "/api/v1/factors/correlation"),
    ("POST", "/api/v1/factors/hypothesis"),
    ("GET", "/api/v1/experiments"),
    ("POST", "/api/v1/validation/cpcv"),
    ("POST", "/api/v1/risk/barra-exposure"),
    ("POST", "/api/v1/portfolio/optimize"),
    ("POST", "/api/v1/backtest/run"),
    ("GET", "/api/v1/shadow/drift"),
    ("GET", "/api/v1/jobs/{job_id}"),
    ("GET", "/api/v1/mcp/catalog"),
}


@pytest.fixture()
def env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("QUANT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("QUANT_DATA_ROOT", str(tmp_path / "quant-lake"))
    store = jobs.StateStore(tmp_path / "state" / "api.db")
    jobs.set_store(store)
    mgr = jobs.JobManager(store, max_workers=2)
    jobs.set_manager(mgr)
    lake = fakes.FakeLake(tmp_path)
    deps.set_overrides(lake=lake, engines=fakes.make_engines())
    yield lake
    mgr.shutdown()
    deps.reset()
    jobs.set_store(None)
    jobs.set_manager(None)


@pytest.fixture()
def client(env):
    with TestClient(app) as c:
        yield c


def wait_job(client: TestClient, job_id: str, timeout: float = 30.0) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f"/api/v1/jobs/{job_id}").json()
        if j["status"] in {"completed", "failed", "cancelled"}:
            return j
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


# ---------------------------------------------------------------------------
# OpenAPI / safety
# ---------------------------------------------------------------------------


def test_openapi_has_all_contract_routes_and_job_events(client):
    spec = client.get("/openapi.json").json()
    got = {(m.upper(), p) for p, ops in spec["paths"].items() for m in ops}
    assert EXPECTED_ROUTES <= got
    assert ("GET", "/api/v1/jobs/{job_id}/events") in got
    assert len(EXPECTED_ROUTES) == 17


def test_no_broker_or_order_routes(client):
    spec = client.get("/openapi.json").json()
    bad = re.compile(r"order|broker|execut|trade|position|account|buy|sell|ctp|qmt", re.IGNORECASE)
    for path, ops in spec["paths"].items():
        assert not bad.search(path), path
        assert set(ops) <= {"get", "post"}, f"unexpected methods on {path}: {set(ops)}"
        assert path.startswith("/api/v1/")
    for r in app.routes:  # FastAPI-level as well (incl. any undocumented route)
        p = getattr(r, "path", "")
        assert not bad.search(p.replace("/backtest", "")), p


def test_cors_allows_web_origin_only(client):
    ok = client.options(
        "/api/v1/health",
        headers={"Origin": "http://127.0.0.1:3100", "Access-Control-Request-Method": "GET"},
    )
    assert ok.headers.get("access-control-allow-origin") == "http://127.0.0.1:3100"
    ok2 = client.options(
        "/api/v1/health", headers={"Origin": "http://localhost:3100", "Access-Control-Request-Method": "GET"}
    )
    assert ok2.headers.get("access-control-allow-origin") == "http://localhost:3100"
    bad = client.options(
        "/api/v1/health", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"}
    )
    assert "access-control-allow-origin" not in bad.headers


# ---------------------------------------------------------------------------
# No synthetic market data when the lake is empty
# ---------------------------------------------------------------------------


def test_degraded_without_snapshot_never_fabricates(tmp_path, monkeypatch):
    pytest.importorskip("quant_core.lake")
    from quant_core.lake import QuantLake

    monkeypatch.setenv("QUANT_STATE_DIR", str(tmp_path / "state"))
    store = jobs.StateStore(tmp_path / "state" / "api.db")
    jobs.set_store(store)
    jobs.set_manager(jobs.JobManager(store))
    deps.set_overrides(lake=QuantLake(tmp_path / "quant-lake"), engines=fakes.make_engines())
    try:
        with TestClient(app) as c:
            h = c.get("/api/v1/health").json()
            assert h["status"] == "no_snapshot" and h["snapshot_id"] is None
            assert "make seed" in h["hint"]
            assert c.get("/api/v1/data/snapshots").json() == []
            for method, url, body in [
                ("get", "/api/v1/data/health", None),
                ("get", "/api/v1/data/universe/CSI500", None),
                ("post", "/api/v1/factors/evaluate", {}),
                ("post", "/api/v1/risk/barra-exposure", {}),
                ("post", "/api/v1/backtest/run", {}),
                ("get", "/api/v1/shadow/drift", None),
            ]:
                r = getattr(c, method)(url, **({"json": body} if body is not None else {}))
                assert r.status_code == 503, (url, r.text)
                err = r.json()["error"]
                assert err["code"] == "DATA_STALE" and "make seed" in err["message"]
    finally:
        deps.reset()
        jobs.set_store(None)
        jobs.set_manager(None)


def test_degraded_with_fake_lake_without_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("QUANT_STATE_DIR", str(tmp_path / "state"))
    jobs.set_store(jobs.StateStore(tmp_path / "state" / "api.db"))
    deps.set_overrides(lake=fakes.FakeLake(tmp_path, with_snapshot=False), engines=fakes.make_engines())
    try:
        with TestClient(app) as c:
            assert c.get("/api/v1/health").json()["status"] == "no_snapshot"
            assert c.get("/api/v1/data/health").status_code == 503
    finally:
        deps.reset()
        jobs.set_store(None)


# ---------------------------------------------------------------------------
# Q1
# ---------------------------------------------------------------------------


def test_health_and_data_endpoints(client):
    h = client.get("/api/v1/health").json()
    assert h == {"status": "ok", "snapshot_id": fakes.SNAP_ID, "markets": ["CN_A"], "mcp_port": 8101}
    dh = client.get("/api/v1/data/health").json()
    assert dh["snapshot_id"] == fakes.SNAP_ID and len(dh["checks"]) == 7
    snaps = client.get("/api/v1/data/snapshots").json()
    assert [s["snapshot_id"] for s in snaps] == [fakes.SNAP_ID]
    u = client.get("/api/v1/data/universe/CSI500").json()
    assert u["count"] == 40 and len(u["members"]) == 40 and u["snapshot_id"] == fakes.SNAP_ID
    assert client.get("/api/v1/data/universe/NOPE").status_code == 404


def test_data_sync_never_fakes_success(client, monkeypatch):
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    r = client.post("/api/v1/data/sync", json={"provider": "tushare", "mode": "check"})
    assert r.status_code == 200
    body = r.json()
    assert body["synthetic_fallback"] is False
    assert body["status"] in {"failed", "unavailable", "ok"}
    if body["status"] != "ok":
        assert body["reason"]
    assert client.post("/api/v1/data/sync", json={"provider": "nope"}).status_code == 422


# ---------------------------------------------------------------------------
# Q2
# ---------------------------------------------------------------------------


def test_factor_catalog_evaluate_correlation(client):
    cat = client.get("/api/v1/factors/catalog").json()
    ids = {f["factor_id"] for f in cat["factors"]}
    assert {"alpha_vp_corr_20d", "alpha_mom_resid_60d", "alpha_earn_sue_pit", "alpha_smart_money_vwap"} <= ids

    r = client.post("/api/v1/factors/evaluate", json={"factor_id": "alpha_vp_corr_20d"})
    assert r.status_code == 200, r.text
    ev = r.json()
    assert ev["snapshot_id"] == fakes.SNAP_ID and ev["admission_passed"] is True
    assert len(ev["decile_navs"]["LS"]) == len(ev["dates"])

    assert client.post("/api/v1/factors/evaluate", json={"factor_id": "no_such_factor"}).status_code == 404
    bad = client.post("/api/v1/factors/evaluate", json={"factor_id": "x", "expression": "close.shift(-5)"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "INVALID_ARGUMENT"
    assert client.post("/api/v1/factors/evaluate", json={"factor_id": "a", "bogus": 1}).status_code == 422

    c = client.post("/api/v1/factors/correlation", json={"factor_ids": ["alpha_vp_corr_20d", "alpha_mom_resid_60d"]})
    assert c.status_code == 200 and c.json()["max_abs_corr"] == 0.7
    assert client.post("/api/v1/factors/correlation", json={"factor_ids": []}).status_code == 422


def test_hypothesis_lifecycle_idempotent_and_ast_guard(client):
    body = {"factor_id": "my_f1", "hypothesis": "volume shock reverses", "expression": "-close.pct_change(5)",
            "idempotency_key": "k1"}
    r = client.post("/api/v1/factors/hypothesis", json=body)
    assert r.status_code == 200, r.text
    h = r.json()
    assert h["status"] == "evaluated" and h["ast_safe"] and not h["duplicate"]
    assert h["hypothesis_id"].startswith("HYP-") and h["trial_id"].startswith("TRIAL-")
    # same idempotency key and same expression hash -> duplicate, no new trial
    again = client.post("/api/v1/factors/hypothesis", json=body).json()
    assert again["duplicate"] is True and again["hypothesis_id"] == h["hypothesis_id"]
    other_key = client.post("/api/v1/factors/hypothesis", json={**body, "idempotency_key": "k2"}).json()
    assert other_key["duplicate"] is True and other_key["trial_id"] == h["trial_id"]
    # registered factor shows in the catalog and is evaluable
    cat = client.get("/api/v1/factors/catalog").json()
    assert any(f["factor_id"] == "my_f1" and f["source"] == "registered" for f in cat["factors"])
    assert client.post("/api/v1/factors/evaluate", json={"factor_id": "my_f1"}).status_code == 200

    unsafe = client.post(
        "/api/v1/factors/hypothesis",
        json={"factor_id": "leak", "hypothesis": "peek", "expression": "close.shift(-3)"},
    ).json()
    assert unsafe["status"] == "rejected" and unsafe["ast_safe"] is False and unsafe["rejection_reason"]

    j = client.post("/api/v1/factors/hypothesis?async_mode=true",
                    json={"factor_id": "my_f2", "hypothesis": "h", "expression": "-close.pct_change(10)"}).json()
    assert j["job_type"] == "hypothesis"
    done = wait_job(client, j["job_id"])
    assert done["status"] == "completed" and done["summary"]["hypothesis"]["status"] == "evaluated"


# ---------------------------------------------------------------------------
# Q3
# ---------------------------------------------------------------------------


def test_experiments_and_cpcv(client, env):
    exps = client.get("/api/v1/experiments?limit=5").json()
    assert exps[0]["experiment_id"] == "EXP-0427"
    r = client.post("/api/v1/validation/cpcv", json={"experiment_id": "EXP-0427", "trial_count_m": 8})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["experiment_id"] == "EXP-0427" and res["verdict"] in {"pass", "reject", "inconclusive"}
    assert res["purge_conflicts"] == 0 and res["n_oos_paths"] >= 1
    assert env.experiments["EXP-0427"].snapshot_id == fakes.SNAP_ID  # registry updated by the gate
    assert client.post("/api/v1/validation/cpcv", json={"experiment_id": "EXP-NOPE"}).status_code == 404

    j = client.post("/api/v1/validation/cpcv?async_mode=true", json={"experiment_id": "alpha_vp_corr_20d"}).json()
    done = wait_job(client, j["job_id"])
    assert done["status"] == "completed" and "dsr" in done["summary"]


# ---------------------------------------------------------------------------
# Q4
# ---------------------------------------------------------------------------


def test_barra_and_optimize(client):
    b = client.post("/api/v1/risk/barra-exposure", json={"experiment_id": "EXP-0427"})
    assert b.status_code == 200, b.text
    assert b.json()["snapshot_id"] == fakes.SNAP_ID and b.json()["styles"]
    o = client.post("/api/v1/portfolio/optimize", json={"constraints": {"max_stock_weight": 0.03}})
    assert o.status_code == 200, o.text
    assert o.json()["solve_status"] == "optimal" and len(o.json()["top_holdings"]) == 30
    assert client.post("/api/v1/portfolio/optimize", json={"constraints": {"nope": 1}}).status_code == 422


# ---------------------------------------------------------------------------
# Q5 / jobs
# ---------------------------------------------------------------------------


def test_backtest_sync_and_async_job_lifecycle(client):
    sync = client.post("/api/v1/backtest/run", json={"experiment_id": "EXP-0427"})
    assert sync.status_code == 200, sync.text
    assert sync.json()["run_id"] == "BT-TEST-1" and sync.json()["snapshot_id"] == fakes.SNAP_ID

    r = client.post("/api/v1/backtest/run?async_mode=true", json={"experiment_id": "EXP-0427"},
                    headers={"Idempotency-Key": "bt-1"})
    assert r.status_code == 200
    job = r.json()
    assert job["status"] in {"queued", "running", "completed"} and job["job_type"] == "backtest"
    dup = client.post("/api/v1/backtest/run?async_mode=true", json={"experiment_id": "EXP-0427"},
                      headers={"Idempotency-Key": "bt-1"}).json()
    assert dup["job_id"] == job["job_id"]  # idempotency_key dedupe

    done = wait_job(client, job["job_id"])
    assert done["status"] == "completed" and done["progress_pct"] == 100
    assert done["summary"]["run_id"] == "BT-TEST-1" and done["snapshot_id"] == fakes.SNAP_ID
    assert client.get("/api/v1/backtest/BT-TEST-1").status_code == 200
    assert client.get("/api/v1/backtest/NOPE").status_code == 404

    # SSE stream for a finished job emits a terminal event
    with client.stream("GET", f"/api/v1/jobs/{job['job_id']}/events") as s:
        text = "".join(s.iter_text())
    assert "event: progress" in text and "event: completed" in text


def test_async_job_failure_is_tracked_and_retryable(client):
    j = client.post("/api/v1/backtest/run?async_mode=true", json={"experiment_id": "EXP-MISSING"},
                    headers={"Idempotency-Key": "bad-1"}).json()
    done = wait_job(client, j["job_id"])
    assert done["status"] == "failed" and "EXP-MISSING" in done["error"]
    retry = client.post("/api/v1/backtest/run?async_mode=true", json={"experiment_id": "EXP-MISSING"},
                        headers={"Idempotency-Key": "bad-1"}).json()
    assert retry["job_id"] != j["job_id"]  # failed jobs can be re-run under the same key
    wait_job(client, retry["job_id"])
    assert client.get("/api/v1/jobs/job_does_not_exist").status_code == 404
    assert client.get("/api/v1/jobs/job_does_not_exist/events").status_code == 404


def test_job_state_machine_transitions(env, tmp_path):
    store = jobs.StateStore(tmp_path / "sm.db")
    mgr = jobs.JobManager(store)
    seen: list[str] = []

    def work(progress):
        seen.append(store.get_job(rec.job_id).status)
        progress(50)
        return {"summary": {"x": 1}}

    rec = mgr.submit("unit", "snap", work)
    assert rec.status == "queued"
    final = mgr.wait(rec.job_id, 10)
    assert seen == ["running"] and final.status == "completed" and final.summary == {"x": 1}
    mgr.shutdown()


# ---------------------------------------------------------------------------
# Q6 / catalog
# ---------------------------------------------------------------------------


def test_shadow_is_read_only_signal_export(client):
    r = client.get("/api/v1/shadow/drift?window_days=60")
    assert r.status_code == 200, r.text
    m = r.json()["authority_manifest"]
    assert m["execution_mode"] == "READ_ONLY_SIGNAL_EXPORT_ONLY" and m["broker_connected"] is False
    assert client.get("/api/v1/shadow/drift?window_days=1").status_code == 422


def test_mcp_catalog_lists_13_tools(client):
    c = client.get("/api/v1/mcp/catalog").json()
    assert c["count"] == 13 and [t["name"] for t in c["tools"]] == MCP_TOOL_NAMES
    assert all(t["calls"] == 0 and t["status"] == "ready" for t in c["tools"])
    assert c["server"]["max_response_bytes"] == 8192


def test_results_are_pinned_to_snapshot(client):
    for url, body in [
        ("/api/v1/factors/evaluate", {"factor_id": "alpha_vp_corr_20d"}),
        ("/api/v1/risk/barra-exposure", {}),
        ("/api/v1/portfolio/optimize", {}),
        ("/api/v1/backtest/run", {}),
    ]:
        assert client.post(url, json=body).json()["snapshot_id"] == fakes.SNAP_ID, url
    assert client.post("/api/v1/factors/evaluate", json={"factor_id": "alpha_vp_corr_20d", "snapshot_id": "snap-x"}).status_code == 404


# ---------------------------------------------------------------------------
# Real stack (auto-skips until quant-lake seed + quant-engines are importable)
# ---------------------------------------------------------------------------

SEED = Path(__file__).resolve().parents[3] / "scripts" / "seed_golden_dataset.py"


@pytest.fixture(scope="module")
def real_lake_root(tmp_path_factory):
    if not SEED.exists():
        pytest.skip("scripts/seed_golden_dataset.py not present yet")
    pytest.importorskip("quant_core.lake")
    for mod in ("factors.engine", "validation.cpcv", "risk.barra_opt", "backtest.engine", "shadow.monitor"):
        pytest.importorskip(f"quant_core.{mod}")
    import subprocess
    import sys

    root = tmp_path_factory.mktemp("real")
    proc = subprocess.run(
        [sys.executable, str(SEED), "--root-dir", str(root / "quant-lake"), "--db-path", str(root / "state" / "reg.db")],
        capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        pytest.skip(f"seed failed: {proc.stderr[-300:]}")
    return root


@pytest.fixture()
def real_client(real_lake_root, tmp_path, monkeypatch):
    from quant_core.lake import QuantLake

    monkeypatch.setenv("QUANT_STATE_DIR", str(tmp_path / "state"))
    store = jobs.StateStore(tmp_path / "state" / "api.db")
    jobs.set_store(store)
    mgr = jobs.JobManager(store)
    jobs.set_manager(mgr)
    deps.set_overrides(lake=QuantLake(real_lake_root / "quant-lake", db_path=real_lake_root / "state" / "reg.db"))
    with TestClient(app) as c:
        yield c
    mgr.shutdown()
    deps.reset()
    jobs.set_store(None)
    jobs.set_manager(None)


def test_real_engines_end_to_end(real_client):
    c = real_client
    snap = c.get("/api/v1/health").json()["snapshot_id"]
    assert snap and c.get("/api/v1/data/health").json()["overall_status"] in {"pass", "warn"}
    ev = c.post("/api/v1/factors/evaluate", json={"factor_id": "alpha_vp_corr_20d"})
    assert ev.status_code == 200, ev.text
    assert ev.json()["snapshot_id"] == snap
    cp = c.post("/api/v1/validation/cpcv", json={"experiment_id": "EXP-0427", "trial_count_m": 8})
    assert cp.status_code == 200, cp.text
    assert cp.json()["purge_conflicts"] == 0
    bx = c.post("/api/v1/risk/barra-exposure", json={})
    assert bx.status_code == 200, bx.text
    op = c.post("/api/v1/portfolio/optimize", json={"constraints": {"max_stock_weight": 0.08}})
    assert op.status_code == 200 and op.json()["solve_status"] in {"optimal", "optimal_inaccurate", "relaxed_optimal"}
    bt = c.post("/api/v1/backtest/run", json={})
    assert bt.status_code == 200, bt.text
    b = bt.json()
    assert b["snapshot_id"] == snap and b["attribution"]["brinson_closure_error_bp"] < 1.0
    sh = c.get("/api/v1/shadow/drift")
    assert sh.status_code == 200 and sh.json()["authority_manifest"]["broker_connected"] is False
