"""REST contract tests for services/quant-api (hermetic: FakeLake + engine stubs; real lake for the no-data case)."""

from __future__ import annotations

import hashlib
import importlib
import re
import subprocess
import sys
import threading
import time
import types
from datetime import UTC, datetime
from pathlib import Path

import fakes
import pytest
from fastapi.testclient import TestClient
from quant_api import deps, jobs, service
from quant_api.catalog import MCP_TOOL_NAMES
from quant_api.main import app
from quant_core import schemas as S

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

V3_ROUTES = {
    ("GET", "/api/v1/glossary"),
    ("GET", "/api/v1/glossary/{term_id}"),
    ("POST", "/api/v1/validation/formal-verify"),
    ("POST", "/api/v1/factors/regime-analysis"),
    ("POST", "/api/v1/portfolio/capacity"),
    ("GET", "/api/v1/research/studies"),
    ("POST", "/api/v1/research/studies"),
    ("POST", "/api/v1/research/dispatch"),
    ("GET", "/api/v1/research/studies/{study_id}"),
    ("GET", "/api/v1/research/studies/{study_id}/events"),
    ("GET", "/api/v1/research/studies/{study_id}/memo"),
    ("POST", "/api/v1/research/studies/{study_id}/publish"),
    ("GET", "/api/v1/research/baselines"),
    ("POST", "/api/v1/research/baselines"),
    ("GET", "/api/v1/research/tombstones"),
    ("POST", "/api/v1/research/tombstones"),
    ("GET", "/api/v1/research/clusters"),
}

FORBIDDEN = re.compile(r"order|broker|execut|trade|position|account|buy|sell|ctp|qmt|place", re.IGNORECASE)


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
    assert EXPECTED_ROUTES <= got  # V2.0 contract: zero breaking changes
    assert V3_ROUTES <= got
    assert ("GET", "/api/v1/jobs/{job_id}/events") in got
    assert len(EXPECTED_ROUTES) == 17 and len(V3_ROUTES) == 17
    assert spec["info"]["version"] == "0.3.0"


def test_no_broker_or_order_routes(client):
    spec = client.get("/openapi.json").json()
    bad = FORBIDDEN
    for path, ops in spec["paths"].items():
        assert not bad.search(path), path
        assert set(ops) <= {"get", "post"}, f"unexpected methods on {path}: {set(ops)}"
        assert path.startswith("/api/v1/")
    for r in app.routes:  # FastAPI-level as well (incl. any undocumented route)
        p = getattr(r, "path", "")
        if not p or p in {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}:
            continue
        assert not bad.search(p), p
        assert p.startswith("/api/v1/"), p
        assert set(getattr(r, "methods", set())) <= {"GET", "POST", "HEAD"}, p


def test_app_version_and_cors_env_var_support():
    assert app.version == "0.3.0"
    root = Path(__file__).resolve().parents[3]
    code = "from quant_api.main import CORS_ORIGINS; print('|'.join(CORS_ORIGINS))"
    paths = [root / "packages/quant-core/src", root / "services/quant-api/src"]
    import os

    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(map(str, paths)),
             "QUANT_CORS_ORIGINS": "http://example.test:4000, http://other.test"},
    )
    assert out.returncode == 0, out.stderr
    origins = out.stdout.strip().split("|")
    assert {"http://127.0.0.1:3100", "http://localhost:3100", "http://example.test:4000", "http://other.test"} <= set(origins)


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


def test_mcp_catalog_lists_21_tools(client):
    c = client.get("/api/v1/mcp/catalog").json()
    assert c["count"] == 21 and [t["name"] for t in c["tools"]] == MCP_TOOL_NAMES
    assert not [t["name"] for t in c["tools"] if FORBIDDEN.search(t["name"].replace("backtest", ""))]
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


# ===========================================================================
# V3.0 · hermetic stubs for the parallel-worker modules (lazy-imported by service.py)
#   quant_core.glossary / validation.formal / factors.regime / risk.capacity / quant_api.orchestrator
# They follow the frozen contract signatures; production code never imports this block.
# ===========================================================================


def _now() -> str:
    return datetime.now(UTC).isoformat()


GLOSSARY = [
    S.GlossaryTerm(
        term_id="rank_ic", term="Rank IC", en_name="Rank Information Coefficient", domain="Q2",
        category=S.GlossaryCategory.FACTOR, aliases=["RankIC", "秩相关IC"],
        short_tip="Spearman correlation between factor and next-period return.",
        formula_latex=r"\rho_s(f_t, r_{t+1})", institutional_threshold=">= 0.03 (WARN 0.02-0.03)",
        common_pitfalls=["small sample", "overlapping returns"], related_terms=["icir"],
        bot_prompt_template="解读 Rank IC，当前值 {rank_ic_mean}，门槛 0.03。" + "细节" * 200,
    ),
    S.GlossaryTerm(
        term_id="icir", term="ICIR", en_name="IC Information Ratio", domain="Q2", category=S.GlossaryCategory.FACTOR,
        short_tip="Annualised mean(IC)/std(IC).", institutional_threshold=">= 1.50", related_terms=["rank_ic"],
        bot_prompt_template="解读 ICIR 当前值 {icir_ann}",
    ),
    S.GlossaryTerm(
        term_id="dsr", term="Deflated Sharpe", en_name="Deflated Sharpe Ratio", domain="Q3",
        category=S.GlossaryCategory.VALIDATION, aliases=["DSR"], short_tip="Sharpe corrected for multiple trials.",
        institutional_threshold=">= 0.95", bot_prompt_template="解读 DSR {dsr}",
    ),
    S.GlossaryTerm(
        term_id="prefix_invariance", term="前缀不变性", en_name="Prefix Invariance", domain="MATH",
        category=S.GlossaryCategory.MATH, short_tip="f(D<=tau) equals f(D_full) on the prefix.",
        institutional_threshold="<= 1e-10", bot_prompt_template="解读前缀漂移 {max_prefix_drift}",
    ),
]


class _SafeCtx(dict):
    def __missing__(self, key: str) -> str:
        return "N/A"


def _g_list_terms(category=None, domain=None, q=None):
    out = list(GLOSSARY)
    if category:
        if category not in {c.value for c in S.GlossaryCategory}:
            raise ValueError(f"unknown category {category!r}")
        out = [t for t in out if t.category.value == category]
    if domain:
        out = [t for t in out if t.domain == domain]
    if q:
        ql = q.lower()
        out = [t for t in out if ql in " ".join([t.term_id, t.term, t.en_name, *t.aliases, t.short_tip]).lower()]
    return out


def _g_get_term(term_id):
    return next((t for t in GLOSSARY if t.term_id == term_id), None)


def _g_render(term_id, context=None):
    t = _g_get_term(term_id)
    if t is None:
        raise KeyError(term_id)
    return t.bot_prompt_template.format_map(_SafeCtx(context or {}))


def _proof(req, bars_df) -> S.FormalProofResult:
    assert bars_df.height > 0
    target = req.expression or req.factor_id or "?"
    bad = "shift(-" in target or "future" in target
    ids = [("T1.AST_CAUSAL", "T1"), ("T1.PREFIX_INVARIANCE", "T1"), ("T2.SCALE_INVARIANCE", "T2"),
           ("T2.COVERAGE", "T2"), ("T3.PIT_MONOTONICITY", "T3"), ("T3.CONSERVATION_PSD", "T3"),
           ("T4.BOOTSTRAP_CI", "T4"), ("T4.CPCV_PBO_DSR", "T4")]
    checks = [
        S.ProofCheckItem(
            check_id=cid, suite=suite, label=cid.split(".")[1].title(),  # type: ignore[arg-type]
            status="fail" if (bad and cid == "T1.PREFIX_INVARIANCE") else "pass",
            statistic="0.0", threshold="<= 1e-10", error_metric="0.0",
            reason_code="LOOKAHEAD_PREFIX_VIOLATION" if (bad and cid == "T1.PREFIX_INVARIANCE") else "OK",
        )
        for cid, suite in ids
    ]
    passed = not bad
    digest = hashlib.sha256(f"{target}|{req.snapshot_id}".encode()).hexdigest()
    return S.FormalProofResult(
        verification_id=f"FV-{digest[:10]}", target_id=req.factor_id or target[:24], universe_id=req.universe_id,
        snapshot_id=req.snapshot_id or fakes.SNAP_ID, as_of="2026-04-01", passed=passed,
        verdict=S.GateVerdict.PASS if passed else S.GateVerdict.REJECT, proofs_passed=len(checks) - (0 if passed else 1),
        proofs_total=len(checks), max_prefix_drift=0.0 if passed else 0.3, bootstrap_rank_ic_ci95=[0.02, 0.04],
        blocking_failures=[] if passed else ["T1.PREFIX_INVARIANCE"], checks=checks, proof_sha256=digest,
    )


def _f_run(bars_df, req, financials_df=None):
    return _proof(req, bars_df)


def _baselines(snapshot_id, universe_id):
    return [
        S.DimensionBaselineRecord(
            baseline_id=f"BL-{dim}", snapshot_id=snapshot_id, universe_id=universe_id, dimension_type=dim,  # type: ignore[arg-type]
            date_range_start="2025-01-02", date_range_end="2026-04-01", current_state="BULL",
            baseline_sha256=hashlib.sha256(dim.encode()).hexdigest(), created_at=_now(),
        )
        for dim in ("market_regime", "volatility_regime", "market_cap_tier", "sw_industry", "barra_style")
    ]


def _r_build(bars_df, snapshot_id, universe_id):
    assert bars_df.height > 0
    return _baselines(snapshot_id, universe_id)


def _r_analyze(bars_df, req, baselines=None):
    assert baselines and len(baselines) == 5
    dominant = req.factor_id == "alpha_mom_resid_60d"
    sl = [
        S.DimensionSliceItem(dim="market_regime", bucket="BULL", strategy_excess_pct=4.0, baseline_return_pct=1.0,
                             alpha_vs_baseline_pct=3.0, rank_ic=0.04, n_days=120,
                             contribution_pct=72.0 if dominant else 40.0, dominant_flag=dominant),
        S.DimensionSliceItem(dim="market_regime", bucket="BEAR", strategy_excess_pct=1.0, baseline_return_pct=-1.0,
                             alpha_vs_baseline_pct=2.0, rank_ic=0.02, n_days=60, contribution_pct=28.0 if dominant else 60.0),
    ]
    return S.RegimeAnalysisResult(
        factor_id=req.factor_id, universe_id=req.universe_id, snapshot_id=req.snapshot_id or fakes.SNAP_ID,
        regime_consistency_score=0.4 if dominant else 0.8, single_dim_dominance_flag=dominant,
        dominant_bucket="BULL" if dominant else None, dominant_share_pct=72.0 if dominant else None,
        warning_summary="DIMENSION_DOMINANCE" if dominant else None, slices=sl, baselines=list(baselines),
    )


def _c_analyze(bars_df, req, target_weights=None, gross_excess_ann_pct=None):
    assert isinstance(target_weights, dict) and abs(sum(target_weights.values()) - 1.0) < 1e-6
    assert isinstance(gross_excess_ann_pct, float), gross_excess_ann_pct  # experiment oos_excess_ann (pct)
    pts = [
        S.CapacityPoint(aum_cny=a, gross_excess_pct=5.0, net_excess_pct=5.0 - 0.5 * i, ir_ann=1.2 - 0.1 * i,
                        impact_bps=2.0 * (i + 1), explicit_cost_bps=10.0, pov_pct=0.5 * (i + 1), fill_rate_pct=100.0 - i)
        for i, a in enumerate(req.aum_grid_cny)
    ]
    return S.CapacityFrontierResult(
        experiment_id=req.experiment_id, universe_id=req.universe_id, snapshot_id=req.snapshot_id or fakes.SNAP_ID,
        half_alpha_aum_cny=5e8, breakeven_aum_cny=3e9, binding_bottleneck="liquidity", points=pts,
        illiquid_names=[{"symbol": fakes.SYMBOLS[0], "pov_pct": 4.9}],
    )


class FakeOrchestrator:
    """In-memory research ledger honouring the frozen `quant_api.orchestrator` contract."""

    def __init__(self) -> None:
        self.studies: dict[str, S.ResearchStudyDetail] = {}
        self.events: dict[str, list[S.StudyEventRecord]] = {}
        self.tombs: list[S.FalsifiedTombstoneRecord] = []
        self.idem: dict[str, S.ResearchDispatchResponse] = {}
        self.golden_calls = 0
        self.publish_calls: list[tuple[str, S.PublishFindingRequest]] = []
        self.plain_list = False
        self._n = 0
        self._lock = threading.RLock()

    # -- internals
    def _ev(self, sid: str, stage: str, etype: str, msg: str, pct: int) -> None:
        evs = self.events.setdefault(sid, [])
        evs.append(S.StudyEventRecord(event_id=f"{sid}-E{len(evs) + 1}", study_id=sid, seq=len(evs) + 1, stage=stage,
                                      event_type=etype, progress_pct=pct, message=msg, created_at=_now()))

    def _new(self, title, status, verdict, *, parent=None, expr=None, family=None, snap=None, **kw):
        with self._lock:
            self._n += 1
            sid = kw.pop("study_id", f"STUDY-{self._n:04d}")
            now = _now()
            det = S.ResearchStudyDetail(
                study_id=sid, parent_study_id=parent, family_id=family or (parent and self.studies[parent].family_id) or sid,
                title=title, status=status, verdict=verdict, snapshot_id=snap or fakes.SNAP_ID, created_at=now,
                updated_at=now, hypothesis=kw.pop("hypothesis", title), expression=expr, **kw,
            )
            self.studies[sid] = det
            self._ev(sid, "HYPOTHESIS", "state_change", f"{status}", 5)
            return det

    def _finish(self, sid: str) -> None:
        with self._lock:
            det = self.studies[sid]
            proof = _proof(S.FormalVerifyRequest(expression=det.expression, factor_id=det.factor_id,
                                                 snapshot_id=det.snapshot_id), types.SimpleNamespace(height=1))
            for c in proof.checks:
                self._ev(sid, "VERIFYING", "proof_check", f"{c.check_id}:{c.status}", 60)
            ok = proof.passed
            upd = dict(status="COMPLETED" if ok else "FALSIFIED", verdict="VERIFIED" if ok else "FALSIFIED",
                       stage="DONE", formal_proof=proof, proofs_passed=proof.proofs_passed, proofs_total=proof.proofs_total,
                       updated_at=_now(), completed_at=_now(), rank_ic_mean=0.035, dsr=0.97)
            self.studies[sid] = det.model_copy(update=upd)
            if not ok:
                self.add_tombstone(factor_id=det.factor_id or "f", expression=det.expression or "", universe_id=det.universe_id,
                                   snapshot_id=det.snapshot_id, failure_code="LOOKAHEAD_PREFIX_VIOLATION", study_id=sid)
            self._ev(sid, "DONE", "terminal", upd["status"], 100)

    # -- contract surface
    def list_clusters(self, probe_health=False):
        return [S.ClusterNodeStatus(cluster_id="engine_sandbox", display_name="Engine Sandbox", adapter="local_subprocess",
                                    agent_cli="python", status="online", slots_total=2, last_heartbeat=_now(),
                                    capabilities=["factor", "model"], detail="probed" if probe_health else "")]

    def ensure_golden_studies(self, snapshot_id):
        with self._lock:
            self.golden_calls += 1
            if self.studies:
                return
            root = self._new("Golden root: VP corr", "COMPLETED", "VERIFIED", study_id="STUDY-GOLD-ROOT",
                             expr="-rolling_corr(ret, dvol, 20)", snap=snapshot_id, kind="factor")
            self._new("Golden child: V2 iteration", "COMPLETED", "INCONCLUSIVE", study_id="STUDY-GOLD-CHILD",
                      parent=root.study_id, expr="-rolling_corr(ret, dvol, 40)", snap=snapshot_id)
            dead = self._new("Golden tombstone: future peek", "FALSIFIED", "FALSIFIED", study_id="STUDY-GOLD-TOMB",
                             expr="close.shift(-3)", snap=snapshot_id)
            self.add_tombstone(factor_id="peek", expression="close.shift(-3)", snapshot_id=snapshot_id,
                               failure_code="LOOKAHEAD_PREFIX_VIOLATION", study_id=dead.study_id)

    def list_studies(self, status=None, verdict=None, family_id=None, parent_study_id=None, q=None, limit=50):
        rows = [d for d in self.studies.values()
                if (not status or d.status == status) and (not verdict or d.verdict == verdict)
                and (not family_id or d.family_id == family_id)
                and (not parent_study_id or d.parent_study_id == parent_study_id)
                and (not q or q.lower() in d.title.lower())][:limit]
        summaries = [S.ResearchStudySummary.model_validate(d.model_dump(include=set(S.ResearchStudySummary.model_fields)))
                     for d in rows]
        if self.plain_list:
            return summaries
        ids = {d.study_id for d in rows}
        edges = [{"parent_study_id": d.parent_study_id, "study_id": d.study_id} for d in rows if d.parent_study_id in ids]
        return {"total": len(summaries), "studies": summaries, "lineage_edges": edges}

    def get_study(self, study_id):
        d = self.studies.get(study_id)
        return d.model_copy(update={"events": list(self.events.get(study_id, []))}) if d else None

    def dispatch_study(self, req, async_mode=True):
        assert req.snapshot_id == fakes.SNAP_ID, "service must pin the snapshot before dispatch"
        key = req.idempotency_key
        if key and key in self.idem:
            return self.idem[key]
        h = service.expr_hash(req.expression or req.factor_id or "")
        hits = [t for t in self.tombs if t.expr_hash == h]
        det = self._new(req.title, "DRAFT" if hits and not req.allow_duplicate else "QUEUED", "PENDING",
                        parent=req.parent_study_id, expr=req.expression, factor_id=req.factor_id,
                        hypothesis=req.hypothesis, dispatch_mode=req.dispatch_mode, target_cluster=req.target_cluster,
                        universe_id=req.universe_id, snap=req.snapshot_id, math_derivation=req.math_derivation)
        summ = S.ResearchStudySummary.model_validate(det.model_dump(include=set(S.ResearchStudySummary.model_fields)))
        warns = [{"tombstone_id": t.tombstone_id, "similarity": 1.0, "layer": "expr_hash"} for t in hits]
        if hits and not req.allow_duplicate:
            return S.ResearchDispatchResponse(study=summ, tombstone_warnings=warns, duplicate_blocked=True)
        job = None
        if async_mode:
            now = datetime.now(UTC)
            job = S.AsyncJobRecord(job_id=f"job_{det.study_id}", job_type="research_dispatch", status="running",
                                   created_at=now, updated_at=now, snapshot_id=req.snapshot_id)
            threading.Timer(0.3, self._finish, args=(det.study_id,)).start()
        else:
            self._finish(det.study_id)
        resp = S.ResearchDispatchResponse(study=summ, job=job, tombstone_warnings=warns)
        if key:
            self.idem[key] = resp
        return resp

    def publish_finding(self, study_id, req):
        self.publish_calls.append((study_id, req))
        det = self.studies[study_id]
        # zero trust: ignore worker_claimed_metrics, recompute server-side
        fv = S.FormalVerifyRequest(expression=req.candidate_expression, factor_id=req.factor_id, snapshot_id=req.snapshot_id)
        proof = _proof(fv, types.SimpleNamespace(height=1))
        diff = {k: {"claimed": v, "verified": 0.035 if proof.passed else None} for k, v in req.worker_claimed_metrics.items()}
        self.studies[study_id] = det.model_copy(update=dict(
            status="COMPLETED" if proof.passed else "FALSIFIED", verdict="VERIFIED" if proof.passed else "FALSIFIED",
            expression=req.candidate_expression, formal_proof=proof, proofs_passed=proof.proofs_passed,
            proofs_total=proof.proofs_total, metrics_summary={"claimed_vs_verified_diff": diff}, updated_at=_now()))
        self._ev(study_id, "PUBLISH", "terminal", "published", 100)
        return self.get_study(study_id)

    def stream_events(self, study_id, after_seq=0):
        end = time.time() + 5
        sent = after_seq
        while True:
            for ev in list(self.events.get(study_id, [])):
                if ev.seq > sent:
                    sent = ev.seq
                    yield ev
            if self.studies[study_id].status in {"COMPLETED", "FALSIFIED", "FAILED"} or time.time() > end:
                return
            time.sleep(0.05)

    def list_baselines(self, snapshot_id=None, universe_id="CSI500"):
        return _baselines(snapshot_id, universe_id) if getattr(self, "_built", False) else []

    def build_baselines(self, snapshot_id=None, universe_id="CSI500"):
        self._built = True
        return _baselines(snapshot_id, universe_id)

    def list_tombstones(self, q=None, expr_hash=None, limit=50):
        return [t for t in self.tombs if (not expr_hash or t.expr_hash == expr_hash)
                and (not q or q.lower() in (t.factor_id + t.lesson_summary).lower())][:limit]

    def add_tombstone(self, factor_id, expression, universe_id="CSI500", snapshot_id=None, failure_code="UNKNOWN",
                      failure_metrics=None, lesson_summary="", study_id=None, created_by="orchestrator"):
        h = service.expr_hash(expression)
        t = S.FalsifiedTombstoneRecord(
            tombstone_id=f"TOMB-{len(self.tombs) + 1:04d}", study_id=study_id, factor_id=factor_id, expression=expression,
            expr_hash=h, ast_fingerprint=h[:8], universe_id=universe_id, snapshot_id=snapshot_id or fakes.SNAP_ID,
            failure_code=failure_code, failure_metrics=failure_metrics or {},
            lesson_summary=lesson_summary or f"{factor_id} falsified: {failure_code}",
            memory_line=f"[FALSIFIED] {factor_id} | {h} | {failure_code}", created_by=created_by, created_at=_now(),
        )
        self.tombs.append(t)
        return t

    def export_memo(self, study_id):
        d = self.studies[study_id]
        return {"filename": f"{study_id}-ic-memo.md", "markdown": f"# IC Memo\n\n{d.title}\n"}


def _patch_module(monkeypatch, name: str, attrs: dict) -> None:
    """Patch the contract functions on the real module when it has landed, else install a stub module.

    Patching (instead of replacing `sys.modules[name]`) keeps sibling imports such as
    `quant_core.validation.cpcv -> .formal._candidate_fdr` working.
    """
    try:
        mod = importlib.import_module(name)
    except ImportError:
        mod = types.ModuleType(name)
        monkeypatch.setitem(sys.modules, name, mod)
    for key, fn in attrs.items():
        monkeypatch.setattr(mod, key, fn, raising=False)


def install_v3_stubs(monkeypatch) -> FakeOrchestrator:
    orch = FakeOrchestrator()
    mods = {
        "quant_core.glossary": {"list_terms": _g_list_terms, "get_term": _g_get_term, "render_bot_prompt": _g_render},
        "quant_core.validation.formal": {"run_formal_verification": _f_run},
        "quant_core.factors.regime": {"build_dimension_baselines": _r_build, "analyze_factor_regimes": _r_analyze},
        "quant_core.risk.capacity": {"analyze_capacity_frontier": _c_analyze},
        "quant_api.orchestrator": {"get_orchestrator": lambda: orch},
    }
    for name, attrs in mods.items():
        _patch_module(monkeypatch, name, attrs)
    return orch


@pytest.fixture()
def v3(env, monkeypatch):
    return install_v3_stubs(monkeypatch)


def _wait_study(client: TestClient, study_id: str, timeout: float = 10.0) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        d = client.get(f"/api/v1/research/studies/{study_id}").json()
        if d["status"] in {"COMPLETED", "FALSIFIED", "FAILED"}:
            return d
        time.sleep(0.05)
    raise AssertionError(f"study {study_id} did not finish")


def _sse_frames(text: str) -> list[dict]:
    out = []
    for block in text.strip().split("\n\n"):
        f = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        if f:
            out.append(f)
    return out


# ---------------------------------------------------------------------------
# V3.0 glossary / empty lake
# ---------------------------------------------------------------------------


def test_v3_glossary_filters_and_404(client, v3):
    r = client.get("/api/v1/glossary")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 4 and set(body["categories"]) == {"factor", "validation", "math"}
    S.GlossaryListResponse.model_validate(body)  # strict contract model
    assert client.get("/api/v1/glossary?category=validation").json()["count"] == 1
    assert client.get("/api/v1/glossary?domain=Q2").json()["count"] == 2
    assert client.get("/api/v1/glossary?q=秩相关").json()["terms"][0]["term_id"] == "rank_ic"
    assert client.get("/api/v1/glossary?category=bogus").status_code == 422
    t = client.get("/api/v1/glossary/dsr")
    assert t.status_code == 200 and S.GlossaryTerm.model_validate(t.json()).term_id == "dsr"
    nf = client.get("/api/v1/glossary/no_such_term")
    assert nf.status_code == 404 and nf.json()["error"]["code"] == "NOT_FOUND"


def test_v3_empty_lake_degrades_cleanly(tmp_path, monkeypatch):
    monkeypatch.setenv("QUANT_STATE_DIR", str(tmp_path / "state"))
    jobs.set_store(jobs.StateStore(tmp_path / "state" / "api.db"))
    deps.set_overrides(lake=fakes.FakeLake(tmp_path, with_snapshot=False), engines=fakes.make_engines())
    orch = install_v3_stubs(monkeypatch)
    try:
        with TestClient(app) as c:
            g = c.get("/api/v1/glossary")
            assert g.status_code == 200 and g.json()["count"] == 4  # no snapshot dependency
            assert c.get("/api/v1/glossary/rank_ic").status_code == 200
            assert c.get("/api/v1/research/clusters").status_code == 200
            ls = c.get("/api/v1/research/studies")
            assert ls.status_code == 200 and ls.json()["total"] == 0 and orch.golden_calls == 0  # nothing seeded w/o snapshot
            assert c.get("/api/v1/research/tombstones").json() == {"count": 0, "items": []}
            for method, url, body in [
                ("post", "/api/v1/validation/formal-verify", {}),
                ("post", "/api/v1/factors/regime-analysis", {}),
                ("post", "/api/v1/portfolio/capacity", {}),
                ("post", "/api/v1/research/dispatch", {"title": "t", "hypothesis": "h", "expression": "-close.pct_change(5)"}),
                ("get", "/api/v1/research/baselines", None),
            ]:
                r = getattr(c, method)(url, **({"json": body} if body is not None else {}))
                assert r.status_code == 503, (url, r.text)
                assert r.json()["error"]["code"] == "DATA_STALE" and "make seed" in r.json()["error"]["message"]
    finally:
        deps.reset()
        jobs.set_store(None)


def test_v3_missing_engine_modules_are_503_not_500(client, monkeypatch):
    # sys.modules[name] = None makes `import name` raise ImportError (parallel worker output not landed yet)
    for name in ("quant_core.validation.formal", "quant_core.factors.regime", "quant_core.risk.capacity",
                 "quant_core.glossary", "quant_api.orchestrator"):
        monkeypatch.setitem(sys.modules, name, None)
    for method, url, body in [
        ("post", "/api/v1/validation/formal-verify", {}),
        ("post", "/api/v1/factors/regime-analysis", {}),
        ("post", "/api/v1/portfolio/capacity", {}),
        ("get", "/api/v1/glossary", None),
        ("get", "/api/v1/research/clusters", None),
    ]:
        r = getattr(client, method)(url, **({"json": body} if body is not None else {}))
        assert r.status_code == 503, (url, r.text)
        assert "not importable" in r.json()["error"]["message"]
    # V2.0 surface is unaffected
    assert client.post("/api/v1/factors/evaluate", json={"factor_id": "alpha_vp_corr_20d"}).status_code == 200


# ---------------------------------------------------------------------------
# V3.0 formal verification / regime / capacity
# ---------------------------------------------------------------------------


def test_v3_formal_verify_sync_async_and_failure(client, v3):
    r = client.post("/api/v1/validation/formal-verify", json={"factor_id": "alpha_vp_corr_20d"})
    assert r.status_code == 200, r.text
    res = S.FormalProofResult.model_validate(r.json())
    assert res.passed and res.proofs_total == 8 and res.snapshot_id == fakes.SNAP_ID and res.artifact_uri

    bad = client.post("/api/v1/validation/formal-verify", json={"factor_id": "peek", "expression": "close.shift(-3)"}).json()
    assert bad["passed"] is False and bad["blocking_failures"] == ["T1.PREFIX_INVARIANCE"] and bad["verdict"] == "reject"

    j = client.post("/api/v1/validation/formal-verify?async_mode=true", json={"factor_id": "alpha_mom_resid_60d"})
    assert j.status_code == 200 and j.json()["job_type"] == "formal_verify"
    done = wait_job(client, j.json()["job_id"])
    assert done["status"] == "completed" and done["summary"]["passed"] is True and len(done["summary"]["checks"]) == 8
    assert done["artifact_uri"]
    # identical async request is deduped by key -> same job
    assert client.post("/api/v1/validation/formal-verify?async_mode=true",
                       json={"factor_id": "alpha_mom_resid_60d"}).json()["job_id"] == done["job_id"]

    assert client.post("/api/v1/validation/formal-verify", json={"factor_id": "no_such"}).status_code == 404
    assert client.post("/api/v1/validation/formal-verify", json={"factor_id": None}).status_code == 422
    assert client.post("/api/v1/validation/formal-verify", json={"bogus": 1}).status_code == 422


def test_v3_regime_and_capacity(client, v3):
    r = client.post("/api/v1/factors/regime-analysis", json={"factor_id": "alpha_vp_corr_20d"})
    assert r.status_code == 200, r.text
    res = S.RegimeAnalysisResult.model_validate(r.json())
    assert res.single_dim_dominance_flag is False and len(res.baselines) == 5 and res.snapshot_id == fakes.SNAP_ID
    dom = client.post("/api/v1/factors/regime-analysis", json={"factor_id": "alpha_mom_resid_60d"}).json()
    assert dom["single_dim_dominance_flag"] is True and dom["dominant_bucket"] == "BULL"
    assert client.post("/api/v1/factors/regime-analysis", json={"factor_id": "nope"}).status_code == 404

    c = client.post("/api/v1/portfolio/capacity", json={"experiment_id": "EXP-0427"})
    assert c.status_code == 200, c.text
    cap = S.CapacityFrontierResult.model_validate(c.json())
    assert len(cap.points) == 6 and cap.half_alpha_aum_cny == 5e8 and cap.calibration_status == "uncalibrated_scenario"
    grid = client.post("/api/v1/portfolio/capacity", json={"aum_grid_cny": [1e7, 1e8]}).json()
    assert len(grid["points"]) == 2
    assert client.post("/api/v1/portfolio/capacity", json={"experiment_id": "EXP-NOPE"}).status_code == 404


# ---------------------------------------------------------------------------
# V3.0 research ledger: studies / dispatch / SSE / memo / publish / baselines / tombstones / clusters
# ---------------------------------------------------------------------------


def test_v3_studies_seed_golden_once_and_filter(client, v3):
    r = client.get("/api/v1/research/studies")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3 and v3.golden_calls == 1
    assert {"study_id": "STUDY-GOLD-CHILD", "parent_study_id": "STUDY-GOLD-ROOT"} in body["lineage_edges"]
    for row in body["studies"]:
        S.ResearchStudySummary.model_validate(row)
    again = client.get("/api/v1/research/studies").json()
    assert again["total"] == 3 and v3.golden_calls == 1  # not re-seeded when the table is non-empty
    assert client.get("/api/v1/research/studies?verdict=FALSIFIED").json()["total"] == 1
    assert client.get("/api/v1/research/studies?parent_study_id=STUDY-GOLD-ROOT").json()["studies"][0]["study_id"] == "STUDY-GOLD-CHILD"
    assert client.get("/api/v1/research/studies?q=golden&limit=2").json()["total"] == 2
    assert client.get("/api/v1/research/studies?limit=0").status_code == 422


def test_v3_studies_accepts_plain_list_from_orchestrator(client, v3):
    v3.plain_list = True
    body = client.get("/api/v1/research/studies").json()
    assert body["total"] == 3 and len(body["lineage_edges"]) == 1  # edges derived from parent ids


def test_v3_dispatch_async_idempotent_alias_sse_memo(client, v3):
    payload = {"title": "VP corr v3", "hypothesis": "volume-price decoupling", "expression": "-rolling_corr(ret, dvol, 30)",
               "factor_id": "vp30"}
    r = client.post("/api/v1/research/dispatch", json=payload, headers={"Idempotency-Key": "idem-1"})
    assert r.status_code == 200, r.text
    resp = S.ResearchDispatchResponse.model_validate(r.json())
    sid = resp.study.study_id
    assert resp.job is not None and resp.job.status in {"queued", "running"} and not resp.duplicate_blocked
    # same Idempotency-Key (also via the /studies alias) returns the identical study, no new row
    alias = client.post("/api/v1/research/studies", json=payload, headers={"Idempotency-Key": "idem-1"}).json()
    assert alias["study"]["study_id"] == sid

    det = _wait_study(client, sid)
    S.ResearchStudyDetail.model_validate(det)
    assert det["verdict"] == "VERIFIED" and det["formal_proof"]["passed"] is True and len(det["events"]) >= 8

    with client.stream("GET", f"/api/v1/research/studies/{sid}/events") as s:
        assert s.headers["content-type"].startswith("text/event-stream")
        text = "".join(s.iter_text())
    frames = _sse_frames(text)
    seqs = [int(f["id"]) for f in frames]
    assert seqs == sorted(seqs) and seqs[0] == 1 and frames[-1]["event"] == "terminal"
    # resume: after_seq query and Last-Event-ID header
    tail = _sse_frames(client.get(f"/api/v1/research/studies/{sid}/events?after_seq={seqs[-3]}").text)
    assert [int(f["id"]) for f in tail] == seqs[-2:]
    tail2 = _sse_frames(client.get(f"/api/v1/research/studies/{sid}/events", headers={"Last-Event-ID": str(seqs[-2])}).text)
    assert [int(f["id"]) for f in tail2] == seqs[-1:]

    memo = client.get(f"/api/v1/research/studies/{sid}/memo").json()
    assert memo["study_id"] == sid and memo["filename"].endswith(".md") and memo["markdown"].startswith("# IC Memo")

    sync = client.post("/api/v1/research/dispatch?async_mode=false",
                       json={**payload, "title": "sync", "expression": "-rolling_corr(ret, dvol, 35)"}).json()
    assert sync["job"] is None and client.get(f"/api/v1/research/studies/{sync['study']['study_id']}").json()["status"] == "COMPLETED"


def test_v3_dispatch_validation_tombstone_block_and_404s(client, v3):
    assert client.post("/api/v1/research/dispatch", json={"title": "t", "hypothesis": "h"}).status_code == 422  # factor needs expr/id
    assert client.post("/api/v1/research/dispatch", json={"title": "t"}).status_code == 422
    assert client.post("/api/v1/research/dispatch", json={"title": "t", "hypothesis": "h", "expression": "x", "bogus": 1}).status_code == 422
    client.get("/api/v1/research/studies")  # seeds the golden tombstone for `close.shift(-3)`
    dup = client.post("/api/v1/research/dispatch", json={"title": "again", "hypothesis": "h", "expression": "close.shift(-3)"}).json()
    assert dup["duplicate_blocked"] is True and dup["tombstone_warnings"] and dup["job"] is None
    allowed = client.post("/api/v1/research/dispatch?async_mode=false", json={
        "title": "override", "hypothesis": "h", "expression": "close.shift(-3)", "allow_duplicate": True}).json()
    assert allowed["duplicate_blocked"] is False
    for url in ("", "/events", "/memo"):
        r = client.get(f"/api/v1/research/studies/NOPE{url}")
        assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND", url
    assert client.post("/api/v1/research/studies/NOPE/publish", json={"candidate_expression": "x"}).status_code == 404


def test_v3_publish_forces_server_side_recompute(client, v3):
    sid = client.post("/api/v1/research/dispatch?async_mode=false", json={
        "title": "ext", "hypothesis": "h", "expression": "-close.pct_change(5)", "dispatch_mode": "external_publish",
        "target_cluster": "ext-cluster"}).json()["study"]["study_id"]
    ok = client.post(f"/api/v1/research/studies/{sid}/publish", json={
        "candidate_expression": "-close.pct_change(7)", "worker_cluster_id": "ext-cluster",
        "worker_claimed_metrics": {"rank_ic_mean": 0.9}})
    assert ok.status_code == 200, ok.text
    det = S.ResearchStudyDetail.model_validate(ok.json())
    assert det.verdict == "VERIFIED" and det.formal_proof and det.formal_proof.passed
    # the fabricated claim is only a claim: the ledger keeps claimed vs server-verified side by side
    assert det.metrics_summary["claimed_vs_verified_diff"]["rank_ic_mean"] == {"claimed": 0.9, "verified": 0.035}
    pinned = v3.publish_calls[-1][1]
    assert pinned.snapshot_id == fakes.SNAP_ID  # snapshot pinned by the service

    lie = client.post(f"/api/v1/research/studies/{sid}/publish", json={
        "candidate_expression": "close.shift(-3)", "worker_claimed_metrics": {"rank_ic_mean": 0.99, "icir_ann": 9.9}})
    assert lie.status_code == 200 and lie.json()["verdict"] == "FALSIFIED"
    assert lie.json()["formal_proof"]["blocking_failures"] == ["T1.PREFIX_INVARIANCE"]


def test_v3_publish_never_returns_verified_without_passing_proof(client, v3, monkeypatch):
    sid = client.post("/api/v1/research/dispatch?async_mode=false",
                      json={"title": "x", "hypothesis": "h", "expression": "-close.pct_change(5)"}).json()["study"]["study_id"]

    def broken(study_id, req):  # a buggy orchestrator that forgets the proof
        return v3.studies[study_id].model_copy(update={"verdict": "VERIFIED", "formal_proof": None})

    monkeypatch.setattr(v3, "publish_finding", broken)
    r = client.post(f"/api/v1/research/studies/{sid}/publish", json={"candidate_expression": "-close.pct_change(9)"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "PROOF_FAILED"


def test_v3_baselines_tombstones_clusters(client, v3):
    assert client.get("/api/v1/research/baselines").json() == {"snapshot_id": fakes.SNAP_ID, "universe_id": "CSI500", "baselines": []}
    built = client.post("/api/v1/research/baselines?universe_id=CSI300").json()
    assert built["universe_id"] == "CSI300" and len(built["baselines"]) == 5 and built["snapshot_id"] == fakes.SNAP_ID
    for b in built["baselines"]:
        S.DimensionBaselineRecord.model_validate(b)
    assert client.get("/api/v1/research/baselines?snapshot_id=snap-x").status_code == 404

    assert client.get("/api/v1/research/tombstones").json()["count"] == 0
    add = client.post("/api/v1/research/tombstones", json={
        "factor_id": "dead_f", "expression": "-close.pct_change(3)", "failure_code": "LOW_DSR", "lesson_summary": "DSR 0.4"})
    assert add.status_code == 200, add.text
    item = add.json()["items"][0]
    assert add.json()["count"] == 1 and item["snapshot_id"] == fakes.SNAP_ID
    S.FalsifiedTombstoneRecord.model_validate(item)
    by_hash = client.get(f"/api/v1/research/tombstones?expr_hash={item['expr_hash']}").json()
    assert by_hash["count"] == 1 and by_hash["items"][0]["tombstone_id"] == item["tombstone_id"]
    assert client.get("/api/v1/research/tombstones?q=dead_f&limit=5").json()["count"] == 1
    assert client.get("/api/v1/research/tombstones?q=zzz").json()["count"] == 0
    assert client.post("/api/v1/research/tombstones", json={"factor_id": "x"}).status_code == 422

    cl = client.get("/api/v1/research/clusters?probe_health=true").json()
    assert [c["cluster_id"] for c in cl["clusters"]] == ["engine_sandbox"] and cl["clusters"][0]["detail"] == "probed"
    S.ClusterNodeStatus.model_validate(cl["clusters"][0])


def test_v3_routes_registered_get_post_only_and_forbidden_words_absent(client, v3):
    spec = client.get("/openapi.json").json()
    for path in (p for p in spec["paths"] if any(seg in p for seg in ("/glossary", "/research", "formal-verify", "regime", "capacity"))):
        assert not FORBIDDEN.search(path), path
        assert set(spec["paths"][path]) <= {"get", "post"}
    assert client.put("/api/v1/research/studies/x").status_code in {404, 405}
    assert client.delete("/api/v1/glossary/rank_ic").status_code == 405


def test_v3_mcp_tool_call_stats_endpoint_lists_new_tools(client, v3):
    names = [t["name"] for t in client.get("/api/v1/mcp/catalog").json()["tools"]]
    assert names[13:] == ["quant_glossary_lookup", "quant_formal_verify", "quant_regime_analysis", "quant_capacity_analysis",
                          "quant_dispatch_research", "quant_get_research_study", "quant_list_research_studies",
                          "quant_publish_research_finding"]


def test_v3_service_sources_have_no_personal_paths_ips_or_secrets():
    root = Path(__file__).resolve().parents[3]
    files = [
        "services/quant-api/src/quant_api/routes.py", "services/quant-api/src/quant_api/service.py",
        "services/quant-api/src/quant_api/catalog.py", "services/quant-api/src/quant_api/main.py",
        "services/quant-mcp/src/quant_mcp/server.py", "services/quant-mcp/src/quant_mcp/tools.py",
        "services/quant-mcp/skills/quant-lead/SKILL.md", "scripts/register_agent_home.mjs",
    ]
    pats = [r"/Users/", r"/home/\w+", r"C:\\\\Users", r"\.ts\.net\b",
            r"\b(?!127\.0\.0\.1\b|0\.0\.0\.0\b)\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
            r"sk-[A-Za-z0-9]{16,}", r"AKIA[0-9A-Z]{12,}", r"(?i)(api[_-]?key|secret|token)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}"]
    for f in files:
        text = (root / f).read_text(encoding="utf-8")
        for p in pats:
            assert not re.search(p, text), f"{f} matches {p}"


# ---------------------------------------------------------------------------
# V3.0 real parallel-worker modules (no stubs; auto-skip until they are importable)
# ---------------------------------------------------------------------------


def test_v3_real_glossary_on_empty_lake(tmp_path, monkeypatch):
    pytest.importorskip("quant_core.glossary")
    monkeypatch.setenv("QUANT_STATE_DIR", str(tmp_path / "state"))
    jobs.set_store(jobs.StateStore(tmp_path / "state" / "api.db"))
    deps.set_overrides(lake=fakes.FakeLake(tmp_path, with_snapshot=False), engines=fakes.make_engines())
    try:
        with TestClient(app) as c:
            g = c.get("/api/v1/glossary")
            assert g.status_code == 200
            body = S.GlossaryListResponse.model_validate(g.json())
            assert body.count >= 64 and {"factor", "validation", "risk", "backtest", "data", "shadow", "math"} <= set(body.categories)
            one = c.get("/api/v1/glossary/rank_ic")
            assert one.status_code == 200 and one.json()["term_id"] == "rank_ic"
            assert c.get("/api/v1/glossary?category=validation").json()["count"] < body.count
            assert c.get("/api/v1/glossary/definitely_not_a_term").status_code == 404
    finally:
        deps.reset()
        jobs.set_store(None)


def test_v3_real_formal_regime_capacity_contracts(client):
    for mod in ("validation.formal", "factors.regime", "risk.capacity"):
        pytest.importorskip(f"quant_core.{mod}")
    f = client.post("/api/v1/validation/formal-verify", json={"factor_id": "alpha_vp_corr_20d", "bootstrap_b": 50})
    assert f.status_code == 200, f.text
    proof = S.FormalProofResult.model_validate(f.json())
    assert proof.snapshot_id == fakes.SNAP_ID and proof.proofs_total >= 8 and len(proof.proof_sha256) == 64
    assert proof.passed is False and proof.blocking_failures  # random fake bars can never pass institutional gates
    r = client.post("/api/v1/factors/regime-analysis", json={"factor_id": "alpha_vp_corr_20d"})
    assert r.status_code == 200, r.text
    assert S.RegimeAnalysisResult.model_validate(r.json()).snapshot_id == fakes.SNAP_ID
    c = client.post("/api/v1/portfolio/capacity", json={"experiment_id": "EXP-0427"})
    assert c.status_code == 200, c.text
    cap = S.CapacityFrontierResult.model_validate(c.json())
    assert len(cap.points) == 6 and cap.calibration_status == "uncalibrated_scenario"
    assert client.post("/api/v1/factors/regime-analysis",
                       json={"factor_id": "alpha_vp_corr_20d", "dimensions": ["bogus"]}).status_code == 422
