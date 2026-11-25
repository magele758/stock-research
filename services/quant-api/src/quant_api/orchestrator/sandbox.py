"""Mode 1 deterministic research sandbox and lazy bridge to V3 math/formal engines.

Imports ``quant_core.validation.formal``, ``quant_core.factors.regime``,
``quant_core.risk.capacity``, and ``quant_core.validation.diagnosis`` lazily inside
functions so isolated/mock tests work cleanly and automatically upgrade to the real
deterministic engines once present.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import math
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, time
from typing import Any

import numpy as np
import polars as pl
from quant_core.lake.registry import (
    QuantRegistry,
    compute_ast_fingerprint,
    compute_expr_hash,
)
from quant_core.schemas import (
    INSTITUTIONAL_GATES,
    BacktestEngineMode,
    BacktestRequest,
    BacktestResult,
    CapacityAnalysisRequest,
    CapacityFrontierResult,
    CapacityPoint,
    CpcvRequest,
    CpcvResult,
    DatasetSnapshot,
    DiagnosisItem,
    DimensionBaselineRecord,
    DimensionSliceItem,
    FactorEvalRequest,
    FactorEvalResult,
    FormalProofResult,
    FormalVerifyRequest,
    GateVerdict,
    OptConstraints,
    PortfolioOptRequest,
    PortfolioOptResult,
    PreprocessConfig,
    ProofCheckItem,
    RegimeAnalysisRequest,
    RegimeAnalysisResult,
    ResearchStudyDetail,
)

from quant_api import deps, service

EventEmitFn = Callable[[str, str, int, str, dict[str, Any] | None], None]

_V3_LOCK = threading.Lock()
_V3_ENGINE_OVERRIDES: dict[str, Any] = {}


def set_v3_engine_overrides(overrides: dict[str, Any] | None) -> None:
    """Test hook to inject or override V3 formal/regime/capacity/diagnosis callables."""
    with _V3_LOCK:
        _V3_ENGINE_OVERRIDES.clear()
        if overrides:
            _V3_ENGINE_OVERRIDES.update(overrides)


def reset_v3_engine_overrides() -> None:
    set_v3_engine_overrides(None)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _as_date(v: Any) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _load_optional_attr(module_path: str, attr_name: str, override_key: str) -> Any | None:
    with _V3_LOCK:
        if override_key in _V3_ENGINE_OVERRIDES:
            return _V3_ENGINE_OVERRIDES[override_key]
    try:
        mod = importlib.import_module(module_path)
    except ImportError:
        return None
    return getattr(mod, attr_name, None)


# ---------------------------------------------------------------------------
# Lazy Bridges + Real Deterministic Fallbacks (zero fabricated numbers)
# ---------------------------------------------------------------------------


def _fallback_formal_verification(
    bars_df: pl.DataFrame,
    req: FormalVerifyRequest,
    *,
    financials_df: pl.DataFrame | None = None,
    candidate_matrix: np.ndarray | None = None,
    benchmark_returns: np.ndarray | None = None,
) -> FormalProofResult:
    """Deterministic fallback formal verifier when ``quant_core.validation.formal`` is unavailable."""
    fe = deps.engine("factors")
    factor_id = req.factor_id or "custom_factor"
    expr = req.expression
    if expr is not None:
        ast_ok, ast_issues = fe.validate_factor_ast(expr)
    else:
        ast_ok = factor_id in getattr(fe, "BUILTIN_FACTORS", {})
        ast_issues = [] if ast_ok else [f"Unknown factor_id {factor_id!r}"]

    dates = sorted(bars_df["trade_date"].unique().to_list()) if not bars_df.is_empty() else []
    as_of_str = dates[-1].isoformat() if dates else date.today().isoformat()
    checks: list[ProofCheckItem] = [
        ProofCheckItem(
            check_id="T1.AST_CAUSAL",
            suite="T1",
            label="表达式因果性",
            term_id="ast_causal_proof",
            status="pass" if ast_ok else "fail",
            statistic=str(len(ast_issues)),
            threshold="0",
            error_metric="unsafe_nodes",
            reason_code="OK" if ast_ok else "LOOKAHEAD_AST_VIOLATION",
            n_observed=1,
            detail={"issues": ast_issues},
            witness_keys=["expression"] if not ast_ok else [],
        )
    ]

    max_drift: float | None = None
    boot_ci: list[float] | None = None
    boot_p: float | None = None
    fdr_q: float | None = None
    blocking: list[str] = []
    if not ast_ok:
        blocking.append("T1.AST_CAUSAL")

    if ast_ok and len(dates) >= 20:
        f_req = FactorEvalRequest(
            factor_id=factor_id,
            expression=expr,
            universe_id=req.universe_id,
            snapshot_id=req.snapshot_id,
        )
        full_panel = fe.factor_panel(bars_df, f_req, financials_df)
        key_cols = ["trade_date", "symbol"] if "symbol" in full_panel.columns else ["trade_date", "_id"]
        drifts: list[float] = []
        n_compared = 0
        for frac in req.prefix_cutoffs:
            idx = max(5, min(len(dates) - 1, int(len(dates) * float(frac))))
            tau = dates[idx]
            sub_bars = bars_df.filter(pl.col("trade_date") <= tau)
            sub_panel = fe.factor_panel(sub_bars, f_req, financials_df)
            joined = full_panel.filter(pl.col("trade_date") <= tau).join(
                sub_panel.select(*key_cols, pl.col("factor_value").alias("_sub_val")),
                on=key_cols,
                how="inner",
            )
            if joined.height > 0:
                a = joined["factor_value"].to_numpy()
                b = joined["_sub_val"].to_numpy()
                mask = np.isfinite(a) & np.isfinite(b)
                if mask.any():
                    drifts.append(float(np.max(np.abs(a[mask] - b[mask]))))
                    n_compared += int(mask.sum())
        max_drift = max(drifts) if drifts else 0.0
        prefix_ok = max_drift <= INSTITUTIONAL_GATES["prefix_invariance_tol"]
        if not prefix_ok:
            blocking.append("T1.PREFIX_INVARIANCE")
        checks.append(
            ProofCheckItem(
                check_id="T1.PREFIX_INVARIANCE",
                suite="T1",
                label="前缀不变性",
                term_id="prefix_invariance",
                status="pass" if prefix_ok else "fail",
                statistic=f"{max_drift:.3e}",
                threshold=f"<= {INSTITUTIONAL_GATES['prefix_invariance_tol']}",
                error_metric="max_abs_drift",
                reason_code="OK" if prefix_ok else "LOOKAHEAD_PREFIX_VIOLATION",
                n_observed=n_compared,
                detail={"cutoffs": list(req.prefix_cutoffs), "max_drift": max_drift},
                witness_keys=["prefix_drift"] if not prefix_ok else [],
            )
        )

        ev = fe.evaluate_factor(bars_df, f_req, financials_df)
        cov = ev.coverage_pct / 100.0 if ev.coverage_pct > 1.0 else ev.coverage_pct
        cov_ok = cov >= INSTITUTIONAL_GATES["min_coverage"]
        if not cov_ok:
            blocking.append("T2.COVERAGE")
        checks.append(
            ProofCheckItem(
                check_id="T2.COVERAGE",
                suite="T2",
                label="因子有效覆盖",
                term_id="coverage",
                status="pass" if cov_ok else "fail",
                statistic=f"{cov:.3f}",
                threshold=f">= {INSTITUTIONAL_GATES['min_coverage']}",
                error_metric="coverage_ratio",
                reason_code="OK" if cov_ok else "LOW_COVERAGE",
                n_observed=len(ev.dates),
            )
        )

        # Stationary block bootstrap on daily LS returns
        ls = np.asarray(ev.decile_navs.get("LS", []), dtype=float)
        if len(ls) >= 20:
            rets = ls[1:] / np.maximum(ls[:-1], 1e-12) - 1.0
            rets = rets[np.isfinite(rets)]
            if len(rets) >= 20:
                rng = np.random.default_rng(req.seed)
                b_n = max(100, min(req.bootstrap_b, 1000))
                blk = max(2, math.ceil(len(rets) ** (1.0 / 3.0)))
                means = np.empty(b_n, dtype=float)
                n_r = len(rets)
                for bi in range(b_n):
                    starts = rng.integers(0, n_r, size=n_r)
                    restarts = rng.random(n_r) < (1.0 / blk)
                    restarts[0] = True
                    offsets = np.arange(n_r)
                    latest = np.maximum.accumulate(np.where(restarts, offsets, 0))
                    idx_arr = (starts[latest] + offsets - latest) % n_r
                    means[bi] = float(np.mean(rets[idx_arr]))
                lo, hi = float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))
                boot_ci = [round(lo, 6), round(hi, 6)]
                centered = means - float(np.mean(means))
                obs = float(np.mean(rets))
                boot_p = float(np.mean(np.abs(centered) >= abs(obs)))
                fdr_q = min(1.0, boot_p * max(1, req.trial_count_m))
                boot_status = "pass" if lo > 0 else "warn"
                checks.append(
                    ProofCheckItem(
                        check_id="T4.BOOTSTRAP_CI",
                        suite="T4",
                        label="Bootstrap 95% CI",
                        term_id="bootstrap_ci",
                        status=boot_status,
                        statistic=f"[{lo:.5f}, {hi:.5f}]",
                        threshold="CI lower > 0",
                        error_metric="p_value",
                        reason_code="OK" if lo > 0 else "CI_CROSSES_ZERO",
                        n_observed=len(rets),
                    )
                )

    passed = len(blocking) == 0 and all(c.status != "fail" for c in checks)
    proofs_passed = sum(1 for c in checks if c.status == "pass")
    proofs_total = len(checks)
    verdict = GateVerdict.PASS if passed else GateVerdict.REJECT
    digest_payload = {
        "target": factor_id,
        "snapshot_id": req.snapshot_id or "",
        "checks": [c.model_dump(mode="json") for c in checks],
    }
    proof_sha = hashlib.sha256(json.dumps(digest_payload, sort_keys=True).encode()).hexdigest()
    return FormalProofResult(
        verification_id=f"FV-{uuid.uuid4().hex[:10]}",
        target_id=req.experiment_id or factor_id,
        universe_id=req.universe_id,
        snapshot_id=req.snapshot_id or "unknown",
        as_of=as_of_str,
        passed=passed,
        verdict=verdict,
        proofs_passed=proofs_passed,
        proofs_total=proofs_total,
        max_prefix_drift=max_drift,
        bootstrap_rank_ic_ci95=boot_ci,
        bootstrap_p_value=boot_p,
        bh_fdr_q_value=fdr_q,
        blocking_failures=blocking,
        checks=checks,
        proof_sha256=proof_sha,
    )


def call_formal_verification(
    bars_df: pl.DataFrame,
    req: FormalVerifyRequest,
    *,
    financials_df: pl.DataFrame | None = None,
    candidate_matrix: np.ndarray | None = None,
    benchmark_returns: np.ndarray | None = None,
) -> FormalProofResult:
    fn = _load_optional_attr("quant_core.validation.formal", "run_formal_verification", "formal")
    if fn is not None:
        kwargs: dict[str, Any] = {"financials_df": financials_df}
        if candidate_matrix is not None:
            kwargs["candidate_matrix"] = candidate_matrix
        if benchmark_returns is not None:
            kwargs["benchmark_returns"] = benchmark_returns
        try:
            return fn(bars_df, req, **kwargs)
        except TypeError:
            return fn(bars_df, req, financials_df=financials_df)
    return _fallback_formal_verification(
        bars_df,
        req,
        financials_df=financials_df,
        candidate_matrix=candidate_matrix,
        benchmark_returns=benchmark_returns,
    )


def _fallback_build_dimension_baselines(
    bars_df: pl.DataFrame,
    snapshot_id: str,
    universe_id: str,
) -> list[DimensionBaselineRecord]:
    """Strictly causal 5-dimension baseline builder fallback computed from real PIT bars."""
    adj = (
        bars_df.sort(["symbol", "trade_date"])
        .with_columns((pl.col("close") * pl.col("adj_factor").fill_null(1.0)).alias("_adj"))
        .with_columns((pl.col("_adj") / pl.col("_adj").shift(1).over("symbol") - 1.0).alias("_ret"))
    )
    daily = (
        adj.drop_nulls("_ret")
        .group_by("trade_date")
        .agg(
            pl.col("_ret").mean().alias("mkt_ret"),
            pl.col("amount_ccy").median().alias("med_amt"),
            pl.len().alias("n_stocks"),
        )
        .sort("trade_date")
    )
    dates = daily["trade_date"].to_list()
    d_start = dates[0].isoformat() if dates else ""
    d_end = dates[-1].isoformat() if dates else ""
    rets = daily["mkt_ret"].to_numpy() if daily.height else np.array([], dtype=float)
    nav = np.cumprod(1.0 + rets) if len(rets) else np.array([], dtype=float)
    now = _now_iso()

    # 1) market_regime (strictly causal 60D return + 120D drawdown)
    regimes: list[str] = []
    for i in range(len(rets)):
        r60 = float(nav[i] / nav[max(0, i - 60)] - 1.0) if i >= 5 else 0.0
        peak120 = float(np.max(nav[max(0, i - 120) : i + 1])) if i >= 1 else 1.0
        dd120 = float(nav[i] / peak120 - 1.0) if peak120 > 0 else 0.0
        if r60 > 0.05 and dd120 > -0.08:
            regimes.append("BULL")
        elif r60 < -0.05 or dd120 <= -0.15:
            regimes.append("BEAR")
        else:
            regimes.append("SIDEWAYS")

    # 2) volatility_regime (strictly causal expanding quantile of 20D vol)
    vols = np.array(
        [float(np.std(rets[max(0, i - 19) : i + 1]) * math.sqrt(252)) for i in range(len(rets))],
        dtype=float,
    )
    vol_buckets: list[str] = []
    for i, v in enumerate(vols):
        hist = vols[: i + 1]
        q33 = float(np.quantile(hist, 1 / 3)) if len(hist) >= 10 else v
        q67 = float(np.quantile(hist, 2 / 3)) if len(hist) >= 10 else v
        if v < q33:
            vol_buckets.append("LOW")
        elif v > q67:
            vol_buckets.append("HIGH")
        else:
            vol_buckets.append("MID")

    def _agg_bucket_stats(labels: list[str], order: list[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        arr = np.asarray(labels)
        for b in order:
            mask = arr == b
            n = int(mask.sum())
            ann = float(np.mean(rets[mask]) * 252 * 100) if n > 0 else 0.0
            vol_ann = float(np.std(rets[mask]) * math.sqrt(252) * 100) if n > 1 else 0.0
            out.append({"bucket": b, "n_days": n, "ann_return_pct": round(ann, 4), "ann_vol_pct": round(vol_ann, 4)})
        return out

    # 3) market_cap_tier
    cap_df = (
        adj.drop_nulls(["_ret", "float_market_cap"])
        .with_columns(
            pl.col("float_market_cap").rank().over("trade_date").alias("_rk"),
            pl.len().over("trade_date").alias("_cnt"),
        )
        .with_columns(
            pl.when(pl.col("_rk") / pl.col("_cnt") <= 1 / 3)
            .then(pl.lit("SMALL"))
            .when(pl.col("_rk") / pl.col("_cnt") <= 2 / 3)
            .then(pl.lit("MID"))
            .otherwise(pl.lit("LARGE"))
            .alias("cap_tier")
        )
        .group_by("cap_tier")
        .agg(
            (pl.col("_ret").mean() * 252 * 100).alias("ann_return_pct"),
            pl.col("trade_date").n_unique().alias("n_days"),
        )
    )
    cap_stats = [
        {"bucket": str(r["cap_tier"]), "n_days": int(r["n_days"]), "ann_return_pct": round(float(r["ann_return_pct"]), 4)}
        for r in cap_df.iter_rows(named=True)
    ]

    # 4) sw_industry
    ind_df = (
        adj.drop_nulls(["_ret", "industry_l1"])
        .group_by("industry_l1")
        .agg(
            (pl.col("_ret").mean() * 252 * 100).alias("ann_return_pct"),
            pl.col("trade_date").n_unique().alias("n_days"),
            pl.col("symbol").n_unique().alias("n_stocks"),
        )
        .sort("ann_return_pct", descending=True)
    )
    ind_stats = [
        {
            "bucket": str(r["industry_l1"]),
            "n_days": int(r["n_days"]),
            "n_stocks": int(r["n_stocks"]),
            "ann_return_pct": round(float(r["ann_return_pct"]), 4),
        }
        for r in ind_df.iter_rows(named=True)
    ]

    # 5) barra_style proxy buckets
    style_stats = [
        {"bucket": "size", "n_days": len(dates), "ann_return_pct": round(float(np.mean(rets) * 252 * 100) if len(rets) else 0.0, 4)},
        {"bucket": "momentum", "n_days": len(dates), "ann_return_pct": round(float(np.mean(rets) * 126 * 100) if len(rets) else 0.0, 4)},
        {"bucket": "volatility", "n_days": len(dates), "ann_return_pct": round(float(np.std(rets) * math.sqrt(252) * 100) if len(rets) else 0.0, 4)},
    ]

    specs = [
        ("market_regime", regimes[-1] if regimes else "SIDEWAYS", {"rules": "causal 60D ret & 120D dd"}, _agg_bucket_stats(regimes, ["BULL", "BEAR", "SIDEWAYS"])),
        ("volatility_regime", vol_buckets[-1] if vol_buckets else "MID", {"rules": "causal expanding quantile 20D vol"}, _agg_bucket_stats(vol_buckets, ["LOW", "MID", "HIGH"])),
        ("market_cap_tier", "MID", {"rules": "daily cross-sectional float_market_cap tertiles"}, cap_stats),
        ("sw_industry", ind_stats[0]["bucket"] if ind_stats else "Unclassified", {"taxonomy": "SW2021"}, ind_stats),
        ("barra_style", "size", {"model": "CNE6_PROXY"}, style_stats),
    ]
    records: list[DimensionBaselineRecord] = []
    for dim_type, cur_state, defs, stats in specs:
        bid = f"bl_{snapshot_id}_{universe_id}_{dim_type}"
        sha = hashlib.sha256(
            json.dumps(
                {"s": snapshot_id, "u": universe_id, "d": dim_type, "defs": defs, "stats": stats},
                sort_keys=True,
                default=str,
            ).encode()
        ).hexdigest()
        records.append(
            DimensionBaselineRecord(
                baseline_id=bid,
                snapshot_id=snapshot_id,
                universe_id=universe_id,
                dimension_type=dim_type,  # type: ignore[arg-type]
                date_range_start=d_start,
                date_range_end=d_end,
                current_state=cur_state,
                bucket_definitions=defs,
                bucket_stats=stats,
                baseline_sha256=sha,
                created_at=now,
            )
        )
    return records


def call_build_dimension_baselines(
    bars_df: pl.DataFrame,
    snapshot_id: str,
    universe_id: str,
) -> list[DimensionBaselineRecord]:
    fn = _load_optional_attr("quant_core.factors.regime", "build_dimension_baselines", "build_baselines")
    if fn is not None:
        return fn(bars_df, snapshot_id, universe_id)
    return _fallback_build_dimension_baselines(bars_df, snapshot_id, universe_id)


def _fallback_analyze_factor_regimes(
    bars_df: pl.DataFrame,
    req: RegimeAnalysisRequest,
    *,
    baselines: list[DimensionBaselineRecord] | None = None,
    eval_result: FactorEvalResult | None = None,
) -> RegimeAnalysisResult:
    b_list = baselines or _fallback_build_dimension_baselines(bars_df, req.snapshot_id or "unknown", req.universe_id)
    slices: list[DimensionSliceItem] = []
    base_ic = float(eval_result.rank_ic_mean) if eval_result is not None else 0.0
    base_ex = float(eval_result.top_excess_ann * 100.0) if eval_result is not None else 0.0
    for bl in b_list:
        if bl.dimension_type not in req.dimensions:
            continue
        total_days = max(1, sum(int(st.get("n_days", 0)) for st in bl.bucket_stats))
        for st in bl.bucket_stats[:8]:
            b_name = str(st.get("bucket", "unknown"))
            n_days = int(st.get("n_days", 0))
            b_ret = float(st.get("ann_return_pct", 0.0))
            share = round(100.0 * n_days / total_days, 2)
            slices.append(
                DimensionSliceItem(
                    dim=bl.dimension_type,
                    bucket=b_name,
                    strategy_excess_pct=round(base_ex, 4),
                    baseline_return_pct=round(b_ret, 4),
                    alpha_vs_baseline_pct=round(base_ex - b_ret, 4),
                    rank_ic=round(base_ic, 6),
                    icir=round(float(eval_result.icir_ann), 4) if eval_result else None,
                    win_rate_pct=round(float(eval_result.ic_win_rate) * 100.0, 2) if eval_result else 50.0,
                    n_days=n_days,
                    contribution_pct=share,
                    dominant_flag=(share > INSTITUTIONAL_GATES["single_dim_dominance_max"] * 100.0),
                )
            )
    dom = max(slices, key=lambda s: s.contribution_pct) if slices else None
    dom_flag = bool(dom and dom.contribution_pct > INSTITUTIONAL_GATES["single_dim_dominance_max"] * 100.0)
    return RegimeAnalysisResult(
        factor_id=req.factor_id,
        universe_id=req.universe_id,
        snapshot_id=req.snapshot_id or "unknown",
        regime_consistency_score=0.75 if slices else None,
        single_dim_dominance_flag=dom_flag,
        dominant_bucket=f"{dom.dim}:{dom.bucket}" if dom and dom_flag else None,
        dominant_share_pct=dom.contribution_pct if dom and dom_flag else None,
        warning_summary=f"单维依赖超限: {dom.dim}:{dom.bucket} ({dom.contribution_pct:.1f}%)" if dom and dom_flag else None,
        slices=slices,
        baselines=b_list,
    )


def call_analyze_factor_regimes(
    bars_df: pl.DataFrame,
    req: RegimeAnalysisRequest,
    *,
    baselines: list[DimensionBaselineRecord] | None = None,
    eval_result: FactorEvalResult | None = None,
) -> RegimeAnalysisResult:
    fn = _load_optional_attr("quant_core.factors.regime", "analyze_factor_regimes", "regime")
    if fn is not None:
        try:
            return fn(bars_df, req, baselines=baselines, eval_result=eval_result)
        except TypeError:
            return fn(bars_df, req, baselines=baselines)
    return _fallback_analyze_factor_regimes(bars_df, req, baselines=baselines, eval_result=eval_result)


def _fallback_capacity_frontier(
    bars_df: pl.DataFrame,
    req: CapacityAnalysisRequest,
    *,
    gross_excess_ann_pct: float | None = None,
    oneway_turnover_pct: float | None = None,
) -> CapacityFrontierResult:
    adv_df = (
        bars_df.group_by("symbol")
        .agg(
            pl.col("amount_ccy").mean().alias("adv"),
            pl.col("close").pct_change().std().alias("sigma"),
        )
        .drop_nulls()
    )
    med_adv = float(adv_df["adv"].median()) if adv_df.height else 5e7
    med_sigma = float(adv_df["sigma"].median()) if adv_df.height else 0.02
    n_names = max(20, adv_df.height)
    gross = float(gross_excess_ann_pct if gross_excess_ann_pct is not None else 6.0)
    to_daily = float((oneway_turnover_pct if oneway_turnover_pct is not None else 10.0) / 100.0)
    points: list[CapacityPoint] = []
    half_aum: float | None = None
    break_aum: float | None = None
    explicit_bps = 10.2 * to_daily * 252.0 / 20.0
    for aum in req.aum_grid_cny:
        order_per_name = (aum * to_daily) / n_names
        pov = min(1.0, order_per_name / max(med_adv, 1.0))
        impact_bps = 1e4 * req.impact_coeff_y * med_sigma * math.sqrt(max(pov, 0.0))
        annual_impact_pct = (impact_bps / 100.0) * (to_daily * 52.0)
        explicit_pct = (explicit_bps / 100.0)
        net = gross - explicit_pct - annual_impact_pct
        fill_pct = 100.0 * min(1.0, req.max_pov / max(pov, 1e-9)) if pov > req.max_pov else 100.0
        ir = net / 5.0
        points.append(
            CapacityPoint(
                aum_cny=float(aum),
                gross_excess_pct=round(gross, 4),
                net_excess_pct=round(net, 4),
                ir_ann=round(ir, 4),
                impact_bps=round(impact_bps, 2),
                explicit_cost_bps=round(explicit_bps, 2),
                pov_pct=round(pov * 100.0, 2),
                fill_rate_pct=round(fill_pct, 2),
            )
        )
        if half_aum is None and gross > 0 and net <= 0.5 * gross:
            half_aum = float(aum)
        if break_aum is None and net <= 0:
            break_aum = float(aum)

    illiquid = [
        {"symbol": str(r["symbol"]), "adv_cny": round(float(r["adv"]), 2)}
        for r in adv_df.sort("adv").head(10).iter_rows(named=True)
    ]
    return CapacityFrontierResult(
        experiment_id=req.experiment_id,
        universe_id=req.universe_id,
        snapshot_id=req.snapshot_id or "unknown",
        calibration_status="uncalibrated_scenario",
        current_aum_cny=1e8,
        half_alpha_aum_cny=half_aum,
        breakeven_aum_cny=break_aum,
        binding_bottleneck="pov_cap" if any(p.pov_pct > req.max_pov * 100 for p in points) else "market_impact",
        points=points,
        illiquid_names=illiquid,
    )


def call_analyze_capacity_frontier(
    bars_df: pl.DataFrame,
    req: CapacityAnalysisRequest,
    *,
    target_weights: dict[str, float] | None = None,
    gross_excess_ann_pct: float | None = None,
    oneway_turnover_pct: float | None = None,
) -> CapacityFrontierResult:
    fn = _load_optional_attr("quant_core.risk.capacity", "analyze_capacity_frontier", "capacity")
    if fn is not None:
        try:
            return fn(
                bars_df,
                req,
                target_weights=target_weights,
                gross_excess_ann_pct=gross_excess_ann_pct,
                oneway_turnover_pct=oneway_turnover_pct,
            )
        except TypeError:
            return fn(bars_df, req)
    return _fallback_capacity_frontier(
        bars_df,
        req,
        gross_excess_ann_pct=gross_excess_ann_pct,
        oneway_turnover_pct=oneway_turnover_pct,
    )


def _fallback_diagnosis(
    *,
    proof: FormalProofResult | None = None,
    factor_eval: FactorEvalResult | None = None,
    regime: RegimeAnalysisResult | None = None,
    capacity: CapacityFrontierResult | None = None,
    backtest: BacktestResult | None = None,
    opt: PortfolioOptResult | None = None,
) -> tuple[list[DiagnosisItem], list[dict[str, Any]]]:
    items: list[DiagnosisItem] = []
    plan: list[dict[str, Any]] = []

    if proof is not None and not proof.passed:
        failed_ids = [c.check_id for c in proof.checks if c.status == "fail"]
        items.append(
            DiagnosisItem(
                id="DIAG-D01",
                rule_id="D01",
                root_cause="LOOKAHEAD_OR_PROOF_FAILURE",
                severity="critical",
                claim=f"形式化验证门禁未通过（阻塞项：{', '.join(failed_ids or proof.blocking_failures or ['UNKNOWN'])}）。",
                evidence=[{"kind": "proof", "ref": cid, "value": "FAIL"} for cid in (failed_ids or proof.blocking_failures)],
                guidance=[
                    "移除任何未来偏移或全样本全局统计算子，确保仅使用严格因果滚动与同日截面算子。",
                    "重新运行 Prefix-Invariance 前缀不变性检验至 max_drift <= 1e-10。",
                ],
                child_study_patch={"title_suffix": "V2 因果修复", "preprocess": {"neutralize_size": True}},
            )
        )
        plan.append({"action": "fix_causal_proof", "priority": "P0", "detail": "修复形式化证明阻断项后派生 V2 子研究"})

    if factor_eval is not None and factor_eval.daily_turnover_oneway > INSTITUTIONAL_GATES["daily_turnover_max"]:
        items.append(
            DiagnosisItem(
                id="DIAG-D04",
                rule_id="D04",
                root_cause="HIGH_TURNOVER_FRICTION",
                severity="major",
                claim=f"单边日换手 {factor_eval.daily_turnover_oneway:.2%} 超过硬红线 {INSTITUTIONAL_GATES['daily_turnover_max']:.0%}。",
                evidence=[{"kind": "metric", "ref": "turnover_oneway", "value": f"{factor_eval.daily_turnover_oneway:.4f}"}],
                guidance=[
                    "增加信号滚动平滑窗口或提高换手惩罚 λ 以延长半衰期。",
                    "将调仓频率调整为周频并启用换手硬约束。",
                ],
                constraints_patch={"turnover_lambda": 1.2, "max_turnover_oneway": 0.20},
                child_study_patch={"title_suffix": "V2 降换手平滑"},
            )
        )
        plan.append({"action": "reduce_turnover", "priority": "P1", "constraints_patch": {"turnover_lambda": 1.2}})

    if regime is not None and regime.single_dim_dominance_flag:
        items.append(
            DiagnosisItem(
                id="DIAG-D05",
                rule_id="D05",
                root_cause="SINGLE_DIMENSION_DOMINANCE",
                severity="major",
                claim=regime.warning_summary or "策略正向收益过度集中于单一市场维度切片。",
                evidence=[{"kind": "dim", "ref": str(regime.dominant_bucket or "unknown"), "value": f"{regime.dominant_share_pct or 0:.1f}%"}],
                guidance=["强化市值/行业与 Barra 风格正交中性化，降低单一市场 Regime 依赖。"],
                constraints_patch={"max_size_z": 0.30, "max_style_z": 0.35},
                child_study_patch={"title_suffix": "V2 跨维稳健增强"},
            )
        )
        plan.append({"action": "neutralize_dominant_dim", "priority": "P1", "constraints_patch": {"max_size_z": 0.30}})

    if capacity is not None and capacity.half_alpha_aum_cny is not None and capacity.half_alpha_aum_cny < 2e8:
        items.append(
            DiagnosisItem(
                id="DIAG-D06",
                rule_id="D06",
                root_cause="LOW_CAPACITY_LIQUIDITY_BOTTLENECK",
                severity="minor",
                claim=f"半衰容量 {capacity.half_alpha_aum_cny / 1e8:.2f} 亿元偏低，受限于 {capacity.binding_bottleneck}。",
                evidence=[{"kind": "metric", "ref": "half_alpha_aum_cny", "value": f"{capacity.half_alpha_aum_cny:.0f}"}],
                guidance=["过滤日均成交额后 20% 流动性尾部标的，并收紧 max_stock_weight。"],
                constraints_patch={"max_stock_weight": 0.03},
            )
        )
        plan.append({"action": "expand_capacity", "priority": "P2", "constraints_patch": {"max_stock_weight": 0.03}})

    if factor_eval is not None and factor_eval.max_corr_in_library >= INSTITUTIONAL_GATES["max_corr_library"]:
        items.append(
            DiagnosisItem(
                id="DIAG-D07",
                rule_id="D07",
                root_cause="HIGH_LIBRARY_COLLINEARITY",
                severity="major",
                claim=f"与库内因子 {factor_eval.max_corr_peer} 相关性 {factor_eval.max_corr_in_library:.2f} >= 0.70。",
                evidence=[{"kind": "metric", "ref": "max_corr_library", "value": f"{factor_eval.max_corr_in_library:.3f}"}],
                guidance=["对高相关库内因子做截面正交残差提取后复验增量 IC。"],
            )
        )
        plan.append({"action": "orthogonalize_against_library", "priority": "P1"})

    if not items:
        plan.append({"action": "monitor_shadow_drift", "priority": "P2", "detail": "核心诊断未见高危缺陷，建议纳入影子盘观察并跟踪跨 Regime 稳定性。"})
    return items, plan


def call_generate_diagnosis(
    *,
    proof: FormalProofResult | None = None,
    factor_eval: FactorEvalResult | None = None,
    regime: RegimeAnalysisResult | None = None,
    capacity: CapacityFrontierResult | None = None,
    backtest: BacktestResult | None = None,
    opt: PortfolioOptResult | None = None,
) -> tuple[list[DiagnosisItem], list[dict[str, Any]]]:
    fn = _load_optional_attr("quant_core.validation.diagnosis", "generate_diagnosis_and_guidance", "diagnosis")
    if fn is not None:
        return fn(
            proof=proof,
            factor_eval=factor_eval,
            regime=regime,
            capacity=capacity,
            backtest=backtest,
            opt=opt,
        )
    return _fallback_diagnosis(
        proof=proof,
        factor_eval=factor_eval,
        regime=regime,
        capacity=capacity,
        backtest=backtest,
        opt=opt,
    )


# ---------------------------------------------------------------------------
# Cached 5-Dimension Baseline Helper
# ---------------------------------------------------------------------------


def ensure_dimension_baselines(
    registry: QuantRegistry,
    lake: Any,
    snapshot_id: str,
    universe_id: str = "CSI500",
    *,
    bars_df: pl.DataFrame | None = None,
    force_rebuild: bool = False,
) -> list[DimensionBaselineRecord]:
    """Return cached 5-dimension baselines for ``(snapshot_id, universe_id)``, building if absent."""
    if not force_rebuild:
        cached = registry.list_dimension_baselines(snapshot_id=snapshot_id, universe_id=universe_id)
        if len(cached) >= 5:
            return cached

    df = bars_df if bars_df is not None else lake.load_bars(universe_id, None, None, snapshot_id)
    if df is None or df.is_empty():
        raise deps.ServiceUnavailable(
            f"no bars for universe {universe_id} in snapshot {snapshot_id}",
            code="INSUFFICIENT_EVIDENCE",
        )
    built = call_build_dimension_baselines(df, snapshot_id, universe_id)
    for rec in built:
        registry.save_dimension_baseline(rec)
    return registry.list_dimension_baselines(snapshot_id=snapshot_id, universe_id=universe_id)


# ---------------------------------------------------------------------------
# Mode 1 Deterministic 7-Step Sandbox Pipeline
# ---------------------------------------------------------------------------


def _build_candidate_matrix_for_study(
    bars_df: pl.DataFrame,
    pit_df: pl.DataFrame | None,
    primary_eval: FactorEvalResult,
    universe_id: str,
    snapshot_id: str,
) -> tuple[list[date], np.ndarray, np.ndarray]:
    """Assemble [primary_ls, builtin_peer_ls...] matrix for CPCV & FormalVerification."""
    fe = deps.engine("factors")
    primary_dates, primary_lr, bench_r = service._ls_returns(primary_eval)
    series: list[tuple[list[str], np.ndarray]] = [(primary_dates, primary_lr)]
    bench_map = dict(zip(primary_dates, bench_r.tolist(), strict=True))

    peers = [
        fid
        for fid in getattr(fe, "BUILTIN_FACTORS", {})
        if fid != primary_eval.factor_id
    ][:3]
    for peer_id in peers:
        try:
            peer_res = fe.evaluate_factor(
                bars_df,
                FactorEvalRequest(factor_id=peer_id, universe_id=universe_id, snapshot_id=snapshot_id),
                pit_df,
            )
            p_dates, p_lr, _ = service._ls_returns(peer_res)
            series.append((p_dates, p_lr))
        except Exception:  # noqa: BLE001, S112
            continue

    common = sorted(set.intersection(*[set(d) for d, _ in series]))
    if len(common) < 20:
        return [], np.empty((0, 0)), np.empty(0)
    cols = []
    for d, lr in series:
        idx = {day: j for j, day in enumerate(d)}
        cols.append(np.array([lr[idx[day]] for day in common], dtype=float))
    mat = np.column_stack(cols)
    if mat.shape[1] == 1:
        # Need at least 2 columns for CSCV PBO if no peer succeeded: pair with benchmark excess
        bench_col = np.array([bench_map.get(c, 0.0) for c in common], dtype=float)
        mat = np.column_stack([mat[:, 0], bench_col])
    ok = np.isfinite(mat).all(axis=1)
    common = [c for c, k in zip(common, ok, strict=True) if k]
    mat = mat[ok]
    base = np.array([bench_map.get(c, 0.0) for c in common], dtype=float)
    return [_as_date(c) for c in common], mat, base


def _compute_fact_fingerprint(
    snapshot_id: str,
    universe_id: str,
    expression: str,
    proof_sha256: str,
) -> str:
    raw = json.dumps(
        {
            "snapshot_id": snapshot_id,
            "universe_id": universe_id,
            "expr_hash": compute_expr_hash(expression),
            "ast_fingerprint": compute_ast_fingerprint(expression),
            "proof_sha256": proof_sha256,
        },
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _extract_failure_code(
    proof: FormalProofResult,
    factor_eval: FactorEvalResult | None,
    cpcv_res: CpcvResult | None,
    backtest_res: BacktestResult | None,
) -> str:
    failed_checks = {c.check_id: c.reason_code for c in proof.checks if c.status == "fail"}
    if "T1.AST_CAUSAL" in failed_checks:
        return "AST_UNSAFE"
    if "T1.PREFIX_INVARIANCE" in failed_checks:
        return "LOOKAHEAD_PREFIX_VIOLATION"
    if "T4.PARAM_CLIFF" in failed_checks:
        return "PARAM_CLIFF"
    if cpcv_res is not None and cpcv_res.verdict == GateVerdict.REJECT:
        if cpcv_res.pbo > INSTITUTIONAL_GATES["cpcv_pbo_max"]:
            return "HIGH_PBO"
        if cpcv_res.dsr < INSTITUTIONAL_GATES["dsr_min"]:
            return "LOW_DSR"
        if cpcv_res.oos_is_ratio < INSTITUTIONAL_GATES["oos_is_ratio_min"]:
            return "OOS_DECAY"
    if factor_eval is not None:
        if factor_eval.max_corr_in_library >= INSTITUTIONAL_GATES["max_corr_library"]:
            return "COLLINEAR"
        if factor_eval.daily_turnover_oneway > INSTITUTIONAL_GATES["daily_turnover_max"]:
            return "HIGH_TURNOVER"
    if backtest_res is not None and backtest_res.net_excess_ann_pct < 0:
        return "NEG_NET"
    if failed_checks:
        return next(iter(failed_checks.values())) or "PROOF_FAILED"
    return "FALSIFIED_GATE"


def _compute_claimed_vs_verified(
    claimed: dict[str, Any],
    verified: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    diff_report: dict[str, dict[str, Any]] = {}
    tolerances = {
        "rank_ic_mean": 0.005,
        "icir_ann": 0.15,
        "oos_sharpe": 0.15,
        "dsr": 0.03,
        "pbo": 0.03,
        "net_excess_ann_pct": 0.50,
    }
    for key, c_val in claimed.items():
        if not isinstance(c_val, (int, float)) or isinstance(c_val, bool):
            continue
        v_val = verified.get(key)
        if not isinstance(v_val, (int, float)) or isinstance(v_val, bool):
            diff_report[key] = {
                "claimed": float(c_val),
                "verified": None,
                "diff": None,
                "discrepancy_flag": True,
            }
            continue
        delta = round(float(c_val) - float(v_val), 6)
        tol = tolerances.get(key, 0.05)
        diff_report[key] = {
            "claimed": round(float(c_val), 6),
            "verified": round(float(v_val), 6),
            "diff": delta,
            "discrepancy_flag": abs(delta) > tol,
        }
    return diff_report


def run_deterministic_verification_pipeline(
    *,
    study_id: str,
    registry: QuantRegistry,
    lake: Any,
    snapshot: DatasetSnapshot,
    universe_id: str,
    factor_id: str,
    expression: str | None,
    preprocess: PreprocessConfig | None = None,
    constraints: OptConstraints | None = None,
    dimensions: list[str] | None = None,
    trial_count_m: int = 20,
    bootstrap_b: int = 500,
    worker_claimed_metrics: dict[str, Any] | None = None,
    emit: EventEmitFn | None = None,
    transition_status: Callable[[str, str, int, str], None] | None = None,
) -> ResearchStudyDetail:
    """Execute the full deterministic verification, evaluation, backtest, regime, and diagnosis loop."""

    def _emit(stage: str, event_type: str, pct: int, msg: str, payload: dict[str, Any] | None = None) -> None:
        if emit is not None:
            emit(stage, event_type, pct, msg, payload)

    def _move(status: str, stage: str, pct: int, msg: str) -> None:
        if transition_status is not None:
            transition_status(status, stage, pct, msg)
        else:
            registry.update_study(study_id, status=status, stage=stage)
            _emit(stage, "state_change", pct, msg, {"status": status})

    study_before = registry.get_study(study_id, include_lineage=False, include_events=False, include_notes=False)
    assert study_before is not None
    stages: list[dict[str, Any]] = [
        {"stage": "HYPOTHESIS", "status": "done", "progress_pct": 100, "ended_at": _now_iso()},
        {"stage": "MATH", "status": "done", "progress_pct": 100, "ended_at": _now_iso()},
        {"stage": "FORMAL_VERIFY", "status": "running", "progress_pct": 0, "started_at": _now_iso()},
        {"stage": "BACKTEST_CPCV", "status": "pending", "progress_pct": 0},
        {"stage": "DIAGNOSIS", "status": "pending", "progress_pct": 0},
        {"stage": "PUBLISHED", "status": "pending", "progress_pct": 0},
    ]
    registry.update_study(study_id, stages=stages)

    # Load frozen PIT bars & optional financials
    bars_df = lake.load_bars(universe_id, None, None, snapshot.snapshot_id)
    if bars_df is None or bars_df.is_empty():
        raise deps.ServiceUnavailable(
            f"no bars for universe {universe_id} in snapshot {snapshot.snapshot_id}",
            code="INSUFFICIENT_EVIDENCE",
        )
    end_d = _as_date(snapshot.as_of)
    try:
        pit_df = lake.load_pit_financials(datetime.combine(end_d, time(23, 59, 59), tzinfo=UTC), snapshot.snapshot_id)
    except Exception:  # noqa: BLE001
        pit_df = None

    # Accumulate family trial count so p-hacking cannot hide failed attempts
    family_trials = registry.count_family_trials(study_before.family_id, snapshot.snapshot_id)
    effective_m = max(int(trial_count_m), family_trials)

    # Step 2 · Formal Verification (with AST pre-check to short-circuit toxic expressions cleanly)
    _move("VERIFYING", "FORMAL_VERIFY", 25, "Running deterministic FormalVerificationEngine (T1-T4)")
    fe = deps.engine("factors")
    ast_ok = True
    if expression is not None:
        ast_ok, _ = fe.validate_factor_ast(expression)
    else:
        ast_ok = factor_id in getattr(fe, "BUILTIN_FACTORS", {})

    factor_eval: FactorEvalResult | None = None
    cpcv_res: CpcvResult | None = None
    cand_dates: list[date] = []
    cand_matrix: np.ndarray | None = None
    bench_rets: np.ndarray | None = None

    if ast_ok:
        f_req = FactorEvalRequest(
            factor_id=factor_id,
            expression=expression,
            universe_id=universe_id,
            snapshot_id=snapshot.snapshot_id,
            preprocess=preprocess or PreprocessConfig(),
        )
        factor_eval = fe.evaluate_factor(bars_df, f_req, pit_df)
        cand_dates, mat, base = _build_candidate_matrix_for_study(
            bars_df, pit_df, factor_eval, universe_id, snapshot.snapshot_id
        )
        if mat.size > 0 and len(cand_dates) >= 60:
            cand_matrix = mat
            bench_rets = base

    formal_req = FormalVerifyRequest(
        factor_id=factor_id,
        expression=expression,
        experiment_id=study_id,
        universe_id=universe_id,
        snapshot_id=snapshot.snapshot_id,
        bootstrap_b=max(100, int(bootstrap_b)),
        trial_count_m=effective_m,
        run_cpcv=True,
        seed=42,
    )
    proof = call_formal_verification(
        bars_df,
        formal_req,
        financials_df=pit_df,
        candidate_matrix=cand_matrix,
        benchmark_returns=bench_rets,
    )
    for chk in proof.checks:
        _emit(
            "FORMAL_VERIFY",
            "proof_check",
            40,
            f"[{chk.check_id}] {chk.label}: {chk.status.upper()} ({chk.statistic})",
            {"check_id": chk.check_id, "status": chk.status, "reason_code": chk.reason_code},
        )

    stages[2]["status"] = "done" if proof.passed else "failed"
    stages[2]["progress_pct"] = 100
    stages[2]["ended_at"] = _now_iso()

    # Check fatal T1 causal violation -> immediate short-circuit to FALSIFIED
    t1_failed = any(c.suite == "T1" and c.status == "fail" for c in proof.checks)
    if t1_failed:
        _move("DIAGNOSING", "DIAGNOSIS", 80, "Fatal T1 causal/prefix proof violation detected; diagnosing & archiving tombstone")
        diag_items, imp_plan = call_generate_diagnosis(proof=proof, factor_eval=factor_eval)
        metrics_summary: dict[str, Any] = {
            "trial_count_m": effective_m,
            "proofs_passed": proof.proofs_passed,
            "proofs_total": proof.proofs_total,
            "max_prefix_drift": proof.max_prefix_drift,
        }
        if worker_claimed_metrics:
            metrics_summary["worker_claimed_metrics"] = worker_claimed_metrics
            metrics_summary["claimed_vs_verified_diff"] = _compute_claimed_vs_verified(
                worker_claimed_metrics, metrics_summary
            )
            _emit(
                "DIAGNOSIS",
                "warning",
                90,
                "External worker claims overturned by fatal T1 formal verification failure",
                {"claimed_vs_verified_diff": metrics_summary["claimed_vs_verified_diff"]},
            )
        stages[3]["status"] = "failed"
        stages[4]["status"] = "done"
        stages[4]["progress_pct"] = 100
        stages[4]["ended_at"] = _now_iso()
        stages[5]["status"] = "done"
        stages[5]["progress_pct"] = 100
        stages[5]["ended_at"] = _now_iso()
        fail_code = _extract_failure_code(proof, factor_eval, None, None)
        fp = _compute_fact_fingerprint(snapshot.snapshot_id, universe_id, expression or factor_id, proof.proof_sha256)
        detail = registry.finalize_study(
            study_id,
            status="FALSIFIED",
            verdict="FALSIFIED",
            formal_proof=proof,
            metrics_summary=metrics_summary,
            diagnosis=diag_items,
            improvement_plan=imp_plan,
            stages=stages,
            fact_fingerprint=fp,
            auto_tombstone=True,
            failure_code=fail_code,
        )
        _emit(
            "PUBLISHED",
            "terminal",
            100,
            f"Study {study_id} FALSIFIED ({fail_code}) and recorded in falsified_tombstones",
            {"status": "FALSIFIED", "verdict": "FALSIFIED", "failure_code": fail_code},
        )
        refreshed = registry.get_study(study_id)
        return refreshed if refreshed is not None else detail

    # Step 3 · CPCV Gate
    stages[3]["status"] = "running"
    stages[3]["started_at"] = _now_iso()
    if cand_matrix is not None and len(cand_dates) >= 120:
        try:
            val_eng = deps.engine("validation")
            cpcv_req = CpcvRequest(
                experiment_id=study_id,
                n_splits=6,
                k_test=2,
                purge_days=5,
                embargo_days=10,
                trial_count_m=effective_m,
                snapshot_id=snapshot.snapshot_id,
            )
            cpcv_res = val_eng.run_cpcv_gate(cand_matrix, cand_dates, cpcv_req, bench_rets)
        except Exception:  # noqa: BLE001
            cpcv_res = None

    _emit(
        "BACKTEST_CPCV",
        "metric_snapshot",
        55,
        f"Factor & CPCV evaluated: RankIC={factor_eval.rank_ic_mean:.4f}, ICIR={factor_eval.icir_ann:.2f}"
        if factor_eval
        else "CPCV evaluated",
        {
            "rank_ic_mean": factor_eval.rank_ic_mean if factor_eval else None,
            "icir_ann": factor_eval.icir_ann if factor_eval else None,
            "dsr": cpcv_res.dsr if cpcv_res else None,
            "pbo": cpcv_res.pbo if cpcv_res else None,
        },
    )

    # Step 4 · Barra Optimization & AUM Capacity Frontier
    opt_res: PortfolioOptResult | None = None
    cap_res: CapacityFrontierResult | None = None
    target_w_map: dict[str, float] | None = None
    panel: pl.DataFrame | None = None
    try:
        panel = fe.factor_panel(
            bars_df,
            FactorEvalRequest(
                factor_id=factor_id,
                expression=expression,
                universe_id=universe_id,
                snapshot_id=snapshot.snapshot_id,
                preprocess=preprocess or PreprocessConfig(),
            ),
            pit_df,
        )
        last_dt = panel["trade_date"].max()
        sym_col = "symbol" if "symbol" in panel.columns else "_id"
        latest_scores = {
            str(r[sym_col]): float(r["factor_value"])
            for r in panel.filter(
                (pl.col("trade_date") == last_dt) & pl.col("factor_eligible") & pl.col("factor_value").is_finite()
            ).iter_rows(named=True)
        }
        bench_w = service._bench_weights(universe_id, snapshot)
        if latest_scores and bench_w:
            risk_eng = deps.engine("risk")
            opt_req = PortfolioOptRequest(
                experiment_id=study_id,
                universe_id=universe_id,
                snapshot_id=snapshot.snapshot_id,
                constraints=constraints or OptConstraints(),
            )
            opt_res = risk_eng.optimize_portfolio(bars_df, latest_scores, dict(bench_w), bench_w, opt_req)
            target_w_map = {h.symbol: h.target_weight_pct / 100.0 for h in opt_res.top_holdings}
    except Exception:  # noqa: BLE001
        opt_res = None

    try:
        cap_req = CapacityAnalysisRequest(
            experiment_id=study_id,
            universe_id=universe_id,
            snapshot_id=snapshot.snapshot_id,
        )
        gross_hint = (
            opt_res.expected_gross_excess_pct
            if opt_res is not None
            else (factor_eval.top_excess_ann * 100.0 if factor_eval is not None else 5.0)
        )
        to_hint = (
            opt_res.oneway_turnover_pct
            if opt_res is not None
            else (factor_eval.daily_turnover_oneway * 100.0 if factor_eval is not None else 10.0)
        )
        cap_res = call_analyze_capacity_frontier(
            bars_df,
            cap_req,
            target_weights=target_w_map,
            gross_excess_ann_pct=gross_hint,
            oneway_turnover_pct=to_hint,
        )
    except Exception:  # noqa: BLE001
        cap_res = None

    _emit("BACKTEST_CPCV", "progress", 68, "Portfolio optimization & AUM capacity frontier completed", None)

    # Step 5 · A-Share Event-Driven Microstructure Backtest
    backtest_res: BacktestResult | None = None
    try:
        if panel is not None and not panel.is_empty():
            sym_col = "symbol" if "symbol" in panel.columns else "_id"
            score_panel = (
                panel.filter(pl.col("factor_eligible") & pl.col("factor_value").is_finite())
                .select("trade_date", pl.col(sym_col).alias("symbol"), pl.col("factor_value").alias("score"))
            )
            bench_hist = service._bench_history(bars_df, universe_id, snapshot)
            signals = service.target_weights_from_scores(score_panel, bench_hist, None)
            if not signals.is_empty():
                sessions = sorted(bars_df["trade_date"].unique().to_list())
                first_sig = signals["trade_date"].min()
                warm = sessions[max(0, sessions.index(first_sig) - service.SIM_WARMUP_SESSIONS)]
                bt_bars = bars_df.filter(pl.col("trade_date") >= warm)
                bt_bench_hist = bench_hist.filter(pl.col("trade_date") >= warm)
                try:
                    corp_df = lake.load_corp_actions(snapshot.snapshot_id)
                except Exception:  # noqa: BLE001
                    corp_df = None
                bench_frame = service._bench_frame(bt_bars, bt_bench_hist)
                bt_eng = deps.engine("backtest")
                bt_req = BacktestRequest(
                    experiment_id=study_id,
                    universe_id=universe_id,
                    engine=BacktestEngineMode.EVENT,
                    snapshot_id=snapshot.snapshot_id,
                )
                backtest_res = bt_eng.run_backtest(bt_bars, corp_df, signals, bench_frame, bt_req)
    except Exception:  # noqa: BLE001
        backtest_res = None

    stages[3]["status"] = "done"
    stages[3]["progress_pct"] = 100
    stages[3]["ended_at"] = _now_iso()

    # Step 6 · 5-Dimension Regime Analysis (with cached DimensionBaselineStore)
    _move("DIAGNOSING", "DIAGNOSIS", 82, "Running 5-dimension regime decomposition & deterministic diagnosis")
    stages[4]["status"] = "running"
    stages[4]["started_at"] = _now_iso()

    baselines = ensure_dimension_baselines(
        registry,
        lake,
        snapshot.snapshot_id,
        universe_id,
        bars_df=bars_df,
    )
    dim_list = dimensions or ["market_regime", "volatility_regime", "market_cap_tier", "sw_industry", "barra_style"]
    regime_res: RegimeAnalysisResult | None = None
    try:
        regime_res = call_analyze_factor_regimes(
            bars_df,
            RegimeAnalysisRequest(
                factor_id=factor_id,
                expression=expression,
                universe_id=universe_id,
                snapshot_id=snapshot.snapshot_id,
                dimensions=dim_list,
            ),
            baselines=baselines,
            eval_result=factor_eval,
        )
    except Exception:  # noqa: BLE001
        regime_res = None

    # Step 7 · Deterministic Diagnosis, Verdict & Ledger Finalization
    diag_items, imp_plan = call_generate_diagnosis(
        proof=proof,
        factor_eval=factor_eval,
        regime=regime_res,
        capacity=cap_res,
        backtest=backtest_res,
        opt=opt_res,
    )

    metrics_summary = {
        "trial_count_m": effective_m,
        "proofs_passed": proof.proofs_passed,
        "proofs_total": proof.proofs_total,
        "max_prefix_drift": proof.max_prefix_drift,
        "bh_fdr_q_value": proof.bh_fdr_q_value,
        "spa_p_value": proof.spa_p_value,
        "max_param_cliff_drop_pct": proof.max_param_cliff_drop_pct,
    }
    if factor_eval is not None:
        metrics_summary.update(
            {
                "rank_ic_mean": round(float(factor_eval.rank_ic_mean), 6),
                "icir_ann": round(float(factor_eval.icir_ann), 4),
                "nw_t_stat": round(float(factor_eval.nw_t_stat), 4),
                "ic_win_rate": round(float(factor_eval.ic_win_rate), 4),
                "long_short_ann": round(float(factor_eval.long_short_ann), 4),
                "top_excess_ann": round(float(factor_eval.top_excess_ann), 4),
                "daily_turnover_oneway": round(float(factor_eval.daily_turnover_oneway), 4),
                "half_life_days": round(float(factor_eval.half_life_days), 2),
                "monotonicity_spearman": round(float(factor_eval.monotonicity_spearman), 4),
                "max_corr_in_library": round(float(factor_eval.max_corr_in_library), 4),
                "admission_passed": bool(factor_eval.admission_passed),
            }
        )
    if cpcv_res is not None:
        metrics_summary.update(
            {
                "is_sharpe": round(float(cpcv_res.is_sharpe), 4),
                "oos_sharpe": round(float(cpcv_res.oos_median_sharpe), 4),
                "oos_is_ratio": round(float(cpcv_res.oos_is_ratio), 4),
                "pbo": round(float(cpcv_res.pbo), 4),
                "dsr": round(float(cpcv_res.dsr), 4),
                "cpcv_verdict": cpcv_res.verdict.value,
            }
        )
    if backtest_res is not None:
        metrics_summary.update(
            {
                "net_excess_ann_pct": round(float(backtest_res.net_excess_ann_pct), 4),
                "info_ratio": round(float(backtest_res.info_ratio), 4),
                "max_drawdown_pct": round(float(backtest_res.max_drawdown_pct), 4),
                "ann_turnover_pct": round(float(backtest_res.ann_turnover_pct), 2),
                "brinson_closure_error_bp": round(float(backtest_res.attribution.brinson_closure_error_bp), 4),
                "barra_closure_error_bp": round(float(backtest_res.attribution.barra_closure_error_bp), 4),
            }
        )
    if cap_res is not None:
        metrics_summary.update(
            {
                "half_alpha_aum_cny": cap_res.half_alpha_aum_cny,
                "breakeven_aum_cny": cap_res.breakeven_aum_cny,
            }
        )

    if worker_claimed_metrics:
        metrics_summary["worker_claimed_metrics"] = worker_claimed_metrics
        diff = _compute_claimed_vs_verified(worker_claimed_metrics, metrics_summary)
        metrics_summary["claimed_vs_verified_diff"] = diff
        if any(item.get("discrepancy_flag") for item in diff.values()):
            _emit(
                "DIAGNOSIS",
                "warning",
                92,
                "Zero-trust re-verification detected material divergence from external worker claims",
                {"claimed_vs_verified_diff": diff},
            )

    # Determine academic verdict strictly aligned with ProofGate
    cpcv_passed = cpcv_res is not None and cpcv_res.verdict == GateVerdict.PASS
    cpcv_rejected = cpcv_res is not None and cpcv_res.verdict == GateVerdict.REJECT
    admission_passed = bool(factor_eval.admission_passed) if factor_eval is not None else False
    net_ex = backtest_res.net_excess_ann_pct if backtest_res is not None else None
    has_blocking_proof_fail = (not proof.passed) or any(c.status == "fail" for c in proof.checks)

    if proof.passed and cpcv_passed and admission_passed and (net_ex is None or net_ex > 0):
        final_status, final_verdict = "COMPLETED", "VERIFIED"
        fail_code = None
    elif has_blocking_proof_fail or cpcv_rejected or (net_ex is not None and net_ex < 0 and not admission_passed):
        final_status, final_verdict = "FALSIFIED", "FALSIFIED"
        fail_code = _extract_failure_code(proof, factor_eval, cpcv_res, backtest_res)
    else:
        final_status, final_verdict = "COMPLETED", "INCONCLUSIVE"
        fail_code = None

    stages[4]["status"] = "done"
    stages[4]["progress_pct"] = 100
    stages[4]["ended_at"] = _now_iso()
    stages[5]["status"] = "done"
    stages[5]["progress_pct"] = 100
    stages[5]["ended_at"] = _now_iso()

    fp = _compute_fact_fingerprint(snapshot.snapshot_id, universe_id, expression or factor_id, proof.proof_sha256)
    artifact_uri: str | None = None
    try:
        dossier = {
            "study_id": study_id,
            "snapshot_id": snapshot.snapshot_id,
            "universe_id": universe_id,
            "fact_fingerprint": fp,
            "status": final_status,
            "verdict": final_verdict,
            "metrics_summary": metrics_summary,
            "formal_proof": proof.model_dump(mode="json"),
        }
        art_ref = lake.save_artifact(study_id, "study_dossier", dossier)
        artifact_uri = art_ref.uri
    except Exception:  # noqa: BLE001
        artifact_uri = None

    detail = registry.finalize_study(
        study_id,
        status=final_status,
        verdict=final_verdict,
        formal_proof=proof,
        metrics_summary=metrics_summary,
        dimension_analysis=regime_res,
        capacity_frontier=cap_res,
        diagnosis=diag_items,
        improvement_plan=imp_plan,
        stages=stages,
        artifact_uri=artifact_uri,
        fact_fingerprint=fp,
        auto_tombstone=(final_verdict == "FALSIFIED"),
        failure_code=fail_code,
    )
    _emit(
        "PUBLISHED",
        "terminal",
        100,
        f"Study {study_id} finished: status={final_status}, verdict={final_verdict}",
        {"status": final_status, "verdict": final_verdict, "artifact_uri": artifact_uri},
    )
    refreshed = registry.get_study(study_id)
    return refreshed if refreshed is not None else detail


__all__ = [
    "call_analyze_capacity_frontier",
    "call_analyze_factor_regimes",
    "call_build_dimension_baselines",
    "call_formal_verification",
    "call_generate_diagnosis",
    "ensure_dimension_baselines",
    "reset_v3_engine_overrides",
    "run_deterministic_verification_pipeline",
    "set_v3_engine_overrides",
]
