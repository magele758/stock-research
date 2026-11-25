"""Deterministic orchestration layer shared by REST routes and MCP tools.

Every function pins its result to a concrete `snapshot_id`, calls the real
`quant_core` lake/engines (never synthesises prices) and raises
`deps.ServiceUnavailable | NotFound | InvalidArgument` for expected failures.
There is deliberately NO order/broker function in this module.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import numpy as np
import polars as pl
from quant_core.schemas import (
    AsyncJobRecord,
    BacktestRequest,
    BacktestResult,
    BarraExposureRequest,
    BarraExposureResult,
    CpcvRequest,
    CpcvResult,
    DataHealthReport,
    DatasetSnapshot,
    ExperimentRecord,
    FactorCorrRequest,
    FactorCorrResult,
    FactorEvalRequest,
    FactorEvalResult,
    FactorHypothesisRequest,
    FactorHypothesisResult,
    PortfolioOptRequest,
    PortfolioOptResult,
    ShadowDriftRequest,
    ShadowDriftResult,
)

from . import deps
from .catalog import MCP_TOOLS
from .deps import InvalidArgument, NotFound, ServiceUnavailable
from .jobs import ProgressFn, get_manager, get_store

# Bars lookback used for risk/optimization/shadow (calendar days before snapshot as_of).
RISK_LOOKBACK_DAYS = int(os.environ.get("QUANT_RISK_LOOKBACK_DAYS", "800"))
MAX_JOB_FACTORS = 12


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _pin(req: Any) -> tuple[Any, DatasetSnapshot]:
    """Resolve the snapshot and return (request pinned to it, snapshot)."""
    snap = deps.require_snapshot(getattr(req, "snapshot_id", None))
    return req.model_copy(update={"snapshot_id": snap.snapshot_id}), snap


def _as_date(v: Any) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _end_of_day(d: date) -> datetime:
    return datetime.combine(d, time(23, 59, 59), tzinfo=UTC)


def _snapshot_end(snap: DatasetSnapshot, end: date | None = None) -> date:
    return end or _as_date(snap.as_of)


def _load_bars(universe_id: str, snap: DatasetSnapshot, start: date | None, end: date | None) -> pl.DataFrame:
    lake = deps.get_lake()
    df = lake.load_bars(universe_id, start, end, snap.snapshot_id)
    if df is None or df.height == 0:
        raise ServiceUnavailable(
            f"no bars for universe {universe_id} in snapshot {snap.snapshot_id} "
            f"({start}..{end}); check universe id or run `make seed`.",
            code="INSUFFICIENT_EVIDENCE",
        )
    return df


def _load_risk_bars(universe_id: str, snap: DatasetSnapshot) -> pl.DataFrame:
    end = _snapshot_end(snap)
    return _load_bars(universe_id, snap, end - timedelta(days=RISK_LOOKBACK_DAYS), end)


def _pit_financials(snap: DatasetSnapshot, as_of: date | None) -> pl.DataFrame | None:
    lake = deps.get_lake()
    try:
        return lake.load_pit_financials(_end_of_day(as_of or _as_date(snap.as_of)), snap.snapshot_id)
    except Exception:  # noqa: BLE001 - financials are optional for price-only factors
        return None


def _bench_weights(universe_id: str, snap: DatasetSnapshot, as_of: date | None = None) -> dict[str, float]:
    lake = deps.get_lake()
    members = lake.load_universe_constituents(universe_id, as_of or _as_date(snap.as_of), snap.snapshot_id)
    w = {m.symbol: float(m.weight) for m in members if m.weight is not None and m.weight > 0}
    if not w:
        n = max(len(members), 1)
        w = {m.symbol: 1.0 / n for m in members}
    tot = sum(w.values()) or 1.0
    return {k: v / tot for k, v in w.items()}


def expr_hash(expression: str) -> str:
    norm = re.sub(r"\s+", "", expression or "")
    return hashlib.sha256(norm.encode()).hexdigest()[:16]


def _save_artifact(run_id: str, name: str, payload: dict[str, Any]) -> str | None:
    try:
        ref = deps.get_lake().save_artifact(run_id, name, payload)
        return ref.uri
    except Exception:  # noqa: BLE001 - artifact persistence is best-effort
        return None


# ---------------------------------------------------------------------------
# Q1 data
# ---------------------------------------------------------------------------


def health() -> dict[str, Any]:
    port = int(os.environ.get("QUANT_MCP_PORT", "8101"))
    try:
        snap = deps.require_snapshot(None)
    except ServiceUnavailable as exc:
        return {"status": "no_snapshot", "snapshot_id": None, "markets": [], "mcp_port": port, "hint": exc.message}
    return {
        "status": "ok",
        "snapshot_id": snap.snapshot_id,
        "markets": [m.value for m in snap.markets],
        "mcp_port": port,
    }


def data_health(snapshot_id: str | None = None) -> DataHealthReport:
    snap = deps.require_snapshot(snapshot_id)
    return deps.get_lake().run_qa_sentinel(snap.snapshot_id)


def snapshots() -> list[DatasetSnapshot]:
    try:
        return list(deps.get_lake().list_snapshots())
    except ServiceUnavailable:
        raise
    except Exception as exc:
        raise ServiceUnavailable(f"{deps.SEED_HINT} ({type(exc).__name__}: {exc})") from exc


def universe(universe_id: str, as_of_date: date | None = None, snapshot_id: str | None = None) -> dict[str, Any]:
    snap = deps.require_snapshot(snapshot_id)
    as_of = as_of_date or _as_date(snap.as_of)
    members = deps.get_lake().load_universe_constituents(universe_id, as_of, snap.snapshot_id)
    if not members:
        raise NotFound(f"universe {universe_id} has no constituents at {as_of} in snapshot {snap.snapshot_id}")
    return {
        "universe_id": universe_id,
        "as_of_date": as_of.isoformat(),
        "snapshot_id": snap.snapshot_id,
        "count": len(members),
        "members": [json.loads(m.model_dump_json()) for m in members],
    }


def data_sync(provider: str, mode: str = "check") -> dict[str, Any]:
    """Validate (or sync) a provider. Never fabricates data; failures are reported verbatim."""
    name = provider.lower()
    latest = deps.latest_snapshot_or_none() if _lake_ok() else None
    base = {
        "provider": name,
        "mode": mode,
        "latest_snapshot_id": latest.snapshot_id if latest else None,
        "latest_snapshot_qa": latest.qa_status if latest else None,
        "synthetic_fallback": False,
    }
    if name not in {"tushare", "baostock", "edgar"}:
        raise InvalidArgument(f"unknown provider {provider!r}; expected tushare|baostock|edgar")
    try:
        from quant_core import (
            providers as prov,  # local import: optional until quant-lake lands
        )
    except ImportError as exc:
        return {**base, "status": "unavailable", "reason": f"quant_core.providers not importable: {exc}"}
    try:
        adapter = prov.get_provider(name)
        adapter.check_credentials()
    except Exception as exc:  # noqa: BLE001 - surface provider failure reason, never fake data
        return {**base, "status": "failed", "reason": f"{type(exc).__name__}: {exc}"}
    if mode == "check":
        return {**base, "status": "ok", "reason": "credentials/connectivity verified; no data written"}
    lake = deps.get_lake()
    sync = getattr(lake, "sync_provider", None)
    if sync is None:
        return {**base, "status": "unsupported",
                "reason": "incremental sync is not exposed by QuantLake; run scripts/seed_golden_dataset.py"}
    try:
        out = sync(name)
    except Exception as exc:  # noqa: BLE001
        return {**base, "status": "failed", "reason": f"{type(exc).__name__}: {exc}"}
    return {**base, "status": "ok", "reason": "incremental sync finished", "detail": out if isinstance(out, dict) else None}


def _lake_ok() -> bool:
    try:
        deps.get_lake()
        return True
    except ServiceUnavailable:
        return False


# ---------------------------------------------------------------------------
# Q2 factors
# ---------------------------------------------------------------------------


def factor_catalog() -> dict[str, Any]:
    fe = deps.engine("factors")
    builtin = []
    for fid, meta in getattr(fe, "BUILTIN_FACTORS", {}).items():
        meta = meta if isinstance(meta, dict) else {}
        builtin.append({"factor_id": fid, "source": "builtin", **{k: v for k, v in meta.items() if _jsonable(v)}})
    registered = []
    for rec in get_store().list_results("hypothesis"):
        registered.append(
            {
                "factor_id": rec["factor_id"],
                "source": "registered",
                "expression": rec.get("expression"),
                "expr_hash": rec.get("expr_hash"),
                "hypothesis_id": rec.get("hypothesis_id"),
                "status": rec.get("status"),
            }
        )
    return {"count": len(builtin) + len(registered), "factors": builtin + registered}


def _jsonable(v: Any) -> bool:
    try:
        json.dumps(v)
        return True
    except (TypeError, ValueError):
        return False


def _resolve_expression(factor_id: str) -> str | None:
    for rec in get_store().list_results("hypothesis"):
        if rec.get("factor_id") == factor_id and rec.get("ast_safe"):
            return rec.get("expression")
    return None


def eval_factor(req: FactorEvalRequest) -> FactorEvalResult:
    req, snap = _pin(req)
    fe = deps.engine("factors")
    if req.expression is None and req.factor_id not in getattr(fe, "BUILTIN_FACTORS", {}):
        expr = _resolve_expression(req.factor_id)
        if expr is None:
            raise NotFound(f"factor {req.factor_id!r} is neither builtin nor a registered hypothesis")
        req = req.model_copy(update={"expression": expr})
    if req.expression is not None:
        ok, warns = fe.validate_factor_ast(req.expression)
        if not ok:
            raise InvalidArgument("expression rejected by AST guard: " + "; ".join(warns))
    end = _snapshot_end(snap, req.end_date)
    bars = _load_bars(req.universe_id, snap, req.start_date, req.end_date)
    pit = _pit_financials(snap, end)
    res = fe.evaluate_factor(bars, req, pit)
    if res.artifact_uri is None:
        uri = _save_artifact(f"factor_{expr_hash(res.expression)}", "factor_eval", json.loads(res.model_dump_json()))
        if uri:
            res = res.model_copy(update={"artifact_uri": uri})
    return res


def factor_corr(req: FactorCorrRequest) -> FactorCorrResult:
    if not req.factor_ids:
        raise InvalidArgument("factor_ids must not be empty")
    req, snap = _pin(req)
    fe = deps.engine("factors")
    bars = _load_bars(req.universe_id, snap, None, None)
    return fe.compute_factor_correlation(bars, req)


def submit_hypothesis(req: FactorHypothesisRequest, *, evaluate: bool = True) -> dict[str, Any]:
    """Register (and by default evaluate) a candidate factor. Idempotent by key and expression hash."""
    req, snap = _pin(req)
    fe = deps.engine("factors")
    store = get_store()
    h = expr_hash(req.expression)
    hid = f"HYP-{h[:10]}"
    if req.idempotency_key:
        prior = store.get_result("hypothesis_idem", req.idempotency_key)
        if prior:
            return {**prior, "result": {**prior["result"], "duplicate": True}}
    existing = store.get_result("hypothesis", hid)
    if existing:
        res = {**existing["result"], "duplicate": True}
        return {**existing, "result": res}
    ok, warns = fe.validate_factor_ast(req.expression)
    n = len(store.list_results("hypothesis")) + 1
    trial_id = f"TRIAL-{n:04d}"
    status = "pending_approval"
    reason = None
    evaluation = None
    if not ok:
        status, reason = "rejected", "AST guard: " + "; ".join(warns)
    elif evaluate:
        try:
            ev = eval_factor(
                FactorEvalRequest(
                    factor_id=req.factor_id,
                    expression=req.expression,
                    universe_id=req.universe_id,
                    snapshot_id=snap.snapshot_id,
                )
            )
            status = "evaluated"
            evaluation = {
                "rank_ic_mean": ev.rank_ic_mean,
                "icir_ann": ev.icir_ann,
                "nw_t_stat": ev.nw_t_stat,
                "long_short_ann": ev.long_short_ann,
                "max_corr_in_library": ev.max_corr_in_library,
                "max_corr_peer": ev.max_corr_peer,
                "admission_passed": ev.admission_passed,
                "artifact_uri": ev.artifact_uri,
            }
        except (InvalidArgument, ServiceUnavailable):
            raise
        except Exception as exc:  # noqa: BLE001 - registered but evaluation failed
            status, reason = "queued", f"evaluation deferred: {type(exc).__name__}: {exc}"
    result = FactorHypothesisResult(
        hypothesis_id=hid,
        factor_id=req.factor_id,
        expr_hash=h,
        ast_safe=bool(ok),
        duplicate=False,
        status=status,  # type: ignore[arg-type]
        trial_id=trial_id,
        rejection_reason=reason,
    )
    record = {
        "hypothesis_id": hid,
        "factor_id": req.factor_id,
        "expr_hash": h,
        "expression": req.expression,
        "hypothesis": req.hypothesis,
        "universe_id": req.universe_id,
        "snapshot_id": snap.snapshot_id,
        "ast_safe": bool(ok),
        "ast_warnings": list(warns),
        "status": status,
        "result": json.loads(result.model_dump_json()),
        "evaluation": evaluation,
    }
    store.put_result("hypothesis", hid, record, snap.snapshot_id)
    if req.idempotency_key:
        store.put_result("hypothesis_idem", req.idempotency_key, record, snap.snapshot_id)
    return record


def list_experiments(limit: int = 20) -> list[ExperimentRecord]:
    try:
        return list(deps.get_lake().list_experiments(limit))
    except ServiceUnavailable:
        raise
    except Exception as exc:
        raise ServiceUnavailable(f"{deps.SEED_HINT} ({type(exc).__name__}: {exc})") from exc


def _find_experiment(experiment_id: str) -> ExperimentRecord | None:
    for rec in deps.get_lake().list_experiments(10_000):
        if rec.experiment_id == experiment_id:
            return rec
    return None


def _known_factor_ids() -> list[str]:
    fe = deps.engine("factors")
    ids = list(getattr(fe, "BUILTIN_FACTORS", {}).keys())
    ids += [r["factor_id"] for r in get_store().list_results("hypothesis") if r.get("ast_safe")]
    return list(dict.fromkeys(ids))


def _experiment_factors(experiment_id: str) -> tuple[list[str], ExperimentRecord | None]:
    """Map experiment/hypothesis/factor id -> candidate factor ids (first = primary)."""
    known = _known_factor_ids()
    rec = _find_experiment(experiment_id)
    if rec is not None:
        text = f"{rec.family_id} {rec.feature_summary} {rec.name}"
        found = [f for f in known if f in text]
        return (found or known[:MAX_JOB_FACTORS]), rec
    hyp = get_store().get_result("hypothesis", experiment_id)
    if hyp:
        return [hyp["factor_id"]], None
    if experiment_id in known:
        return [experiment_id], None
    raise NotFound(
        f"experiment {experiment_id!r} not found in registry (known factors: {', '.join(known[:6])}...)"
    )


# --- factor score panel & returns -------------------------------------------------


def _ls_returns(res: FactorEvalResult) -> tuple[list[str], np.ndarray, np.ndarray]:
    """Daily long-short and benchmark returns from decile NAV paths."""
    ls = np.asarray(res.decile_navs.get("LS", []), dtype=float)
    bench = np.asarray(res.decile_navs.get("BENCH", []), dtype=float)
    dates = list(res.dates)
    n = min(len(ls), len(dates))
    if n < 3:
        raise ServiceUnavailable("factor evaluation returned too few dates", code="INSUFFICIENT_EVIDENCE")
    ls, dates = ls[:n], dates[:n]
    lr = ls[1:] / ls[:-1] - 1.0
    br = (bench[:n][1:] / bench[:n][:-1] - 1.0) if len(bench) >= n else np.zeros(n - 1)
    return dates[1:], lr, br


_LS_CACHE: dict[tuple[str, str, str, str], tuple[list[str], np.ndarray, np.ndarray]] = {}


def candidate_returns(
    experiment_id: str, universe_id: str, snap: DatasetSnapshot, trial_count_m: int, progress: ProgressFn | None = None
) -> tuple[list[date], np.ndarray, np.ndarray, list[str]]:
    factors, _ = _experiment_factors(experiment_id)
    primary, rest = factors[0], [f for f in _known_factor_ids() if f not in factors[:1]]
    cand = list(dict.fromkeys(factors + rest))[: max(2, min(MAX_JOB_FACTORS, trial_count_m))]
    series: dict[str, tuple[list[str], np.ndarray]] = {}
    bench: dict[str, float] = {}
    for i, fid in enumerate(cand):
        expr = _resolve_expression(fid)
        ck = (snap.snapshot_id, universe_id, fid, expr or "")
        if ck not in _LS_CACHE:  # snapshots are immutable, so (snapshot, universe, factor, expr) is a safe key
            res = eval_factor(
                FactorEvalRequest(factor_id=fid, expression=expr, universe_id=universe_id, snapshot_id=snap.snapshot_id)
            )
            _LS_CACHE[ck] = _ls_returns(res)
        d, lr, br = _LS_CACHE[ck]
        series[fid] = (d, lr)
        if fid == primary:
            bench = dict(zip(d, br.tolist(), strict=True))
        if progress:
            progress(10 + int(60 * (i + 1) / len(cand)))
    common = sorted(set.intersection(*[set(d) for d, _ in series.values()]))
    if len(common) < 120:
        raise ServiceUnavailable(
            f"only {len(common)} common dates across candidates; CPCV needs >=120", code="INSUFFICIENT_EVIDENCE"
        )
    cols, keep = [], []
    for fid in cand:
        d, lr = series[fid]
        idx = {x: j for j, x in enumerate(d)}
        cols.append(np.array([lr[idx[x]] for x in common], dtype=float))
        keep.append(fid)
    matrix = np.column_stack(cols)
    ok = np.isfinite(matrix).all(axis=1)
    common = [c for c, k in zip(common, ok, strict=True) if k]
    matrix = matrix[ok]
    base = np.array([bench.get(c, 0.0) for c in common], dtype=float)
    return [_as_date(c) for c in common], matrix, base, keep


def run_cpcv(req: CpcvRequest, progress: ProgressFn | None = None) -> CpcvResult:
    req, snap = _pin(req)
    val = deps.engine("validation")
    _, rec = _experiment_factors(req.experiment_id)
    universe_id = rec.universe_id if rec else "CSI500"
    dates, matrix, base, cand = candidate_returns(req.experiment_id, universe_id, snap, req.trial_count_m, progress)
    res = val.run_cpcv_gate(matrix, dates, req, base)
    # persist the outcome in the experiment registry
    ann = float(np.mean(matrix[:, 0]) * 252.0 * 100.0)
    now = datetime.now(UTC)
    record = ExperimentRecord(
        experiment_id=req.experiment_id,
        family_id=rec.family_id if rec else cand[0],
        name=rec.name if rec else cand[0],
        model_type=rec.model_type if rec else "single_factor",
        feature_summary=rec.feature_summary if rec else cand[0],
        universe_id=universe_id,
        snapshot_id=snap.snapshot_id,
        trial_count_m=max(req.trial_count_m, len(cand)),
        is_sharpe=res.is_sharpe,
        oos_sharpe=res.oos_median_sharpe,
        oos_is_ratio=res.oos_is_ratio,
        cpcv_pbo=res.pbo,
        dsr=res.dsr,
        oos_excess_ann=ann,
        verdict=res.verdict,
        status_badge=res.verdict.value.upper(),
        created_at=rec.created_at if rec else now,
        notes=f"CPCV gate over {len(cand)} candidate series ({', '.join(cand[:4])}...) on {snap.snapshot_id}",
    )
    try:
        deps.get_lake().save_experiment(record)
    except Exception:  # noqa: BLE001 - result is still valid; registry write is best effort
        pass
    return res


# ---------------------------------------------------------------------------
# alpha scores -> weights (Q4/Q5)
# ---------------------------------------------------------------------------

def factor_score_panel(
    bars: pl.DataFrame, factor_ids: list[str], pit: pl.DataFrame | None, universe_id: str
) -> pl.DataFrame:
    """Composite cross-sectional score panel [trade_date, symbol, score] from `factors.engine.factor_panel`.

    Each factor is preprocessed by the engine (MAD winsorize, industry/size neutralization, zscore);
    the composite is the equal-weight mean of the factors' eligible `factor_value`s.
    """
    fe = deps.engine("factors")
    frames = []
    for i, fid in enumerate(factor_ids):
        req = FactorEvalRequest(factor_id=fid, expression=_resolve_expression(fid), universe_id=universe_id)
        panel = fe.factor_panel(bars, req, pit)
        sym = "symbol" if "symbol" in panel.columns else "_id"
        frames.append(
            panel.filter(pl.col("factor_eligible") & pl.col("factor_value").is_not_null())
            .select("trade_date", pl.col(sym).alias("symbol"), pl.col("factor_value").alias(f"s{i}"))
        )
    out = frames[0]
    for f in frames[1:]:
        out = out.join(f, on=["trade_date", "symbol"], how="inner")
    cols = [c for c in out.columns if c not in {"trade_date", "symbol"}]
    return out.with_columns(pl.mean_horizontal(*cols).alias("score")).select("trade_date", "symbol", "score")


def _alpha_inputs(experiment_id: str, universe_id: str, snap: DatasetSnapshot) -> tuple[pl.DataFrame, pl.DataFrame, dict[str, float]]:
    factors, _ = _experiment_factors(experiment_id)
    bars = _load_risk_bars(universe_id, snap)
    pit = _pit_financials(snap, None)
    panel = factor_score_panel(bars, factors, pit, universe_id)
    last = panel["trade_date"].max()
    latest = panel.filter(pl.col("trade_date") == last)
    scores = {r["symbol"]: float(r["score"]) for r in latest.iter_rows(named=True)}
    return bars, panel, scores


def _tilted_weights(scores: dict[str, float], bench: dict[str, float], cap: float = 0.02) -> dict[str, float]:
    """Alpha-tilted benchmark used as the pre-optimization portfolio for Barra diagnostics."""
    w = {s: b * float(np.exp(np.clip(scores.get(s, 0.0), -2, 2) * 0.35)) for s, b in bench.items()}
    for _ in range(5):
        tot = sum(w.values()) or 1.0
        w = {k: min(v / tot, cap) for k, v in w.items()}
    tot = sum(w.values()) or 1.0
    return {k: v / tot for k, v in w.items()}


def barra(req: BarraExposureRequest) -> BarraExposureResult:
    req, snap = _pin(req)
    risk = deps.engine("risk")
    bars, _, scores = _alpha_inputs(req.experiment_id, req.universe_id, snap)
    bench = _bench_weights(req.universe_id, snap)
    return risk.compute_barra_exposure(bars, _tilted_weights(scores, bench), bench, req)


def optimize(req: PortfolioOptRequest) -> PortfolioOptResult:
    req, snap = _pin(req)
    risk = deps.engine("risk")
    bars, _, scores = _alpha_inputs(req.experiment_id, req.universe_id, snap)
    bench = _bench_weights(req.universe_id, snap)
    # Current holdings are unknown (read-only research): assume the benchmark as the starting book,
    # so the turnover constraint measures active moves rather than a 100% cash-to-portfolio build.
    return risk.optimize_portfolio(bars, scores, dict(bench), bench, req)


# ---------------------------------------------------------------------------
# Q5 backtest, Q6 shadow
# ---------------------------------------------------------------------------


def _bench_history(bars: pl.DataFrame, universe_id: str, snap: DatasetSnapshot) -> pl.DataFrame:
    """Point-in-time benchmark weights per (trade_date, symbol).

    Constituent weights are re-read at the first session of each month (no look-ahead from the
    snapshot date), held through the month, restricted to names with a bar that day and renormalized.
    """
    lake = deps.get_lake()
    firsts: dict[tuple[int, int], date] = {}
    for d in sorted(bars["trade_date"].unique().to_list()):
        firsts.setdefault((d.year, d.month), d)
    frames = []
    for (y, m), d0 in firsts.items():
        members = lake.load_universe_constituents(universe_id, d0, snap.snapshot_id)
        wts = [(mm.symbol, float(mm.weight)) for mm in members if mm.weight is not None and mm.weight > 0]
        if not wts:  # weights unavailable: equal-weight the members rather than guessing sizes
            wts = [(mm.symbol, 1.0) for mm in members]
        if wts:
            frames.append(pl.DataFrame({"_ym": [y * 100 + m] * len(wts), "symbol": [w[0] for w in wts],
                                        "_w": [w[1] for w in wts]}))
    if not frames:
        raise ServiceUnavailable(f"no constituents for {universe_id} in snapshot {snap.snapshot_id}",
                                 code="INSUFFICIENT_EVIDENCE")
    w = pl.concat(frames)
    hist = (
        bars.select("trade_date", "symbol")
        .with_columns((pl.col("trade_date").dt.year() * 100 + pl.col("trade_date").dt.month()).alias("_ym"))
        .join(w, on=["_ym", "symbol"], how="inner")
    )
    return hist.with_columns((pl.col("_w") / pl.col("_w").sum().over("trade_date")).alias("benchmark_weight")).select(
        "trade_date", "symbol", "benchmark_weight"
    )


def _bench_frame(bars: pl.DataFrame, bench_hist: pl.DataFrame) -> pl.DataFrame:
    """Benchmark frame for the engine: daily return from PIT weights x adjusted bar returns, plus weights."""
    rets = (
        bars.sort(["symbol", "trade_date"])
        .with_columns((pl.col("close") * pl.col("adj_factor")).alias("_adj"))
        .with_columns((pl.col("_adj") / pl.col("_adj").shift(1).over("symbol") - 1.0).alias("ret"))
        .select("trade_date", "symbol", "ret")
    )
    daily = (
        bench_hist.join(rets, on=["trade_date", "symbol"], how="inner")
        .drop_nulls("ret")
        .group_by("trade_date")
        .agg(((pl.col("ret") * pl.col("benchmark_weight")).sum() / pl.col("benchmark_weight").sum()).alias("benchmark_return"))
    )
    return bench_hist.join(daily, on="trade_date", how="inner").sort("trade_date", "symbol")


TILT_STRENGTH = 0.35
SIM_WARMUP_SESSIONS = 5


def target_weights_from_scores(scores: pl.DataFrame, bench_hist: pl.DataFrame, start: date | None) -> pl.DataFrame:
    """Fully-invested long-only rule: each day w_i ∝ bench_i * exp(0.35 * clip(z_i, -2, 2)).

    A bounded alpha tilt around the PIT benchmark weights (max ~2x a neutral weight), so the result
    measures factor alpha rather than cash or size drag. Pure function of PIT inputs: no look-ahead.
    """
    df = scores.filter(pl.col("trade_date") >= start) if start else scores
    tilted = df.join(bench_hist, on=["trade_date", "symbol"], how="inner").with_columns(
        (pl.col("benchmark_weight") * (pl.col("score").clip(-2.0, 2.0) * TILT_STRENGTH).exp()).alias("_raw")
    )
    return (
        tilted.with_columns((pl.col("_raw") / pl.col("_raw").sum().over("trade_date")).alias("target_weight"))
        .select("trade_date", "symbol", "target_weight")
        .sort("trade_date", "symbol")
    )


def run_backtest(req: BacktestRequest, progress: ProgressFn | None = None) -> BacktestResult:
    req, snap = _pin(req)
    bt = deps.engine("backtest")
    lake = deps.get_lake()
    factors, _ = _experiment_factors(req.experiment_id)
    end = _snapshot_end(snap, req.end_date)
    start = req.start_date
    bars = _load_bars(req.universe_id, snap, (start - timedelta(days=120)) if start else None, end)
    if progress:
        progress(15)
    pit = _pit_financials(snap, end)
    bench_hist = _bench_history(bars, req.universe_id, snap)
    signals = target_weights_from_scores(factor_score_panel(bars, factors, pit, req.universe_id), bench_hist, start)
    if signals.is_empty():
        raise ServiceUnavailable(
            "no eligible factor scores in the backtest window (factor warm-up or coverage)", code="INSUFFICIENT_EVIDENCE"
        )
    # Simulation begins at the first available signal (minus a short volume-history warm-up): the factor
    # lookback period would otherwise be scored as an all-cash drag against a fully invested benchmark.
    sessions = sorted(bars["trade_date"].unique().to_list())
    first = signals["trade_date"].min()
    warm = sessions[max(0, sessions.index(first) - SIM_WARMUP_SESSIONS)]
    bars = bars.filter(pl.col("trade_date") >= warm)
    bench_hist = bench_hist.filter(pl.col("trade_date") >= warm)
    if progress:
        progress(40)
    try:
        corp = lake.load_corp_actions(snap.snapshot_id)
    except Exception:  # noqa: BLE001
        corp = None
    bench = _bench_frame(bars, bench_hist)
    res = bt.run_backtest(bars, corp, signals, bench, req)
    if res.artifact_uri is None:
        uri = _save_artifact(res.run_id, "backtest_result", json.loads(res.model_dump_json()))
        if uri:
            res = res.model_copy(update={"artifact_uri": uri})
    get_store().put_result("backtest", res.run_id, json.loads(res.model_dump_json()), snap.snapshot_id)
    return res


def get_backtest(run_id: str) -> BacktestResult:
    payload = get_store().get_result("backtest", run_id)
    if payload is None:
        raise NotFound(f"backtest run {run_id!r} not found (was it completed? check the job id)")
    return BacktestResult.model_validate(payload)


def shadow(req: ShadowDriftRequest) -> ShadowDriftResult:
    req, snap = _pin(req)
    sh = deps.engine("shadow")
    bars = _load_risk_bars(req.universe_id, snap)
    return sh.compute_shadow_drift(bars, req)


# ---------------------------------------------------------------------------
# async jobs
# ---------------------------------------------------------------------------


def _job_key(kind: str, req: Any, snap_id: str) -> str:
    raw = json.dumps(
        {"k": kind, "s": snap_id, "r": json.loads(req.model_dump_json())}, sort_keys=True, default=str
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def enqueue_backtest(req: BacktestRequest, idempotency_key: str | None = None, dedupe: bool = False) -> AsyncJobRecord:
    req, snap = _pin(req)
    key = idempotency_key or (_job_key("backtest", req, snap.snapshot_id) if dedupe else None)

    def work(progress: ProgressFn) -> dict[str, Any]:
        res = run_backtest(req, progress)
        return {"summary": backtest_job_summary(res), "artifact_uri": res.artifact_uri}

    return get_manager().submit("backtest", snap.snapshot_id, work, idempotency_key=key)


def enqueue_cpcv(req: CpcvRequest, idempotency_key: str | None = None, dedupe: bool = False) -> AsyncJobRecord:
    req, snap = _pin(req)
    key = idempotency_key or (_job_key("cpcv", req, snap.snapshot_id) if dedupe else None)

    def work(progress: ProgressFn) -> dict[str, Any]:
        res = run_cpcv(req, progress)
        return {
            "summary": {
                "experiment_id": res.experiment_id,
                "verdict": res.verdict.value,
                "dsr": res.dsr,
                "pbo": res.pbo,
                "oos_is_ratio": res.oos_is_ratio,
                "oos_median_sharpe": res.oos_median_sharpe,
                "is_sharpe": res.is_sharpe,
                "negative_path_count": res.negative_path_count,
                "n_oos_paths": res.n_oos_paths,
                "purge_conflicts": res.purge_conflicts,
            }
        }

    return get_manager().submit("cpcv", snap.snapshot_id, work, idempotency_key=key)


def enqueue_hypothesis(req: FactorHypothesisRequest, dedupe: bool = True) -> AsyncJobRecord:
    req, snap = _pin(req)
    key = req.idempotency_key or (_job_key("hypothesis", req, snap.snapshot_id) if dedupe else None)

    def work(progress: ProgressFn) -> dict[str, Any]:
        progress(20)
        rec = submit_hypothesis(req, evaluate=True)
        return {"summary": {"hypothesis": rec["result"], "evaluation": rec.get("evaluation")}}

    return get_manager().submit("hypothesis", snap.snapshot_id, work, idempotency_key=key)


def backtest_job_summary(res: BacktestResult) -> dict[str, Any]:
    return {
        "run_id": res.run_id,
        "experiment_id": res.experiment_id,
        "engine": res.engine.value,
        "ann_return_pct": res.ann_return_pct,
        "net_excess_ann_pct": res.net_excess_ann_pct,
        "info_ratio": res.info_ratio,
        "max_drawdown_pct": res.max_drawdown_pct,
        "ann_turnover_pct": res.ann_turnover_pct,
        "total_rejections": res.total_rejections,
    }


def get_job(job_id: str) -> AsyncJobRecord:
    rec = get_manager().get(job_id)
    if rec is None:
        raise NotFound(f"job {job_id!r} not found")
    return rec


# ---------------------------------------------------------------------------
# MCP catalog
# ---------------------------------------------------------------------------


def mcp_catalog() -> dict[str, Any]:
    stats = get_store().tool_stats()
    tools = []
    for t in MCP_TOOLS:
        s = stats.get(t["name"])
        calls = int(s["calls"]) if s else 0
        tools.append(
            {
                **t,
                "status": "ready",
                "calls": calls,
                "errors": int(s["errors"]) if s else 0,
                "avg_ms": round(s["total_ms"] / calls, 1) if s and calls else None,
                "last_ms": s["last_ms"] if s else None,
                "last_status": s["last_status"] if s else None,
                "last_at": s["last_at"] if s else None,
            }
        )
    return {
        "server": {
            "transport": "streamable-http",
            "url": f"http://127.0.0.1:{os.environ.get('QUANT_MCP_PORT', '8101')}/mcp",
            "stdio": "python -m quant_mcp.server --transport stdio",
            "bearer_required": bool(os.environ.get("QUANT_MCP_BEARER_TOKEN")),
            "max_response_bytes": 8192,
        },
        "count": len(tools),
        "tools": tools,
    }
