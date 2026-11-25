# QuantLab Pro · 机构级量化投研系统（Pro-Quant V3.0）

**QuantLab Pro V3.0** 是一套面向 **A 股（CN_A）与美股（US_EQ）双市场** 的机构级 AI-Native 多因子量化投研与形式化验证平台，涵盖 PIT（Point-in-Time）零幸存者偏差数据湖、截面预处理与 Newey-West HAC / Stationary Block Bootstrap 95% CI 因子检验、AST + 多截点前缀不变性零未来函数形式化证明、CPCV/DSR/PBO/BH-FDR/Hansen SPA/参数敏感性悬崖防过拟合门禁、Barra CNE6 风险模型与 `cvxpy` 组合优化 + AUM 策略容量前沿、A 股微结构高保真撮合回测与 Brinson/Barra 业绩归因、影子盘 Perold 实施缺口漂移监控、**85 项全终端专业量化术语交互 Tips 与 Bot 深度解读**，以及 **Bot 调度开发集群做闭环回测/形式化论证并持久化回写 `/research` 研究实验室（血缘树 + 维度基线 + 证伪墓碑库 + 确定性根因诊断与改进行动）**。

> **安全边界**：本系统严格遵循**只读研究与影子监控**原则（`broker_connected=False`，无任何下单/撤单/实盘券商接口），所有量化指标与形式化证明均由确定性数学/统计引擎计算并绑定不可变 `snapshot_id`、`as_of` 与 `proof_sha256`；任何外部数据源拉取失败均显式报错，**绝不静默伪造合成价格**；任何集群回传的 `VERIFIED` 结论必须通过服务端零信任独立复算与证明链核验。

---

## 仓库架构

```text
stock-research/
├── packages/quant-core/           # 确定性量化核心引擎 + PIT 数据湖 + 形式化证明（Python 3.12+ / DuckDB / Polars / cvxpy）
│   └── src/quant_core/
│       ├── schemas.py             # Q1–Q8 共享 Pydantic v2 契约与统一机构门禁常量 INSTITUTIONAL_GATES
│       ├── glossary/              # 85 项专业量化术语 SSOT（中英定义、公式、机构阈值、典型陷阱与 Bot 追问模板）
│       ├── lake/                  # DuckDB + Parquet PIT 数据湖、7 项 QA 哨兵、Qlib Bin 导出、V3 研究账本/血缘/墓碑/维度基线表
│       ├── providers/             # Tushare / BaoStock / SEC EDGAR / Massive 真实接入适配器（零静默兜底）
│       ├── factors/               # AST 未来函数守卫、截面预处理、RankIC/ICIR/NW-t/Bootstrap CI、5 维 Regime 切片稳定性矩阵
│       ├── validation/            # Purged K-Fold + CPCV(15 路径) + DSR + PBO + Prefix-Invariance + BH-FDR/SPA + 参数悬崖 + D01–D07 根因诊断
│       ├── risk/                  # Barra CNE6 10 风格 WLS + Ledoit-Wolf PSD 协方差 + cvxpy 优化器 + 6 档 AUM 策略容量衰减前沿
│       ├── backtest/              # 向量化快筛 + A 股事件驱动微结构撮合（T+1/ST 10%/涨跌停/停牌/冲击）+ 守恒不变量 + Brinson/Barra 归因
│       └── shadow/                # Perold 实施缺口漂移 + 因子 IC 衰减 + AuthorityGateManifest
├── services/quant-api/            # FastAPI 量化工作台后端（:8100，34 个 /api/v1/* 端点 + 研究编排器/沙箱/集群适配 + SSE）
├── services/quant-mcp/            # Python MCP SDK v2 Server（:8101/mcp Streamable HTTP + Stdio，21 个 quant_* 工具，<=8KiB Envelope）
│   └── skills/quant-lead/SKILL.md # quant-lead 永久量化主研 Bot Runbook（术语解读、数学推演铁律、集群调度与墓碑库查重）
├── apps/web/                      # Next.js 16 + React 19 + TypeScript 机构量化终端（:3100，7 大视图含 /research + 全术语 Tips + Bot Dock）
├── scripts/
│   ├── seed_golden_dataset.py     # 显式灌入双市场 PIT Golden 基准数据集（A 股 60 标的 + 美股 12 标的，含退市/ST/重述链）
│   └── register_agent_home.mjs    # 零侵入幂等注册 quant-lead Bot 到本地 Agent Home Daemon（支持 --dry-run）
└── docs/                          # 行业调研、V2/V3 差距分析、PRD-Map、Agent Home 集成设计、V2/V3 统一契约与实机截图
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
make dev-web  # QuantLab Pro V3 机构量化终端 -> http://127.0.0.1:3100/research
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

3. 按脚本输出将以下两行加入 `ppeng-agent-core/.env` 并重启 Daemon，即可在 Agent Home 与 Web 端 Bot Dock 中调用全部 21 个 `quant_*` MCP 工具：

   ```bash
   RAW_AGENT_MCP_EXPAND_HTTP=true
   RAW_AGENT_MCP_HTTP='[{"url":"http://127.0.0.1:8101/mcp","transport":"streamable-http","bearerEnv":"QUANT_MCP_BEARER_TOKEN"}]'
   ```

   可选：若同时设置了 `QUANT_MCP_BEARER_TOKEN`，请在启动 `make dev-mcp` 与 Agent Home Daemon 时保持一致；若 Agent Home 开启了 `RAW_AGENT_AUTH_TOKEN`，请在 `apps/web/.env.local` 配置服务端同名环境变量（浏览器永不接触 Token，由 Next.js 同源代理 `/api/agent-home/[...path]` 服务端注入）。

---

## 质量验收与测试

```bash
make test     # 运行全量 132 项 pytest（PIT 数据湖 + 量化内核 + V3 形式化验证 + 研究账本 + 集群编排 + FastAPI + 21 MCP 工具）
make ci       # 全量 pytest + Next.js TypeScript 校验与生产构建
```

---

## 核心规划与设计文档

- [docs/architecture/V3-INSTITUTIONAL-CONTRACT.md](docs/architecture/V3-INSTITUTIONAL-CONTRACT.md) — V3.0 顶级投行级升级统一契约（形式化证明 T1–T4、85 项术语库、5 张持久化研究表、集群零信任回写、21 MCP 工具与 `/research` 终端）
- [docs/architecture/IMPLEMENTATION-CONTRACT.md](docs/architecture/IMPLEMENTATION-CONTRACT.md) — V2.0 基础跨服务契约（数据湖 Schema、REST API、MCP Envelope 预算与 A2UI 协议）
- [docs/research/v3-gap-math-formal.md](docs/research/v3-gap-math-formal.md) — V3 数学严谨性与形式化验证差距分析
- [docs/research/v3-gap-cluster-knowledge.md](docs/research/v3-gap-cluster-knowledge.md) — V3 集群编排、持久化研究血缘与维度基线架构
- [docs/research/v3-gap-terminal-ux.md](docs/research/v3-gap-terminal-ux.md) — V3 投行级终端 UI、术语 Tips 与 `/research` 研究实验室交互设计
- [docs/plan/01-prd-map.md](docs/plan/01-prd-map.md) — Pro-Quant PRD-Map（`Q1–Q7` 七大域 × 27 核心模块）
- [docs/plan/03-agent-home-integration.md](docs/plan/03-agent-home-integration.md) — Agent Home（`ppeng-agent-core`）无侵入双向集成方案
