"""Purged combinatorial validation of frozen daily candidate return series."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from itertools import combinations, pairwise
from math import e, isfinite, sqrt

import numpy as np
from scipy.stats import kurtosis as sample_kurtosis
from scipy.stats import norm, rankdata
from scipy.stats import skew as sample_skew

from quant_core.schemas import INSTITUTIONAL_GATES, CpcvRequest, CpcvResult, GateVerdict

_ANNUALIZATION = sqrt(252)
_PBO_LIMIT = INSTITUTIONAL_GATES["cpcv_pbo_max"]
_DSR_LIMIT = INSTITUTIONAL_GATES["dsr_min"]
_OOS_IS_LIMIT = INSTITUTIONAL_GATES["oos_is_ratio_min"]


@dataclass(frozen=True)
class PurgedSplit:
    test_folds: tuple[int, ...]
    train: np.ndarray
    test: np.ndarray
    purged: np.ndarray
    embargoed: np.ndarray


def _purged_split(
    length: int, test: np.ndarray, folds: tuple[int, ...], purge: int, embargo: int
) -> PurgedSplit:
    """Use session offsets and include the label's endpoint quote in the overlap test.

    A row anchored at i uses [i, i + purge + 1). Embargo starts after the
    final test label endpoint of each contiguous test block.
    """
    anchors = np.arange(length)
    test_mask = np.zeros(length, dtype=bool)
    test_mask[test] = True
    overlap = np.zeros(length, dtype=bool)
    buffer = np.zeros(length, dtype=bool)
    for block in np.split(test, np.flatnonzero(np.diff(test) != 1) + 1):
        start, end = int(block[0]), int(block[-1]) + purge
        overlap[max(0, start - purge) : end + 1] = True
        buffer[end + 1 : end + embargo + 1] = True
    return PurgedSplit(
        folds,
        anchors[~(test_mask | overlap | buffer)],
        test,
        anchors[overlap & ~test_mask],
        anchors[buffer & ~(test_mask | overlap)],
    )


def build_cpcv_splits(
    n_observations: int,
    n_splits: int = 6,
    k_test: int = 2,
    purge_days: int = 5,
    embargo_days: int = 10,
) -> tuple[list[np.ndarray], list[PurgedSplit], list[tuple[int, ...]]]:
    """Return contiguous folds, split masks and split IDs by OOS path and fold."""
    if not 2 <= n_splits <= n_observations or not 1 <= k_test < n_splits:
        raise ValueError("Require 1 <= k_test < n_splits <= number of observations")
    if min(purge_days, embargo_days) < 0:
        raise ValueError("Purge and embargo lengths must be nonnegative")
    folds = np.array_split(np.arange(n_observations), n_splits)
    splits = []
    assignments = [[] for _ in folds]
    for combo in combinations(range(n_splits), k_test):
        test = np.concatenate([folds[index] for index in combo])
        for index in combo:
            assignments[index].append(len(splits))
        splits.append(_purged_split(n_observations, test, combo, purge_days, embargo_days))
    return folds, splits, list(zip(*assignments, strict=True))


def compute_deflated_sharpe(
    sharpe_daily: float, n_obs: int, n_trials: int, trial_sr_std: float, skew: float, kurtosis: float
) -> float:
    """Bailey–López de Prado DSR using daily Sharpe and ordinary kurtosis."""
    if n_obs < 2 or n_trials < 1 or trial_sr_std < 0:
        raise ValueError(
            "DSR requires at least two observations, one trial, and nonnegative trial dispersion"
        )
    if not all(isfinite(value) for value in (sharpe_daily, trial_sr_std, skew, kurtosis)) or kurtosis < 1:
        raise ValueError("DSR requires finite moments and ordinary kurtosis >= 1")
    benchmark = 0.0
    if n_trials > 1:
        gamma = float(np.euler_gamma)
        benchmark = trial_sr_std * (
            (1 - gamma) * norm.isf(1 / n_trials) + gamma * norm.isf(1 / (n_trials * e))
        )
    variance = 1 - skew * sharpe_daily + (kurtosis - 1) * sharpe_daily**2 / 4
    if variance <= 0:
        raise ValueError("Sharpe sampling variance must be positive")
    return float(norm.cdf((sharpe_daily - benchmark) * sqrt(n_obs - 1) / sqrt(variance)))


def _sharpe(returns: np.ndarray) -> np.ndarray:
    mean = np.mean(returns, axis=0)
    if len(returns) < 2:
        return np.full(mean.shape, np.nan)
    std = np.std(returns, axis=0, ddof=1)
    variable = (std > 0) & (np.ptp(returns, axis=0) > 0)
    return np.divide(mean, std, out=np.full(mean.shape, np.nan), where=variable)


def _candidate_matrix(values: np.ndarray) -> np.ndarray:
    matrix = np.asarray(values, dtype=float)
    if matrix.ndim != 2 or not matrix.size or not np.isfinite(matrix).all():
        raise ValueError("Require a finite date-by-candidate matrix on a common frozen window")
    return matrix


def compute_cscv_pbo(candidate_returns_matrix: np.ndarray, n_blocks: int = 8) -> float:
    """Compute CSCV PBO using symmetric partitions and average ranks for OOS ties.

    Candidate order resolves an exact in-sample tie. Identical candidate series
    are collapsed in first-registration order so duplicates cannot change ranks.
    """
    matrix = _candidate_matrix(candidate_returns_matrix)
    if n_blocks < 4 or n_blocks % 2 or len(matrix) < n_blocks:
        raise ValueError("CSCV requires at least four even, nonempty blocks")
    _, unique = np.unique(matrix, axis=1, return_index=True)
    matrix = matrix[:, np.sort(unique)]
    if matrix.shape[1] < 2:
        raise ValueError("CSCV requires at least two distinct comparable candidates")
    blocks = np.array_split(np.arange(len(matrix)), n_blocks)
    poor = total = 0
    for indices in combinations(range(n_blocks), n_blocks // 2):
        complement = sorted(set(range(n_blocks)).difference(indices))
        train = _sharpe(matrix[np.concatenate([blocks[i] for i in indices])])
        test = _sharpe(matrix[np.concatenate([blocks[i] for i in complement])])
        if not (np.isfinite(train).all() and np.isfinite(test).all()):
            raise ValueError("CSCV candidate Sharpe must be defined in every half-window")
        winner = int(np.argmax(train))
        omega = rankdata(test, method="average")[winner] / (len(test) + 1)
        poor += int(omega <= 0.5)
        total += 1
    return poor / total


def _walk_forward(
    matrix: np.ndarray, dates: list[date], folds: list[np.ndarray], purge: int, embargo: int
) -> list[dict]:
    """Expanding, label-mature training, followed by validation and untouched OOS."""
    summaries = []
    for index in range(2, len(folds)):
        validation, test = folds[index - 1], folds[index]
        train = np.arange(max(0, int(validation[0]) - purge - embargo))
        validation = validation[validation + purge + embargo < test[0]]
        if min(len(train), len(validation), len(test)) < 2:
            continue
        in_sample = _sharpe(matrix[train])
        val_sharpe = _sharpe(matrix[validation])
        eligible = np.isfinite(in_sample) & np.isfinite(val_sharpe)
        if not eligible.any():
            continue
        winner = int(np.argmax(np.where(eligible, val_sharpe, -np.inf)))
        oos_sharpe = float(_sharpe(matrix[test, winner]))
        if not isfinite(oos_sharpe):
            continue
        summaries.append(
            {
                "fold": index,
                "candidate_index": winner,
                "train_start": dates[int(train[0])].isoformat(),
                "train_end": dates[int(train[-1])].isoformat(),
                "validation_start": dates[int(validation[0])].isoformat(),
                "validation_end": dates[int(validation[-1])].isoformat(),
                "test_start": dates[int(test[0])].isoformat(),
                "test_end": dates[int(test[-1])].isoformat(),
                "train_count": len(train),
                "validation_count": len(validation),
                "test_count": len(test),
                "is_sharpe": float(in_sample[winner] * _ANNUALIZATION),
                "validation_sharpe": float(val_sharpe[winner] * _ANNUALIZATION),
                "oos_sharpe": oos_sharpe * _ANNUALIZATION,
                "oos_compounded_return": float(np.prod(1 + matrix[test, winner]) - 1),
            }
        )
    return summaries


def _ranges(indices: np.ndarray, dates: list[date]) -> list[list[str]]:
    if not len(indices):
        return []
    return [
        [dates[int(block[0])].isoformat(), dates[int(block[-1])].isoformat()]
        for block in np.split(indices, np.flatnonzero(np.diff(indices) != 1) + 1)
    ]


def _count_conflicts(split: PurgedSplit, purge: int, embargo: int) -> int:
    # Independent check of the generated training rows against each test block.
    hits = np.zeros(len(split.train), dtype=bool)
    for block in np.split(split.test, np.flatnonzero(np.diff(split.test) != 1) + 1):
        lower = int(block[0]) - purge
        upper = int(block[-1]) + purge + embargo
        hits |= (split.train >= lower) & (split.train <= upper)
    return int(np.count_nonzero(hits))


def run_cpcv_gate(
    returns_matrix: np.ndarray,
    dates: list[date],
    req: CpcvRequest,
    baseline_returns: np.ndarray | None = None,
) -> CpcvResult:
    """Select by training Sharpe in each split and evaluate selected OOS returns.

    Columns contain comparable net daily candidate returns. A baseline, when
    supplied, is reported independently on the same complete path calendars.
    Callers evaluating net active Sharpe should supply net active daily returns.
    """
    matrix = _candidate_matrix(returns_matrix)
    if len(dates) != len(matrix) or any(left >= right for left, right in pairwise(dates)):
        raise ValueError("Dates must match matrix rows and be strictly increasing")
    if req.trial_count_m < 1:
        raise ValueError("trial_count_m must be positive")
    baseline = None
    if baseline_returns is not None:
        baseline = np.asarray(baseline_returns, dtype=float)
        if baseline.shape != (len(dates),) or not np.isfinite(baseline).all():
            raise ValueError("Baseline must contain one finite return for each date")

    folds, splits, paths = build_cpcv_splits(
        len(dates),
        req.n_splits,
        req.k_test,
        req.purge_days,
        req.embargo_days,
    )
    conflicts = sum(_count_conflicts(split, req.purge_days, req.embargo_days) for split in splits)
    selections = {}
    split_audit = []
    for index, split in enumerate(splits):
        evidence = {
            "split": index,
            "test_folds": list(split.test_folds),
            "train_ranges": _ranges(split.train, dates),
            "test_ranges": _ranges(split.test, dates),
            "purged_ranges": _ranges(split.purged, dates),
            "embargo_ranges": _ranges(split.embargoed, dates),
        }
        if len(split.train) >= 2:
            candidate_sharpes = _sharpe(matrix[split.train])
            if np.isfinite(candidate_sharpes).any():
                winner = int(np.argmax(np.where(np.isfinite(candidate_sharpes), candidate_sharpes, -np.inf)))
                selections[index] = (winner, float(candidate_sharpes[winner]))
                evidence["candidate_index"] = winner
        split_audit.append(evidence)

    pbo = 0.0
    pbo_known = True
    try:
        pbo = compute_cscv_pbo(matrix)
    except ValueError as error:
        pbo_known = False
        pbo_note = str(error)
    else:
        pbo_note = "CSCV over eight consecutive blocks of the frozen matrix"

    trials = max(req.trial_count_m, matrix.shape[1])
    unique_trials = np.unique(matrix, axis=1)
    trial_sharpes = _sharpe(unique_trials)
    trial_evidence = np.isfinite(trial_sharpes).all() and (trials == 1 or len(trial_sharpes) > 1)
    dispersion = float(np.std(trial_sharpes, ddof=1)) if trial_evidence and len(trial_sharpes) > 1 else 0.0
    path_sharpes, path_dsrs, path_audit = [], [], []
    for path in paths:
        audit = {"split_by_fold": list(path)}
        path_audit.append(audit)
        if any(index not in selections for index in path):
            continue
        daily = np.concatenate(
            [matrix[fold, selections[index][0]] for fold, index in zip(folds, path, strict=True)]
        )
        sharpe = float(_sharpe(daily))
        if not isfinite(sharpe):
            continue
        path_sharpes.append(sharpe * _ANNUALIZATION)
        audit["oos_sharpe"] = path_sharpes[-1]
        if trial_evidence and len(daily) >= 4:
            try:
                dsr = compute_deflated_sharpe(
                    sharpe,
                    len(daily),
                    trials,
                    dispersion,
                    float(sample_skew(daily)),
                    float(sample_kurtosis(daily, fisher=False)),
                )
            except ValueError:
                continue
            path_dsrs.append(dsr)
            audit["dsr"] = dsr

    is_sharpe = (
        float(np.median([value[1] for value in selections.values()]) * _ANNUALIZATION) if selections else 0.0
    )
    oos_sharpe = float(np.median(path_sharpes)) if path_sharpes else 0.0
    ratio = oos_sharpe / is_sharpe if is_sharpe > 0 else 0.0
    dsr = float(np.median(path_dsrs)) if path_dsrs else 0.0
    complete = len(path_sharpes) == len(paths)
    dsr_known = len(path_dsrs) == len(paths)
    checks = [
        {
            "name": "PBO",
            "value": pbo if pbo_known else None,
            "threshold": _PBO_LIMIT,
            "passed": pbo_known and pbo <= _PBO_LIMIT,
            "note": pbo_note,
        },
        {
            "name": "DSR",
            "value": dsr if dsr_known else None,
            "threshold": _DSR_LIMIT,
            "passed": dsr_known and dsr >= _DSR_LIMIT,
            "n_trials": trials,
            "trial_sr_std_daily": dispersion,
        },
        {
            "name": "OOS/IS",
            "value": ratio if complete else None,
            "threshold": _OOS_IS_LIMIT,
            "passed": complete and is_sharpe > 0 and ratio >= _OOS_IS_LIMIT,
        },
        {
            "name": "Purge and embargo",
            "value": conflicts,
            "threshold": 0,
            "passed": conflicts == 0,
            "split_manifest": split_audit,
            "paths": path_audit,
        },
    ]
    known_failure = (
        conflicts > 0
        or (pbo_known and pbo > _PBO_LIMIT)
        or (dsr_known and dsr < _DSR_LIMIT)
        or (complete and not checks[2]["passed"])
    )
    walk_forward = _walk_forward(matrix, dates, folds, req.purge_days, req.embargo_days)
    checks.append(
        {
            "name": "Walk-forward evidence",
            "value": len(walk_forward),
            "threshold": 1,
            "passed": bool(walk_forward),
        }
    )
    verdict = GateVerdict.INCONCLUSIVE
    if known_failure:
        verdict = GateVerdict.REJECT
    elif all(check["passed"] for check in checks):
        verdict = GateVerdict.PASS
    baseline_sharpe = float(_sharpe(baseline)) if baseline is not None else float("nan")
    from .formal import _candidate_fdr

    spa, fdr_items = _candidate_fdr(matrix, baseline, trials)
    return CpcvResult(
        experiment_id=req.experiment_id,
        n_splits=req.n_splits,
        k_test=req.k_test,
        n_combinations=len(splits),
        n_oos_paths=len(paths),
        purge_days=req.purge_days,
        embargo_days=req.embargo_days,
        purge_conflicts=conflicts,
        is_sharpe=is_sharpe,
        oos_median_sharpe=oos_sharpe,
        oos_is_ratio=ratio,
        pbo=pbo,
        dsr=dsr,
        negative_path_count=sum(value < 0 for value in path_sharpes),
        path_sharpes=path_sharpes,
        baseline_path_sharpes=[baseline_sharpe * _ANNUALIZATION] * len(paths)
        if isfinite(baseline_sharpe)
        else [],
        walk_forward_folds=walk_forward,
        verdict=verdict,
        gate_details=checks,
        fdr_q_value=fdr_items[0]["q_value"],
        spa_p_value=spa["spa_p_value"],
        fdr_items=fdr_items,
    )
