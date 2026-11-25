"""Idempotent Golden PIT Research Ledger Seeder (zero fabricated numbers).

Populates 5 cached ``dimension_baselines`` and 3 real reproducible studies for a
snapshot when ``research_studies`` is empty:
  - ``STD-0001``: Root study ``alpha_vp_corr_20d``
  - ``STD-0002``: V2 child iteration ``alpha_mom_resid_60d`` (parent ``STD-0001``)
  - ``STD-0003``: Falsified look-ahead counterexample blocked by formal verification -> ``falsified_tombstones``
"""

from __future__ import annotations

from typing import Any

from quant_core.schemas import MathDerivationSpec, ResearchDispatchRequest

from quant_api import deps
from quant_api.orchestrator.engine import ClusterResearchOrchestrator, get_orchestrator


def ensure_golden_studies(
    snapshot_id: str | None = None,
    *,
    universe_id: str = "CSI500",
    orchestrator: ClusterResearchOrchestrator | None = None,
) -> dict[str, Any]:
    """Idempotently seed 5 real dimension baselines and 3 real deterministic studies for ``snapshot_id``."""
    orch = orchestrator or get_orchestrator()
    snap = deps.require_snapshot(snapshot_id)
    reg = orch.get_registry()

    existing_baselines = reg.list_dimension_baselines(snapshot_id=snap.snapshot_id, universe_id=universe_id)
    if len(existing_baselines) < 5:
        existing_baselines = orch.build_or_get_baselines(
            snapshot_id=snap.snapshot_id,
            universe_id=universe_id,
            force_rebuild=False,
        )

    existing_studies = reg.list_studies(snapshot_id=snap.snapshot_id, limit=20)
    if existing_studies:
        return {
            "seeded": False,
            "snapshot_id": snap.snapshot_id,
            "universe_id": universe_id,
            "study_ids": [s.study_id for s in existing_studies],
            "baseline_count": len(existing_baselines),
            "tombstone_count": len(reg.list_tombstones(snapshot_id=snap.snapshot_id)),
        }

    # STD-0001: Root study on alpha_vp_corr_20d (real deterministic calculation)
    std1_req = ResearchDispatchRequest(
        title="量价背离相关性 20D Alpha 根研究",
        hypothesis="在 CSI500 股票池中，过去 20 日收益率与对数成交量变化的负相关性捕捉放量滞涨与缩量企稳的微观结构反转效应。",
        kind="factor",
        family_id="FAM-VP-ALPHA",
        topic_tags=["volume_price", "reversal", universe_id.lower()],
        dispatch_mode="engine_sandbox",
        target_cluster="engine_sandbox",
        universe_id=universe_id,
        snapshot_id=snap.snapshot_id,
        factor_id="alpha_vp_corr_20d",
        expression="-pl.rolling_corr(close.pct_change(), volume_shares.log1p().diff(), window_size=20)",
        math_derivation=MathDerivationSpec(
            economic_hypothesis="短期放量上涨往往伴随过度情绪与流动性透支，而缩量回调体现筹码锁定，二者相关系数取负具有截面反转预测力。",
            mechanism="每日截面计算 20 日滚动量价相关性并做 MAD 去极值与行业/市值/Barra 风格中性化。",
            formula_latex=r"f_{i,t} = -\operatorname{Corr}_{20}\!\left(\frac{C_{i,s}}{C_{i,s-1}}-1,\; \Delta\ln(1+V_{i,s})\right)",
            derivation_steps=[
                "构造日度收益率 r_t = close.pct_change() 与对数成交量变化 dV_t = log1p(volume_shares).diff()",
                "计算严格因果 20 日滚动相关系数并取负号，使放量滞涨股得低分、缩量企稳股得高分",
                "截面执行 MAD(k=3.5) 去极值、申万行业中性化、对数市值中性化及 Barra 正交化",
            ],
            assumptions=[
                {"id": "A1", "text": "日线 OHLCV 与复权/行业数据严格满足 PIT 零前视", "status": "pass"},
                {"id": "A2", "text": "20 日量价信号半衰期足以覆盖周频换手成本", "status": "pending"},
            ],
            boundary_conditions=["排除停牌、ST 及一字涨跌停不可成交标的", "截面有效覆盖率 >= 80%"],
            expected_sign="positive",
            expected_failure_regimes=["单边高波动趋势急升行情（放量连板动量占优）"],
        ),
        trial_count_m=10,
        bootstrap_b=300,
        created_by="quant-lead",
    )
    res1 = orch.dispatch_research(std1_req, async_mode=False, study_id="STD-0001")

    # STD-0002: V2 child iteration study (parent_study_id="STD-0001") on alpha_mom_resid_60d
    std2_req = ResearchDispatchRequest(
        title="行业市值中性残差动量 60D V2 迭代研究",
        hypothesis="针对 STD-0001 短周期量价信号换手偏高的问题，改用剔除近 5 日短期反转的 60 日波动率调整动量因子以降低换手并延长半衰期。",
        kind="factor",
        parent_study_id=res1.study.study_id,
        family_id="FAM-VP-ALPHA",
        topic_tags=["momentum", "low_turnover", "v2_iteration", universe_id.lower()],
        dispatch_mode="engine_sandbox",
        target_cluster="engine_sandbox",
        universe_id=universe_id,
        snapshot_id=snap.snapshot_id,
        factor_id="alpha_mom_resid_60d",
        expression="((close * adj_factor) / (close * adj_factor).shift(55) - 1.0).shift(5) / ((close * adj_factor).pct_change().rolling_std(window_size=60) + 1e-6)",
        math_derivation=MathDerivationSpec(
            economic_hypothesis="中期价格动量叠加波动率缩放并在截面上剔除行业与市值影响，可捕获持续盈利预期扩散且显著降低日均换手。",
            mechanism="剔除最近 5 个交易日反转噪声后计算 55 日复权收益，并除以 60 日实现波动率进行风险缩放。",
            formula_latex=r"f_{i,t} = \frac{P^{\text{adj}}_{i,t-5}/P^{\text{adj}}_{i,t-60} - 1}{\sigma_{60}(r^{\text{adj}}_{i,t}) + 10^{-6}}",
            derivation_steps=[
                "计算复权收盘价 P_adj = close * adj_factor，保证尺度保序且消除除权跳空",
                "取滞后 5 日的 55 日动量收益，避免 A 股 1 周微结构反转污染",
                "除以 60 日滚动收益率标准差缩放高波噪声，并经标准中性化流水线评估",
            ],
            assumptions=[
                {"id": "A1", "text": "复权因子连续且无除权跳变（QA-02 PASS）", "status": "pass"},
                {"id": "A2", "text": "60 日波动调整动量在震荡市与牛市中保持较低换手", "status": "pending"},
            ],
            boundary_conditions=["需 60 日历史预热窗口", "单边换手目标 <= 15%"],
            expected_sign="positive",
            expected_failure_regimes=["剧烈风格切换与 V 型反转期"],
        ),
        trial_count_m=12,
        bootstrap_b=300,
        created_by="quant-lead",
    )
    res2 = orch.dispatch_research(std2_req, async_mode=False, study_id="STD-0002")

    # STD-0003: Look-ahead counterexample blocked by formal verification -> falsified_tombstones
    std3_req = ResearchDispatchRequest(
        title="未来收益前视泄漏对照实验（证伪墓碑）",
        hypothesis="对照验证形式化证明门禁对未来价格偏移算子 shift(-1) 的零容忍拦截能力，并沉淀结构化墓碑防复发。",
        kind="factor",
        parent_study_id=res1.study.study_id,
        family_id="FAM-VP-ALPHA",
        topic_tags=["lookahead_counterexample", "formal_gate", "tombstone"],
        dispatch_mode="engine_sandbox",
        target_cluster="engine_sandbox",
        universe_id=universe_id,
        snapshot_id=snap.snapshot_id,
        factor_id="alpha_lookahead_counterexample",
        expression="close.shift(-1) / close - 1.0",
        math_derivation=MathDerivationSpec(
            economic_hypothesis="刻意引入未来一日收盘价 shift(-1) 作为反例金丝雀，验证 T1.AST_CAUSAL 必拦截并自动入墓碑库。",
            mechanism="前视偏移算子违背因果性公理，必须在 VERIFYING 阶段被强制短路判 FALSIFIED。",
            formula_latex=r"f^{\text{leaky}}_{i,t} = \frac{C_{i,t+1}}{C_{i,t}} - 1 \quad (\text{FORBIDDEN: } t+1 > t)",
            derivation_steps=["构造显式前视表达式 close.shift(-1) / close - 1.0", "提交至 FormalVerificationEngine 触发 AST_UNSAFE 硬拦截"],
            assumptions=[{"id": "A1", "text": "因果性前提（故意违背）", "status": "fail"}],
            boundary_conditions=["任何未来算子一律 FALSIFIED 且不可覆盖"],
            expected_sign="positive",
            expected_failure_regimes=["实盘全时段（数学上不可实现）"],
        ),
        trial_count_m=15,
        bootstrap_b=200,
        created_by="quant-lead",
    )
    res3 = orch.dispatch_research(std3_req, async_mode=False, study_id="STD-0003")

    return {
        "seeded": True,
        "snapshot_id": snap.snapshot_id,
        "universe_id": universe_id,
        "study_ids": [res1.study.study_id, res2.study.study_id, res3.study.study_id],
        "verdicts": {
            res1.study.study_id: res1.study.verdict,
            res2.study.study_id: res2.study.verdict,
            res3.study.study_id: res3.study.verdict,
        },
        "baseline_count": len(existing_baselines),
        "tombstone_count": len(reg.list_tombstones(snapshot_id=snap.snapshot_id)),
    }


__all__ = ["ensure_golden_studies"]
