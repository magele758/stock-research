# Pro-Quant V2.0 升级契约（全团队统一对齐基准）

> **背景**：用户对 V0.1 方案给出明确纠偏反馈：
> 1. V0.1 偏基本面研报，**必须转向专业机构级量化分析平台（Pro-Quant Research Platform）**；
> 2. **Chat 助手不再自造轮子**，直接接入并在用户本机的 **Agent Home（`<ppeng-agent-core>`）** 中构建专属 Quant Bot / Bot 群来辅助；
> 3. 明确**量化工具与数据获取的实战工程方案**（覆盖 A 股为主，兼顾 ETF/期货/期权及美港股扩展）；
> 4. 深度拆解并补齐“玩具/雏形量化（包括用户旧项目 `<legacy-quant-repo>`）”到**专业机构级量化分析的 8 大核心 Gap**；
> 5. 全面重构 **PRD-Map、开发计划与高保真原型 UI**，彻底告别稚嫩感，达到机构量化研究终端的专业水准。

---

## 1. 七大核心产品域统一编号（所有文档与 UI 原型严格对齐）

| 域 ID | 产品域名称 | 涵盖核心能力（Pro-Quant 核心闭环） |
| :---: | :--- | :--- |
| **Q1** | **量化数据底座与质量哨兵（Data Lake & PIT QA）** | `Q1.1` 多源接入与增量调度（日频/分钟/财务 PIT/历史成分/历史申万行业/两融/龙虎榜/一致预期/期货贴水） · `Q1.2` 统一存储（DuckDB + Parquet + Qlib Bin + PG Metadata） · `Q1.3` 零幸存者偏差/退市/复权/PIT 自动化质量审计与对账大盘 |
| **Q2** | **Alpha 因子工坊与统计学实验室（Alpha Lab）** | `Q2.1` 因子表达式 DSL / Python / Polars / Qlib Alpha158 编辑器 · `Q2.2` 截面预处理管线（MAD 去极值、Z-Score/Rank、行业与市值正交中性化） · `Q2.3` 因子诊断 Tearsheet（RankIC / ICIR / t-stat、10 分层单调性曲线、IC 衰减期、换手率、相关性矩阵与拥挤度） |
| **Q3** | **多因子合成与过拟合防御台（Model & Validation）** | `Q3.1` 线性/ICIR 加权/LightGBM/CatBoost/时序截面模型训练与实验追踪（Experiment Registry） · `Q3.2` 严格防过拟合验证门禁（Purged K-Fold / CPCV 组合清除交叉验证、Deflated Sharpe Ratio [DSR]、PBO 过拟合概率、Walk-Forward 样本外滚动验证） |
| **Q4** | **组合构建与 Barra 风格风险终端（Portfolio & Risk）** | `Q4.1` Barra CNE5/CNE6 风格风险模型（Size / Beta / Momentum / Volatility / NonlinearSize / Book-to-Price / Liquidity / EarningsYield / Growth / Leverage）与特质风险协方差矩阵 · `Q4.2` 凸优化器（`cvxpy` 均值方差/Max-Sharpe/最小方差/风险平价，支持行业中性、风格暴露上限、单股权重上限、换手惩罚与成分股偏离约束） |
| **Q5** | **机构级撮合回测与业绩归因（Backtest & Attribution）** | `Q5.1` 向量化快筛 + 事件驱动高保真双回测引擎 · `Q5.2` A 股微结构真实约束（T+1、涨跌停一字板撮合失败、停牌冻结、成交量参与率上限 POV、平方根/Almgren-Chriss 冲击成本、双边佣金+印花税+过户费、非前复权真实成交价+分红送转现金流） · `Q5.3` Brinson 归因 + Barra 因子收益分解（纯 Alpha vs 风格 Beta vs 行业暴露）与水下回撤分析 |
| **Q6** | **影子实盘、因子衰减监控与风控闸门（Shadow & Drift）** | `Q6.1` 每日影子组合（Paper/Shadow Portfolio）自动调仓模拟与实盘 vs 回测滑点漂移归因 · `Q6.2` 因子 IC 滚动衰减/拥挤度/状态切换（Regime Shift）实时预警 · `Q6.3` 严格研究/交易隔离（Authority Gate：只产出调仓指令清单/信号文件与风控审阅，绝不盲目直连自动实盘下单） |
| **Q7** | **Agent Home Quant Bot 深度协同与自动化研发（Agentic Quant）** | `Q7.1` Agent Home（`ppeng-agent-core` Daemon `27070`）双向集成：Quant MCP Server + `@ppeng/agent-quant` Domain Bundle + 专属 Quant Bot / Sparse-MoE 专业 Bot 群 · `Q7.2` 嵌入式 Bot 协作侧栏（上下文画布注入、实时 Steer、A2UI v0.9 交互参数/审批卡片、5 层记忆跨实验防重复踩坑） · `Q7.3` 受控 RD-Agent 因子研发闭环（假设提出 → 沙箱代码执行 → CPCV/DSR 硬门禁淘汰 → 失败教训入记忆） · `Q7.4` Agent Home 例行任务 Routine + 飞书双向推送（含 `[NO_UPDATE]` 静默与异常唤醒） |

---

## 2. Quant MCP Server 统一工具契约（前后端 & Agent Home 共用）

所有供 Agent Home Quant Bot 调用的量化能力统一以只读/受控 MCP Tool 暴露（Prefix: `quant_`）：
- **数据层**：`quant_data_health`、`quant_query_pit_panel`、`quant_universe_constituents`
- **因子层**：`quant_eval_factor`、`quant_factor_correlation`、`quant_submit_factor_hypothesis`
- **模型与检验层**：`quant_run_cpcv_gate`、`quant_list_experiments`
- **组合与风险层**：`quant_barra_exposure`、`quant_optimize_portfolio`
- **回测与归因层**：`quant_run_backtest`、`quant_attribution_tearsheet`、`quant_shadow_drift_check`

*(高副作用或长时计算工具如 `quant_run_backtest` / `quant_submit_factor_hypothesis` 遵循 Agent Home `approvalMode` / `bg_run` + `wait_for` 零 Token 持久等待契约。)*

---

## 3. 本轮 3 路 `herdr-flow` 路径边界（shared 模式）
- `quant-gap-data` (`codex`): 独占 `docs/research/03-quant-gap-and-data-tooling.md`
- `agent-home-prd` (`agy`): 独占 `docs/plan/03-agent-home-integration.md`, `docs/plan/01-prd-map.md`, `docs/plan/02-dev-plan.md`
- `pro-quant-ui` (`cursor-high`): 独占 `docs/design/prototype-v1-quant.html`
