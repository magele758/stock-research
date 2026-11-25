"""The 13 `quant_*` tool implementations.

Each `tool_*` returns the serialized McpEnvelope text (<= 8 KiB). Heavy payloads are written
to artifacts and referenced via `quant-artifact://...`. No tool places orders or talks to brokers.
"""

from __future__ import annotations

import json
import os
import time
from collections import Counter
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import polars as pl
from pydantic import ValidationError
from quant_api import deps, service
from quant_api.jobs import get_manager, get_store
from quant_core.schemas import (
    ArtifactRef,
    BacktestRequest,
    BarraExposureRequest,
    CpcvRequest,
    FactorCorrRequest,
    FactorEvalRequest,
    FactorHypothesisRequest,
    McpEnvelope,
    McpErrorCode,
    OptConstraints,
    PortfolioOptRequest,
    ShadowDriftRequest,
)

from . import envelope as E

SYNC_BUDGET_S = float(os.environ.get("QUANT_MCP_SYNC_BUDGET_S", "12"))
Result = tuple[McpEnvelope, dict[str, Any] | None]


def _j(model: Any) -> dict[str, Any]:
    return json.loads(model.model_dump_json())


def _d(v: str | None) -> date | None:
    return date.fromisoformat(v[:10]) if v else None


def _snap(snapshot_id: str | None) -> tuple[str, str]:
    s = deps.require_snapshot(snapshot_id)
    return s.snapshot_id, s.as_of.isoformat()


def _art_from_uri(uri: str | None, rid: str, name: str, payload: dict[str, Any]) -> ArtifactRef:
    if uri:
        try:
            meta = deps.get_lake().registry.get_artifact_meta(uri)
            if meta:
                return ArtifactRef(uri=uri, format=meta["format"], rows=int(meta["rows"]), sha256=meta["sha256"])
        except Exception:  # noqa: BLE001
            pass
    return E.spill_json(rid, name, payload)


def _save_df(rid: str, name: str, df: pl.DataFrame) -> ArtifactRef:
    try:
        return deps.get_lake().save_artifact(rid, name, df)
    except Exception:  # noqa: BLE001
        path = deps.state_dir() / "mcp-artifacts"
        path.mkdir(parents=True, exist_ok=True)
        return E.spill_json(rid, name, {"rows": df.head(1000).to_dicts(), "total_rows": df.height})


# ---------------------------------------------------------------------------
# runner: error mapping, stats, size guard
# ---------------------------------------------------------------------------


def run_tool(name: str, impl: Callable[[str], Result]) -> str:
    rid = E.new_request_id()
    t0 = time.perf_counter()
    status = "ok"
    full: dict[str, Any] | None = None
    try:
        env, full = impl(rid)
        status = env.status
    except deps.NotFound as exc:
        env, status = E.error_envelope(rid, McpErrorCode.NOT_FOUND, exc.message), "error"
    except deps.InvalidArgument as exc:
        env, status = E.error_envelope(rid, McpErrorCode.INVALID_ARGUMENT, exc.message), "error"
    except deps.ServiceUnavailable as exc:
        code = McpErrorCode(exc.code) if exc.code in McpErrorCode.__members__ else McpErrorCode.DATA_STALE
        env, status = E.error_envelope(rid, code, exc.message, exc.retryable), "error"
    except ValidationError as exc:
        env, status = E.error_envelope(rid, McpErrorCode.INVALID_ARGUMENT, _short_validation(exc)), "error"
    except ValueError as exc:
        env, status = E.error_envelope(rid, McpErrorCode.INVALID_ARGUMENT, str(exc)), "error"
    except TimeoutError as exc:
        env, status = E.error_envelope(rid, McpErrorCode.TIMEOUT, str(exc), True), "error"
    except Exception as exc:  # noqa: BLE001
        env = E.error_envelope(rid, McpErrorCode.INSUFFICIENT_EVIDENCE, f"{type(exc).__name__}: {exc}")
        status = "error"
    try:
        get_store().record_tool_call(name, (time.perf_counter() - t0) * 1000.0, status)
    except Exception:  # noqa: BLE001 - stats are best effort
        pass
    return E.to_text(env, full)


def _short_validation(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:5])


def _job_result(
    rid: str,
    rec: Any,
    as_of: str | None,
    extra: dict[str, Any] | None = None,
    poll_hint: str = "",
) -> Result:
    state = rec.status
    base = {"job_type": rec.job_type, "progress_pct": rec.progress_pct}
    if state == "completed":
        summary = {**(rec.summary or {}), **(extra or {})}
        art = None
        if rec.artifact_uri:
            art = _art_from_uri(rec.artifact_uri, rid, f"{rec.job_type}_result", {"summary": rec.summary})
        return (
            E.ok_envelope(rid, snapshot_id=rec.snapshot_id, as_of=as_of, summary=summary, artifact=art,
                          job_id=rec.job_id, job_state="completed"),
            None,
        )
    if state in {"queued", "running"}:
        summary = {**base, "message": f"accepted; poll by calling this tool again with job_id={rec.job_id}. {poll_hint}".strip()}
        return (
            E.ok_envelope(rid, snapshot_id=rec.snapshot_id, as_of=as_of, summary=summary, status="accepted",
                          job_id=rec.job_id, job_state=state),
            None,
        )
    env = E.error_envelope(rid, McpErrorCode.INSUFFICIENT_EVIDENCE, rec.error or f"job {state}")
    return env.model_copy(update={"job_id": rec.job_id, "job_state": state, "snapshot_id": rec.snapshot_id}), None


def _await(enqueue: Callable[[], Any], async_mode: bool, job_id: str | None) -> Any:
    """Fast-accept (async_mode) or bounded wait (<=SYNC_BUDGET_S) so we never hit the 20s MCP HTTP timeout."""
    if job_id:
        return service.get_job(job_id)
    rec = enqueue()
    if async_mode:
        return rec
    return get_manager().wait(rec.job_id, timeout=SYNC_BUDGET_S) or rec


# ---------------------------------------------------------------------------
# 1-3 data
# ---------------------------------------------------------------------------


def tool_data_health(snapshot_id: str | None = None) -> str:
    def impl(rid: str) -> Result:
        rep = service.data_health(snapshot_id)
        full = _j(rep)
        art = E.spill_json(rid, "data_health_report", full)
        bad = [c for c in rep.checks if c.status != "pass"]
        summary = {
            "overall_status": rep.overall_status,
            "freshness_sec": rep.freshness_sec,
            "coverage_pct": rep.coverage_pct,
            "stale_datasets": rep.stale_datasets[:10],
            "failed_checks": rep.failed_checks[:10],
            "checks_total": len(rep.checks),
            "checks_not_pass": [
                {"id": c.check_id, "status": c.status, "affected": c.affected_count, "samples": c.sample_symbols[:3]}
                for c in bad[:10]
            ],
            "datasets": [
                {"dataset": d.dataset, "latest": d.latest_date, "coverage_pct": d.coverage_pct, "pit": d.pit_status}
                for d in rep.datasets[:12]
            ],
            "fact_fingerprint": rep.fact_fingerprint,
        }
        status = "ok" if rep.overall_status == "pass" else "degraded"
        return (
            E.ok_envelope(rid, snapshot_id=rep.snapshot_id, as_of=rep.as_of.isoformat(), summary=summary, artifact=art,
                          status=status, coverage_pct=rep.coverage_pct),
            full,
        )

    return run_tool("quant_data_health", impl)


_DEFAULT_PANEL_COLS = ["close", "volume_shares", "amount_ccy", "float_market_cap"]


def tool_query_pit_panel(
    universe_id: str = "CSI500",
    start_date: str | None = None,
    end_date: str | None = None,
    symbols: list[str] | None = None,
    columns: list[str] | None = None,
    preview_limit: int = 10,
    snapshot_id: str | None = None,
) -> str:
    def impl(rid: str) -> Result:
        sid, as_of = _snap(snapshot_id)
        limit = max(1, min(int(preview_limit), E.PREVIEW_MAX_ROWS))
        end = _d(end_date) or date.fromisoformat(as_of[:10])
        start = _d(start_date) or end - timedelta(days=30)
        df = deps.get_lake().load_bars(universe_id, start, end, sid)
        if df is None or df.height == 0:
            raise deps.NotFound(f"no bars for {universe_id} in {start}..{end} (snapshot {sid})")
        if symbols:
            df = df.filter(pl.col("symbol").is_in(symbols))
            if df.height == 0:
                raise deps.NotFound("none of the requested symbols are in the panel for this window")
        cols = [c for c in (columns or _DEFAULT_PANEL_COLS) if c in df.columns]
        if not cols:
            raise deps.InvalidArgument(f"no valid columns; available: {', '.join(df.columns[:30])}")
        stats = {}
        for c in cols:
            s = df[c]
            if not s.dtype.is_numeric():
                continue
            nn = s.drop_nulls()
            stats[c] = {
                "count": nn.len(),
                "null_pct": round(100.0 * s.null_count() / max(s.len(), 1), 3),
                "p05": nn.quantile(0.05) if nn.len() else None,
                "p50": nn.quantile(0.5) if nn.len() else None,
                "p95": nn.quantile(0.95) if nn.len() else None,
            }
        art = _save_df(rid, "pit_panel", df)
        prev = df.sort(["trade_date", "symbol"], descending=[True, False]).select(["symbol", "trade_date", *cols]).head(limit)
        summary = {
            "universe_id": universe_id,
            "window": [start.isoformat(), end.isoformat()],
            "rows": df.height,
            "symbols": df["symbol"].n_unique(),
            "trade_dates": df["trade_date"].n_unique(),
            "stats": stats,
            "preview_rows": prev.height,
        }
        return E.ok_envelope(rid, snapshot_id=sid, as_of=as_of, summary=summary, preview=prev.to_dicts(), artifact=art), None

    return run_tool("quant_query_pit_panel", impl)


def tool_universe_constituents(
    universe_id: str = "CSI500",
    as_of_date: str | None = None,
    include_symbols: bool = False,
    snapshot_id: str | None = None,
) -> str:
    def impl(rid: str) -> Result:
        sid, as_of = _snap(snapshot_id)
        data = service.universe(universe_id, _d(as_of_date), sid)
        members = data["members"]
        weights = [m["weight"] for m in members if m.get("weight") is not None]
        top = sorted((m for m in members if m.get("weight") is not None), key=lambda m: -m["weight"])[:10]
        asof_d = date.fromisoformat(data["as_of_date"])
        summary: dict[str, Any] = {
            "universe_id": universe_id,
            "as_of_date": data["as_of_date"],
            "count": data["count"],
            "weight_sum": round(sum(weights), 6) if weights else None,
            "weight_quality": dict(Counter(m.get("weight_quality") for m in members)),
            "top_weights": [{"symbol": m["symbol"], "w": m["weight"]} for m in top],
        }
        try:  # change vs ~1 month earlier
            prior = deps.get_lake().load_universe_constituents(universe_id, asof_d - timedelta(days=30), sid)
            now_s, old_s = {m["symbol"] for m in members}, {m.symbol for m in prior}
            summary["changes_vs_30d"] = {"added": len(now_s - old_s), "removed": len(old_s - now_s),
                                         "added_sample": sorted(now_s - old_s)[:5], "removed_sample": sorted(old_s - now_s)[:5]}
        except Exception:  # noqa: BLE001
            pass
        try:  # industry weight distribution
            bars = deps.get_lake().load_bars(universe_id, asof_d - timedelta(days=7), asof_d, sid)
            last = bars.filter(pl.col("trade_date") == bars["trade_date"].max())
            wdf = pl.DataFrame({"symbol": [m["symbol"] for m in members], "w": [m.get("weight") or 0.0 for m in members]})
            ind = (last.join(wdf, on="symbol").group_by("industry_l1").agg(pl.col("w").sum()).sort("w", descending=True).head(8))
            summary["industry_weights_top8"] = {r["industry_l1"]: round(r["w"], 4) for r in ind.iter_rows(named=True)}
        except Exception:  # noqa: BLE001
            pass
        full = {"members": members} if include_symbols else None
        art = E.spill_json(rid, "universe_members", {"members": members}) if include_symbols else None
        prev = None
        if include_symbols:
            prev = [{"symbol": m["symbol"], "weight": m.get("weight")} for m in members[: E.PREVIEW_MAX_ROWS]]
            summary["symbols_note"] = f"first {len(prev)} of {data['count']}; full list in artifact"
        return E.ok_envelope(rid, snapshot_id=sid, as_of=as_of, summary=summary, preview=prev, artifact=art), full

    return run_tool("quant_universe_constituents", impl)


# ---------------------------------------------------------------------------
# 4-6 factors
# ---------------------------------------------------------------------------


def tool_eval_factor(
    factor_id: str = "alpha_vp_corr_20d",
    expression: str | None = None,
    universe_id: str = "CSI500",
    start_date: str | None = None,
    end_date: str | None = None,
    horizons: list[int] | None = None,
    snapshot_id: str | None = None,
) -> str:
    def impl(rid: str) -> Result:
        req = FactorEvalRequest(
            factor_id=factor_id, expression=expression, universe_id=universe_id, snapshot_id=snapshot_id,
            start_date=_d(start_date), end_date=_d(end_date), **({"horizons": horizons} if horizons else {}),
        )
        res = service.eval_factor(req)
        full = _j(res)
        art = _art_from_uri(res.artifact_uri, rid, "factor_eval", full)
        summary = {
            "factor_id": res.factor_id,
            "universe_id": res.universe_id,
            "ast_safe": res.ast_safe,
            "ast_warnings": res.ast_warnings[:3],
            "rank_ic_mean": res.rank_ic_mean, "ic_std": res.ic_std, "icir_ann": res.icir_ann,
            "nw_t_stat": res.nw_t_stat, "ic_win_rate": res.ic_win_rate,
            "long_short_ann": res.long_short_ann, "top_excess_ann": res.top_excess_ann,
            "daily_turnover_oneway": res.daily_turnover_oneway, "half_life_days": res.half_life_days,
            "monotonicity_spearman": res.monotonicity_spearman,
            "max_corr_in_library": res.max_corr_in_library, "max_corr_peer": res.max_corr_peer,
            "coverage_pct": res.coverage_pct, "admission_passed": res.admission_passed,
            "gate_checks": [{k: g.get(k) for k in ("name", "check", "passed", "value", "threshold") if k in g}
                            for g in res.gate_checks[:8]],
            "decile_ann_returns": res.decile_ann_returns,
            "ic_decay": res.ic_decay,
            "residual_exposures": dict(list(res.residual_exposures.items())[:6]),
        }
        return (E.ok_envelope(rid, snapshot_id=res.snapshot_id, as_of=_as_of_of(res.snapshot_id), summary=summary,
                              artifact=art, coverage_pct=res.coverage_pct, warnings=res.ast_warnings[:3]), full)

    return run_tool("quant_eval_factor", impl)


def _as_of_of(snapshot_id: str) -> str | None:
    try:
        return deps.require_snapshot(snapshot_id).as_of.isoformat()
    except Exception:  # noqa: BLE001
        return None


def tool_factor_correlation(
    factor_ids: list[str],
    universe_id: str = "CSI500",
    method: str = "spearman",
    snapshot_id: str | None = None,
) -> str:
    def impl(rid: str) -> Result:
        req = FactorCorrRequest(factor_ids=factor_ids, universe_id=universe_id, snapshot_id=snapshot_id,
                                method=method)  # type: ignore[arg-type]
        res = service.factor_corr(req)
        full = _j(res)
        art = E.spill_json(rid, "factor_corr_matrix", full)
        pairs = sorted(res.high_corr_pairs, key=lambda p: -abs(float(p.get("corr", p.get("rho", 0.0)) or 0.0)))[:15]
        summary = {"universe_id": res.universe_id, "method": res.method, "labels": res.labels[:15],
                   "n_factors": len(res.labels), "max_abs_corr": res.max_abs_corr, "high_corr_pairs": pairs}
        return E.ok_envelope(rid, snapshot_id=res.snapshot_id, as_of=_as_of_of(res.snapshot_id), summary=summary, artifact=art), full

    return run_tool("quant_factor_correlation", impl)


def tool_submit_factor_hypothesis(
    factor_id: str,
    hypothesis: str,
    expression: str,
    universe_id: str = "CSI500",
    snapshot_id: str | None = None,
    idempotency_key: str | None = None,
    async_mode: bool = False,
    job_id: str | None = None,
) -> str:
    def impl(rid: str) -> Result:
        sid, as_of = _snap(snapshot_id)
        req = FactorHypothesisRequest(factor_id=factor_id, hypothesis=hypothesis, expression=expression,
                                      universe_id=universe_id, snapshot_id=sid, idempotency_key=idempotency_key)
        rec = _await(lambda: service.enqueue_hypothesis(req), async_mode, job_id)
        if rec.status == "completed" and rec.summary:
            h = rec.summary.get("hypothesis", {})
            summary = {**h, "evaluation": rec.summary.get("evaluation"),
                       "memory_hint": "if rejected/duplicate: record in project.memory [FALSIFIED] graveyard"}
            return E.ok_envelope(rid, snapshot_id=sid, as_of=as_of, summary=summary, job_id=rec.job_id, job_state="completed"), None
        return _job_result(rid, rec, as_of)

    return run_tool("quant_submit_factor_hypothesis", impl)


# ---------------------------------------------------------------------------
# 7-8 validation
# ---------------------------------------------------------------------------


def tool_run_cpcv_gate(
    experiment_id: str,
    n_splits: int = 6,
    k_test: int = 2,
    purge_days: int = 5,
    embargo_days: int = 10,
    trial_count_m: int = 20,
    snapshot_id: str | None = None,
    async_mode: bool = False,
    job_id: str | None = None,
) -> str:
    def impl(rid: str) -> Result:
        sid, as_of = _snap(snapshot_id)
        req = CpcvRequest(experiment_id=experiment_id, n_splits=n_splits, k_test=k_test, purge_days=purge_days,
                          embargo_days=embargo_days, trial_count_m=trial_count_m, snapshot_id=sid)
        rec = _await(lambda: service.enqueue_cpcv(req, dedupe=True), async_mode, job_id)
        return _job_result(rid, rec, as_of)

    return run_tool("quant_run_cpcv_gate", impl)


def tool_list_experiments(limit: int = 20, sort_by: str = "created_at") -> str:
    def impl(rid: str) -> Result:
        sid, as_of = _snap(None)
        recs = service.list_experiments(max(1, min(int(limit), 100)))
        if sort_by in {"dsr", "oos_sharpe", "cpcv_pbo", "oos_is_ratio"}:
            recs = sorted(recs, key=lambda r: getattr(r, sort_by), reverse=sort_by != "cpcv_pbo")
        rows = [
            {"exp_id": r.experiment_id, "factor": r.family_id, "model": r.model_type, "snap": r.snapshot_id,
             "oos_sharpe": r.oos_sharpe, "dsr": r.dsr, "pbo": r.cpcv_pbo, "oos_is": r.oos_is_ratio,
             "m": r.trial_count_m, "verdict": r.verdict.value}
            for r in recs
        ]
        summary = {"count": len(rows), "sort_by": sort_by,
                   "verdicts": dict(Counter(r.verdict.value for r in recs))}
        full = {"experiments": [_j(r) for r in recs]}
        return E.ok_envelope(rid, snapshot_id=sid, as_of=as_of, summary=summary, preview=rows[: E.PREVIEW_MAX_ROWS]), full

    return run_tool("quant_list_experiments", impl)


# ---------------------------------------------------------------------------
# 9-10 risk / portfolio
# ---------------------------------------------------------------------------


def tool_barra_exposure(
    experiment_id: str = "EXP-0427", universe_id: str = "CSI500", snapshot_id: str | None = None
) -> str:
    def impl(rid: str) -> Result:
        res = service.barra(BarraExposureRequest(experiment_id=experiment_id, universe_id=universe_id,
                                                 snapshot_id=snapshot_id))
        full = _j(res)
        art = E.spill_json(rid, "barra_exposure", full)
        inds = sorted(res.industries, key=lambda i: -abs(i.active_weight_pct))[:10]
        summary = {
            "experiment_id": res.experiment_id, "universe_id": res.universe_id, "as_of": res.as_of,
            "predicted_te_pct": res.predicted_te_pct,
            "risk_share": {"style": res.style_risk_share, "industry": res.industry_risk_share,
                           "specific": res.specific_risk_share},
            "psd_min_eigenvalue": res.psd_min_eigenvalue,
            "styles": [{"f": s.factor, "pre": s.pre_opt, "post": s.post_opt, "limit": s.limit, "breach": s.breached}
                       for s in res.styles],
            "top_industry_dev": [{"ind": i.industry, "active_pct": i.active_weight_pct, "limit": i.limit_pct} for i in inds],
            "breached_styles": [s.factor for s in res.styles if s.breached],
        }
        return E.ok_envelope(rid, snapshot_id=res.snapshot_id, as_of=_as_of_of(res.snapshot_id), summary=summary, artifact=art), full

    return run_tool("quant_barra_exposure", impl)


def tool_optimize_portfolio(
    experiment_id: str = "EXP-0427",
    universe_id: str = "CSI500",
    constraints: dict[str, Any] | None = None,
    snapshot_id: str | None = None,
) -> str:
    def impl(rid: str) -> Result:
        cons = OptConstraints.model_validate(constraints or {})
        res = service.optimize(PortfolioOptRequest(experiment_id=experiment_id, universe_id=universe_id,
                                                   snapshot_id=snapshot_id, constraints=cons))
        full = _j(res)
        art = E.spill_json(rid, "portfolio_opt", full)
        top = [{"sym": h.symbol, "ind": h.industry, "w_pct": h.target_weight_pct, "active_pct": h.active_weight_pct,
                "impact_bps": h.est_impact_bps, "tradable": h.tradable_status} for h in res.top_holdings[:15]]
        summary = {
            "opt_run_id": res.opt_run_id, "solver": res.solver, "solve_status": res.solve_status,
            "solve_ms": res.solve_ms, "relaxation_notes": res.relaxation_notes[:4],
            "expected_gross_excess_pct": res.expected_gross_excess_pct, "expected_cost_pct": res.expected_cost_pct,
            "expected_net_excess_pct": res.expected_net_excess_pct, "predicted_te_pct": res.predicted_te_pct,
            "expected_ir": res.expected_ir, "oneway_turnover_pct": res.oneway_turnover_pct,
            "effective_n_stocks": res.effective_n_stocks, "max_constraint_violation": res.max_constraint_violation,
            "breached_styles": [s.factor for s in res.style_exposures if s.breached],
            "note": "target-weight proposal only; read-only signal, no order placement exists",
        }
        status = "degraded" if res.solve_status in {"relaxed_optimal", "optimal_inaccurate"} else "ok"
        if res.solve_status == "infeasible":
            env = E.error_envelope(rid, McpErrorCode.INFEASIBLE, "optimization infeasible: " + "; ".join(res.relaxation_notes[:3]))
            return env.model_copy(update={"summary": summary, "snapshot_id": res.snapshot_id}), full
        return (E.ok_envelope(rid, snapshot_id=res.snapshot_id, as_of=_as_of_of(res.snapshot_id), summary=summary,
                              preview=top, artifact=art, status=status), full)

    return run_tool("quant_optimize_portfolio", impl)


# ---------------------------------------------------------------------------
# 11-12 backtest / attribution
# ---------------------------------------------------------------------------


def tool_run_backtest(
    experiment_id: str = "EXP-0427",
    universe_id: str = "CSI500",
    engine: str = "event",
    start_date: str | None = None,
    end_date: str | None = None,
    initial_cash: float = 10_000_000.0,
    microstructure: dict[str, Any] | None = None,
    snapshot_id: str | None = None,
    async_mode: bool = False,
    job_id: str | None = None,
) -> str:
    def impl(rid: str) -> Result:
        sid, as_of = _snap(snapshot_id)
        payload: dict[str, Any] = {
            "experiment_id": experiment_id, "universe_id": universe_id, "engine": engine, "snapshot_id": sid,
            "start_date": _d(start_date), "end_date": _d(end_date), "initial_cash": initial_cash,
        }
        if microstructure:
            payload["microstructure"] = microstructure
        req = BacktestRequest.model_validate(payload)
        rec = _await(lambda: service.enqueue_backtest(req, dedupe=True), async_mode, job_id)
        if rec.status == "completed" and rec.summary and rec.summary.get("run_id"):
            res = service.get_backtest(rec.summary["run_id"])
            summary = {
                **service.backtest_job_summary(res),
                "bench_ann_return_pct": res.bench_ann_return_pct, "excess_max_drawdown_pct": res.excess_max_drawdown_pct,
                "calmar": res.calmar, "daily_win_rate_pct": res.daily_win_rate_pct,
                "profit_loss_ratio": res.profit_loss_ratio, "total_fills": res.total_fills,
                "friction": _j(res.friction),
                "recent_rejections": [_j(r) for r in res.recent_rejections[:3]],
                "n_days": len(res.dates),
            }
            art = _art_from_uri(res.artifact_uri, rid, "backtest_result", _j(res))
            return (E.ok_envelope(rid, snapshot_id=res.snapshot_id, as_of=as_of, summary=summary, artifact=art,
                                  job_id=rec.job_id, job_state="completed"), None)
        return _job_result(rid, rec, as_of, poll_hint="Results: run_id is in the completed summary.")

    return run_tool("quant_run_backtest", impl)


def tool_attribution_tearsheet(run_id: str | None = None, job_id: str | None = None) -> str:
    def impl(rid: str) -> Result:
        rid_ = run_id
        if not rid_ and job_id:
            job = service.get_job(job_id)
            if job.status != "completed":
                return _job_result(rid, job, None)
            rid_ = (job.summary or {}).get("run_id")
        if not rid_:
            raise deps.InvalidArgument("provide run_id (or the completed backtest's job_id)")
        res = service.get_backtest(rid_)
        full = _j(res)
        dd_worst = sorted(enumerate(res.excess_dd), key=lambda t: t[1])[:5]
        summary = {
            "run_id": res.run_id, "experiment_id": res.experiment_id, "engine": res.engine.value,
            "net_excess_ann_pct": res.net_excess_ann_pct, "info_ratio": res.info_ratio,
            "max_drawdown_pct": res.max_drawdown_pct,
            "attribution": _j(res.attribution),
            "friction": _j(res.friction),
            "worst_excess_drawdowns": [{"date": res.dates[i] if i < len(res.dates) else None, "dd_pct": v} for i, v in dd_worst],
        }
        art = _art_from_uri(res.artifact_uri, rid, "backtest_result", full)
        return (E.ok_envelope(rid, snapshot_id=res.snapshot_id, as_of=_as_of_of(res.snapshot_id), summary=summary, artifact=art), full)

    return run_tool("quant_attribution_tearsheet", impl)


# ---------------------------------------------------------------------------
# 13 shadow
# ---------------------------------------------------------------------------


def tool_shadow_drift_check(
    shadow_portfolio_id: str = "SHADOW-CSI500-MAIN",
    universe_id: str = "CSI500",
    window_days: int = 60,
    snapshot_id: str | None = None,
) -> str:
    def impl(rid: str) -> Result:
        res = service.shadow(ShadowDriftRequest(shadow_portfolio_id=shadow_portfolio_id, universe_id=universe_id,
                                                window_days=window_days, snapshot_id=snapshot_id))
        full = _j(res)
        art = E.spill_json(rid, "shadow_drift", full)
        m = res.authority_manifest
        tail = lambda xs: xs[-5:]
        summary = {
            "shadow_portfolio_id": res.shadow_portfolio_id, "risk_state": res.risk_state,
            "median_is_delta_bps": res.median_is_delta_bps, "total_is_bps": res.total_is_bps,
            "fill_rate_pct": res.fill_rate_pct,
            "last_dates": tail(res.dates), "gap_bps_tail": tail(res.gap_bps),
            "liquidity_bps_tail": tail(res.liquidity_bps), "limit_queue_bps_tail": tail(res.limit_queue_bps),
            "factor_decay": [{"id": f.factor_id, "ic60": f.rank_ic_60d, "pct": f.hist_percentile,
                              "crowding": f.crowding_index, "status": f.status} for f in res.factor_decay[:8]],
            "authority": {"manifest_id": m.manifest_id, "mode": m.execution_mode, "broker_connected": m.broker_connected,
                          "risk_passed": m.risk_passed, "sha256": m.sha256, "signals": len(m.signals),
                          "blocked": sum(1 for s in m.signals if s.risk_check == "BLOCKED")},
            "fact_fingerprint": res.fact_fingerprint,
        }
        prev = [{"sym": s.symbol, "side": s.side, "dw_pct": s.delta_weight_pct, "shares": s.est_shares, "risk": s.risk_check}
                for s in m.signals[:10]]
        status = "ok" if res.risk_state in {"ok"} else "degraded"
        return (E.ok_envelope(rid, snapshot_id=res.snapshot_id, as_of=_as_of_of(res.snapshot_id), summary=summary,
                              preview=prev, artifact=art, status=status), full)

    return run_tool("quant_shadow_drift_check", impl)
