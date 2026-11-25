"""Multiple-testing and out-of-sample validation."""

from .cpcv import compute_cscv_pbo, compute_deflated_sharpe, run_cpcv_gate
from .diagnosis import generate_diagnosis_and_guidance
from .formal import (
    benjamini_hochberg_fdr,
    check_prefix_invariance,
    hansen_spa_test,
    run_formal_verification,
    run_parameter_cliff_test,
    stationary_block_bootstrap,
)

__all__ = [
    "benjamini_hochberg_fdr",
    "check_prefix_invariance",
    "compute_cscv_pbo",
    "compute_deflated_sharpe",
    "generate_diagnosis_and_guidance",
    "hansen_spa_test",
    "run_cpcv_gate",
    "run_formal_verification",
    "run_parameter_cliff_test",
    "stationary_block_bootstrap",
]
