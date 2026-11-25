# QuantLab Pro · 专业机构级量化投研系统（Pro-Quant V2.0）

**QuantLab Pro V2.0** 是一套面向 **A 股（CN_A）与美股（US_EQ）双市场** 的机构级 AI-Native 多因子量化投研与风控平台，涵盖 PIT（Point-in-Time）零幸存者偏差数据湖、截面预处理与 Newey-West HAC 因子检验、CPCV/DSR/PBO 防过拟合门禁、Barra CNE6 风险模型与 `cvxpy` 组合优化、A 股微结构高保真撮合回测与 Brinson/Barra 业绩归因、影子盘 Perold 实施缺口漂移监控，以及与本地 **Agent Home（`ppeng-agent-core`）** 的无侵入双向融合（Quant MCP `:8101` + `quant-lead` Bot Dock + A2UI v0.9 实时调参）。

> **安全边界**：本系统严格遵循**只读研究与影子监控**原则（`broker_connected=False`，无任何下单/撤单/实盘券商接口），所有量化指标均由确定性数学/统计引擎计算并绑定不可变 `snapshot_id` 与 `as_of`；任何外部数据源拉取失败均显式报错，**绝不静默伪造合成价格**。

---

## 仓库架构

```text
stock-research/
├── packages/quant-core/           # 确定性量化核心引擎 + PIT 数据湖（Python 3.13 / DuckDB / Polars / cvxpy）
│   └── src/quant_core/
│       ├── schemas.py             # Q1–Q7 共享 Pydantic v2 契约
│       ├── lake/                  # DuckDB + Parquet PIT 数据湖、7 项 QA 哨兵、Qlib Bin 导出、快照/实验注册表
│       ├── providers/             # Tushare / BaoStock / SEC EDGAR / Massive 真实接入适配器（零静默兜底）
│       ├── factors/               # AST 未来函数守卫、MAD/行业/市值/Barra 截面预处理、RankIC/ICIR/NW-t/10 分层
│       ├── validation/            # Purged K-Fold + CPCV(N=6,k=2 -> 15 路径) + DSR + CSCV PBO
│       ├── risk/                  # Barra CNE6 10 风格 WLS + Ledoit-Wolf PSD 协方差 + cvxpy(CLARABEL/OSQP) 优化器
│       ├── backtest/              # 向量化快筛 + A 股事件驱动微结构撮合（T+1/2026-07-06 ST 10%/涨跌停/停牌/冲击）+ Brinson/Barra 归因
│       └── shadow/                # Perold 实施缺口漂移 + 因子 IC 衰减 + AuthorityGateManifest
├── services/quant-api/            # FastAPI 量化工作台后端（:8100，17 个 /api/v1/* 端点 + 异步 Job & SSE）
├── services/quant-mcp/            # Python MCP SDK v2 Server（:8101/mcp Streamable HTTP + Stdio，13 个 quant_* 工具，<=8KiB Envelope）
│   └── skills/quant-lead/SKILL.md # quant-lead 永久量化主研 Bot Runbook 与已证伪因子墓碑库约定
├── apps/web/                      # Next.js 16 + React 19 + TypeScript 机构量化终端（:3100，6 大视图 + Agent Home Bot Dock）
├── scripts/
│   ├── seed_golden_dataset.py     # 显式灌入双市场 PIT Golden 基准数据集（A 股 60 标的 + 美股 12 标的，含退市/ST/重述链）
│   └── register_agent_home.mjs    # 零侵入幂等注册 quant-lead Bot 到本地 Agent Home Daemon（支持 --dry-run）
└── docs/                          # 行业调研、8 大机构量化 Gap、PRD-Map、Agent Home 集成设计、实施契约与实机截图
```

---

## 快速启动

### 1. 环境初始化与灌入 Golden PIT 验证数据集

```bash
make setup    # 创建 .venv（uv 安装 quant-core + dev 依赖）并安装 apps/web 依赖
make seed     # 显式构建 snap-golden-20261125-v1（CN_A + US_EQ）、执行 7 项 QA 哨兵并导出 Qlib Bin
```

### 2. 启动三大服务（三个终端）

```bash
make dev-api  # FastAPI 量化引擎服务 -> http://127.0.0.1:8100/api/v1/health
make dev-mcp  # Quant MCP Server (Streamable HTTP) -> http://127.0.0.1:8101/mcp
make dev-web  # QuantLab Pro V2 机构量化终端 -> http://127.0.0.1:3100/alpha
```

### 3. 接入本地 Agent Home（`ppeng-agent-core`）

1. 先查看注册计划（不改动任何状态）：

   ```bash
   node scripts/register_agent_home.mjs --dry-run
   ```

2. 确认本地 Agent Home Daemon 运行后（自动探测 `AGENT_HOME_DAEMON_URL` -> `dev-lab.ports.json` -> `:27070` -> `:37070`），一键幂等注册永久 `quant-lead` Bot：

   ```bash
   make register-bot
   ```

3. 按脚本输出将以下两行加入 `ppeng-agent-core/.env` 并重启 Daemon，即可在 Agent Home 与 Web 端 Bot Dock 中调用全部 13 个 `quant_*` MCP 工具：

   ```bash
   RAW_AGENT_MCP_EXPAND_HTTP=true
   RAW_AGENT_MCP_HTTP='[{"url":"http://127.0.0.1:8101/mcp","transport":"streamable-http","bearerEnv":"QUANT_MCP_BEARER_TOKEN"}]'
   ```

   可选：若同时设置了 `QUANT_MCP_BEARER_TOKEN`，请在启动 `make dev-mcp` 与 Agent Home Daemon 时保持一致；若 Agent Home 开启了 `RAW_AGENT_AUTH_TOKEN`，请在 `apps/web/.env.local` 配置服务端同名环境变量（浏览器永不接触 Token，由 Next.js 同源代理 `/api/agent-home/[...path]` 服务端注入）。

---

## 质量验收与测试

```bash
make test     # 运行全量 75 项 pytest（PIT 数据湖 9 + 量化内核 30 + FastAPI 18 + MCP 18）
make ci       # 全量 pytest + Next.js TypeScript 校验与生产构建
```

---

## 核心规划与设计文档

- [docs/architecture/IMPLEMENTATION-CONTRACT.md](docs/architecture/IMPLEMENTATION-CONTRACT.md) — 跨服务统一契约（数据湖 Schema、REST API、13 个 MCP 工具、Envelope 预算与 A2UI 协议）
- [docs/research/03-quant-gap-and-data-tooling.md](docs/research/03-quant-gap-and-data-tooling.md) — 8 大机构量化 Gap 补齐与 12 类数据源/DuckDB+Parquet+Qlib 湖仓方案
- [docs/plan/01-prd-map.md](docs/plan/01-prd-map.md) — Pro-Quant V2.0 PRD-Map（`Q1–Q7` 七大域 × 27 核心模块）
- [docs/plan/03-agent-home-integration.md](docs/plan/03-agent-home-integration.md) — Agent Home（`ppeng-agent-core`）无侵入双向集成方案
- [docs/plan/02-dev-plan.md](docs/plan/02-dev-plan.md) — Phase 0 → MVP → V1 → V2 路线图与定量门禁
- [docs/design/prototype-v1-quant.html](docs/design/prototype-v1-quant.html) — 离线高保真交互设计稿
