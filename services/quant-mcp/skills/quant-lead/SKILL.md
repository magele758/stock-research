---
name: quant-lead
description: Pro-Quant V3.0 量化主研 Runbook（21 个 quant_* 工具）：INSTITUTIONAL_GATES 正式入库门槛、因子检验、CPCV/DSR、形式化证明、跨维度 Regime、AUM 容量、术语深度解读、集群调度研究与零信任复算写回、研究血缘树与墓碑库查重、Barra 约束调参 A2UI、零荐股红线。当用户提到 因子/RankIC/ICIR/CPCV/DSR/PBO/FDR/形式化证明/前缀不变性/Regime/容量/术语/研究课题/派发/墓碑/Barra/组合优化/回测/归因/影子盘/漂移 时使用。
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

正式入库硬门槛对齐 `INSTITUTIONAL_GATES`（SSOT：`quant_core.schemas`；前端 `lib/thresholds.ts` 同源），观察/WARN 区间只可继续观察、不得入库：

| 指标 | 正式入库（PASS） | 观察（WARN） |
| --- | --- | --- |
| RankIC 均值 | ≥ 0.03 | 0.02–0.03 |
| ICIR（年化） | ≥ 1.50 | 1.00–1.50 |
| Newey-West t | ≥ 3.00 | 2.00–3.00 |
| 日均单边换手 | ≤ 15% | 15%–20% |
| 库内最大相关 | < 0.70 | 0.50–0.70 |
| 分层单调性 ρ | ≥ 0.90 | 0.70–0.90 |
| 覆盖率 | ≥ 80% | 70%–80% |
| CPCV PBO | ≤ 0.25 | 0.20–0.25 |
| DSR | ≥ 0.95 | 0.90–0.95 |
| OOS/IS Sharpe | ≥ 0.50 | 0.40–0.50 |
| BH-FDR q 值 / SPA p 值 | ≤ 0.05 | 0.05–0.10 |
| 参数邻域最大衰减 | ≤ 30% | 30%–50% |
| 前缀不变性漂移 | ≤ 1e-10 | — |
| 归因闭合误差 | ≤ 1.0 bp | — |
| Barra 风格暴露 | \|z\| ≤ 0.50σ | 0.30–0.50σ |
| 单维度贡献占比 | ≤ 60% | 50%–60% |

门槛数值以工具返回的 `gate_checks` / `proof checks` 为准；本表与工具不一致时以工具为准并提示用户核对版本。

## 2. CPCV / DSR 过拟合门槛

`quant_run_cpcv_gate(experiment_id, n_splits=6, k_test=2, purge_days=5, embargo_days=10, trial_count_m)`：

| 指标 | 通过 | 拒绝 |
| --- | --- | --- |
| DSR（按 `trial_count_m` 校正） | ≥ 0.95 | < 0.80 |
| PBO（CSCV） | ≤ 0.25 | > 0.50 |
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

## 7. 专业术语与实时指标深度解读

用户问"什么是 X / 这个数值好不好 / 解读一下当前指标"时：

1. `quant_glossary_lookup(query_or_term_id, category?, context_values?)` 取术语定义、公式、机构门槛与常见陷阱（无需数据快照，数据湖为空也可用）。
2. 把当前工具返回的真实数值作为 `context_values` 传入（如 `{"rank_ic_mean": 0.031, "icir_ann": 1.8}`），按 `bot_prompt` 生成解读。
3. 回答结构固定：**定义 → 公式 → 当前值 vs 门槛（PASS/WARN/FAIL）→ 常见陷阱 → 下一步工具动作**。
4. 无实时数值时只讲定义与门槛，明确写"当前值未验证"，禁止凭记忆补数。

## 8. 数学推演与形式化证明铁律（严禁臆想）

1. 任何新因子/方法的提案必须附 `math_derivation`：经济假设 → 机制 → 公式（LaTeX）→ 推导步骤 → 假设清单 → 边界条件 → 预期符号 → 预期失效 Regime。缺项即视为"未推演"，不得派发。
2. 推演只能陈述**待检验命题**，不得把"应该有效"当作结论；结论只来自 `quant_formal_verify` 的 8 项证明链：
   - **T1** AST 因果 / 多截点前缀不变性（漂移 ≤1e-10，null 掩码一致）；
   - **T2** 尺度不变性 / 覆盖率；
   - **T3** PIT 时间单调 / 守恒与 PSD；
   - **T4** Stationary Bootstrap 置信区间、CPCV+PBO+DSR、BH-FDR+SPA、参数悬崖。
3. `passed=false` 或存在 `blocking_failures` 时：只汇报失败 `check_id` 与 `reason_code`，禁止选择性引用通过项，禁止标 `VERIFIED`。
4. 引用 `proof_sha256` + `snapshot_id` 作为证据指纹；`trial_count_m` 如实累计。
5. `quant_regime_analysis`：`single_dim_dominance_flag=true`（单桶贡献 >60%）必须在结论首段红字提示；`quant_capacity_analysis` 为未校准情景分析（`calibration_status`），只能表述"半衰容量/盈亏平衡规模"，不得当作交易额度。

## 9. 开发集群调度研究与零信任复算写回

1. 派发：`quant_dispatch_research(title, hypothesis, expression|factor_id, parent_study_id?, dispatch_mode, target_cluster, math_derivation, async_mode=true)`（需审批）。
   - `engine_sandbox`：服务端确定性 7 步流水线（墓碑查重 → 形式化证明 → 评估/CPCV → Barra/容量 → 事件回测 → Regime → 诊断与改进计划）；
   - `cluster_dev_worker`：只读数据湖的隔离沙箱 Worker；`quant_list_research_studies(view=clusters)` 先看集群在线与队列。
2. 轮询：用返回的 `study_id` 调 `quant_get_research_study(study_id, include_events_tail=10)`，状态机 `DRAFT→QUEUED→DISPATCHED→DEVELOPING→VERIFYING→DIAGNOSING→COMPLETED|FALSIFIED|FAILED`。
3. **零信任写回**：外部 Worker 的任何指标都只是"声明"。用 `quant_publish_research_finding(candidate_expression, study_id?, worker_cluster_id, worker_claimed_metrics, math_derivation)` 提交，服务端 FormalVerifier 重新复算；回复必须对照 `claimed_metrics` 与 `verified_metrics`，差异原样报告。
4. 工具返回 `PROOF_FAILED` / `CLUSTER_UNAVAILABLE` 时如实说明，不得绕过复算或把声明值当结果。
5. 诊断（D01–D07）给出 `improvement_plan` 后，若要派发 V2 迭代子课题，必须挂 `parent_study_id` 并等待用户确认。

## 10. 研究血缘树与墓碑库查重

1. 提出新假设前先 `quant_list_research_studies(view="tombstones", query=<关键词或 expr_hash>)`；命中三层查重（`expr_hash` 精确 / AST 指纹 / 关键词相似）任一项，先汇报已有教训，不得静默重跑。
2. `quant_dispatch_research` 返回 `DUPLICATE_TOMBSTONE` 时：展示 `tombstone_warnings`，仅当用户给出明确改进理由才可 `allow_duplicate=true` 再派发。
3. 查看课题演进：`quant_list_research_studies(view="lineage", parent_study_id=<根课题>)`，沿 `parent_study_id` 展示"根课题 → 迭代子课题"树与各自 `verdict`。
4. 维度基线：`view="baselines"` 查看 5 维市场基线（market_regime / volatility_regime / market_cap_tier / sw_industry / barra_style）当前状态，作为 Regime 对照参考。
5. 课题终态为 `FALSIFIED` 时按第 4 节写 `project.memory` 墓碑行（沿用 `memory_line` 字段）；`VERIFIED` 仅在服务端 `formal_proof.passed=true` 时才可复述。
