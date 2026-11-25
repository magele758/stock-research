# QuantLab Pro V3.0 · 顶级投行量化研究与形式化验证统一实施契约

> **文档性质**：跨 Worker（`v3-formal-engine` / `v3-cluster-ledger` / `v3-api-mcp` / `v3-terminal-ui`）唯一事实契约。融合 Stage 1 三强架构报告：[v3-gap-math-formal.md](../research/v3-gap-math-formal.md)、[v3-gap-cluster-knowledge.md](../research/v3-gap-cluster-knowledge.md)、[v3-gap-terminal-ux.md](../research/v3-gap-terminal-ux.md)。
> **兼容红线**：100% 保留 V2.0 全部 17+ 契约 REST 端点、13 个 V2 `quant_*` MCP 工具与 `McpEnvelope.schema_version="2.0"`（`<= 8,192` 字节），严守只读无下单安全正则（仅 `GET`/`POST`，零 `order|broker|execut|trade|position|account|buy|sell|ctp|qmt` 路径/工具名，`AuthorityGateManifest.broker_connected=False`）。

---

## 1. 统一门槛常量 SSOT（`quant_core.schemas.INSTITUTIONAL_GATES`）

后端引擎、形式化验证器、术语百科、前端 `lib/thresholds.ts` 与 `quant-lead SKILL.md` 全部对齐同一门槛源：

| 指标 | 正式入库硬门禁 | 观察 / WARN 区间 |
| --- | --- | --- |
| `rank_ic_min` | `>= 0.03` | `0.02 – 0.03` |
| `icir_ann_min` | `>= 1.50` | `1.00 – 1.50` |
| `nw_t_stat_min` | `>= 3.00` | `2.00 – 3.00` |
| `daily_turnover_max` | `<= 0.15` (15%) | `0.15 – 0.20` |
| `max_corr_library` | `< 0.70` | `0.50 – 0.70` |
| `monotonicity_min` | `>= 0.90` | `0.70 – 0.90` |
| `min_coverage` | `>= 0.80` | `0.70 – 0.80` |
| `cpcv_pbo_max` | `<= 0.25` | `0.20 – 0.25` |
| `dsr_min` | `>= 0.95` | `0.90 – 0.95` |
| `oos_is_ratio_min` | `>= 0.50` | `0.40 – 0.50` |
| `bh_fdr_q_max` | `<= 0.05` | `0.05 – 0.10` |
| `spa_p_max` | `<= 0.05` | `0.05 – 0.10` |
| `param_cliff_drop_max` | `<= 0.30` (30%) | `0.30 – 0.50` |
| `prefix_invariance_tol` | `<= 1e-10` | — |
| `closure_error_bp_max` | `<= 1.0 bp` | — |
| `barra_style_hard_limit` | `abs(z) <= 0.50σ` | `0.30 – 0.50σ` |
| `single_dim_dominance_max` | `<= 0.60` (60%) | `0.50 – 0.60` |

---

## 2. 四大核心子系统与模块边界

### 2.1 `v3-formal-engine`（`packages/quant-core/src/quant_core/`）
1. **`validation/formal.py` (`run_formal_verification`)**：
   - `T1.AST_CAUSAL`：调用 `validate_factor_ast`，零未来算子/零 `center=True`。
   - `T1.PREFIX_INVARIANCE`：**真实多截点时间前缀截断重算**（默认截断点 `[0.45, 0.65, 0.85]`），分别在 `bars.filter(trade_date <= tau)` 与全量 `bars` 上从原始数据重建预处理与因子信号，计算 `max_prefix_drift = max |f(D_{<=tau}) - f(D_full)|`，断言 `<= 1e-10` 且 null/NaN 掩码完全一致；对注入全样本标准化/未来均值的反例必检出并判 `fail` (`LOOKAHEAD_PREFIX_VIOLATION`)。
   - `T2.SCALE_INVARIANCE`：对价格组实施正比例变换 $a \in \{0.1, 10.0\}$，验证因子截面秩相关性 $\rho \ge 0.9999$（尺度不变/保序）。
   - `T2.COVERAGE`：核对有效覆盖率 $\ge 0.80$ 与成熟交易日 $\ge 120$。
   - `T3.PIT_MONOTONICITY`：若传入财报事实，核对 `report_period_end <= ann_date <= as_of` 零倒挂。
   - `T3.CONSERVATION_PSD`：独立验算 Barra 协方差 $\lambda_{\min} \ge -10^{-10}$、权重单纯形 $|\sum w - 1| \le 10^{-6}$、Brinson/Barra 归因闭合误差 $\le 1.0\text{ bp}$。
   - `T4.BOOTSTRAP_CI`：Politis–Romano **Stationary Block Bootstrap**（`np.random.default_rng(seed)`，期望块长 $\ell = \max(h, \lceil n^{1/3}\rceil)$，$B \ge 500$ 默认 `1000`），输出 RankIC / ICIR / 多空年化 95% CI 与单侧/双侧 p-value。
   - `T4.CPCV_PBO_DSR`：串联真实 `run_cpcv_gate`，零 purge 冲突、PBO/DSR/OOS-IS。
   - `T4.BH_FDR_SPA`：跨历史 `trial_count_m` 计算 **Benjamini–Hochberg FDR q-value**（同时算 BY 敏感性）与 **Hansen SPA** p-value。
   - `T4.PARAM_CLIFF`：对表达式窗口参数做 $\pm 10\% / \pm 20\%$ 扰动并重评，计算邻点最大衰减 `max_neighbor_drop_pct`，识别“针尖过拟合”（`> 30%` 告警、`> 50%` 失败）。
   - 输出冻结哈希 `proof_sha256` 的 `FormalProofResult`。
2. **`factors/regime.py` (`build_dimension_baselines`, `analyze_factor_regimes`)**：
   - 严格因果 5 维市场基线（`market_regime`: BULL/BEAR/SIDEWAYS 带滞后状态机；`volatility_regime`: LOW/MID/HIGH 严格扩展/后向分位；`market_cap_tier`: LARGE/MID/SMALL；`sw_industry`: 申万一级；`barra_style`: CNE6 代理风格）。
   - 计算每个维度分桶的策略超额、基准收益、RankIC、ICIR、胜率、样本日、贡献占比，输出 **Regime Consistency Score (RCS)** 与 **Single-Dimension Dominance Warning**（单桶正向贡献 `> 60%` 触发 `DIMENSION_DOMINANCE`）。
3. **`risk/capacity.py` (`analyze_capacity_frontier`)**：
   - 6 档 AUM 网格（`10M, 50M, 100M, 500M, 1B, 3B CNY`），结合历史 ADV、波动率、平方根冲击 $Y\sigma\sqrt{Q/\text{ADV}}$ 与 `max_pov` 模拟净超额、IR、冲击 bps、PoV、成交率，计算 `half_alpha_aum_cny`（净超额衰减 50% 规模）、`breakeven_aum_cny`（归零规模）及流动性瓶颈 Top-10 标的。
4. **`validation/diagnosis.py` (`generate_diagnosis_and_guidance`)**：
   - 确定性规则引擎 `D01`–`D07`（前视泄漏 / 风格 Beta 伪装 / 多重检验过拟合 / 换手冲击吞噬 / 单一 Regime 依赖 / 流动性容量不足 / 因子库高度共线），每条必须绑定具体证据（`proof_check_id` / `slice_id` / 实测值），输出可执行 `improvement_plan`（含可选 `constraints_patch` 与 `child_study_patch`）。
5. **现有 V2 引擎增量丰富（完全向后兼容）**：
   - `factors/engine.py`：在 `evaluate_factor` 中填充可选 `rank_ic_ci95`、`icir_ci95`、`long_short_ci95`、`bootstrap_b`、`prefix_invariance_passed`、`max_prefix_drift`、`dimension_slices`（同时扩展 `compute_factor_correlation` 支持自定义表达式）。
   - `risk/barra_opt.py` & `backtest/engine.py`：填充可选 `invariants`（5 项守恒徽章）与轻量化 `capacity_frontier`。

### 2.2 `v3-cluster-ledger`（`packages/quant-core/src/quant_core/glossary/`, `lake/registry.py`, `services/quant-api/src/quant_api/orchestrator/`）
1. **术语知识图谱 SSOT（`quant_core/glossary/`）**：
   - `terms.json` + `registry.py`：内置 **64+ 条**权威机构量化术语（覆盖 `Q1`–`Q6` 与 `MATH` 全部 7 大类），每条含 `term_id`, `term`, `en_name`, `domain`, `category`, `aliases`, `short_tip`, `formula_latex`, `formula_lines`, `institutional_threshold`, `threshold_spec`, `common_pitfalls`, `related_terms`, `bot_prompt_template`。
2. **持续研究知识库（`lake/registry.py` 扩展，仍写入 `data/state/quant_registry.db`）**：
   - 5 张 SQLite WAL / PG18 兼容新表：`research_studies`, `dimension_baselines`, `falsified_tombstones`, `study_events`, `research_notes`。
   - 支持假说父子血缘递归查询（`parent_study_id` / `family_id`）、三层已证伪墓碑查重（`expr_hash` 精确 + 归一化 `ast_fingerprint` + 关键词 Jaccard）、`finalize_study()` 硬门禁（`formal_proof.passed == False` 严禁标为 `VERIFIED`）、以及针对 Golden 快照幂等预灌 3 条真实确定性研究课题（根课题 + V2 迭代子课题 + 已证伪墓碑课题）与 5 维基线。
3. **双模集群研究编排器（`services/quant-api/src/quant_api/orchestrator/`）**：
   - `ClusterResearchOrchestrator` 9 态状态机（`DRAFT -> QUEUED -> DISPATCHED -> DEVELOPING -> VERIFYING -> DIAGNOSING -> COMPLETED | FALSIFIED | FAILED`），每步写入 `study_events` 供 SSE 推送。
   - **Mode 1 `engine_sandbox`**：零外部依赖确定性 7 步研究流水线（前置墓碑查重 → `FormalVerificationEngine` → 因子/CPCV 评估 → Barra/Capacity → Event 回测 → 5 维 Regime 对照 → 确定性诊断与改进计划 → 落库 + 自动生成 IC Memo Markdown）。
   - **Mode 2 `cluster_dev_worker`**：安全白名单适配器 `local_subprocess` / `herdr_hc` / `ssh_runner`（`shell=False`、只读数据湖 `QUANT_LAKE_READ_ONLY=1`、隔离沙箱目录 `data/sandboxes/<study_id>`、硬超时熔断）；所有外部 Worker 产出强制走主控 `FormalVerificationEngine` 复算（零信任外部指标声明），并支持 `publish_external_finding()` 结构化回写。

### 2.3 `v3-api-mcp`（`services/quant-api/`, `services/quant-mcp/`, `scripts/register_agent_home.mjs`）
1. **新增 REST 端点（全量 `GET`/`POST`，零禁词正则冲突）**：
   - `GET /api/v1/glossary` & `GET /api/v1/glossary/{term_id}`
   - `POST /api/v1/validation/formal-verify`
   - `POST /api/v1/factors/regime-analysis`
   - `POST /api/v1/portfolio/capacity`
   - `GET /api/v1/research/studies` & `POST /api/v1/research/studies` & `POST /api/v1/research/dispatch`
   - `GET /api/v1/research/studies/{study_id}` & `GET /api/v1/research/studies/{study_id}/events` (SSE) & `GET /api/v1/research/studies/{study_id}/memo`
   - `POST /api/v1/research/studies/{study_id}/publish`
   - `GET /api/v1/research/baselines` & `POST /api/v1/research/baselines`
   - `GET /api/v1/research/tombstones` & `POST /api/v1/research/tombstones`
   - `GET /api/v1/research/clusters`
2. **新增 8 个 MCP 工具（总计 21 个，全部 `<= 8,192` 字节，长任务支持 `async_mode=true`）**：
   - `quant_glossary_lookup`
   - `quant_formal_verify`
   - `quant_regime_analysis`
   - `quant_capacity_analysis`
   - `quant_dispatch_research`
   - `quant_get_research_study`
   - `quant_list_research_studies`
   - `quant_publish_research_finding`
3. 同步更新 `services/quant-mcp/skills/quant-lead/SKILL.md` 与 `scripts/register_agent_home.mjs`（`BOT_DESCRIPTION <= 2000` 字符）。

### 2.4 `v3-terminal-ui`（`apps/web/`）
1. **全站术语 Tips + ⌘K 术语速查库 + Bot 深度解读**：
   - 统一阈值源 `lib/thresholds.ts`；前端内置术语库并可合并 `/api/v1/glossary`；
   - 单例 Portal `TermPopoverHost` + 轻量 `<TermTip />`（180ms hover / click pin / 避让右侧 Bot Dock / 零 CDN `FormulaBlock` / 当前数值 PASS/WARN/FAIL 对照条 / 常见陷阱 / 一键「让 Quant-Lead 结合当前数据解读」+ 离线本地确定性卡）；
   - TopBar 升级 `V3.0 PRO-QUANT` + `⌘K` 术语搜索按钮 + `GlossaryDrawer`。
2. **全新第 7 视图 `/research`（SideNav 首位，`Q7.3`）**：
   - 顶部 `ClusterStrip` + `NewStudyDrawer`（含实时墓碑相似度预警、父研究选择、集群选择、维度基线选择、一键「交给 Quant-Lead 起草」或「直接派发集群」）；
   - 左栏（248px，可折叠）：`StudyTree` 课题演进树 / `TombstoneList` 已证伪墓碑库 / `BaselinePanel` 5 维市场基线库；
   - 右主区（586px）：6 段式投行研究报告（`PipelineStepper` + 实时 SSE 日志、`MathCard` 数学推演、`ProofLedger` 8 项形式化证明链可展开、`DimensionMatrix` 跨维度热力图 + 单维依赖红旗、`DiagnosisCards` 根因诊断与一键派发 V2 迭代子研究/写入 `/portfolio` 约束/问 Bot、`ArtifactCard` + IC Memo Markdown 导出）。
3. **现有 6 视图投行级升级**：
   - 全量挂载 `<TermTip />`；
   - `/alpha` 增加 Bootstrap 95% CI 副行 + 维度切片稳定性矩阵 + 一键派发集群复研；
   - `/models` 增加 BH-FDR 多重检验校正图/表 + 参数敏感性悬崖图；
   - `/portfolio` & `/backtest` 增加 AUM 策略容量衰减前沿曲线（`CapacityFrontier`）+ 形式化守恒证明徽章（`InvariantStrip`）；
   - `/shadow` 增加 `DEGRADING` 因子一键派发复研；
   - `AgentHomeDock` 增加 `/查术语`、`/派发研究` 快捷指令、`QuantStudyCard` A2UI 卡片及 `/research` 画布上下文透传。
