---
name: quant-lead
description: Pro-Quant 量化主研 Runbook：因子检验流程、CPCV/DSR 过拟合门槛、Barra 约束调参 A2UI 卡片、已证伪因子写 project.memory 规范与零荐股红线。当用户提到 因子/RankIC/ICIR/CPCV/DSR/PBO/Barra/组合优化/回测/归因/影子盘/漂移 时使用。
---

# quant-lead 量化主研 Runbook

所有数值只来自 `quant_*` MCP 工具（确定性内核）。结论必须带 `snapshot_id` + `as_of`；没有工具证据就说"未验证"。

## 0. 红线（不可协商）

1. **零荐股**：只输出统计指标、因子暴露、样本外验证、漂移分析。禁止"建议买入/卖出某股""保证收益""目标价"。调仓输出只能叫"只读信号清单（READ_ONLY_SIGNAL_EXPORT_ONLY）"，系统无任何券商下单能力，也不得协助接入。
2. **数据不足不编造**：`DATA_STALE` / `INSUFFICIENT_EVIDENCE` / `degraded` 时，如实报告并建议 `make seed` 或数据同步，不得用记忆数字顶替。
3. **耗时工具异步**：`quant_run_backtest`、`quant_run_cpcv_gate`、`quant_submit_factor_hypothesis` 默认 `async_mode=true`，取到 `job_id` 后用 `wait_for` / 轮询（`GET /api/v1/jobs/{id}`），单次 MCP 调用不得阻塞 >20s。
4. **大结果不灌上下文**：只读 `summary` / `preview` 与 `artifact.uri`；需要明细时让用户在 Web 工作台 `/alpha` `/backtest` 查看。

## 1. 因子检验标准流程

1. `quant_data_health` → 确认 `overall_status` 非 fail、记下 `snapshot_id`。
2. 检索 `project.memory` 的「已证伪因子墓碑库」与 `quant_list_experiments`，同质因子（同 `expr_hash` 或描述等价）不得重复提交。
3. `quant_submit_factor_hypothesis`（需审批；AST 禁止 `shift(-k)`、未来索引、系统调用）。`ast_safe=false` 直接终止并说明原因。
4. `quant_eval_factor`：看 RankIC 均值、ICIR、Newey-West t、分层单调性、换手、半衰期、覆盖率、`gate_checks`。
5. `quant_factor_correlation`：与因子库 |ρ|>0.5 视为冗余（0.3–0.5 需增量 ICIR 论证）。
6. 通过准入后才进入 CPCV 门禁（第 2 节）。

准入参考线（以工具 `gate_checks` 为准）：|RankIC|≥0.02、ICIR_ann≥1.0、|NW t|≥2.0、覆盖率≥80%、单调性 ρ≥0.7、库内最大相关 <0.5。

## 2. CPCV / DSR 过拟合门槛

`quant_run_cpcv_gate(experiment_id, n_splits=6, k_test=2, purge_days=5, embargo_days=10, trial_count_m)`：

| 指标 | 通过 | 拒绝 |
| --- | --- | --- |
| DSR（按 `trial_count_m` 校正） | > 0.95 | < 0.80 |
| PBO（CSCV） | < 0.25 | > 0.50 |
| OOS/IS Sharpe 比 | ≥ 0.5 | < 0.3 |
| 负收益 OOS 路径占比 | ≤ 20% | > 40% |
| purge 冲突 `purge_conflicts` | 必须 = 0 | 任何 >0 视为实现错误，停止并上报 |

规则：`trial_count_m` 必须如实累计（含已淘汰试验），不得为通过门槛调小；介于通过/拒绝之间为 `inconclusive`，只能继续观察，不得入库。每次改表达式都算一次新试验。

## 3. Barra 约束调参（A2UI 卡片）

组合优化出现 `infeasible` / `relaxed_optimal` / 风格越界时，用 `a2ui_render` 渲染可调卡片（数值来自 `quant_barra_exposure` / `quant_optimize_portfolio`）：

```json
{
  "surfaceId": "opt_tune_<experiment_id>",
  "catalogId": "https://a2ui.org/specification/v0_9/basic_catalog.json",
  "messages": [
    {"createSurface": {"surfaceId": "opt_tune_<experiment_id>", "catalogId": "https://a2ui.org/specification/v0_9/basic_catalog.json"}},
    {"updateDataModel": {"surfaceId": "opt_tune_<experiment_id>", "value": {"turnoverLambda": 0.6, "maxIndustryDev": 0.03, "maxSizeZ": 0.5, "maxStockWeight": 0.02, "experimentId": "<experiment_id>"}}},
    {"updateComponents": {"surfaceId": "opt_tune_<experiment_id>", "components": [
      {"id": "root", "component": "Card", "child": "col"},
      {"id": "col", "component": "Column", "children": ["title", "f_lambda", "f_ind", "f_size", "btn"]},
      {"id": "title", "component": "Text", "text": "组合优化约束调参（Barra CNE6 · cvxpy）"},
      {"id": "f_lambda", "component": "TextField", "label": "换手惩罚 λ", "value": {"path": "/turnoverLambda"}},
      {"id": "f_ind", "component": "TextField", "label": "行业主动偏离上限", "value": {"path": "/maxIndustryDev"}},
      {"id": "f_size", "component": "TextField", "label": "Size 暴露上限 (σ)", "value": {"path": "/maxSizeZ"}},
      {"id": "btn", "component": "Button", "label": "应用约束并重跑优化", "action": {"name": "quant.rerun_optimization", "sendDataModel": true}}
    ]}}
  ]
}
```

收到 `[a2ui:action quant.rerun_optimization] {...}` 后：把 dataModel 映射到 `quant_optimize_portfolio` 的 `constraints`（`turnover_lambda` / `max_industry_dev` / `max_size_z` / `max_stock_weight`），重跑并用「前后对比表」回复（净超额、TE、单边换手、风格越界数、`solve_status`）。放宽约束时必须说明代价（TE/换手/容量），且 `max_constraint_violation` 须 ≈ 0。

因子提交审批卡动作：`quant.approve_cpcv_gate`（提交假设并异步跑 CPCV）、`quant.reject_and_memorize`（淘汰并写墓碑，见第 4 节）。

## 4. 淘汰因子 → `project.memory` 墓碑库

任一情形即写入（`memory_write` 到 `project.memory`，一条一行）：CPCV 判 `reject`/`inconclusive`；准入失败；与库内因子 |ρ|>0.5；回测净超额为负或摩擦吞噬 >50% 毛超额。

```
[FALSIFIED] <factor_id> | <expr_hash> | <原因代码: LOW_DSR|HIGH_PBO|OOS_DECAY|HIGH_TURNOVER|COLLINEAR(<peer>)|AST_UNSAFE|NEG_NET> | DSR=<x> PBO=<y> OOS/IS=<z> | snap=<snapshot_id> | <YYYY-MM-DD>
```

提出新假设前先检索 `[FALSIFIED]`；同 `expr_hash` 工具会返回 `duplicate=true`，此时只汇报已有结论，不重跑。用户偏好（股票池、换手上限、行业/风格容忍度）写 `user.memory`；当前研究主线写 `session.long`。

## 5. 回测与归因

- `quant_run_backtest(engine="event")` 才计入 T+1、涨跌停（含 2026-07-06 起沪深主板 ST 10%）、停牌、PoV 冲击与印花税；`screening` 仅快筛，不得据此下结论。
- 报告必含：年化/超额/IR/MaxDD、`friction` 拆解、`total_rejections`（涨跌停/停牌未成交）、归因闭合误差 (`brinson_closure_error_bp`, `barra_closure_error_bp`，应≈0)。
- `quant_attribution_tearsheet(run_id|job_id)` 读已完成回测的 Brinson/Barra 归因。

## 6. 影子盘漂移

`quant_shadow_drift_check`：关注 `risk_state`、`median_is_delta_bps`、`fill_rate_pct`、`factor_decay`（`KILL-SWITCH` 立即提示人工复核）、`authority_manifest.sha256`。`fact_fingerprint` 与上次相同且无告警 → 回复 `[NO_UPDATE]`。
