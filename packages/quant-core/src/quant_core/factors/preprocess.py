"""Daily factor cross-sections, with audited imputation and weighted projection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
import polars as pl
from scipy.stats import rankdata

from quant_core.schemas import PreprocessConfig

MIN_INDUSTRY_OBSERVATIONS = 10
ORTHOGONALITY_TOLERANCE = 1e-8


@dataclass
class PreprocessDiagnostics:
    eligible: int = 0
    observed: int = 0
    industry_filled: int = 0
    market_filled: int = 0
    low_coverage_days: int = 0
    constant_days: int = 0
    omitted_design: set[str] = field(default_factory=set)
    residual_exposures: dict[str, float] = field(default_factory=dict)

    @property
    def coverage(self) -> float:
        return self.observed / self.eligible if self.eligible else 0.0


def numeric_column(frame: pl.DataFrame, name: str) -> np.ndarray:
    if name not in frame.columns:
        return np.full(frame.height, np.nan)
    values = frame[name].cast(pl.Float64, strict=False).to_numpy()
    return np.where(np.isfinite(values), values, np.nan)


def tradability(frame: pl.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return suspended, one-price up-limit and one-price down-limit masks."""
    suspended = (
        frame["is_suspended"].cast(pl.Boolean).fill_null(False).to_numpy()
        if "is_suspended" in frame.columns
        else np.zeros(frame.height, dtype=bool)
    )
    opening = numeric_column(frame, "open")
    one_price = (np.abs(opening - numeric_column(frame, "high")) <= 1e-8) & (
        np.abs(opening - numeric_column(frame, "low")) <= 1e-8
    )
    return (
        suspended,
        one_price & (np.abs(opening - numeric_column(frame, "up_limit")) <= 1e-8),
        one_price & (np.abs(opening - numeric_column(frame, "down_limit")) <= 1e-8),
    )


def eligible_mask(frame: pl.DataFrame, config: PreprocessConfig) -> np.ndarray:
    if not config.filter_limit_st:
        return np.ones(frame.height, dtype=bool)
    suspended, up, down = tradability(frame)
    st = (
        frame["is_st"].cast(pl.Boolean).fill_null(False).to_numpy()
        if "is_st" in frame.columns
        else np.zeros(frame.height, dtype=bool)
    )
    return ~(suspended | up | down | st)


def winsorize_mad(values: np.ndarray, k: float) -> np.ndarray:
    """Clip finite values at median ± k·1.4826·MAD, falling back to IQR and SD."""
    observed = values[np.isfinite(values)]
    if not observed.size:
        return values.copy()
    median = np.median(observed)
    scale = 1.4826 * np.median(np.abs(observed - median))
    if scale == 0:
        lower, upper = np.quantile(observed, [0.25, 0.75])
        scale = (upper - lower) / 1.349
    if scale == 0:
        scale = np.std(observed)
    return np.clip(values, median - k * scale, median + k * scale)


def neutralize(
    values: np.ndarray,
    industries: Sequence[str] | np.ndarray | None = None,
    market_caps: np.ndarray | None = None,
    *,
    styles: Mapping[str, np.ndarray] | None = None,
    weights: np.ndarray | None = None,
) -> tuple[np.ndarray, dict[str, float]]:
    """Project onto the weighted residual space of intercept, industry, size and styles."""
    y = np.asarray(values, dtype=float)
    if y.ndim != 1 or not y.size or not np.isfinite(y).all():
        raise ValueError("Neutralization requires a nonempty finite factor vector")

    design = {"Intercept": np.ones_like(y)}
    reference = None
    if industries is not None:
        groups = np.asarray(industries, dtype=str)
        if groups.shape != y.shape:
            raise ValueError("Industry membership must match the factor vector")
        group_names = np.unique(groups)
        reference = (f"Industry:{group_names[0]}", (groups == group_names[0]).astype(float))
        for group in group_names[1:]:
            design[f"Industry:{group}"] = (groups == group).astype(float)
    if market_caps is not None:
        cap = np.asarray(market_caps, dtype=float)
        if cap.shape != y.shape or not np.isfinite(cap).all() or np.any(cap <= 0):
            raise ValueError("Market capitalizations must be positive and finite")
        design["Size"] = np.log(cap)
    for name, exposure in (styles or {}).items():
        if name not in design:
            design[name] = np.asarray(exposure, dtype=float)

    if any(column.shape != y.shape or not np.isfinite(column).all() for column in design.values()):
        raise ValueError("Every design column must be finite and match the factor vector")
    weight = np.ones_like(y) if weights is None else np.asarray(weights, dtype=float)
    if weight.shape != y.shape or not np.isfinite(weight).all() or np.any(weight <= 0):
        raise ValueError("Regression weights must be positive and finite")
    weight = weight / weight.max()
    root_weight = np.sqrt(weight)
    x = np.column_stack(list(design.values()))
    # This reparameterizes the same column space and avoids a large common offset
    # in log capitalization dominating the singular-value rank threshold.
    x[:, 1:] -= np.average(x[:, 1:], axis=0, weights=weight)
    weighted_x = x * root_weight[:, None]
    norms = np.linalg.norm(weighted_x, axis=0)
    scaled_x = weighted_x / np.where(norms > 0, norms, 1.0)
    u, singular, _ = np.linalg.svd(scaled_x, full_matrices=False)
    tolerance = np.finfo(float).eps * max(scaled_x.shape) * singular[0]
    basis = u[:, singular > tolerance]
    weighted_y = y * root_weight
    residual = weighted_y - basis @ (basis.T @ weighted_y)
    residual -= basis @ (basis.T @ residual)
    residual_norm = np.linalg.norm(residual)
    if residual_norm <= np.finfo(float).eps * max(scaled_x.shape) * np.linalg.norm(weighted_y):
        residual[:] = 0
        residual_norm = 0.0

    if reference is not None:
        design[reference[0]] = reference[1]
    errors = {}
    for name, column in design.items():
        weighted_column = column * root_weight
        norm = np.linalg.norm(weighted_column) * residual_norm
        errors[name] = float(abs(weighted_column @ residual) / norm) if norm > 0 else 0.0
    if max(errors.values()) > ORTHOGONALITY_TOLERANCE:
        raise ArithmeticError("Factor residual exceeds the weighted orthogonality tolerance")
    return residual / root_weight, errors


def preprocess_panel(
    panel: pl.DataFrame,
    config: PreprocessConfig,
    *,
    style_columns: Sequence[str] = (),
    allow_imputation: bool = True,
) -> tuple[pl.DataFrame, PreprocessDiagnostics]:
    """Transform each eligible trade-date cross-section independently."""
    if not np.isfinite(config.mad_k) or config.mad_k <= 0:
        raise ValueError("mad_k must be positive and finite")
    if not 0 <= config.min_coverage <= 1:
        raise ValueError("min_coverage must be between zero and one")

    panel = panel.sort(["trade_date", "_id"])
    eligible = eligible_mask(panel, config)
    raw = numeric_column(panel, "factor_raw")
    caps = numeric_column(panel, "float_market_cap")
    use_cap = config.neutralize_size and "float_market_cap" in panel.columns
    industries = (
        panel["industry_l1"].cast(pl.String).fill_null("Unknown").to_numpy()
        if "industry_l1" in panel.columns
        else np.full(panel.height, "Unknown")
    )
    styles = {name: numeric_column(panel, name) for name in style_columns}
    weight = numeric_column(panel, "neutralization_weight")
    weighted = "neutralization_weight" in panel.columns
    result = np.full(panel.height, np.nan)
    diagnostics = PreprocessDiagnostics(
        eligible=int(eligible.sum()),
        observed=int(np.sum(eligible & np.isfinite(raw))),
    )
    if config.neutralize_size and not use_cap:
        diagnostics.omitted_design.add("Size")
    if config.neutralize_industry and "industry_l1" not in panel.columns:
        diagnostics.omitted_design.add("Industry")
    all_dates = panel["trade_date"].to_numpy()
    boundaries = np.flatnonzero(all_dates[1:] != all_dates[:-1]) + 1
    for rows in np.split(np.arange(panel.height), boundaries):
        selected = rows[eligible[rows]]
        if not selected.size:
            continue
        values = raw[selected]
        observed = np.isfinite(values)
        if not observed.any() or observed.mean() < config.min_coverage:
            diagnostics.low_coverage_days += 1
            continue
        groups = industries[selected]
        if config.winsorize_mad:
            values = winsorize_mad(values, config.mad_k)
        if allow_imputation and not observed.all():
            market_median = float(np.median(values[observed]))
            for group in np.unique(groups[~observed]):
                members = groups == group
                group_observed = values[members & observed]
                missing = members & ~observed
                if group_observed.size >= MIN_INDUSTRY_OBSERVATIONS:
                    values[missing] = np.median(group_observed)
                    diagnostics.industry_filled += int(missing.sum())
                else:
                    values[missing] = market_median
                    diagnostics.market_filled += int(missing.sum())
        valid = np.isfinite(values)
        if use_cap:
            valid &= np.isfinite(caps[selected]) & (caps[selected] > 0)
        if weighted:
            valid &= np.isfinite(weight[selected]) & (weight[selected] > 0)
        if valid.mean() < config.min_coverage:
            diagnostics.low_coverage_days += 1
            continue
        if valid.sum() < 2:
            diagnostics.constant_days += 1
            continue
        selected = selected[valid]
        exposure = {}
        for name, column in styles.items():
            selected_column = column[selected]
            if np.isfinite(selected_column).all():
                exposure[name] = selected_column
            else:
                diagnostics.omitted_design.add(name)
        values, errors = neutralize(
            values[valid],
            industries[selected] if config.neutralize_industry else None,
            caps[selected] if use_cap else None,
            styles=exposure,
            weights=weight[selected] if weighted else None,
        )
        for name, error in errors.items():
            diagnostics.residual_exposures[name] = max(error, diagnostics.residual_exposures.get(name, 0.0))
        if np.ptp(values) == 0:
            diagnostics.constant_days += 1
            continue
        if config.standardize == "rank":
            values = (rankdata(values, method="average") - 0.5) / len(values) - 0.5
        else:
            weights = weight[selected] if weighted else None
            centered = values - np.average(values, weights=weights)
            scale = np.sqrt(np.average(centered**2, weights=weights))
            if scale <= 0 or not np.isfinite(scale):
                diagnostics.constant_days += 1
                continue
            values = centered / scale
        result[selected] = values
    return panel.with_columns(
        pl.Series("factor_value", result, nan_to_null=True),
        pl.Series("factor_eligible", eligible),
    ), diagnostics
