"""Institutional Investment Committee (IC) Memo Markdown generator."""

from __future__ import annotations

from typing import Any

from quant_core.schemas import ResearchStudyDetail


def _fmt_num(val: Any, suffix: str = "", digits: int = 4) -> str:
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return f"{val:.{digits}f}{suffix}"
    return "N/A"


def _fmt_cny(val: Any) -> str:
    if not isinstance(val, (int, float)) or isinstance(val, bool):
        return "N/A"
    if val >= 1e8:
        return f"{val / 1e8:.2f} 亿 CNY"
    if val >= 1e4:
        return f"{val / 1e4:.1f} 万 CNY"
    return f"{val:.0f} CNY"


def render_ic_memo(detail: ResearchStudyDetail) -> str:
    """Render an institutional-grade IC Memo Markdown document from ``ResearchStudyDetail``."""
    lines: list[str] = [
        f"# Investment Committee Research Memo · {detail.study_id}",
        "",
        f"> **课题标题**：{detail.title}  ",
        f"> **学术结论（Verdict）**：`{detail.verdict}` · **流程状态**：`{detail.status}` · **类别**：`{detail.kind}`  ",
        f"> **快照与股票池**：`{detail.snapshot_id}` / `{detail.universe_id}` · **家族**：`{detail.family_id}` · **父课题**：`{detail.parent_study_id or 'ROOT'}`  ",
        f"> **执行模式 / 集群**：`{detail.dispatch_mode}` / `{detail.target_cluster}` · **创建人**：`{detail.created_by}`  ",
        f"> **创建时间**：`{detail.created_at}` · **完成时间**：`{detail.completed_at or 'IN_PROGRESS'}`",
        "",
        "---",
        "",
        "## 1. 研究假说与数学推演",
        "",
        f"- **核心假说**：{detail.hypothesis or '（未提供）'}",
        f"- **目标因子 / 表达式**：`{detail.factor_id or 'N/A'}` — `{detail.expression or 'builtin'}`",
    ]

    if detail.math_derivation is not None:
        md = detail.math_derivation
        lines.append(f"- **经济学机制**：{md.mechanism or md.economic_hypothesis}")
        lines.append(f"- **预期方向**：`{md.expected_sign}`")
        if md.formula_latex:
            lines.extend(["", "$$", md.formula_latex, "$$", ""])
        if md.derivation_steps:
            lines.append("### 推导步骤")
            for i, step in enumerate(md.derivation_steps, start=1):
                lines.append(f"{i}. {step}")
            lines.append("")
        if md.assumptions:
            lines.extend(["### 前提假设", "| 编号 | 假设内容 | 校验状态 |", "| :--- | :--- | :---: |"])
            for idx, asm in enumerate(md.assumptions, start=1):
                aid = asm.get("id", f"A{idx}")
                text = asm.get("text", str(asm))
                st = asm.get("status", "pending")
                lines.append(f"| {aid} | {text} | `{st}` |")
            lines.append("")
        if md.boundary_conditions:
            lines.append("**边界条件**：" + "；".join(md.boundary_conditions))
        if md.expected_failure_regimes:
            lines.append("**理论失效 Regime**：" + "、".join(md.expected_failure_regimes))

    lines.extend(["", "---", "", "## 2. 形式化验证证明链（Formal Proof Ledger）", ""])
    if detail.formal_proof is not None:
        fp = detail.formal_proof
        lines.extend(
            [
                f"- **总判定**：`{'PASS' if fp.passed else 'FAIL'}`（`{fp.proofs_passed}/{fp.proofs_total}` 通过，`verdict={fp.verdict.value}`）",
                f"- **证明指纹 (`proof_sha256`)**：`{fp.proof_sha256}`",
                (
                    f"- **最大前缀漂移**：`{fp.max_prefix_drift if fp.max_prefix_drift is not None else 'N/A'}` · "
                    f"**BH-FDR q**：`{_fmt_num(fp.bh_fdr_q_value)}` · **SPA p**：`{_fmt_num(fp.spa_p_value)}` · "
                    f"**最大参数悬崖衰减**：`{_fmt_num(fp.max_param_cliff_drop_pct, '%', 2)}`"
                ),
            ]
        )
        if fp.bootstrap_rank_ic_ci95:
            lines.append(
                f"- **Bootstrap 95% CI**：`[{fp.bootstrap_rank_ic_ci95[0]:.6f}, {fp.bootstrap_rank_ic_ci95[1]:.6f}]`（p = `{_fmt_num(fp.bootstrap_p_value)}`）"
            )
        if fp.blocking_failures:
            lines.append("- **阻断失败项**：" + ", ".join(f"`{b}`" for b in fp.blocking_failures))
        lines.extend(
            [
                "",
                "| 检查 ID | 套件 | 证明项 | 观测统计量 | 机构门槛 | 误差指标 | 状态 |",
                "| :--- | :---: | :--- | :--- | :--- | :--- | :---: |",
            ]
        )
        for c in fp.checks:
            lines.append(
                f"| `{c.check_id}` | {c.suite} | {c.label} | `{c.statistic}` | `{c.threshold}` | `{c.error_metric}` | **{c.status.upper()}** |"
            )
    else:
        lines.append("_暂无形式化证明记录。_")

    lines.extend(["", "---", "", "## 3. 核心定量绩效与零信任核验审计", ""])
    m = detail.metrics_summary or {}
    lines.extend(
        [
            "| 指标 | 确定性复算值 | 指标 | 确定性复算值 |",
            "| :--- | :---: | :--- | :---: |",
            f"| RankIC 均值 | `{_fmt_num(m.get('rank_ic_mean'))}` | ICIR 年化 | `{_fmt_num(m.get('icir_ann'), '', 2)}` |",
            f"| Newey-West t | `{_fmt_num(m.get('nw_t_stat'), '', 2)}` | IC 胜率 | `{_fmt_num((m.get('ic_win_rate') or 0) * 100 if isinstance(m.get('ic_win_rate'), (int, float)) else None, '%', 1)}` |",
            f"| OOS 中位夏普 | `{_fmt_num(m.get('oos_sharpe'), '', 2)}` | OOS/IS 比值 | `{_fmt_num(m.get('oos_is_ratio'), '', 2)}` |",
            f"| CPCV PBO | `{_fmt_num(m.get('pbo'), '', 3)}` | DSR | `{_fmt_num(m.get('dsr'), '', 3)}` |",
            f"| 净超额年化 (EVENT) | `{_fmt_num(m.get('net_excess_ann_pct'), '%', 2)}` | 信息比率 IR | `{_fmt_num(m.get('info_ratio'), '', 2)}` |",
            f"| 单边日换手 | `{_fmt_num((m.get('daily_turnover_oneway') or 0) * 100 if isinstance(m.get('daily_turnover_oneway'), (int, float)) else None, '%', 2)}` | 库内最大相关 | `{_fmt_num(m.get('max_corr_in_library'), '', 3)}` |",
            f"| 半衰容量 (Half-Alpha) | `{_fmt_cny(m.get('half_alpha_aum_cny'))}` | 盈亏平衡容量 | `{_fmt_cny(m.get('breakeven_aum_cny'))}` |",
        ]
    )

    diff_map = m.get("claimed_vs_verified_diff")
    if isinstance(diff_map, dict) and diff_map:
        lines.extend(
            [
                "",
                "### 零信任外部自报 vs 主控复算差异表",
                "",
                "| 指标 | 外部 Worker 自报 | 主控复算值 | 偏差 (Claimed − Verified) | 异常标记 |",
                "| :--- | :---: | :---: | :---: | :---: |",
            ]
        )
        for key, item in diff_map.items():
            lines.append(
                f"| `{key}` | `{item.get('claimed')}` | `{item.get('verified')}` | `{item.get('diff')}` | `{'ALERT' if item.get('discrepancy_flag') else 'OK'}` |"
            )

    lines.extend(["", "---", "", "## 4. 跨维度 Regime 稳定性热力表", ""])
    if detail.dimension_analysis is not None and detail.dimension_analysis.slices:
        da = detail.dimension_analysis
        lines.append(
            f"- **Regime 一致性评分 (RCS)**：`{_fmt_num(da.regime_consistency_score, '', 3)}` · "
            f"**单维依赖告警**：`{'TRIGGERED (' + str(da.dominant_bucket) + ')' if da.single_dim_dominance_flag else 'PASS'}`"
        )
        if da.warning_summary:
            lines.append(f"- **告警摘要**：{da.warning_summary}")
        lines.extend(
            [
                "",
                "| 维度 | 分桶 | 策略超额 (%) | 基线收益 (%) | Δ 超额 (%) | RankIC | 样本日 | 贡献占比 (%) | 红旗 |",
                "| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
            ]
        )
        for s in da.slices[:30]:
            lines.append(
                f"| `{s.dim}` | {s.bucket} | {s.strategy_excess_pct:.2f} | {s.baseline_return_pct:.2f} | "
                f"{s.alpha_vs_baseline_pct:.2f} | {_fmt_num(s.rank_ic)} | {s.n_days} | {s.contribution_pct:.1f} | "
                f"{'DIM_FLAG' if s.dominant_flag else 'OK'} |"
            )
    else:
        lines.append("_暂无跨维度 Regime 分析记录。_")

    if detail.capacity_frontier is not None and detail.capacity_frontier.points:
        cf = detail.capacity_frontier
        lines.extend(
            [
                "",
                "---",
                "",
                "## 5. AUM 策略容量衰减前沿",
                "",
                (
                    f"- **校准状态**：`{cf.calibration_status}` · **瓶颈约束**：`{cf.binding_bottleneck}` · "
                    f"**半衰容量**：`{_fmt_cny(cf.half_alpha_aum_cny)}` · **归零容量**：`{_fmt_cny(cf.breakeven_aum_cny)}`"
                ),
                "",
                "| AUM 规模 | 毛超额 (%) | 净超额 (%) | 年化 IR | 冲击 (bps) | PoV (%) | 成交率 (%) |",
                "| :--- | :---: | :---: | :---: | :---: | :---: | :---: |",
            ]
        )
        for p in cf.points:
            lines.append(
                f"| {_fmt_cny(p.aum_cny)} | {p.gross_excess_pct:.2f} | {p.net_excess_pct:.2f} | "
                f"{p.ir_ann:.2f} | {p.impact_bps:.1f} | {p.pov_pct:.2f} | {p.fill_rate_pct:.1f} |"
            )

    lines.extend(["", "---", "", "## 6. 根因诊断与迭代改进指导", ""])
    if detail.diagnosis:
        for d in detail.diagnosis:
            lines.append(f"### [{d.severity.upper()}] {d.rule_id} · {d.root_cause}")
            lines.append(f"- **诊断结论**：{d.claim}")
            if d.evidence:
                ev_str = "; ".join(f"{e.get('kind', 'ev')}:{e.get('ref', '')}={e.get('value', '')}" for e in d.evidence)
                lines.append(f"- **绑定证据**：`{ev_str}`")
            for g in d.guidance:
                lines.append(f"  - 建议：{g}")
            lines.append("")
    else:
        lines.append("- 当前未触发高危诊断规则。")

    if detail.improvement_plan:
        lines.append("### 下一步可执行改进清单")
        for idx, step in enumerate(detail.improvement_plan, start=1):
            action = step.get("action", "iterate")
            prio = step.get("priority", "P1")
            desc = step.get("detail") or step.get("rationale") or str(step)
            lines.append(f"{idx}. `[{prio}] {action}` — {desc}")

    lines.extend(["", "---", "", "## 7. 假说血缘与复现指纹", ""])
    if detail.lineage:
        lines.extend(
            [
                "| Study ID | 父课题 | 标题 | Verdict | OOS SR | DSR | PBO |",
                "| :--- | :--- | :--- | :---: | :---: | :---: | :---: |",
            ]
        )
        for node in detail.lineage:
            lines.append(
                f"| `{node.study_id}` | `{node.parent_study_id or 'ROOT'}` | {node.title} | "
                f"`{node.verdict}` | {_fmt_num(node.oos_sharpe, '', 2)} | {_fmt_num(node.dsr, '', 3)} | {_fmt_num(node.pbo, '', 3)} |"
            )
        lines.append("")

    lines.extend(
        [
            "```text",
            f"study_id         : {detail.study_id}",
            f"snapshot_id      : {detail.snapshot_id}",
            f"universe_id      : {detail.universe_id}",
            f"fact_fingerprint : {detail.fact_fingerprint or 'N/A'}",
            f"proof_sha256     : {detail.formal_proof.proof_sha256 if detail.formal_proof else 'N/A'}",
            f"artifact_uri     : {detail.artifact_uri or 'N/A'}",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


__all__ = ["render_ic_memo"]
