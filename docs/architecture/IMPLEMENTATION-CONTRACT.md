# Pro-Quant V2.0 工程实现共享契约（Wave 1 四路并行对齐规范）

> 所有 `herdr-flow` Worker（`quant-lake`, `quant-engines`, `quant-api-mcp`, `quant-web`）必须严格遵守本契约的模块路径、API 路由、MCP 工具名与数据规范。

---

## 1. 仓库拓扑与独占路径边界

- `packages/quant-core/src/quant_core/schemas.py`：**主控已固化**（Pydantic v2 唯一真相，禁止 Worker 随意破坏已有字段名；若确实需要扩展，保持向后兼容可选字段）。
- **`quant-lake` (`agy`)**：
  - `packages/quant-core/src/quant_core/lake/`
  - `packages/quant-core/src/quant_core/providers/`
  - `packages/quant-core/tests/test_lake_pit.py`
  - `scripts/seed_golden_dataset.py`
- **`quant-engines` (`codex`)**：
  - `packages/quant-core/src/quant_core/factors/`
  - `packages/quant-core/src/quant_core/validation/`
  - `packages/quant-core/src/quant_core/risk/`
  - `packages/quant-core/src/quant_core/backtest/`
  - `packages/quant-core/src/quant_core/shadow/`
  - `packages/quant-core/tests/test_quant_engines.py`
- **`quant-api-mcp` (`cursor-sonnet`)**：
  - `services/quant-api/`（FastAPI `:8100`）
  - `services/quant-mcp/`（FastMCP Streamable HTTP `:8101` + Stdio）
  - `scripts/register_agent_home.mjs`（零侵入注册 Agent Home `quant-lead` Bot 与 Quant Skills）
- **`quant-web` (`cursor-high`)**：
  - `apps/web/`（Next.js 16 + React 19 + TypeScript + Tailwind CSS `:3100`）

---

## 2. Python 核心包内部接口契约（`quant-lake` ↔ `quant-engines` ↔ `quant-api-mcp`）

### 2.1 `quant_core.lake`（`quant-lake` 导出）
- `QuantLake(root_dir: Path | str)`：
  - 根目录结构：`data/quant-lake/{raw,canonical,manifests,qlib,quarantine,artifacts}` + SQLite/PostgreSQL 元数据注册表（本地默认 SQLite `data/state/quant_registry.db`，设 `QUANT_DATABASE_URL=postgresql://...` 自动切 PG）。
  - `get_latest_snapshot() -> DatasetSnapshot`
  - `list_snapshots() -> list[DatasetSnapshot]`
  - `load_bars(universe_id: str, start_date: date | None, end_date: date | None, snapshot_id: str | None = None) -> pl.DataFrame`（返回含 `listing_id, symbol, name, market, board, trade_date, open, high, low, close, pre_close, vwap, volume_shares, amount_ccy, adj_factor, up_limit, down_limit, is_suspended, is_st, float_market_cap, industry_l1` 的 PIT 面板）
  - `load_pit_financials(as_of: datetime, snapshot_id: str | None = None, availability_mode: AvailabilityMode = STRICT_PUBLIC) -> pl.DataFrame`（严格按 `published_at <= as_of` 重放发布/修订/撤回）
  - `load_corp_actions(snapshot_id: str | None = None) -> pl.DataFrame`
  - `load_universe_constituents(universe_id: str, as_of_date: date, snapshot_id: str | None = None) -> list[UniverseMember]`
  - `run_qa_sentinel(snapshot_id: str | None = None) -> DataHealthReport`（7 项硬核审计：复权连续性、PIT 倒挂、退市覆盖、缺失非零、成分权重和、涨跌停规则跨日、分钟/日线一致性）
  - `export_qlib_bin(snapshot_id: str, market: str = "CN_A") -> dict`（借鉴旧项目增量签名校验 + `_staging` 原子切换）
  - `list_experiments(limit: int = 20) -> list[ExperimentRecord]` & `save_experiment(rec: ExperimentRecord) -> None`
  - `save_artifact(run_id: str, name: str, payload: dict | pl.DataFrame) -> ArtifactRef`

### 2.2 `quant_core` 引擎入口（`quant-engines` 导出）
- `quant_core.factors.engine`:
  - `BUILTIN_FACTORS: dict[str, dict]`（至少内置 4 个专业因子：`alpha_vp_corr_20d`, `alpha_mom_resid_60d`, `alpha_earn_sue_pit`, `alpha_smart_money_vwap`）
  - `validate_factor_ast(expression: str) -> tuple[bool, list[str]]`（静态禁止 `shift(-k)` / 未来索引 / 危险系统调用）
  - `evaluate_factor(bars_df: pl.DataFrame, req: FactorEvalRequest, pit_fin_df: pl.DataFrame | None = None) -> FactorEvalResult`
  - `compute_factor_correlation(bars_df: pl.DataFrame, req: FactorCorrRequest) -> FactorCorrResult`
- `quant_core.validation.cpcv`:
  - `run_cpcv_gate(returns_matrix: np.ndarray, dates: list[date], req: CpcvRequest, baseline_returns: np.ndarray | None = None) -> CpcvResult`
  - `compute_deflated_sharpe(sharpe_daily: float, n_obs: int, n_trials: int, trial_sr_std: float, skew: float, kurtosis: float) -> float`
  - `compute_cscv_pbo(candidate_returns_matrix: np.ndarray, n_blocks: int = 8) -> float`
- `quant_core.risk.barra_opt`:
  - `compute_barra_exposure(bars_df: pl.DataFrame, weights: dict[str, float], bench_weights: dict[str, float], req: BarraExposureRequest) -> BarraExposureResult`
  - `optimize_portfolio(bars_df: pl.DataFrame, alpha_scores: dict[str, float], prev_weights: dict[str, float], bench_weights: dict[str, float], req: PortfolioOptRequest) -> PortfolioOptResult`（真实调用 `cvxpy` + CLARABEL/OSQP 求解并支持 `infeasible` 软松弛诊断与整手取整复核）
- `quant_core.backtest.engine`:
  - `get_price_limit_pct(board: BoardType, is_st: bool, trade_date: date) -> float | None`（含 **2026-07-06 沪深主板 ST 从 5% 调为 10%** 历史规则生效切换！）
  - `run_backtest(bars_df: pl.DataFrame, corp_actions_df: pl.DataFrame | None, signals_df: pl.DataFrame, bench_df: pl.DataFrame, req: BacktestRequest) -> BacktestResult`（同时支持 `screening` 与 `event` A 股微结构引擎 + Brinson-Fachler/Barra 闭合归因）
- `quant_core.shadow.monitor`:
  - `compute_shadow_drift(bars_df: pl.DataFrame, req: ShadowDriftRequest) -> ShadowDriftResult`（Perold Implementation Shortfall + 滚动 60D IC 衰减 + Authority Gate 只读信号签核单，物理零下单）

---

## 3. FastAPI REST API 契约（`services/quant-api`, Port `8100`）

前缀统一为 `/api/v1`（CORS 允许 `http://127.0.0.1:3100` / `http://localhost:3100`）：
- `GET  /api/v1/health` → `{ status: "ok", snapshot_id, markets, mcp_port: 8101 }`
- `GET  /api/v1/data/health` → `DataHealthReport`
- `GET  /api/v1/data/snapshots` → `list[DatasetSnapshot]`
- `GET  /api/v1/data/universe/{universe_id}` → `{ universe_id, as_of_date, count, members }`
- `POST /api/v1/data/sync` → 触发指定 Provider（`tushare|baostock|edgar`）增量同步或校验（**严禁 Provider 失败时静默生成假数据**；明确返回失败原因与可用快照状态）
- `GET  /api/v1/factors/catalog` → 内置 + 已登记因子列表
- `POST /api/v1/factors/evaluate` → `FactorEvalResult`
- `POST /api/v1/factors/correlation` → `FactorCorrResult`
- `POST /api/v1/factors/hypothesis` → `FactorHypothesisResult`
- `GET  /api/v1/experiments` → `list[ExperimentRecord]`
- `POST /api/v1/validation/cpcv` → `CpcvResult`
- `POST /api/v1/risk/barra-exposure` → `BarraExposureResult`
- `POST /api/v1/portfolio/optimize` → `PortfolioOptResult`
- `POST /api/v1/backtest/run` → `BacktestResult`（支持 `?async_mode=true` 返回 `AsyncJobRecord`）
- `GET  /api/v1/shadow/drift` → `ShadowDriftResult`
- `GET  /api/v1/jobs/{job_id}` → `AsyncJobRecord`
- `GET  /api/v1/mcp/catalog` → 13 个 `quant_*` MCP 工具状态与调用统计（供前端 `/data` 页展示）

---

## 4. FastMCP Server 契约（`services/quant-mcp`, Streamable HTTP `:8101/mcp` + `--transport stdio`）

必须完整暴露 `V2-QUANT-CONTRACT.md` 的全部 **13 个 `quant_*` 工具**，响应统一包装为 `McpEnvelope`（单次响应 JSON ≤ 8 KiB，大表落盘返回 `artifact.uri`）：
1. `quant_data_health`
2. `quant_query_pit_panel`
3. `quant_universe_constituents`
4. `quant_eval_factor`
5. `quant_factor_correlation`
6. `quant_submit_factor_hypothesis`
7. `quant_run_cpcv_gate`
8. `quant_list_experiments`
9. `quant_barra_exposure`
10. `quant_optimize_portfolio`
11. `quant_run_backtest`
12. `quant_attribution_tearsheet`
13. `quant_shadow_drift_check`

---

## 5. Next.js 专业量化前端与 Agent Home Bot Dock 契约（`apps/web`, Port `3100`）

- 环境变量：
  - `NEXT_PUBLIC_QUANT_API_URL`（默认 `http://127.0.0.1:8100`）
  - `AGENT_HOME_DAEMON_URL`（服务端运行时自动探测顺序：环境变量 → 读取 `<ppeng-agent-core>/.agent-state/dev-lab.ports.json` → `http://127.0.0.1:27070` → `http://127.0.0.1:37070`）
  - `RAW_AGENT_AUTH_TOKEN`（仅在 Next.js Route Handler `/api/agent-home/[...path]` 服务端注入 `Authorization: Bearer`，永不暴露给浏览器）
- 6 大视图与路由（视觉与信息密度严格看齐 `docs/design/prototype-v1-quant.html`）：
  - `/alpha`（默认落地页）、`/models`、`/portfolio`、`/backtest`、`/data`、`/shadow`
- 右侧 **Agent Home Quant Bot Dock**：
  - 实时探测 `/api/agent-home/api/health`（或 `/api/bots`）：
    - 当本机 Agent Home Daemon 在线时：真实调用 `POST /api/agent-home/api/bots/quant-lead/open`、`POST /api/agent-home/api/sessions/:id/stream`（在最新 `user` 消息头部拼接 `<quant_canvas_context>...</quant_canvas_context>` 保 `system v19` KV Cache）、`POST /api/agent-home/api/sessions/:id/steer`、`POST /api/agent-home/api/sessions/:id/a2ui/action`；
    - 当本机 Agent Home Daemon 暂未启动时：明确显示 `DAEMON OFFLINE` 状态与一键复制启动命令，并允许以只读本地诊断模式（调用 `quant-api` 真实端点）体验快捷指令与 A2UI 约束联动，绝不报错白屏！
