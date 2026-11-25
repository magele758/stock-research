"""Evidence-driven diagnostics and reusable research configuration patches."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from quant_core.factors.engine import BUILTIN_FACTORS
from quant_core.schemas import (
    INSTITUTIONAL_GATES,
    BacktestResult,
    CapacityFrontierResult,
    DiagnosisItem,
    FactorEvalResult,
    FormalProofResult,
    PortfolioOptResult,
    RegimeAnalysisResult,
)


def generate_diagnosis_and_guidance(
    *,
    proof: FormalProofResult | None = None,
    factor_eval: FactorEvalResult | None = None,
    regime: RegimeAnalysisResult | None = None,
    capacity: CapacityFrontierResult | None = None,
    backtest: BacktestResult | None = None,
    opt: PortfolioOptResult | None = None,
) -> tuple[list[DiagnosisItem], list[dict[str, Any]]]:
    """Return triggered rules in stable order and the patches for their follow-up runs."""
    diagnoses: list[DiagnosisItem] = []
    gates = INSTITUTIONAL_GATES
    checks = proof.checks if proof else []

    def emit(
        rule: str,
        cause: str,
        severity: str,
        claim: str,
        evidence: list[dict[str, Any]],
        guidance: list[str],
        *,
        hypothesis: str,
        constraints: dict[str, Any] | None = None,
        preprocess: dict[str, Any] | None = None,
    ) -> None:
        child: dict[str, Any] = {"title": f"{rule} {cause}复验", "hypothesis": hypothesis}
        if factor_eval:
            builtin = BUILTIN_FACTORS.get(factor_eval.factor_id, {})
            child["factor_id"] = factor_eval.factor_id
            if factor_eval.expression != builtin.get("expression"):
                child["expression"] = factor_eval.expression
        if preprocess:
            child["preprocess"] = preprocess
        if constraints:
            child["constraints"] = constraints
        diagnoses.append(
            DiagnosisItem(
                id=rule,
                rule_id=rule,
                root_cause=cause,
                severity=severity,
                claim=claim,
                evidence=evidence,
                guidance=guidance,
                constraints_patch=constraints,
                child_study_patch=child,
            )
        )

    leakage = [
        {
            "proof_check_id": check.check_id,
            "reason_code": check.reason_code,
            "statistic": check.statistic,
            "witness_keys": check.witness_keys,
        }
        for check in checks
        if check.status == "fail" and (check.suite == "T1" or check.check_id == "T3.PIT_MONOTONICITY")
    ]
    if (
        not leakage
        and factor_eval
        and (not factor_eval.ast_safe or factor_eval.prefix_invariance_passed is False)
    ):
        leakage.append(
            {
                "proof_check_id": ("T1.AST_CAUSAL" if not factor_eval.ast_safe else "T1.PREFIX_INVARIANCE"),
                "max_prefix_drift": factor_eval.max_prefix_drift,
                "ast_warnings": factor_eval.ast_warnings if not factor_eval.ast_safe else [],
            }
        )
    if leakage:
        emit(
            "D01",
            "前视泄漏",
            "critical",
            "因果或时点检查发现前视证据。",
            leakage,
            [
                "按证据键重现首次分歧，将全样本变换改为尾随窗口或当日截面。",
                "按决策时点连接已披露版本，再复验 AST、前缀不变性和 PIT 日期链。",
            ],
            hypothesis="修正信号时钟后，各截点的前缀信号及缺失掩码与全量运行一致。",
        )

    style_limit = gates["barra_style_hard_limit"]
    exposures = [
        {"style": item.factor, "post_opt_z": item.post_opt, "limit_z": min(item.limit, style_limit)}
        for item in (opt.style_exposures if opt else [])
        if abs(item.post_opt) > min(item.limit, style_limit)
    ]
    if backtest:
        attribution = backtest.attribution
        net = backtest.friction.net_excess_pct
        closes = max(abs(attribution.brinson_closure_error_bp), abs(attribution.barra_closure_error_bp))
        style_share = attribution.barra_style_pct / net if net > 0 else 0.0
        if closes <= gates["closure_error_bp_max"] and style_share >= 0.60:
            exposures.append(
                {
                    "invariant_key": "barra_closure",
                    "closure_error_bp": closes,
                    "style_contribution_pct": attribution.barra_style_pct,
                    "net_excess_pct": net,
                    "style_share": style_share,
                    "style_detail": attribution.barra_style_detail,
                }
            )
    if exposures:
        emit(
            "D02",
            "风格 Beta 依赖",
            "major",
            "Barra 主动暴露越限或归因显示超额依赖风格。",
            exposures,
            [
                "在相同基准和成本上回放行业、市值和 Barra 风格中性版本。",
                "比较正交残差的 OOS RankIC 区间、净超额和跟踪误差。",
            ],
            hypothesis="收紧风格敞口后，残差因子在独立 OOS 窗保有正向净超额。",
            constraints={"max_size_z": style_limit / 2, "max_style_z": style_limit / 2},
            preprocess={
                "neutralize_industry": True,
                "neutralize_size": True,
                "orthogonalize_barra": True,
                "standardize": "zscore",
            },
        )

    selection = [
        {
            "proof_check_id": check.check_id,
            "reason_code": check.reason_code,
            "statistic": check.statistic,
            "threshold": check.threshold,
        }
        for check in checks
        if check.check_id in {"T4.CPCV_PBO_DSR", "T4.BH_FDR_SPA", "T4.PARAM_CLIFF"}
        and check.status in {"warn", "fail"}
    ]
    if proof and not any(check.check_id == "T4.BH_FDR_SPA" for check in checks):
        q, spa = proof.bh_fdr_q_value, proof.spa_p_value
        if (q is not None and q > gates["bh_fdr_q_max"]) or (spa is not None and spa > gates["spa_p_max"]):
            selection.append({"proof_check_id": "T4.BH_FDR_SPA", "bh_fdr_q": q, "spa_p": spa})
    if selection:
        emit(
            "D03",
            "多重检验过拟合风险",
            "major",
            "选择偏差或参数稳定性证据未达到门槛。",
            selection,
            [
                "预登记主假设、候选族和参数邻域，保留全部失败试验。",
                "在新隔离窗复验 BH/BY、SPA、PBO、DSR 和参数邻域。",
            ],
            hypothesis="冻结候选族和参数后，确认窗仍通过搜索偏差及邻域稳定性检查。",
        )

    frontier_data = capacity
    if frontier_data is None:
        embedded = (backtest.capacity_frontier if backtest else None) or (
            opt.capacity_frontier if opt else None
        )
        if embedded:
            try:
                frontier_data = CapacityFrontierResult.model_validate(embedded)
            except ValidationError:
                pass  # Older results can contain a partial capacity projection.
    point = None
    if frontier_data and frontier_data.points:
        # Use only measured grid points for fill-rate findings.
        point = next(
            (item for item in frontier_data.points if item.aum_cny == frontier_data.current_aum_cny),
            None,
        )

    costs = []
    if backtest:
        friction = backtest.friction
        sum_costs = (
            friction.commission_pct
            + friction.stamp_duty_pct
            + friction.transfer_fee_pct
            + friction.impact_slippage_pct
            + friction.limit_suspend_pct
        )
        gross = friction.gross_excess_pct
        closure_bp = abs(gross - friction.net_excess_pct - sum_costs) * 100
        if gross > 0 and sum_costs >= gross / 2 and closure_bp <= gates["closure_error_bp_max"]:
            costs.append(
                {
                    "invariant_key": "friction_closure",
                    "gross_excess_pct": gross,
                    "net_excess_pct": friction.net_excess_pct,
                    "impact_slippage_pct": friction.impact_slippage_pct,
                    "friction_pct": sum_costs,
                    "friction_share": sum_costs / gross,
                    "closure_error_bp": closure_bp,
                    "ann_turnover_pct": backtest.ann_turnover_pct,
                }
            )
    if opt and opt.expected_gross_excess_pct > 0:
        share = opt.expected_cost_pct / opt.expected_gross_excess_pct
        if share >= 0.5:
            costs.append(
                {
                    "opt_run_id": opt.opt_run_id,
                    "estimated_cost_pct": opt.expected_cost_pct,
                    "gross_excess_pct": opt.expected_gross_excess_pct,
                    "estimated_cost_share": share,
                    "oneway_turnover_pct": opt.oneway_turnover_pct,
                }
            )
    if point and point.gross_excess_pct > 0 and point.fill_rate_pct >= 100 - 1e-8:
        annual_cost_pct = point.gross_excess_pct - point.net_excess_pct
        if annual_cost_pct >= point.gross_excess_pct / 2:
            costs.append(
                {
                    "aum_cny": point.aum_cny,
                    "gross_excess_pct": point.gross_excess_pct,
                    "net_excess_pct": point.net_excess_pct,
                    "impact_bps": point.impact_bps,
                    "explicit_cost_bps": point.explicit_cost_bps,
                    "scenario_cost_share": annual_cost_pct / point.gross_excess_pct,
                }
            )
    if costs:
        emit(
            "D04",
            "换手与执行摩擦",
            "major",
            "执行摩擦消耗至少一半毛超额。",
            costs,
            [
                "固定基准及信号，提高换手惩罚，比较周度与月度调仓。",
                "对比佣金、税费和冲击分项，并复验净超额、IR、成交率及 TE。",
            ],
            hypothesis="降低调仓强度可在风险约束内改善扣费后的超额收益。",
            constraints={"turnover_lambda": 0.90, "max_turnover_oneway": 0.20},
        )

    dominant = [
        {
            "slice_id": f"{item.dim}:{item.bucket}",
            "contribution_pct": item.contribution_pct,
            "n_days": item.n_days,
            "ci95": item.ci95,
        }
        for item in (regime.slices if regime else [])
        if item.dominant_flag and item.contribution_pct > gates["single_dim_dominance_max"] * 100
    ]
    if regime and regime.single_dim_dominance_flag and not dominant and regime.dominant_bucket:
        dominant.append({"slice_id": regime.dominant_bucket, "contribution_pct": regime.dominant_share_pct})
    if regime and dominant:
        for evidence in dominant:
            evidence["regime_consistency_score"] = regime.regime_consistency_score
        emit(
            "D05",
            "单一市场切片依赖",
            "major",
            "一个或多个分桶承载超过六成正贡献。",
            dominant,
            [
                "对主导状态进行留一状态及留一连续阶段回测。",
                "用仅依赖当时数据的状态过滤，复核其他状态的净超额与覆盖率。",
            ],
            hypothesis="剔除主导分桶后，策略在其余状态仍有正向超额。",
        )

    if frontier_data:
        aum = frontier_data.current_aum_cny
        below_half = frontier_data.half_alpha_aum_cny is not None and aum >= frontier_data.half_alpha_aum_cny
        beyond_break = frontier_data.breakeven_aum_cny is not None and aum >= frontier_data.breakeven_aum_cny
        low_fill = point is not None and point.fill_rate_pct < 95
        if below_half or beyond_break or low_fill:
            evidence = {
                "aum_cny": aum,
                "half_alpha_aum_cny": frontier_data.half_alpha_aum_cny,
                "breakeven_aum_cny": frontier_data.breakeven_aum_cny,
                "binding_bottleneck": frontier_data.binding_bottleneck,
                "calibration_status": frontier_data.calibration_status,
                "point": point.model_dump(mode="json") if point else None,
                "illiquid_names": frontier_data.illiquid_names,
            }
            emit(
                "D06",
                "流动性容量不足",
                "major",
                "目标资金规模触及容量阈值或情景成交率低于 95%。",
                [evidence],
                [
                    "按时点 ADV 和预计单量设置最低流动性门槛及个股权重上限。",
                    "测试分批调仓，对降低 ADV 与提高冲击系数的情景重算六档容量。",
                ],
                hypothesis="控制低流动性敞口后，目标规模在压力情景中保持净超额和成交率。",
                constraints={"max_stock_weight": 0.03},
            )

    if factor_eval and factor_eval.max_corr_in_library >= gates["max_corr_library"]:
        peer = factor_eval.max_corr_peer
        peer_coverage = next(
            (
                entry.get("peer_common_coverage_pct", {}).get(peer)
                for entry in factor_eval.gate_checks
                if entry.get("key") == "max_corr_in_library"
            ),
            None,
        )
        if peer_coverage is None or peer_coverage >= gates["min_coverage"] * 100:
            emit(
                "D07",
                "因子库高度共线",
                "major",
                "因子与库内同类信号的日均绝对相关达到门槛。",
                [
                    {
                        "factor_id": factor_eval.factor_id,
                        "peer": peer,
                        "max_abs_rank_corr": factor_eval.max_corr_in_library,
                        "peer_common_coverage_pct": peer_coverage,
                        "threshold": gates["max_corr_library"],
                    }
                ],
                [
                    "按训练窗拟合对同类因子的正交投影，冻结投影系数后应用到 OOS。",
                    "比较排除相似因子后的增量 RankIC、净 IR 和共同覆盖率。",
                ],
                hypothesis="移除与库内最相似信号重叠的分量后，仍存在可重现的增量效应。",
            )

    plan = [
        {
            "diagnosis_id": item.id,
            "rule_id": item.rule_id,
            "guidance": item.guidance,
            "constraints_patch": item.constraints_patch,
            "child_study_patch": item.child_study_patch,
        }
        for item in diagnoses
    ]
    return diagnoses, plan
