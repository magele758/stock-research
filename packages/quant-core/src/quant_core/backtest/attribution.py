"""Independent Brinson-Fachler and Barra attribution with Carino linking."""

from __future__ import annotations

import numpy as np


def brinson_fachler(
    portfolio_weights: np.ndarray,
    benchmark_weights: np.ndarray,
    security_returns: np.ndarray,
    industries: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Allocation, selection and interaction by sorted industry, in return units.

    For groups outside the benchmark, use the equal-weight return of their
    securities as a reference. For groups outside the portfolio, set its
    hypothetical sector return equal to the sector benchmark return.
    """
    wp = np.asarray(portfolio_weights, dtype=float)
    wb = np.asarray(benchmark_weights, dtype=float)
    returns = np.asarray(security_returns, dtype=float)
    industries = np.asarray(industries)
    if wp.ndim != 1 or not (wp.shape == wb.shape == returns.shape == industries.shape):
        raise ValueError("Brinson requires aligned vectors of holdings, returns and industries")
    _, group = np.unique(industries, return_inverse=True)
    pw = np.bincount(group, weights=wp)
    bw = np.bincount(group, weights=wb)
    reference = np.bincount(group, weights=returns) / np.bincount(group)
    rb = np.divide(np.bincount(group, weights=wb * returns), bw, out=reference, where=bw != 0)
    rp = np.divide(np.bincount(group, weights=wp * returns), pw, out=rb.copy(), where=pw != 0)
    return (pw - bw) * (rb - wb @ returns), bw * (rp - rb), (pw - bw) * (rp - rb)


def barra_decomposition(
    active_weights: np.ndarray,
    style_exposures: np.ndarray,
    industry_dummies: np.ndarray,
    factor_returns: np.ndarray,
    residual_returns: np.ndarray,
) -> tuple[np.ndarray, float, float]:
    """Map fitted style and industry factor returns plus residuals to P&L."""
    active = np.asarray(active_weights, dtype=float)
    styles = np.nan_to_num(np.asarray(style_exposures, dtype=float), nan=0.0)
    industries = np.asarray(industry_dummies, dtype=float)
    factors = np.asarray(factor_returns, dtype=float)
    residuals = np.asarray(residual_returns, dtype=float)
    if (
        active.ndim != 1
        or styles.ndim != 2
        or industries.ndim != 2
        or styles.shape[0] != len(active)
        or industries.shape[0] != len(active)
        or residuals.shape != active.shape
        or factors.shape != (styles.shape[1] + industries.shape[1],)
    ):
        raise ValueError("Barra attribution requires aligned active weights, exposures and factor returns")
    n_styles = styles.shape[1]
    return (
        (active @ styles) * factors[:n_styles],
        float(active @ industries @ factors[n_styles:]),
        float(active @ residuals),
    )


def _log_spread_slope(portfolio: np.ndarray, benchmark: np.ndarray) -> np.ndarray:
    difference = portfolio - benchmark
    denominator = 1 + benchmark
    return np.divide(
        np.log1p(difference / denominator),
        difference,
        out=np.array(1 / denominator, copy=True),
        where=difference != 0,
    )


def relative_link_weights(portfolio_returns: np.ndarray, benchmark_returns: np.ndarray) -> np.ndarray:
    """Carino weights linking additive daily effects to terminal relative NAV."""
    rp = np.asarray(portfolio_returns, dtype=float)
    rb = np.asarray(benchmark_returns, dtype=float)
    if rp.shape != rb.shape or np.any(rp <= -1) or np.any(rb <= -1):
        raise ValueError("Carino linking requires aligned returns above -100%")
    p = np.expm1(np.log1p(rp).sum())
    b = np.expm1(np.log1p(rb).sum())
    return _log_spread_slope(rp, rb) / (_log_spread_slope(p, b) * (1 + b))
