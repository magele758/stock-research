"""Reproducible causal, numerical and statistical checks for frozen factor research."""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterator, Sequence
from datetime import date, datetime
from hashlib import sha256
from json import dumps
from math import ceil, floor, log, sqrt
from numbers import Integral
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl
from scipy.special import digamma

from quant_core.factors import ast_guard
from quant_core.factors import engine as factors
from quant_core.schemas import (
    INSTITUTIONAL_GATES as GATES,
)
from quant_core.schemas import (
    BacktestEngineMode,
    BacktestRequest,
    CpcvRequest,
    FactorEvalRequest,
    FormalProofResult,
    FormalVerifyRequest,
    GateVerdict,
    ProofCheckItem,
)

_MIN_SESSIONS = 120
_MAX_WITNESSES = 12
_MONETARY_BAR_FIELDS = frozenset(
    (
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "vwap",
        "tail_vwap",
        "up_limit",
        "down_limit",
        "adj_open",
        "adj_high",
        "adj_low",
        "adj_close",
        "amount_ccy",
        "amount",
        "float_market_cap",
        "free_float_market_cap",
        "market_cap",
        "total_market_cap",
        "equity",
        "book_equity",
        "shareholders_equity",
        "net_profit_ttm",
        "net_income_ttm",
        "net_profit_deducted_ttm",
        "total_debt",
        "interest_bearing_debt",
        "revenue_ttm",
        "revenue_ttm_prior_year",
        "net_profit_ttm_prior_year",
        "total_assets",
        "eps_ttm",
        "eps",
    )
)
_MONETARY_FACT_METRICS = ("eps", "net_profit_deducted", "ocf", "equity", "revenue", "total_debt")
ComputePanel = Callable[[pl.DataFrame, FactorEvalRequest], pl.DataFrame]


def _block_len(length: int) -> int:
    return max(1, ceil(np.cbrt(length)))


def _stationary_indices(length: int, block_len: int, n_boot: int, seed: int) -> Iterator[np.ndarray]:
    if block_len < 1 or n_boot < 1:
        raise ValueError("block_len and n_boot must be positive")
    rng = np.random.default_rng(seed)
    offsets = np.arange(length)
    # For each observation, retain the last restart's start plus the distance from it.
    for _ in range(n_boot):
        starts = rng.integers(length, size=length)
        restart = rng.random(length) < (1 / block_len)
        restart[0] = True
        latest_restart = np.maximum.accumulate(np.where(restart, offsets, 0))
        yield (starts[latest_restart] + offsets - latest_restart) % length


def _bootstrap_statistics(
    series: np.ndarray,
    *,
    block_len: int | None = None,
    n_boot: int = 500,
    seed: int = 42,
    stat_fn: Callable[[np.ndarray], float] | None = None,
) -> dict[str, Any]:
    values = np.asarray(series, dtype=float)
    if values.ndim != 1 or not np.isfinite(values).any():
        raise ValueError("Bootstrap requires a one-dimensional series with finite observations")
    if np.isinf(values).any():
        raise ValueError("Bootstrap series cannot include infinite values")
    if block_len is None:
        block_len = _block_len(len(values))
    statistic = np.mean if stat_fn is None else stat_fn
    sampled, null_sampled = [], []
    observed = values[np.isfinite(values)]
    point = float(statistic(observed))
    if not np.isfinite(point):
        raise ValueError("The statistic is undefined on the observed series")
    center = float(observed.mean())
    for indices in _stationary_indices(len(values), block_len, n_boot, seed):
        sample = values[indices]
        sample = sample[np.isfinite(sample)]
        if sample.size:
            sampled.append(float(statistic(sample)))
            null_sampled.append(float(statistic(sample - center)))
    estimates = np.asarray(sampled)
    null_values = np.asarray(null_sampled)
    estimates = estimates[np.isfinite(estimates)]
    null_values = null_values[np.isfinite(null_values)]
    if not len(estimates) or not len(null_values):
        raise ValueError("The statistic is undefined on every bootstrap draw")
    return {
        "point": point,
        "ci95": np.percentile(estimates, [2.5, 97.5]).tolist(),
        "p_value": float((1 + np.count_nonzero(np.abs(null_values) >= abs(point))) / (len(null_values) + 1)),
        "p_value_one_sided": float((1 + np.count_nonzero(null_values >= point)) / (len(null_values) + 1)),
        "valid_draws": len(null_values),
        "block_len": block_len,
        "n_boot": n_boot,
    }


def stationary_block_bootstrap(
    series: np.ndarray,
    *,
    block_len: int | None = None,
    n_boot: int = 500,
    seed: int = 42,
    stat_fn=None,
) -> dict[str, Any]:
    """Return the percentile interval and a two-sided centered-null p-value.

    Finite observations are passed to the statistic after each draw. Their
    positions stay in the sampling calendar, preserving gaps and run lengths.
    """
    result = _bootstrap_statistics(series, block_len=block_len, n_boot=n_boot, seed=seed, stat_fn=stat_fn)
    del result["p_value_one_sided"], result["valid_draws"]
    return result


def benjamini_hochberg_fdr(
    p_values: Sequence[float], total_trials_m: int | None = None, alpha: float = 0.05
) -> dict[str, Any]:
    """Adjust observed p-values, treating unobserved registered slots as p=1."""
    p = np.asarray(p_values, dtype=float)
    if p.ndim != 1 or not np.all(np.isfinite(p) & (p >= 0) & (p <= 1)):
        raise ValueError("p_values must be one-dimensional and within [0, 1]")
    m = len(p) if total_trials_m is None else total_trials_m
    if isinstance(m, bool) or not isinstance(m, Integral) or m < len(p) or not 0 < alpha <= 1:
        raise ValueError("Require m >= number of p-values and 0 < alpha <= 1")
    m = int(m)
    order = np.argsort(p, kind="stable")
    raw = m * p[order] / np.arange(1, len(p) + 1)
    adjusted = np.minimum.accumulate(raw[::-1])[::-1][np.argsort(order)]
    bh = adjusted.clip(max=1)
    harmonic = float(digamma(m + 1) + np.euler_gamma) if m else 0.0
    by = (adjusted * harmonic).clip(0, 1)
    return {
        "q_values": bh.tolist(),
        "by_q_values": by.tolist(),
        "rejected": (bh <= alpha).tolist(),
        "m": m,
        "alpha": alpha,
    }


def _return_differences(candidate_returns: np.ndarray, benchmark_returns: np.ndarray | None) -> np.ndarray:
    candidates = np.asarray(candidate_returns, dtype=float)
    if candidates.ndim == 1:
        candidates = candidates[:, None]
    if candidates.ndim != 2 or candidates.shape[0] < 2 or candidates.shape[1] < 1:
        raise ValueError("Require at least two dates and one candidate")
    benchmark = (
        np.zeros(len(candidates)) if benchmark_returns is None else np.asarray(benchmark_returns, float)
    )
    if benchmark.shape != (len(candidates),):
        raise ValueError("Require one benchmark return per candidate observation date")
    differences = candidates - benchmark[:, None]
    if not np.isfinite(differences).all():
        raise ValueError("Candidate and benchmark returns must be finite on the common window")
    return differences


def _return_tests(differences: np.ndarray, *, n_boot: int, seed: int) -> tuple[dict[str, float], np.ndarray]:
    # Each draw is common across every candidate, preserving cross-candidate dependence.
    n = len(differences)
    means = differences.mean(axis=0)
    residuals = differences - means
    centered = np.stack(
        [residuals[indices].mean(axis=0) for indices in _stationary_indices(n, _block_len(n), n_boot, seed)]
    )
    varying = np.ptp(differences, axis=0) > 0
    centered[:, ~varying] = 0.0
    p = (1 + np.count_nonzero(centered >= means, axis=0)) / (n_boot + 1)
    std = centered.std(axis=0)
    # Degenerate distributions use White's unstudentized statistic; an exact
    # benchmark tie contributes zero to either maximum.
    has_scale = varying & (std > 0)
    wrc_p = (1 + np.count_nonzero(centered.max(axis=1) >= means.max())) / (n_boot + 1)
    if not has_scale.any():
        return {"spa_p_value": float(wrc_p), "wrc_p_value": float(wrc_p), "t_stat": 0.0}, p
    t = means[has_scale] / std[has_scale]
    cut = sqrt(2 * max(log(log(n)), 0.0))
    recenter = np.where(t < -cut, t, 0.0)
    t_stat = max(float(t.max()), 0.0)
    boot_stat = (centered[:, has_scale] / std[has_scale] + recenter).max(axis=1).clip(min=0)
    spa_p = (1 + np.count_nonzero(boot_stat >= t_stat)) / (n_boot + 1)
    # A positive deterministic differential is outside SPA's variance assumption.
    # The unstudentized result remains valid for the degenerate comparison.
    if np.any((~has_scale) & (means > 0)):
        spa_p = min(spa_p, wrc_p)
    return {"spa_p_value": float(spa_p), "wrc_p_value": float(wrc_p), "t_stat": t_stat}, p


def hansen_spa_test(
    candidate_returns: np.ndarray,
    benchmark_returns: np.ndarray | None = None,
    *,
    n_boot: int = 500,
    seed: int = 42,
) -> dict[str, Any]:
    """Hansen consistent SPA and unstudentized White RC on daily return differences."""
    return _return_tests(_return_differences(candidate_returns, benchmark_returns), n_boot=n_boot, seed=seed)[
        0
    ]


def _candidate_fdr(
    candidate_returns: np.ndarray,
    benchmark_returns: np.ndarray | None,
    trial_count_m: int,
    *,
    n_boot: int = 500,
    seed: int = 42,
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    differences = _return_differences(candidate_returns, benchmark_returns)
    spa, p_values = _return_tests(differences, n_boot=n_boot, seed=seed)
    fdr = benjamini_hochberg_fdr(p_values, max(trial_count_m, differences.shape[1]), GATES["bh_fdr_q_max"])
    return spa, [
        {
            "candidate_index": i,
            "p_value": float(p),
            "q_value": fdr["q_values"][i],
            "by_q_value": fdr["by_q_values"][i],
            "rejected": fdr["rejected"][i],
        }
        for i, p in enumerate(p_values)
    ]


def _signal_values(panel: pl.DataFrame) -> pl.DataFrame:
    identity = next(name for name in ("_id", "listing_id", "symbol") if name in panel.columns)
    value = "factor_value" if "factor_value" in panel.columns else "score"
    # Check the raw signal too. A rank transform may hide the leaked scale or level of a signal.
    raw = [pl.col("factor_raw").cast(pl.Float64)] if "factor_raw" in panel.columns else []
    return panel.select(
        "trade_date",
        pl.col(identity).cast(pl.String).alias("listing_id"),
        pl.col(value).cast(pl.Float64).alias("factor_value"),
        *raw,
    )


def _aligned_signals(left: pl.DataFrame, right: pl.DataFrame) -> pl.DataFrame:
    return (
        left.with_columns(pl.lit(True).alias("_left"))
        .join(
            right.with_columns(pl.lit(True).alias("_right")),
            on=["trade_date", "listing_id"],
            how="full",
            coalesce=True,
            suffix="_peer",
            validate="1:1",
        )
        .sort("trade_date", "listing_id")
    )


def _session_dates(bars: pl.DataFrame) -> list[date]:
    return (
        bars.select(pl.col("trade_date").cast(pl.Date))
        .drop_nulls()
        .unique()
        .sort("trade_date")
        .to_series()
        .to_list()
    )


def _check_prefix_invariance(
    bars_df: pl.DataFrame,
    req: FactorEvalRequest,
    *,
    cutoffs: Sequence[float] = (0.45, 0.65, 0.85),
    tol: float = 1e-10,
    compute_fn: ComputePanel | None = None,
    full_panel: pl.DataFrame | None = None,
    financials_df: pl.DataFrame | None = None,
) -> dict[str, Any]:
    if any(not 0 < cutoff < 1 for cutoff in cutoffs) or not np.isfinite(tol) or tol < 0:
        raise ValueError("Cutoffs must be in (0, 1) and tolerance must be finite and nonnegative")
    sessions = _session_dates(bars_df) if "trade_date" in bars_df.columns else []
    sessions = [
        day
        for day in sessions
        if (req.start_date is None or day >= req.start_date) and (req.end_date is None or day <= req.end_date)
    ]
    days = (
        sorted({sessions[min(len(sessions) - 2, ceil(len(sessions) * frac) - 1)] for frac in cutoffs})
        if len(sessions) >= 2
        else []
    )
    drift = 0.0
    n_compared = 0
    witness_keys: list[str] = []
    if days:

        def compute(raw: pl.DataFrame) -> pl.DataFrame:
            if compute_fn is not None:
                return compute_fn(raw, req)
            return factors.factor_panel(raw, req, financials_df)

        full = _signal_values(compute(bars_df) if full_panel is None else full_panel)
        for day in days:
            at_cutoff = pl.col("trade_date").cast(pl.Date) <= day
            prefix = _signal_values(compute(bars_df.filter(at_cutoff)))
            aligned = _aligned_signals(full.filter(at_cutoff), prefix)
            mismatched = (
                aligned.select(pl.any_horizontal(pl.col("_left", "_right").is_null()))
                .to_series()
                .to_numpy()
                .copy()
            )
            for field in ("factor_value", "factor_raw"):
                if field not in full.columns:
                    continue
                a, b = aligned[field], aligned[field + "_peer"]
                a_vals, b_vals = a.to_numpy(), b.to_numpy()
                common = np.isfinite(a_vals) & np.isfinite(b_vals)
                if field == "factor_value":
                    n_compared += int(common.sum())
                delta = np.abs(a_vals[common] - b_vals[common])
                drift = max(drift, float(delta.max(initial=0)))
                mismatched |= a.is_null().to_numpy() != b.is_null().to_numpy()
                mismatched |= (np.isnan(a_vals) != np.isnan(b_vals)) | np.isinf(a_vals) | np.isinf(b_vals)
                mismatched[common] |= delta > tol
            max_new = _MAX_WITNESSES - len(witness_keys)
            if max_new > 0 and mismatched.any():
                witnesses = (
                    aligned.filter(pl.Series(mismatched)).select("trade_date", "listing_id").head(max_new)
                )
                witness_keys.extend(
                    f"{day}/{trade_day}/{listing}" for trade_day, listing in witnesses.iter_rows()
                )
    return {
        "passed": n_compared > 0 and not witness_keys,
        "max_drift": min(drift, float(np.finfo(np.float64).max)),
        "cutoffs_tested": [day.isoformat() for day in days],
        "n_compared": n_compared,
        "witness_keys": witness_keys,
    }


def check_prefix_invariance(
    bars_df: pl.DataFrame,
    req: FactorEvalRequest,
    *,
    cutoffs: Sequence[float] = (0.45, 0.65, 0.85),
    tol: float = 1e-10,
    compute_fn: ComputePanel | None = None,
) -> dict[str, Any]:
    """Rebuild each prefix from raw bars; an injected kernel may return factor_value or score."""
    return _check_prefix_invariance(bars_df, req, cutoffs=cutoffs, tol=tol, compute_fn=compute_fn)


def _expression_windows(expression: str) -> list[tuple[str, ast.expr, int, int, int]]:
    """Locate integer arguments with temporal semantics in the allowlisted DSL."""
    tree = ast.parse(expression.strip(), mode="eval")
    windows = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name, source = ast_guard._function_name(node.func)
        name = ast_guard._ALIASES.get(name, name)
        args = node.args[int(source is None) :]
        if name in ast_guard._ROLLING or name == "rolling_corr":
            if name == "rolling_corr":
                # 'other' may be passed by keyword, but any positional call has it first.
                args = args[1:]
            params = ast_guard._arguments(
                args,
                [kw for kw in node.keywords if kw.arg not in ("other", "center")],
                ("window_size", "min_samples", "center"),
                aliases={"window": "window_size", "min_periods": "min_samples"},
            )
            window = params["window_size"]
            minimum = ast_guard._literal(params["min_samples"]) if "min_samples" in params else 1
            windows.append((f"{name}.window_size", window, ast_guard._literal(window), minimum, 1024))
            if "min_samples" in params:
                arg = params["min_samples"]
                windows.append(
                    (f"{name}.min_samples", arg, ast_guard._literal(arg), 1, ast_guard._literal(window))
                )
        elif name in ast_guard._LAGGED:
            key = "limit" if name == "forward_fill" else "n"
            params = ast_guard._arguments(args, node.keywords, (key,))
            if key in params:
                arg = params[key]
                windows.append((f"{name}.{key}", arg, ast_guard._literal(arg), 0, 1024))
    return windows


def _perturb_expression(expression: str, node: ast.expr, value: int) -> str:
    # AST columns count UTF-8 bytes. Splice the original literal's exact source span.
    lines = expression.strip().encode().splitlines(keepends=True)
    beginning = sum(len(line) for line in lines[: node.lineno - 1]) + node.col_offset
    ending = sum(len(line) for line in lines[: node.end_lineno - 1]) + node.end_col_offset
    source = b"".join(lines)
    return (source[:beginning] + str(value).encode() + source[ending:]).decode()


def _window_values(center: int, minimum: int, maximum: int) -> list[int]:
    if not center:
        return sorted({0, min(maximum, 1), min(maximum, 2)})
    return sorted(
        {min(max(floor(center * scale + 0.5), minimum), maximum) for scale in (0.8, 0.9, 1.0, 1.1, 1.2)}
    )


def _labeled_signal(panel: pl.DataFrame, labels: pl.DataFrame) -> pl.DataFrame:
    return panel.join(labels, on=["_id", "trade_date"], how="left", validate="1:1").sort("trade_date", "_id")


def _icir(observed: np.ndarray) -> float:
    std = observed.std(ddof=1) if len(observed) > 1 else 0.0
    return float(observed.mean() * sqrt(252) / std) if std > 0 else 0.0


def _parameter_cliff(
    bars_df: pl.DataFrame,
    req: FactorEvalRequest,
    financials_df: pl.DataFrame | None = None,
    ic: np.ndarray | None = None,
    labels: pl.DataFrame | None = None,
) -> dict[str, Any]:
    if not req.horizons or req.horizons[0] < 1:
        raise ValueError("Parameter sensitivity needs a positive primary trading-session horizon")
    expression = req.expression
    if expression is not None:
        safe, issues = ast_guard.validate_factor_ast(expression)
        if not safe:
            raise ValueError(issues[0])
        params = _expression_windows(expression)
    else:
        if req.factor_id not in factors.BUILTIN_FACTORS:
            raise ValueError(f"Unknown factor {req.factor_id!r}")
        windows = factors._BUILTIN_WINDOWS.get(req.factor_id, {}).copy()
        if financials_df is None or financials_df.is_empty():
            windows.pop("sue_history", None)
        if {"tail_vwap", "tail_volume_shares"}.issubset(bars_df.columns):
            windows.pop("adv_window", None)
        params = [
            (
                name,
                None,
                center,
                windows["skip_lag"] + 1 if name == "momentum_lag" else 1,
                windows["momentum_lag"] - 1 if name == "skip_lag" else 1024,
            )
            for name, center in windows.items()
        ]
    if not params:
        return {"passed": True, "max_neighbor_drop_pct": 0.0, "curves": []}

    horizon = req.horizons[0]
    target = f"_forward_{horizon}"
    if labels is None:
        labels = factors._label_table(factors._cutoff(factors._prepare_bars(bars_df), req), (horizon,))

    def reeval(variant: FactorEvalRequest, override: dict[str, int] | None = None) -> np.ndarray:
        panel = factors.factor_panel(bars_df, variant, financials_df, windows=override)
        return factors._daily_ic(_labeled_signal(panel, labels), target)

    center_ic = reeval(req) if ic is None else ic
    # Evaluate the same chronological holdout for every window. Common finite
    # sessions remove warm-up effects from comparisons within each curve.
    start = len(center_ic) // 2
    curves = []
    for name, node, center, minimum, maximum in params:
        values = _window_values(center, minimum, maximum)
        alternatives = []
        for value in values:
            variant = req
            override = None
            if value != center:
                if node is None:
                    override = {name: value}
                else:
                    variant = req.model_copy(
                        update={"expression": _perturb_expression(expression, node, value)}
                    )
            alternatives.append(center_ic[start:] if value == center else reeval(variant, override)[start:])
        estimates = np.stack(alternatives)
        common = np.isfinite(estimates).all(axis=0)
        n_observed = int(common.sum())
        selected = values.index(center)
        icirs = [_icir(series[common]) if n_observed >= 2 else None for series in estimates]
        ic_means = [float(series[common].mean()) if n_observed else None for series in estimates]
        reference = icirs[selected]
        drop = (
            max(0.0, max((reference - value) / abs(reference) for value in icirs))
            if reference is not None and abs(reference) > 1e-12
            else 0.0
        )
        curves.append(
            {
                "param": name,
                "values": values,
                "icir": icirs,
                "rank_ic": ic_means,
                "selected_index": selected,
                "max_neighbor_drop_pct": drop,
                "n_observed": n_observed,
                "has_evidence": n_observed >= 2
                and len(values) > 1
                and reference is not None
                and abs(reference) > 1e-12,
            }
        )
    max_drop = max(curve["max_neighbor_drop_pct"] for curve in curves)
    return {
        "passed": all(curve["has_evidence"] for curve in curves)
        and max_drop <= GATES["param_cliff_drop_max"],
        "max_neighbor_drop_pct": max_drop,
        "curves": curves,
    }


def run_parameter_cliff_test(bars_df: pl.DataFrame, req: FactorEvalRequest) -> dict[str, Any]:
    """Perturb supported temporal parameters by ±10% and ±20% on one common holdout."""
    return _parameter_cliff(bars_df, req)


def _scale_evidence(
    raw: pl.DataFrame, req: FactorEvalRequest, full: pl.DataFrame, financials: pl.DataFrame | None
) -> dict[str, Any]:
    reference = _signal_values(full)
    correlations = []
    witness_keys: list[str] = []
    for scale in (0.1, 10.0):
        # Convert the currency unit consistently across prices and their monetary
        # denominators so liquidity, valuation and size controls remain comparable.
        scaled_bars = raw.with_columns(
            (pl.col(name).cast(pl.Float64) * scale).alias(name)
            for name in raw.columns
            if name in _MONETARY_BAR_FIELDS
        )
        scaled_financials = financials
        if financials is not None and {"metric_code", "value"}.issubset(financials.columns):
            scaled_financials = financials.with_columns(
                pl.when(pl.col("metric_code").is_in(_MONETARY_FACT_METRICS))
                .then(pl.col("value").cast(pl.Float64) * scale)
                .otherwise(pl.col("value").cast(pl.Float64))
                .alias("value")
            )
        peer = _signal_values(factors.factor_panel(scaled_bars, req, scaled_financials))
        aligned = _aligned_signals(reference, peer)
        values, peer_values = aligned["factor_value"].to_numpy(), aligned["factor_value_peer"].to_numpy()
        mask = np.isfinite(values) != np.isfinite(peer_values)
        mask |= np.isinf(values) | np.isinf(peer_values)
        mask |= aligned.select(pl.any_horizontal(pl.col("_left", "_right").is_null())).to_series().to_numpy()
        for rows in factors._date_sections(aligned):
            day = aligned["trade_date"][int(rows[0])]
            rho = factors._correlation(values[rows], peer_values[rows], "spearman", min_names=3)
            if rho is not None:
                correlations.append(rho)
            if (
                mask[rows].any()
                or (rho is not None and rho < 0.9999)
                or (
                    rho is None
                    and np.isfinite(values[rows]).any()
                    and np.ptp(values[rows][np.isfinite(values[rows])]) > 0
                    and np.isfinite(values[rows]).sum() >= 3
                )
            ) and len(witness_keys) < _MAX_WITNESSES:
                witness_keys.append(f"{scale}/{day}")
    return {
        "min_spearman": min(correlations, default=None),
        "n_observed": len(correlations),
        "price_scales": [0.1, 10.0],
        "witness_keys": witness_keys,
    }


def _local_date(value: Any, zone: ZoneInfo) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return factors._timestamp(value, zone).astimezone(zone).date()


def _pit_evidence(financials: pl.DataFrame, as_of: date) -> dict[str, Any]:
    fields = {"report_period_end", "ann_date"}
    if not fields.issubset(financials.columns):
        return {
            "missing_fields": sorted(fields - set(financials.columns)),
            "n_observed": 0,
            "witness_keys": [],
        }
    count = 0
    witness_keys = []
    for row in financials.iter_rows(named=True):
        zone = factors._US_TZ if row.get("market") == "US_EQ" else factors._CN_TZ

        try:
            cutoff = min(as_of, _local_date(row["as_of"], zone)) if row.get("as_of") is not None else as_of
            report_end = _local_date(row["report_period_end"], zone)
            announcement = _local_date(row["ann_date"], zone)
            valid = report_end <= announcement <= cutoff
            publication = row.get("published_at")
            collected = row.get("first_seen_at")
            published = factors._timestamp(publication, zone) if publication is not None else None
            if published is not None:
                valid &= announcement <= published.astimezone(zone).date() <= cutoff
                if row.get("decision_at") is not None:
                    valid &= published <= factors._timestamp(row["decision_at"], zone)
                if collected is not None:
                    valid &= published <= factors._timestamp(collected, zone)
        except (ValueError, TypeError, OverflowError):
            valid = False
        if not valid:
            count += 1
            row_key = (
                row.get("fact_id") or _canonical_hash({key: str(value) for key, value in row.items()})[:16]
            )
            witness_keys.append(str(row_key))
    return {
        "n_observed": financials.height,
        "violation_count": count,
        "witness_keys": sorted(witness_keys)[:_MAX_WITNESSES],
    }


def _conservation_evidence(
    raw: pl.DataFrame, panel: pl.DataFrame, factor_req: FactorEvalRequest, target: str
) -> dict[str, Any]:
    from quant_core.backtest.engine import run_backtest
    from quant_core.risk.barra_opt import fit_barra_risk_model

    risk = fit_barra_risk_model(raw.with_columns(pl.col("_id").alias("symbol")))
    eigenvalue = float(np.linalg.eigvalsh(risk.factor_covariance)[0])
    # X F X' preserves PSD when F and the diagonal specific variances are PSD.
    if len(risk.symbols) <= 500:
        eigenvalue = min(eigenvalue, float(np.linalg.eigvalsh(risk.covariance)[0]))
    specific = float(np.min(risk.specific_variance))
    signals = panel.filter(pl.col("factor_value").is_finite()).select("_id", "trade_date", "factor_value")
    if signals.is_empty():
        return {
            "min_cov_eigenvalue": eigenvalue,
            "min_specific_variance": specific,
            "n_observed": 0,
            "has_evidence": False,
        }
    signals = signals.sort(["trade_date", "factor_value", "_id"], descending=[False, True, False])
    signals = (
        signals.with_columns(
            pl.int_range(pl.len()).over("trade_date").alias("_rank"),
            (pl.len().over("trade_date") / 5).ceil().alias("_count"),
        )
        .filter(pl.col("_rank") < pl.col("_count"))
        .select(
            pl.col("_id").alias("listing_id"),
            "trade_date",
            (1 / pl.col("_count")).alias("target_weight"),
        )
    )
    budget_error = float(
        signals.group_by("trade_date")
        .agg(pl.col("target_weight").sum())
        .select((pl.col("target_weight") - 1).abs().max())
        .item()
    )
    replay = run_backtest(
        raw,
        None,
        signals,
        pl.DataFrame(),
        BacktestRequest(
            experiment_id=target,
            universe_id=factor_req.universe_id,
            snapshot_id=factor_req.snapshot_id,
            engine=BacktestEngineMode.SCREENING,
        ),
    )
    p, b = replay.strategy_nav[-1], replay.bench_nav[-1]
    active_pct = 100 * (p / b - 1)
    attribution = replay.attribution
    costs_pct = replay.friction.total_friction_bps / 100
    brinson_total = (
        attribution.brinson_allocation_pct
        + attribution.brinson_selection_pct
        + attribution.brinson_interaction_pct
        - costs_pct
    )
    barra_total = (
        attribution.barra_style_pct
        + attribution.barra_industry_pct
        + attribution.barra_specific_alpha_pct
        + attribution.barra_cost_pct
    )
    return {
        "min_cov_eigenvalue": eigenvalue,
        "min_specific_variance": specific,
        "weight_simplex_error": budget_error,
        "brinson_closure_bp": max(
            100 * abs(brinson_total - active_pct), attribution.brinson_closure_error_bp
        ),
        "barra_closure_bp": max(100 * abs(barra_total - active_pct), attribution.barra_closure_error_bp),
        "n_observed": len(replay.dates),
        "has_evidence": replay.total_fills > 0,
        "portfolio_basis": "SCREENING factor top quintile replay",
    }


def _check(
    check_id: str,
    label: str,
    status: str,
    statistic: Any,
    threshold: str,
    metric: str,
    *,
    reason: str = "OK",
    n_observed: int = 0,
    detail: dict[str, Any] | None = None,
    witness_keys: list[str] | None = None,
) -> ProofCheckItem:
    return ProofCheckItem(
        check_id=check_id,
        suite=check_id.split(".")[0],
        label=label,
        status=status,
        statistic="—" if statistic is None else str(statistic),
        threshold=threshold,
        error_metric=metric,
        reason_code=reason,
        n_observed=n_observed,
        detail=detail or {},
        witness_keys=witness_keys or [],
    )


def _statistical_proof(
    raw: pl.DataFrame,
    panel: pl.DataFrame,
    labels: pl.DataFrame,
    daily_ic: np.ndarray,
    req: FormalVerifyRequest,
) -> tuple[ProofCheckItem, dict[str, Any] | None]:
    n_observed = int(np.isfinite(daily_ic).sum())
    l = _block_len(len(daily_ic))
    unsupported = n_observed < _MIN_SESSIONS or len(daily_ic) < 10 * l or req.bootstrap_b < 500
    proof = {
        "check_id": "T4.BOOTSTRAP_CI",
        "label": "平稳区块自助置信区间",
        "statistic": None,
        "metric": "rank_ic_ci95_lower",
        "threshold": "CI₉₅ lower > 0; B ≥ 500; mature sessions ≥ 120",
        "n_observed": n_observed,
    }
    if unsupported:
        return _check(
            **proof,
            status="insufficient",
            reason="BOOTSTRAP_SUPPORT",
            detail={"block_len": l, "n_boot": req.bootstrap_b},
        ), None
    aligned = _labeled_signal(panel, labels)
    nav, *_ = factors._paper_groups(aligned, raw["trade_date"].max())
    ls_daily = np.diff(np.r_[1.0, np.asarray(nav["LS"])]) / np.r_[1.0, np.asarray(nav["LS"])[:-1]]
    if len(ls_daily):
        ls_daily[-1] = np.nan  # the final signal has no next-session paper return
    settings = {"block_len": l, "n_boot": req.bootstrap_b, "seed": req.seed}
    rank_ic = _bootstrap_statistics(daily_ic, **settings)
    finite_ic = daily_ic[np.isfinite(daily_ic)]
    icir = _bootstrap_statistics(daily_ic, stat_fn=_icir, **settings) if np.ptp(finite_ic) else None
    long_short = (
        _bootstrap_statistics(
            ls_daily, stat_fn=lambda observed: np.expm1(252 * np.log1p(observed).mean()), **settings
        )
        if np.isfinite(ls_daily).any() and np.nanmin(ls_daily) > -1
        else None
    )
    low = rank_ic["ci95"][0]
    proof["statistic"] = low
    return _check(
        **proof,
        status="pass" if low > 0 else "fail",
        reason="OK" if low > 0 else "BOOTSTRAP_CI_INCLUDES_NULL",
        detail={"rank_ic": rank_ic, "icir": icir, "long_short": long_short, "seed": req.seed},
    ), rank_ic


def _candidate_proofs(
    matrix: np.ndarray | None,
    benchmark: np.ndarray | None,
    sessions: list[date],
    req: FormalVerifyRequest,
    target: str,
    rank_ci: dict[str, Any] | None,
) -> tuple[ProofCheckItem, ProofCheckItem, float | None, float | None]:
    from .cpcv import run_cpcv_gate

    cpcv = {
        "check_id": "T4.CPCV_PBO_DSR",
        "label": "组合净化交叉验证",
        "statistic": None,
        "metric": "pbo",
        "threshold": (
            f"PBO ≤ {GATES['cpcv_pbo_max']}; DSR ≥ {GATES['dsr_min']}; "
            f"OOS/IS ≥ {GATES['oos_is_ratio_min']}; purge conflicts = 0"
        ),
    }
    multiple = {
        "check_id": "T4.BH_FDR_SPA",
        "label": "多重检验与优势预测能力",
        "statistic": None,
        "metric": "fdr_q_value",
        "threshold": f"BH q ≤ {GATES['bh_fdr_q_max']}; SPA p ≤ {GATES['spa_p_max']}",
    }
    cpcv_status = "insufficient" if req.run_cpcv else "not_applicable"
    cpcv_reason = "MISSING_CANDIDATE_MATRIX" if req.run_cpcv else "CPCV_DISABLED"
    if matrix is None:
        # Pre-registered slots without a supplied observation contribute p=1.
        # The target-only RankIC q-value cannot establish family-level SPA.
        evidence = {}
        q = None
        if rank_ci is not None:
            target_p = rank_ci["p_value_one_sided"]
            result = benjamini_hochberg_fdr([target_p], req.trial_count_m)
            q = result["q_values"][0]
            evidence = {
                "target_rank_ic_p_value": target_p,
                "by_q_value": result["by_q_values"][0],
                "trial_count_m": req.trial_count_m,
                "observed_trials": 1,
                "p_value_basis": "Target RankIC only",
            }
            multiple["statistic"] = q
        return (
            _check(**cpcv, status=cpcv_status, reason=cpcv_reason),
            _check(**multiple, status="insufficient", reason="MISSING_CANDIDATE_MATRIX", detail=evidence),
            q,
            None,
        )
    try:
        excess = _return_differences(matrix, benchmark)
        if len(excess) > len(sessions):
            raise ValueError("Candidate matrix is longer than the bars session calendar")
        candidate_dates = sessions[-len(excess) :]
        spa, items = _candidate_fdr(
            matrix, benchmark, req.trial_count_m, n_boot=req.bootstrap_b, seed=req.seed
        )
    except ValueError as error:
        detail = {"message": str(error)}
        return (
            _check(**cpcv, status=cpcv_status, reason="INVALID_CANDIDATE_EVIDENCE", detail=detail),
            _check(**multiple, status="insufficient", reason="INVALID_CANDIDATE_EVIDENCE", detail=detail),
            None,
            None,
        )

    q = items[0]["q_value"]  # API contract orders the target candidate first.
    p = spa["spa_p_value"]
    multiple["statistic"] = q
    detail = {
        **spa,
        "target_candidate_index": 0,
        "fdr_items": items,
        "trial_count_m": max(req.trial_count_m, len(items)),
        "n_boot": req.bootstrap_b,
        "window": [candidate_dates[0].isoformat(), candidate_dates[-1].isoformat()],
    }
    if len(excess) < _MIN_SESSIONS or req.bootstrap_b < 500 or np.ptp(excess[:, 0]) == 0:
        status, reason = "insufficient", "MULTIPLE_TESTING_SUPPORT"
    elif q <= GATES["bh_fdr_q_max"] and p <= GATES["spa_p_max"]:
        status, reason = "pass", "OK"
    elif 1 / (req.bootstrap_b + 1) * detail["trial_count_m"] > GATES["bh_fdr_q_max"]:
        status, reason = "insufficient", "FDR_RESOLUTION"
    else:
        status, reason = "fail", "MULTIPLE_TESTING_REJECTED"
    fdr_check = _check(**multiple, status=status, reason=reason, n_observed=len(excess), detail=detail)
    if not req.run_cpcv or len(excess) < _MIN_SESSIONS:
        return (
            _check(
                **cpcv,
                status=cpcv_status,
                reason="CPCV_SUPPORT" if req.run_cpcv else cpcv_reason,
                n_observed=len(excess),
            ),
            fdr_check,
            q,
            p,
        )

    result = run_cpcv_gate(
        excess,
        candidate_dates,
        CpcvRequest(
            experiment_id=req.experiment_id or target,
            snapshot_id=req.snapshot_id,
            trial_count_m=req.trial_count_m,
        ),
    )
    detail = {
        "pbo": result.pbo,
        "dsr": result.dsr,
        "oos_is_ratio": result.oos_is_ratio,
        "purge_conflicts": result.purge_conflicts,
        "negative_path_count": result.negative_path_count,
        "path_sharpes": result.path_sharpes,
        "walk_forward_folds": result.walk_forward_folds,
    }
    cpcv["statistic"] = result.pbo
    status, reason = {
        GateVerdict.PASS: ("pass", "OK"),
        GateVerdict.REJECT: ("fail", "CPCV_GATE_REJECTED"),
        GateVerdict.INCONCLUSIVE: ("insufficient", "CPCV_SUPPORT"),
    }[result.verdict]
    return (
        _check(**cpcv, status=status, reason=reason, n_observed=len(excess), detail=detail),
        fdr_check,
        q,
        p,
    )


def _canonical_hash(value: Any) -> str:
    return sha256(dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _frame_hash(frame: pl.DataFrame | None) -> str | None:
    if frame is None:
        return None
    fields = sorted(frame.columns)
    if not fields:
        return sha256(b"").hexdigest()
    canonical = frame.select(fields).sort(fields).rechunk()
    return sha256(canonical.write_ipc(None).getvalue()).hexdigest()


def _array_hash(values: np.ndarray | None) -> str | None:
    if values is None:
        return None
    array = np.asarray(values, dtype="<f8")
    return sha256(str(array.shape).encode() + array.tobytes()).hexdigest()


def run_formal_verification(
    bars_df: pl.DataFrame,
    req: FormalVerifyRequest,
    *,
    financials_df: pl.DataFrame | None = None,
    candidate_matrix: np.ndarray | None = None,
    benchmark_returns: np.ndarray | None = None,
) -> FormalProofResult:
    """Run the ten proofs on a frozen snapshot.

    Candidate rows cover the bars calendar or a trailing contiguous window;
    column zero is the candidate being admitted. Strategy checks without a
    supplied candidate matrix explicitly report insufficient evidence.
    """
    if req.bootstrap_b < 1 or req.trial_count_m < 1 or req.seed < 0:
        raise ValueError("bootstrap_b and trial_count_m must be positive, and seed must be nonnegative")
    factor_req = FactorEvalRequest(
        factor_id=req.factor_id or ("custom" if req.expression is not None else "alpha_vp_corr_20d"),
        expression=req.expression,
        universe_id=req.universe_id,
        snapshot_id=req.snapshot_id,
    )
    raw = factors._prepare_bars(bars_df)
    sessions = _session_dates(raw)
    as_of = sessions[-1] if sessions else None
    target = req.experiment_id or factor_req.factor_id
    manifest = {
        "request": req.model_dump(mode="json"),
        "gates": GATES,
        "bars_sha256": _frame_hash(bars_df),
        "financials_sha256": _frame_hash(financials_df),
        "candidate_sha256": _array_hash(candidate_matrix),
        "benchmark_sha256": _array_hash(benchmark_returns),
    }

    if req.expression is not None:
        safe, issues = ast_guard.validate_factor_ast(req.expression)
    else:
        safe = factor_req.factor_id in factors.BUILTIN_FACTORS
        issues = [] if safe else ["Factor is not a registered builtin"]
    checks = [
        _check(
            "T1.AST_CAUSAL",
            "表达式因果性",
            "pass" if safe else "fail",
            len(issues),
            "0",
            "unsafe_nodes",
            reason="OK" if safe else "LOOKAHEAD_AST_VIOLATION",
            detail={"issues": issues, "input_sha256": _canonical_hash(manifest)},
        ),
    ]
    prefix = None
    scale = None
    panel = None
    daily_ic = np.empty(0)
    labels = pl.DataFrame()
    coverage = None
    raw_coverage = None
    processed_coverage = None
    if safe and sessions:
        try:
            panel = factors.factor_panel(bars_df, factor_req, financials_df)
            prefix = _check_prefix_invariance(
                bars_df,
                factor_req,
                cutoffs=req.prefix_cutoffs,
                tol=GATES["prefix_invariance_tol"],
                full_panel=panel,
                financials_df=financials_df,
            )
            if not prefix["witness_keys"]:
                scale = _scale_evidence(bars_df, factor_req, panel, financials_df)
                labels = factors._label_table(raw, (1,))
                daily_ic = factors._daily_ic(_labeled_signal(panel, labels), "_forward_1")
                eligible = int(panel["factor_eligible"].sum())
                if eligible:
                    raw_coverage, processed_coverage = (
                        float(
                            panel.select((pl.col(field).is_finite() & pl.col("factor_eligible")).sum()).item()
                            / eligible
                        )
                        for field in ("factor_raw", "factor_value")
                    )
                    coverage = min(raw_coverage, processed_coverage)
        except (ValueError, pl.exceptions.PolarsError) as error:
            checks[0].detail["signal_error"] = str(error)

    baseline_missing = prefix is None or not prefix["n_compared"]
    prefix_failed = prefix is not None and bool(prefix["witness_keys"])
    status = "fail" if prefix_failed else "insufficient" if baseline_missing else "pass"
    reason = "LOOKAHEAD_PREFIX_VIOLATION" if prefix_failed else "PREFIX_SUPPORT" if baseline_missing else "OK"
    checks.append(
        _check(
            "T1.PREFIX_INVARIANCE",
            "前缀不变性",
            status,
            prefix["max_drift"] if prefix is not None else None,
            f"≤ {GATES['prefix_invariance_tol']}",
            "max_abs_drift",
            reason=reason,
            n_observed=prefix["n_compared"] if prefix else 0,
            detail=prefix,
            witness_keys=prefix["witness_keys"] if prefix else None,
        )
    )
    scale_witness = scale["witness_keys"] if scale else []
    scale_rho = scale["min_spearman"] if scale else None
    checks.append(
        _check(
            "T2.SCALE_INVARIANCE",
            "价格尺度保序性",
            "fail" if scale_witness else "insufficient" if scale_rho is None else "pass",
            scale_rho,
            "Spearman ≥ 0.9999",
            "min_spearman",
            reason="SCALE_RANK_DRIFT" if scale_witness else "SCALE_SUPPORT" if scale_rho is None else "OK",
            n_observed=scale["n_observed"] if scale else 0,
            detail=scale,
            witness_keys=scale_witness,
        )
    )
    maturity = int(np.isfinite(daily_ic).sum())
    coverage_status = (
        "insufficient"
        if coverage is None
        else "fail"
        if coverage < GATES["min_coverage"]
        else "insufficient"
        if maturity < _MIN_SESSIONS
        else "pass"
    )
    checks.append(
        _check(
            "T2.COVERAGE",
            "因子有效覆盖",
            coverage_status,
            coverage,
            f"coverage ≥ {GATES['min_coverage']}; mature sessions ≥ {_MIN_SESSIONS}",
            "coverage_ratio",
            reason="OK"
            if coverage_status == "pass"
            else "LOW_COVERAGE"
            if coverage_status == "fail"
            else "COVERAGE_SUPPORT",
            n_observed=maturity,
            detail={
                "coverage_ratio": coverage,
                "raw_coverage_ratio": raw_coverage,
                "processed_coverage_ratio": processed_coverage,
                "mature_sessions": maturity,
                "sessions": len(daily_ic),
            },
        )
    )

    pit = _pit_evidence(financials_df, as_of) if financials_df is not None and as_of is not None else {}
    pit_count = pit.get("violation_count")
    if pit_count:
        pit_status, pit_reason = "fail", "PIT_DATE_INVERSION"
    elif pit.get("n_observed"):
        pit_status, pit_reason = "pass", "OK"
    elif financials_df is None and factor_req.factor_id != "alpha_earn_sue_pit":
        pit_status, pit_reason = "not_applicable", "NO_FINANCIAL_FACTS"
    else:
        pit_status, pit_reason = "insufficient", "PIT_SUPPORT"
    checks.append(
        _check(
            "T3.PIT_MONOTONICITY",
            "PIT 时间单调性",
            pit_status,
            pit_count,
            "0",
            "date_inversions",
            reason=pit_reason,
            n_observed=pit.get("n_observed", 0),
            detail=pit,
            witness_keys=pit.get("witness_keys"),
        )
    )
    conservation = {}
    if panel is not None and not prefix_failed and "close" in raw.columns and len(sessions) >= 2:
        try:
            conservation = _conservation_evidence(raw, panel, factor_req, target)
        except (ValueError, np.linalg.LinAlgError, pl.exceptions.PolarsError) as error:
            conservation = {"message": str(error)}
    errors = [conservation.get(key) for key in ("brinson_closure_bp", "barra_closure_bp")]
    closure = max(errors) if all(error is not None for error in errors) else None
    conservation_failed = (
        conservation.get("min_cov_eigenvalue", 0) < -1e-10
        or conservation.get("min_specific_variance", 0) < -1e-10
        or conservation.get("weight_simplex_error", 0) > 1e-6
        or (closure is not None and closure > GATES["closure_error_bp_max"])
    )
    conservation_status = (
        "fail" if conservation_failed else "pass" if conservation.get("has_evidence") else "insufficient"
    )
    checks.append(
        _check(
            "T3.CONSERVATION_PSD",
            "守恒与协方差半正定",
            conservation_status,
            closure,
            f"λmin ≥ -1e-10; simplex error ≤ 1e-6; closure ≤ {GATES['closure_error_bp_max']} bp",
            "closure_bp",
            reason="CONSERVATION_VIOLATION"
            if conservation_failed
            else "OK"
            if conservation.get("has_evidence")
            else "CONSERVATION_SUPPORT",
            n_observed=conservation.get("n_observed", 0),
            detail=conservation,
        )
    )

    bootstrap_check, rank_ci = _statistical_proof(raw, panel, labels, daily_ic, req)
    checks.append(bootstrap_check)
    cpcv_check, fdr_check, q, spa = _candidate_proofs(
        candidate_matrix, benchmark_returns, sessions, req, target, rank_ci
    )
    checks.extend((cpcv_check, fdr_check))
    cliff = None
    cliff_status, cliff_reason = "insufficient", "PARAM_CLIFF_SUPPORT"
    if maturity:
        cliff = _parameter_cliff(bars_df, factor_req, financials_df, daily_ic, labels)
        if not cliff["curves"]:
            cliff_status, cliff_reason = "not_applicable", "NO_WINDOW_PARAMETER"
        elif any(not curve["has_evidence"] for curve in cliff["curves"]):
            pass
        elif cliff["max_neighbor_drop_pct"] > 0.50:
            cliff_status, cliff_reason = "fail", "PARAMETER_CLIFF"
        elif cliff["max_neighbor_drop_pct"] > GATES["param_cliff_drop_max"]:
            cliff_status, cliff_reason = "warn", "PARAMETER_SENSITIVITY"
        else:
            cliff_status, cliff_reason = "pass", "OK"
    checks.append(
        _check(
            "T4.PARAM_CLIFF",
            "参数邻域稳定性",
            cliff_status,
            cliff["max_neighbor_drop_pct"] if cliff is not None else None,
            f"warn > {GATES['param_cliff_drop_max']}; fail > 0.50",
            "max_neighbor_drop_pct",
            reason=cliff_reason,
            n_observed=max((curve["n_observed"] for curve in cliff["curves"]), default=0) if cliff else 0,
            detail=cliff,
        )
    )

    failures = [check.check_id for check in checks if check.status == "fail"]
    unresolved = [check.check_id for check in checks if check.status in ("warn", "insufficient")]
    verdict = GateVerdict.REJECT if failures else GateVerdict.INCONCLUSIVE if unresolved else GateVerdict.PASS
    proof = FormalProofResult(
        verification_id="FV-" + checks[0].detail["input_sha256"][:16],
        target_id=target,
        universe_id=req.universe_id,
        snapshot_id=req.snapshot_id or "unversioned",
        as_of=as_of.isoformat() if as_of is not None else "",
        passed=verdict == GateVerdict.PASS,
        verdict=verdict,
        proofs_passed=sum(check.status == "pass" for check in checks),
        proofs_total=len(checks),
        max_prefix_drift=prefix["max_drift"] if prefix else None,
        bootstrap_rank_ic_ci95=rank_ci["ci95"] if rank_ci else None,
        bootstrap_p_value=rank_ci["p_value"] if rank_ci else None,
        bh_fdr_q_value=q,
        spa_p_value=spa,
        max_param_cliff_drop_pct=cliff["max_neighbor_drop_pct"] if cliff else None,
        blocking_failures=failures + unresolved,
        checks=checks,
        param_sensitivity=cliff["curves"] if cliff else [],
        proof_sha256="",
    )
    return proof.model_copy(
        update={
            "proof_sha256": _canonical_hash(
                proof.model_dump(
                    mode="json",
                    exclude={"proof_sha256", "artifact_uri"},
                )
            )
        }
    )
