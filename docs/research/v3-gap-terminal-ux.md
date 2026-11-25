# QuantLab Pro V3.0 · 投行级量化终端 UX、全站术语 TermTip 与 `/research` 研究实验室架构方案

> 范围：`apps/web/src`（Next.js 16 + React 19 + Tailwind v4 Token，零图表第三方依赖）。
> 对标：Goldman Sachs Marquee / Bloomberg PORT / Two Sigma Venn / FactSet Alpha Testing / QuantConnect Research。
> 本文所有「现状」均来自对 `apps/web/src/app`、`src/components`、`src/lib` 真实源码的逐文件审计；所有「新增后端字段 / 接口」均标注 **[V3 契约]**，需与后端 V3 Worker 对齐后落地，前端在字段缺失时必须走诚实空态（与现有 `DataSlot` / `EmptyState` / `ApiNotice` 「不展示任何伪造数值」原则一致）。
> 阈值来源标注：**[代码]** = 现行前端常量；**[建议]** = V3 新增建议值，需在 `lib/thresholds.ts` 统一登记后生效。

---

## 0. 设计约束速查（1440px 基准）

| 项 | 现状来源 | 数值 |
| :--- | :--- | :--- |
| 顶栏 `.chrome` | `globals.css` | 高 44px |
| 主网格 `.app` | `grid-template-columns: 200px minmax(0,1fr) 360px` | SideNav 200 / Main 1fr / Bot Dock 360 |
| Main 可用宽 | 1440 − 200 − 360 − padding 28 − 滚动条 8 | **≈ 844px**（Dock 收起时 ≈ 1204px） |
| Main 可用高（900px 视口） | 900 − 44 − 40 | ≈ 816px |
| 字号体系 | body 12px；`.kl` 10.5；`.kv` 17 mono；`.tbl th` 9.5 mono | 维持，不新增字号档 |
| 色彩 | `--sig / --pass / --warn / --up / --dn / --mem` + `*-bg` + `*-line` | 双主题 Token 已完备，新增组件只用 Token |
| 圆角 | 全站 2px | 维持 |
| 现有浮层 | `.pop`（absolute, z 40）、`#qtip`（fixed, z 60, `pointer-events:none`） | `#qtip` **不可交互**，无法承载按钮 |
| 现有图表原语 | `ChartBox / LineChart / BarChart / Heatmap / Tornado / Waterfall / CpcvHistogram / WalkForward / ShadowDriftChart / Sparkline` | 全部 SVG；`LineChart` x 轴只支持日期字符串；`Heatmap` 只支持方阵；`Sparkline` 是区间位置计而非序列线 |

**z-index 统一刻度（V3 新增，写入 `globals.css` 注释）**：`chrome 20` → `.pop 40` → `drawer-veil 44` → `drawer 45` → `term-popover 55` → `#qtip 60`。TermTip 浮层必须高于抽屉（抽屉内也有 TermTip），低于图表 hover 提示。

---

## 1. V2.0 Web 终端审计 & 投行级 UX Gap

### 1.1 全局结构性问题（跨页面）

| # | 现状（源码事实） | Gap | V3 动作 |
| :---: | :--- | :--- | :--- |
| G1 | 专业指标仅以纯文本出现在 `Kpi label`、`<th>`、`Tag`、`Panel title`；`Kpi` 的 `title` 原生提示在 6 个视图中几乎未被使用 | 无就地解释，NW t / PBO / DSR / Brinson 交互项 / Perold IS / ENC / Ledoit-Wolf PSD 对非数学背景用户不可读 | 全站 `<TermTip />` + Glossary Drawer（第 2 章） |
| G2 | 阈值分散为局部常量：`AlphaView.THRESHOLDS` + `EXP_LIMIT`、`ModelsView.PBO_MAX / DSR_MIN / OOS_IS_MIN`、`meta.BARRA_HARD_LIMIT`、`BacktestView.closureTag(<1bp)` 与 `specShare ≥ 65`、`PortfolioView.psdOk(λmin ≥ −1e-10)` | 术语卡的「机构红线」若再写一份必然漂移 | 新建 `lib/thresholds.ts` 作为唯一来源，视图与术语库共同引用；后端 `gate_checks[].threshold` 仍优先（沿用 `AlphaView.threshold()` 逻辑） |
| G3 | `TooltipHost` 驱动的 `#qtip` 为 `pointer-events:none` + `white-space:nowrap`，靠 `[data-tip]` 委托 | 无法放按钮、无法多行排版公式 | 新建单例 `TermPopoverHost`（Portal + fixed + 交互），与 `#qtip` 并存；TermTip 触发点禁止嵌套在 `[data-tip]` 元素内 |
| G4 | Bot 联动只有页级按钮：`/alpha`「Bot 解读」、`/models`「CPCV 过拟合体检」、`/portfolio`「Barra 风险诊断」、`/backtest`「Bot 归因解读」、`/data`「体检」、`/shadow` 行内「诊断」 | 无法对**单个异常指标**一键发问 | `DockController` 新增 `explainTerm()`，每个 TermTip 与每条异常 `gs` 条带都可直达 Bot |
| G5 | 无研究生命周期视图；SideNav 的 Experiment Registry 是扁平列表；`ExperimentRecord.family_id` 在前端**未被消费**；后端已存在 `POST /factors/hypothesis`（`quant_submit_factor_hypothesis`）但**无 UI** | 无假设 → 推导 → 证明 → 回测 → 诊断 → 迭代的闭环；无墓碑库，重复试错 | 第 7 视图 `/research`（第 3 章）；`family_id` 作为研究树与 BH-FDR 家族的天然分组键 |
| G6 | 后端 `cpcv / backtest / hypothesis` 均支持 `async_mode` 并提供 `GET /jobs/{job_id}/events`（SSE）与 `AsyncJobRecord.progress_pct`，前端**完全未使用**，仍以 180s / 240s 同步超时阻塞 | 长任务无进度、无日志、无法后台运行 | 新增 `lib/use-event-stream.ts`，`/research` 流水线与 `/backtest` event 引擎改走异步任务 |
| G7 | `REGIME_BANDS` 是 `charts/core.tsx` 中硬编码的 3 段日期（微盘踩踏 / 924 急升 / 关税冲击），仅作为背景色带 | Regime 只有视觉，没有定量切片 | 后端输出 `dimension_slices`；前端 `MatrixHeatmap` 呈现 Regime × Vol × Cap 稳定性矩阵 |
| G8 | 全站只有点估计（RankIC、Sharpe、IR），无任何置信区间 | 无法区分「显著」与「运气」 | Bootstrap 95% CI 显示在 KPI 副行与柱状图误差须 |
| G9 | 形式化校验零散：Brinson/Barra 闭合误差 Tag、PSD Tag、AST SAFE Tag 各自为政 | 没有统一的「证明链」心智模型 | 统一 `ProofBadge / InvariantStrip` 组件，在 `/portfolio`、`/backtest`、`/research` 复用 |
| G10 | `meta.viewFromPath()` 未命中时回落为 `'alpha'` | 新增 `/research` 若不登记，`<quant_canvas_context>.active_view` 会被错误上报为 `/alpha` | `ViewKey` 与 `VIEWS` 必须同步加入 `research`（第 5 章） |
| G11 | SideNav 分区计数硬编码 `<span>06</span>` | 新增视图后计数错误 | 改为 `String(VIEWS.length).padStart(2,'0')` |
| G12 | `QUICK_CMDS` 固定 3 条（IC / CPCV / Barra），`.bcmds` 两列网格 | 无术语与研究派发入口 | 新增 `/查术语`、`/派发集群研究`，网格改三列保持两行高度不变 |

### 1.2 逐页审计

#### `/alpha` · `AlphaView.tsx`（692 行）

- **现有**：因子 Tab、表达式编辑器（自研高亮）、5 项预处理 Chip、Z-Score/Rank、残余暴露 Meter（±0.3σ）、8 格 KPI、门禁条带 `gstrip`、4 面板（10 分层净值、滚动 60D IC、IC 衰减/行业 IC、相关性矩阵）。
- **Gap**：
  1. 8 个 KPI 标题（RankIC 均值、ICIR 年化、NW t-stat、多空年化、Top 组超额、单边日换手、IC 半衰期、库内 MaxCorr）与 5 个预处理 Chip、`AST SAFE`、`PIT asof=ann_date` 均无解释。
  2. 只有全样本点估计，**缺 Bootstrap 95% CI**；IC 衰减柱无误差须。
  3. **缺 Regime / 波动率 / 市值维度切片稳定性矩阵**；行业 IC 只是一维柱图，看不出「IC 是否只来自某一 Regime 或小盘」。
  4. 门禁失败条带（`warnings`）只能看，不能「一键查术语」或「派发集群深度研判」。

#### `/models` · `ModelsView.tsx`（345 行）

- **现有**：5 格 KPI（IS Sharpe / OOS 中位夏普 / PBO / DSR / Purge 冲突）、Experiment Registry 表、CPCV 路径直方图、PBO/DSR 门禁条、Walk-Forward 时间线。
- **Gap**：
  1. KPI 与表头（IS SR、OOS/IS、PBO、DSR、M）无解释；`M=trial_count_m` 对 DSR 的作用不可见。
  2. **缺 BH-FDR 多重检验校正**：同一 `family_id` 下多个实验并列展示，却不给 q-value，存在「挑最好的那个」偏差。
  3. **缺参数敏感性悬崖图（Cliff Test）**：无法判断 OOS Sharpe 是否只在某个参数点附近成立。

#### `/portfolio` · `PortfolioView.tsx`（460 行）

- **现有**：目标函数 Seg + 5 个约束滑杆、cvxpy 代码片段、求解状态、优化前风险分解（PSD Tag + λmin）、5 格 KPI（净超额 / TE / IR / 换手 / ENC）、风控门禁条带、Barra 龙卷风、行业偏离柱、Top 持仓表。
- **Gap**：
  1. ENC、λ、TE、`RELAXED_OPTIMAL`、`PSD OK`、λmin、`CAP`、`T+1_LOCK` 无解释。
  2. **缺 AUM 策略容量与冲击衰减前沿（Capacity Frontier）**：只有单点 `est_impact_bps`，看不到规模放大后净超额如何被冲击吞噬。
  3. 守恒校验只有 PSD 一项，缺 Σw=1、行业主动权重和=0、约束违背量、TE 复算一致性等形式化徽章。

#### `/backtest` · `BacktestView.tsx`（468 行）

- **现有**：双引擎 Seg、10 格 KPI、净值+水下回撤、摩擦瀑布、撮合失败审计表、Brinson-Fachler + Barra 闭合归因与风格拆解。
- **Gap**：
  1. Brinson 交互项、闭合误差、Calmar、GROSS/NET 口径、POV、印花税等无解释。
  2. **缺 Capacity Frontier**（回测口径的规模回放）。
  3. 守恒律只在两张表底部以 `closureTag` 出现，缺现金守恒、摩擦守恒、T+1 不变量等统一证明徽章。

#### `/data` · `DataView.tsx`（542 行）

- **现有**：5 格 KPI（overall_status / 覆盖率 / 新鲜度 / 质量哨兵 / 数据集）、数据集状态矩阵、7 项 PIT 质量哨兵（后端 `QA-01-OHLCV-GEOM` … `QA-07-PRICE-LIMIT-RULES`）、Quant MCP 工具状态表、快照清单。
- **Gap**：7 项哨兵、PIT 对账列、manifest sha256、fact_fingerprint、MCP 副作用 `READ / ASK / BG_RUN` 均无解释。

#### `/shadow` · `ShadowView.tsx`（346 行）

- **现有**：5 格 KPI（IS / 成交率 / 跳空 / 流动性 / 涨跌停排队）、理论 vs 影子漂移、Perold IS 拆解柱、Authority Gate 清单、在产因子衰减表（含「诊断」按钮 → `dock.diagnoseFactor`）。
- **Gap**：IS、Perold、历史分位、拥挤度、`KILL-SWITCH`、`SIGNAL-ONLY`、Authority Gate 无解释；`DEGRADING` 因子只能单次对话诊断，不能派发成可追踪的子研究。

#### `AgentHomeDock.tsx`（892 行）/ `TopBar.tsx` / `SideNav.tsx`

- **Dock**：上下文画布 `buildCanvasContext()` 只覆盖 6 视图分支，800 字符预算；A2UI 本地 action 仅 `rerun_optimization / open_view / open_factor`；`open_view` 只接受 `VIEWS` 中的路径。
- **TopBar**：右侧依次为 API / Agent Home 健康芯片、PRD、主题 Seg、Dock 折叠按钮；无全局搜索与快捷键；版本标签仍为 `V2.0 PRO-QUANT`。
- **SideNav**：6 个视图 + 扁平实验列表；无研究树入口。

### 1.3 可操作闭环原则（V3 设计准则）

1. **每个异常值两步可达解释**：Hover 180ms 出卡 → 卡内一键发给 Quant-Lead。
2. **每个 FAIL/WARN 一步可派发**：所有 `gs.fail / gs.warn` 条带、证明链 FAIL 行、诊断卡都带「派发集群深度复研」次级按钮，统一进入 `/research` 新建抽屉（预填父研究与上下文）。
3. **零伪造**：后端未返回 V3 字段时显示 `EmptyState title="V3 FIELD PENDING" detail="后端未返回 dimension_slices"`，不显示占位数字。
4. **零 Emoji、零渐变装饰**：唯一允许的渐变是现有 10 分层图例色条；状态只用 Token 色 + 文字标签。

---

## 2. 全站专业术语 TermTip + Bot 联动体系

### 2.1 组件分层

```
AppShell
 ├─ TooltipHost            (现有 #qtip，图表 hover，保持不变)
 ├─ TermPopoverHost        (新增 · 单例 Portal · 渲染唯一一张 TermCard)
 └─ GlossaryDrawer         (新增 · 全站术语速查库 · ⌘K)

任意视图
 └─ <TermTip termId="pbo" currentValue={c.pbo} threshold={PBO_MAX} context={{experiment_id}}>CPCV PBO</TermTip>
      └─ 轻量触发器：只负责 hover/focus/click 意图 → 写入 glossary store
```

- **单例原则**：每页 TermTip 触发点可能 40+ 个，触发器只是一个 `<button className="tt-trg">`，不持有浮层 DOM；全局同一时刻最多一张卡（打开新卡即关闭旧卡）。
- **store**：`lib/glossary/store.ts` 使用 `useSyncExternalStore`，状态 `{ active: {termId, anchorRect, value, threshold, context, pinned} | null, pageValues: Map<TermId, ValueSnapshot> }`。TermTip 挂载时把 `currentValue` 写入 `pageValues`，Glossary Drawer 由此展示「本页当前值」。
- **术语数据懒加载**：触发器只依赖 `TermId` 字符串类型；`TermCard` 首次打开时 `import('@/lib/glossary/terms')`，避免 85 条术语文本进入首屏 bundle。

### 2.2 触发点视觉规范

| 挂载位置 | 触发形态 | CSS |
| :--- | :--- | :--- |
| KPI 标题 `.kl`、Panel 标题 `.pt`、滑杆标签 `.slh span`、Chip 文本 | 文本虚线下划线 | `text-decoration: underline dotted var(--tx3); text-underline-offset: 3px; text-decoration-thickness: 1px; cursor: help` |
| 表头 `.tbl th`（9.5px mono 大写） | 同上，offset 2px | hover 时下划线色 `var(--sig)` |
| 徽章 `.tag`、`.gs b`、证明链行 | 右侧 9px 微图标 `info`（圆 + i，`Icon` 新增） | `color: var(--tx3)`；hover `var(--sig)`；占位 11px 不撑高行 |
| 已 PASS 的数值本体 | 不加触发（避免全屏下划线噪声） | — |

- 触发器为 `<button type="button">`，`font: inherit; color: inherit`，不改变原布局；`.kl` 的 `overflow:hidden; text-overflow:ellipsis` 保持生效（浮层走 Portal，不会被裁剪）。
- 挂载 TermTip 时移除该元素的原生 `title`，避免双提示。

### 2.3 交互规范

| 行为 | 规格 |
| :--- | :--- |
| Hover 打开 | 进入触发器 **180ms** 后打开（防误触）；离开触发器与卡片两者后 **140ms** 关闭（允许鼠标斜穿进卡片） |
| Click | 立即打开并 **Pin**（卡片右上角显示 `PINNED` 小标签 + 关闭 ×）；Pin 状态下 hover 其他触发器不切换 |
| 键盘 | 触发器可 Tab 聚焦；`:focus-visible` 立即打开；`Enter / Space` = Pin；卡内按钮可 Tab；`Esc` 关闭并把焦点还给触发器 |
| 关闭 | `Esc`、点击卡片与触发器之外区域、路由切换、触发器卸载 |
| 无障碍 | 触发器 `aria-describedby` 指向卡片 id；卡片 `role="dialog" aria-modal="false" aria-labelledby` 标题 |
| 触屏 | 首次 tap = Pin 打开；再次 tap = 关闭 |

**定位算法（`lib/use-floating.ts`，零依赖）**：

1. 读取触发器 `getBoundingClientRect()`，卡片宽 `W = 360`，实际高 `H = min(contentHeight, 480, innerHeight − 60)`。
2. 首选 `bottom-start`：`x = rect.left`，`y = rect.bottom + 6`。
3. 下方空间不足且上方足够 → 翻转为 `top-start`：`y = rect.top − 6 − H`；两侧都不足 → `y` 夹在 `[52, innerHeight − 8 − H]`（52 = 顶栏 44 + 8）。
4. 水平夹取：触发器位于 Main 区时右边界为 `innerWidth − (botOpen ? 360 : 0) − 8`，**不遮挡 Bot Dock**，保证点击「让 Quant-Lead 解读」后回答立即可见；触发器位于 Dock 内时左边界改为 Dock 左缘之左。
5. 监听 `resize` 与捕获阶段 `scroll`（`.main` 是滚动容器），用 `requestAnimationFrame` 节流重定位；触发器滚出视口时自动关闭（Pin 状态除外，Pin 卡跟随锚点并在锚点不可见时贴边）。

### 2.4 机构术语卡（TermCard，360px）

```
┌──────────────────────────────────────────────────────┐
│ CPCV PBO 过拟合概率 · Probability of Backtest Overfit  [Q3] [PINNED ×] │  .tt-h   13px/620 + 9.5 mono 标签
│ 机构红线  PBO ≤ 0.25 · 来源 gate_details             │  .tt-red  10px mono, 红线值 var(--up)
├──────────────────────────────────────────────────────┤
│ 白话  把所有参数组合在样本内挑出的“冠军”，拿到样本外   │  .tt-plain 11.5px/1.6 var(--tx)
│       排名跌到中位数以下的概率。越高越像是在拟合噪声。 │
│ ┌ 定义 ────────────────────────────────────────────┐ │
│ │ λ_c = logit( ω̄_c ) ,  ω̄_c = rank_OOS(n*) / (N+1)   │ │  .tt-fx  mono 11px, bg var(--bg), border var(--line)
│ │ PBO = ∫_{−∞}^{0} f(λ) dλ   (C(N,k) 组合上的经验分布) │ │
│ └──────────────────────────────────────────────────┘ │
│ 当前画布  0.31   [FAIL]   超阈 +0.06（+24%）         │  .tt-now
│ ▕━━━━━━━━━━━━━━━━━┃━━━━●━━━━━━▏ 0 ··· 0.25 ··· 1    │  1 行位置计（复用 .mtb 视觉）
│ 常见陷阱                                              │
│ · 只报告 PBO 不报告 M（试验次数）：M 越大 PBO 越容易虚低 │  .tt-pit 10.5px var(--tx2)
│ · purge/embargo 过短会让 PBO 系统性偏乐观              │
│ 相关  DSR · CPCV · Purge · BH-FDR                     │  相关术语 chip，点击在卡内切换
├──────────────────────────────────────────────────────┤
│ [ 让 Quant-Lead 结合当前数据解读 ]  [ 术语速查库  ⌘K ] │  .btn.sm.pri + .btn.sm
└──────────────────────────────────────────────────────┘
```

- 中段（白话→相关）独立滚动，头尾固定。
- **当前画布对照**（仅当传入 `currentValue`）：`evaluate(term, value, thresholdOverride)` → `{verdict: PASS|WARN|FAIL|N/A, gapAbs, gapPct, text}`；`WARN` 带宽由术语定义的 `warn` 字段决定（例如 DSR 0.90–0.95 为 WARN）。阈值优先级：`threshold` prop（视图传入、已含后端 `gate_checks` 覆盖）> `lib/thresholds.ts` > 术语默认值。
- **公式渲染**：`FormulaBlock` 为约 60 行的零依赖微渲染器，支持 `_{}` → `<sub>`、`^{}` → `<sup>`、`\sum \sqrt \Sigma \lambda \rho \le \ge \cdot` → Unicode，逐行编号；不引入 KaTeX/MathJax，不访问任何 CDN。
- 主题：只使用 `--bg / --bg1 / --line / --tx* / --pass / --warn / --up / --sig` Token；阴影复用 `--shadow`。

### 2.5 Bot 联动协议

**按钮 A「让 Quant-Lead 结合当前数据解读」** → `dock.explainTerm(payload)`（`DockController` 新增）：

```ts
interface TermQuery {
  termId: TermId;
  value?: number | string | null;
  threshold?: string;          // 已格式化，如 "≤ 0.25"
  verdict?: 'PASS' | 'WARN' | 'FAIL' | 'N/A';
  gap?: string;                // 如 "+0.06 (+24%)"
  view: string;                // VIEWS path
  context?: Record<string, string | number | null>; // experiment_id / factor_id / study_id 等
}
```

- Dock 切到 `quant-lead`，在线时发送（显示文本与真实 prompt 分离，沿用 `sendOnline(b, text, { display })`）：
  - 显示：`/查术语 PBO · 当前 0.31 · FAIL`
  - 正文：`<term_query>{...JSON...}</term_query>` + 固定指令「1) 含义 2) 当前值为何 FAIL 3) 最可能根因（引用 quant_canvas_context 数值）4) 下一步验证动作；如需复研请给出 /research 子研究草案」。
  - 与现有 KV-Cache 约束一致：只进入最新 user 消息，不触碰 system（`STABLE_SYSTEM_VERSION v19`）。
- 画布注入：`buildCanvasContext` 新增可选 `term_focus: {id, value, verdict}`，在 800 字符裁剪时优先级高于 `tearsheet_metrics` 尾部键。
- **Daemon 离线**：不伪造 LLM 回答，改为本地确定性卡片 `termCard()`（`local-diagnostics.ts` 新增，复用 `buildSurfaceEnvelopes` 的 `Card / Text / Button`），内容即术语库文本 + 当前值判定，标记 `LOCAL · DETERMINISTIC`。

**按钮 B「打开全站术语速查库」** → `openGlossary(termId)`，抽屉打开并定位到该词条。

### 2.6 Glossary Drawer（全站术语速查库）

- **入口**：TopBar 新增 `ib` 图标按钮（`search` 图标 + `⌘K` 键帽文字，宽约 56px，位于 PRD 按钮左侧）；全局快捷键 `⌘K / Ctrl+K`（`isComposing` 时忽略；焦点在 Dock 输入框时仍生效）；TermCard 按钮 B；Dock `/查术语`（空参数时）。
- **位置**：从 Main 左缘（x = 200）滑出，宽 **560px**，高 = 视口 − 44，覆盖 Main、**不覆盖 Bot Dock**；背后 `drawer-veil` 仅覆盖 Main 区（`rgba` 由 `color-mix(var(--bg) 55%)` 生成）。
- **布局**：

```
┌ GLOSSARY · 85 TERMS ─────────────────────── [Esc] ┐
│ [ 搜索 RankIC / 过拟合 / Brinson …            ⌘K ] │  input 28px, autoFocus
│ [ALL][本页 12][Q1][Q2][Q3][Q4][Q5][Q6][MATH]       │  .seg
├──────────────────┬────────────────────────────────┤
│ 结果列表 196px   │ 详情 340px（TermCard 同构，     │
│ Q3 · CPCV PBO    │ 无 PINNED，带「本页当前值」）    │
│ Q3 · DSR   ●FAIL │                                │
│ ...              │                                │
└──────────────────┴────────────────────────────────┘
```

- **搜索**：`lib/glossary/search.ts`，对 `zh / en / id / aliases` 做大小写不敏感子串 + 首字母缩写评分（`nw` 命中 `Newey–West`），本页挂载词条加权；`↑ ↓` 选择、`Enter` 打开详情、`⌘Enter` 直接发给 Quant-Lead。
- 列表项右侧显示本页当前判定点（`pageValues` 中有值时），可快速扫出「本页所有 FAIL 术语」。

### 2.7 术语数据结构

```ts
// lib/glossary/types.ts
export type TermDomain = 'Q1' | 'Q2' | 'Q3' | 'Q4' | 'Q5' | 'Q6' | 'MATH';
export interface TermThreshold {
  op: '>=' | '<=' | '<' | '>' | 'abs<=' | 'eq' | 'range';
  value: number | [number, number];
  warn?: number | [number, number]; // WARN 带
  unit?: '%' | 'bp' | 'σ' | 'x' | 'D' | '';
  label: string;                    // 展示用，如 "PBO ≤ 0.25"
  source: 'code' | 'proposal';      // 对应本文 [代码] / [建议]
}
export interface GlossaryTerm {
  id: TermId;
  zh: string;
  en: string;
  domain: TermDomain;
  prd?: string;            // 如 'Q3.2'
  aliases: string[];
  plain: string;           // 1–2 句白话
  formula: string[];       // FormulaBlock 行
  symbols?: string[];      // 符号说明
  threshold?: TermThreshold;
  pitfalls: string[];      // 1–2 条
  related: TermId[];
}
```

### 2.8 术语清单（85 项）与挂载点映射

> 挂载点缩写：A=/alpha M=/models P=/portfolio B=/backtest D=/data S=/shadow R=/research K=AgentHomeDock T=TopBar。

#### Q1 · 数据底座（11）

| termId | 中文 / English | 白话 | 机构红线 | 挂载点 |
| :--- | :--- | :--- | :--- | :--- |
| `point_in_time` | PIT 时点数据 / Point-in-Time | 只用当时已公开的数据，财报按公告日而非报告期入表 | ann_date ≤ t 违规 0 条 [建议] | D 对账列 · A 编辑器 `PIT asof` · T PIT 锁 |
| `survivorship_bias` | 幸存者偏差 / Survivorship Bias（QA-04） | 只用今天还活着的股票回测会系统性高估收益 | 退市股纳入率 100% [建议] | D 哨兵 · R 证明链 |
| `look_ahead_bias` | 前视偏差 / Look-ahead Bias | 计算时偷看了未来才知道的信息 | 违规 0 [建议] | A AST 标签 · R |
| `corp_action_adj` | 复权与公司行动 / Corporate Action Adj.（QA-02） | 分红送转会让价格跳空，必须用复权因子还原 | 复权跳变 0 条 [建议] | D |
| `ohlcv_geometry` | OHLCV 几何一致性（QA-01） | 最低价不能高于开收盘，最高价不能低于开收盘 | 违规 0 条 [建议] | D |
| `historical_constituents` | 历史成分与权重 / Historical Constituents（QA-05） | 指数成分要用当日真实名单，不能用今天的名单 | 权重和偏差 < 0.1% [建议] | D · T UNIVERSE |
| `sw_industry_pit` | 申万行业 PIT 归属（QA-06） | 行业分类会调整，须按当日归属做中性化 | 重叠/缺失 0 [建议] | D · A 行业中性 Chip |
| `price_limit_rules` | 涨跌停规则 / Price Limit Rules（QA-07） | 主板 ±10%、创业板/科创板 ±20%、ST ±5%、北交所 ±30% | 规则错配 0 [建议] | D · B 拒单原因 |
| `coverage` | 覆盖率 / Coverage | 有有效因子值的股票占股票池比例 | ≥ 80% [代码 `min_coverage`] | A 编辑器状态栏 · D KPI |
| `freshness` | 数据新鲜度 / Freshness | 最新数据距今多久 | ≤ 1 交易日 [建议] | D KPI |
| `fact_fingerprint` | 事实指纹 / Fact Fingerprint & Snapshot | 一次计算所用数据的哈希，用于完全复现 | 复算哈希一致 [建议] | D · S · R Artifact |

#### Q2 · Alpha 因子（18）

| termId | 中文 / English | 白话 | 机构红线 | 挂载点 |
| :--- | :--- | :--- | :--- | :--- |
| `rank_ic` | RankIC 均值 / Rank Information Coefficient | 因子排名与下期收益排名的相关度，日均值 | ≥ 0.03 [代码] | A KPI · K |
| `icir` | ICIR 年化 / IC Information Ratio | IC 的稳定性：均值 / 标准差 × √252 | ≥ 1.5 [代码] | A KPI |
| `nw_t_stat` | NW t 统计量 / Newey–West t-stat | 扣除自相关后 IC 均值显著异于 0 的程度 | ≥ 3.0 [代码] | A KPI |
| `ic_win_rate` | IC 胜率 / IC Hit Rate | IC 为正的交易日占比 | ≥ 55% [建议] | A KPI 副行 |
| `ic_decay` | IC 滞后衰减 / IC Decay by Lag | 信号延迟 k 日后还剩多少预测力 | Lag5 保留 ≥ 50% [建议] | A 面板 |
| `half_life` | IC 半衰期 / Half-life | IC 衰减到一半所需天数，决定调仓频率 | ≥ 调仓周期 [建议] | A KPI |
| `monotonicity` | 分层单调性 / Decile Monotonicity ρ | Q1→Q10 收益是否单调递增 | Spearman ρ ≥ 0.8 [建议] | A 面板 Tag |
| `long_short` | 多空收益 / Long-Short (Q10−Q1) | 做多最好一组、做空最差一组的年化收益 | > 0 且 CI 不跨零 [建议] | A KPI |
| `top_excess` | Top 组超额 / Top-Decile Excess | A 股难以做空，真正可拿到的是多头超额 | > 0 [建议] | A KPI |
| `turnover_oneway` | 单边日换手 / One-way Turnover | 每天要换掉多少持仓，直接决定成本 | ≤ 20% [代码] | A KPI |
| `max_corr_library` | 库内最大相关 / Max Corr in Library | 和已有因子最像的那个有多像 | < 0.70 [代码] | A KPI · 相关矩阵 |
| `mad_winsorize` | MAD 去极值 / MAD Winsorization | 用中位数绝对偏差截断离群值 | k = 3.5 [代码 `mad_k`] | A Chip |
| `industry_neutral` | 行业中性化 / Industry Neutralization | 剔除行业轮动带来的伪 Alpha | 残余行业暴露 ≤ 0.3σ [代码] | A Chip |
| `size_neutral` | 市值中性化 / Size Neutralization | 剔除小盘溢价 | Size 残余 ≤ 0.3σ [代码] | A Chip · Meter |
| `barra_orthogonal` | Barra 风格正交 / Style Orthogonalization | 把已知风格因子成分剥离，只留增量信息 | 残余风格 ≤ 0.3σ [代码] | A Chip |
| `zscore_rank` | 标准化 / Z-Score vs Rank | 截面标准化方式；Rank 更抗极值 | — | A Seg |
| `residual_exposure` | 残余风格暴露 / Residual Exposure | 预处理后因子仍与风格因子的相关程度 | \|z\| ≤ 0.30σ [代码 `EXP_LIMIT`] | A Meter 标题 |
| `ast_safe` | AST 安全 / AST Safety Check | 静态扫描表达式，拦截未来函数与危险调用 | SAFE [代码] | A 状态栏 Tag |

#### Q3 · 模型与过拟合防御（10）

| termId | 中文 / English | 白话 | 机构红线 | 挂载点 |
| :--- | :--- | :--- | :--- | :--- |
| `sharpe_is_oos` | 样本内/外夏普 / IS vs OOS Sharpe | 训练期 vs 未见数据上的风险调整收益 | — | M KPI · 表头 |
| `oos_is_ratio` | OOS/IS 比 / OOS-to-IS Ratio | 样本外保留了多少样本内表现 | ≥ 0.5 [代码] | M KPI · 表头 |
| `cpcv` | 组合清除交叉验证 / CPCV | 把时间切 N 组、取 k 组做测试，生成多条 OOS 路径 | 路径数 ≥ 15 [建议] | M 面板 · R |
| `purge` | 清除 / Purging | 删掉训练集中与测试集标签窗口重叠的样本 | 冲突 = 0 [代码] | M KPI |
| `embargo` | 禁运期 / Embargo | 测试集之后再空出一段，防止序列相关泄漏 | ≥ 标签窗口 [建议] | M KPI 副行 · WF 图例 |
| `pbo` | 过拟合概率 / PBO | 样本内冠军在样本外跌到中位数以下的概率 | ≤ 0.25 [代码] | M KPI · 门禁 · R |
| `dsr` | 紧缩夏普 / Deflated Sharpe Ratio | 扣除多重试验与非正态后，夏普仍为真的概率 | ≥ 0.95 [代码]，0.90–0.95 WARN [建议] | M KPI · 门禁 · R |
| `trial_count_m` | 试验次数 / Trial Count M | 为得到当前结果一共试了多少种配置 | 必须申报 [建议] | M KPI 副行 · 表头 |
| `walk_forward` | 滚动样本外 / Walk-Forward | 按时间顺序滚动训练与测试 | OOS IR 均值 ≥ 0 [代码 Tag 逻辑] | M 面板 |
| `negative_paths` | 负夏普路径占比 / Negative Path Ratio | CPCV 路径中亏钱路径的比例 | ≤ 20% [建议] | M 门禁 |

#### Q4 · 组合与风险（13）

| termId | 中文 / English | 白话 | 机构红线 | 挂载点 |
| :--- | :--- | :--- | :--- | :--- |
| `barra_cne6` | Barra CNE6 风险模型 | 用风格 + 行业 + 特质三部分解释股票风险 | — | P 面板标题 |
| `style_exposure` | 风格暴露 / Active Style Exposure (σ) | 组合相对基准在某风格上偏了几个标准差 | \|z\| ≤ 0.5σ 硬线 [代码] | P 龙卷风 · 门禁 |
| `industry_active_dev` | 行业主动偏离 / Industry Active Weight | 某行业权重比基准多/少多少 | ±3% [代码默认] | P 滑杆 · 柱图 |
| `tracking_error` | 跟踪误差 / Tracking Error | 相对基准收益波动的年化标准差 | ≤ 目标 TE（默认 5%）[代码] | P KPI · 滑杆 |
| `information_ratio` | 信息比率 / IR | 超额收益 / 跟踪误差 | ≥ 0.5 [建议] | P KPI · B KPI |
| `turnover_lambda` | 换手惩罚 λ / L1 Turnover Penalty | 目标函数里对调仓量的惩罚强度 | — | P 滑杆 |
| `enc` | 有效持股数 / Effective Number of Constituents | 1 / Σw²，衡量集中度 | ≥ 30 [建议] | P KPI |
| `ledoit_wolf` | Ledoit-Wolf 收缩 / Shrinkage Covariance | 把样本协方差向结构化目标收缩，降低估计噪声 | 收缩强度 δ ∈ [0,1] | P 风险分解 · R |
| `psd` | 半正定 / PSD & λmin | 协方差矩阵最小特征值不能为负，否则优化器可“造出”负风险 | λmin ≥ −1e-10 [代码] | P Tag · R 证明链 |
| `risk_decomposition` | 风险分解 / Risk Decomposition | 预测 TE 中风格、行业、特质各占多少 | 特质占比 ≥ 50% [建议] | P 面板 |
| `opt_objective` | 优化目标 / Objective | Max Sharpe、均值方差、最小方差、风险平价的取舍 | — | P Seg |
| `solve_status` | 求解状态 / Solve Status | `RELAXED_OPTIMAL` 表示放松了约束才有解 | OPTIMAL [代码 Tag] | P 求解行 |
| `capacity_frontier` | 策略容量前沿 / Capacity Frontier | 管理规模放大后净超额被冲击成本吞噬的曲线 | 容量点：净超额 = 50% 毛超额 [建议] | P · B 新面板 |

#### Q5 · 回测与归因（13）

| termId | 中文 / English | 白话 | 机构红线 | 挂载点 |
| :--- | :--- | :--- | :--- | :--- |
| `t_plus_1` | T+1 交收约束 | 当天买入当天不能卖 | 违规 0 [建议] | B 拒单 · P 持仓 Tag |
| `pov` | 成交量参与率 / POV | 单票成交不超过当日成交量的比例 | ≤ max_pov [代码配置] | B 摩擦 · P 容量 |
| `sqrt_impact` | 平方根冲击 / Square-root Impact | 冲击成本 ∝ σ·√(下单量 / 日成交量) | — | B 瀑布 · P 冲击列 |
| `stamp_duty` | 印花税等摩擦 / Stamp Duty & Fees | 卖出 0.05% 印花税 + 佣金 + 过户费 | — | B 瀑布 |
| `gross_vs_net` | 毛/净口径 / GROSS vs NET | 向量化快筛未扣真实摩擦，事件驱动才是净值 | 晋级必须 NET [建议] | B KPI Tag · 引擎 Seg |
| `brinson_allocation` | 配置效应 / Allocation Effect | 超配了跑赢基准的行业带来的收益 | — | B 归因表 |
| `brinson_selection` | 选股效应 / Selection Effect | 行业内选出更好的股票带来的收益 | — | B 归因表 |
| `brinson_interaction` | 交互效应 / Interaction Effect | 既超配又选得好产生的交叉项，常被误归入选股 | — | B 归因表 |
| `closure_error` | 闭合误差 / Closure Error | 各分项之和与总超额的差 | \|ε\| < 1bp [代码] | B 表底 · R 证明链 |
| `barra_attribution` | 真假 Alpha 甄别 / Barra Attribution | 把超额拆成纯 Alpha、风格 Beta、行业、成本 | 特质 Alpha ≥ 65% [代码] | B 面板 Tag |
| `max_drawdown` | 最大回撤 / Max Drawdown & Underwater | 从峰值到谷底的最大跌幅 | — | B KPI · 水下图 |
| `calmar` | Calmar 比率 | 年化收益 / \|最大回撤\| | ≥ 1.0 [建议] | B KPI |
| `excess_drawdown` | 超额回撤 / Excess Drawdown | 相对基准的最大回撤 | — | B KPI |

#### Q6 · 影子实盘与衰减（11）

| termId | 中文 / English | 白话 | 机构红线 | 挂载点 |
| :--- | :--- | :--- | :--- | :--- |
| `implementation_shortfall` | 执行落差 / Implementation Shortfall | 理论组合与实际可成交组合的收益差 | 窗口 IS ≤ 50bp [建议] | S KPI |
| `perold_is` | Perold IS 拆解 | 把 IS 拆成跳空、流动性冲击、涨跌停排队等 | 分项和 = 总 IS [建议] | S 面板 |
| `open_gap` | 开盘跳空损耗 / Open Gap Cost | 信号收盘生成、次日开盘成交的价差 | — | S KPI |
| `liquidity_impact` | 流动性冲击 / Liquidity Impact | 下单量相对成交量过大导致的滑点 | — | S KPI |
| `limit_queue` | 涨跌停排队 / Limit-up Queue | 涨停买不进、跌停卖不出的机会损失 | — | S KPI |
| `fill_rate` | 成交率 / Fill Rate | 影子成交名义 / 目标名义 | ≥ 95% [建议] | S KPI |
| `crowding_index` | 拥挤度 / Crowding Index | 同类资金扎堆同一因子的程度 | ≤ 0.8 [建议] | S 表头 |
| `hist_percentile` | 历史分位 / Historical Percentile | 当前 60D IC 在自身历史中的位置 | ≥ 20% 分位 [建议] | S 表头 |
| `kill_switch` | 熔断 / Kill-Switch & DEGRADING | 衰减到阈值以下自动停用 | — | S 状态列 |
| `authority_gate` | 权限闸门 / Authority Gate | 研究只导出信号文件，物理隔离券商下单 | broker_connected = false [代码类型] | S 面板 · `SIGNAL-ONLY` Tag |
| `regime_shift` | 状态切换 / Regime Shift | 市场风格突变导致因子失效 | — | S · R 维度热力图 |

#### MATH · 统计与形式化验证（9）

| termId | 中文 / English | 白话 | 机构红线 | 挂载点 |
| :--- | :--- | :--- | :--- | :--- |
| `newey_west_hac` | Newey–West HAC 方差 | 对自相关与异方差稳健的标准误 | lag = ⌊4(T/100)^{2/9}⌋ [建议] | A NW t 卡相关 · R |
| `bootstrap_ci` | Bootstrap 95% 置信区间 | 有放回重抽样 B 次得到统计量分布的 2.5%–97.5% 分位 | 下界 > 0（B ≥ 2000，块长 ≥ 半衰期）[建议] | A KPI 副行 · R 证明链 |
| `bh_fdr` | BH-FDR 多重检验 / Benjamini–Hochberg | 同时检验很多策略时，控制「假发现」占比 | q ≤ 0.05，0.05–0.10 WARN [建议] | M 新卡 · R |
| `prefix_invariance` | 前缀不变性证明 / Prefix Invariance | 截断未来数据后历史信号不变 ⇒ 零前视 | 最大偏差 = 0（浮点 ≤ 1e-12）[建议] | R 证明链 · A |
| `ast_causal_proof` | AST 因果证明 / AST Causality | 表达式依赖图中所有引用的时间下标 ≤ t | 违规节点 0 [建议] | R · A |
| `conservation_law` | 守恒律 / Conservation Invariants | 现金、权重、归因、摩擦在会计上必须闭合 | 各项 \|ε\| ≤ 容差 [建议] | P · B · R |
| `param_cliff` | 参数敏感性悬崖 / Parameter Cliff | 参数挪一格性能就崩，说明结果是“针尖上的点” | 邻点衰减 ≤ 30% [建议] | M 新卡 · R |
| `regime_classification` | Regime 划分 / Regime Classification | 牛/熊/震荡、高/低波、大/小盘的定量切分口径 | 口径与快照绑定 [建议] | A 切片矩阵 · R 基线 |
| `dimension_dependence` | 单维依赖度 / Single-Dimension Dependence | 超额是否主要来自某一个 Regime / 市值层 | 单桶贡献 ≤ 60% [建议] | A 切片矩阵 · R 热力图 |

> 合计 11 + 18 + 10 + 13 + 13 + 11 + 9 = **85** 项。TopBar 的 PIT 锁与 UNIVERSE、Dock 的 `KV-Cache Safe` / `Steer` / `A2UI` 属于产品机制，作为 **SYS** 域另行补充，不计入专业术语数量。

---

## 3. 第 7 视图 `/research` · 量化研究实验室与集群调度台

### 3.1 定位与路由

- `VIEWS` 新增 `{ key: 'research', label: '研究实验室·集群调度', path: '/research', prd: 'Q7.3', icon: 'research' }`，放在列表**首位**（研究是其余 6 视图的上游调度中心）；对齐 V2 契约 `Q7.3 受控 RD-Agent 研发闭环`。
- URL 状态：`/research?study=STD-0012&tab=tree|tomb|base&sec=proof`。页面组件用到 `useSearchParams`，`app/research/page.tsx` 必须以 `<Suspense>` 包裹 `ResearchView`（Next 16 客户端搜索参数约束）。
- `useWorkbench` 新增 `studyId / setStudyId`，与 URL 双向同步；Dock A2UI `quant.open_study` 只写 URL。

### 3.2 1440px 布局（Main ≈ 844px）

```
┌ ViewHeader ─ /research [Q7.3]  量化研究实验室与集群调度台 ─────────── [让 Bot 发起] [＋ 新建研究] ┐
│ sub: 假设 → 数学推演 → 形式化验证 → CPCV/回测 → 诊断 → 发布 · CSI500 · snapshot …               │
├ ClusterStrip（h 30）────────────────────────────────────────────────────────────────────────────┤
│ ● engine_sandbox 2/4 · Q1   ● ubuntu1_cluster 6/8 · Q3   ○ herdr_agent OFFLINE   SSE ● LIVE  ↻ │
├──────────────────────┬──────────────────────────────────────────────────────────────────────────┤
│ 左栏 248px（sticky） │ 右主区 586px（独立纵向滚动）                                              │
│ [课题树 14][墓碑 9]  │ ① 执行流水线  ─────────────────────────────────────────── h ≈ 96 (+日志 180) │
│ [维度基线]           │ ② 数学推演与前提假设卡 ─────────────────────────────────── h ≈ 220          │
│ ┌──────────────────┐ │ ③ 形式化验证证明链（8 行）────────────────────────────────── h ≈ 30+8×28     │
│ │ ▾ STD-0012 量价… │ │ ④ 跨维度稳定性热力图 & 基线对照 ────────────────────────── h ≈ 300          │
│ │   VERIFIED  1.42 │ │ ⑤ 根因诊断 & 改进指导 ─────────────────────────────────── h 自适应          │
│ │   ├ STD-0015 V2  │ │ ⑥ Artifact 与复现指纹 ──────────────────────────────────── h ≈ 120          │
│ │   │ RUNNING  ··· │ │                                                                          │
│ │   └ STD-0019 V3  │ │                                                                          │
│ │     FALSIFIED    │ │                                                                          │
│ └──────────────────┘ │                                                                          │
└──────────────────────┴──────────────────────────────────────────────────────────────────────────┘
```

- 网格：`.rsg { display:grid; grid-template-columns: 248px minmax(0,1fr); gap:10px; }`；844 − 248 − 10 = **586px** 主区。
- 左栏 `position: sticky; top: 0; height: calc(100vh − 44px − 24px − header)`，内部列表 `overflow-y:auto`，长树不推动右侧报告。
- 左栏可折叠为 36px 竖条（按钮在栏头），主区扩展到 ≈ 798px；Dock 收起时主区 ≈ 958px（未折叠）/ 1170px（折叠）。所有右侧面板以 586px 为最小设计宽度验收。
- `ResearchView` 不重复渲染 SideNav 的 Experiment Registry；SideNav 底部列表保持现状。

### 3.3 顶部指挥区

#### 3.3.1 「新建策略/方法研究」抽屉（NewStudyDrawer）

- 与 Glossary Drawer 同位（Main 左缘滑出，宽 560，不覆盖 Dock），两者互斥。
- 字段（自上而下，标签列 96px）：

| 字段 | 控件 | 说明 |
| :--- | :--- | :--- |
| 研究标题 | input | 必填，≤ 60 字 |
| 研究类型 | `Seg`：因子 / 多因子模型 / 组合构建方法 / 执行方法 | 决定流水线中 BACKTEST/CPCV 阶段调用的后端能力 |
| 想法 / 数学公式 | 复用 `ExprEditor` 视觉的 mono 文本区（6 行） | 自然语言或表达式均可；表达式先过前端 AST 预检提示 |
| 父研究 `parent_study_id` | 下拉（来自课题树，按 `family_id` 分组） | 从诊断卡「创建 V2 子研究」进入时预填且锁定 |
| 调度集群 `engine_sandbox` | 三选一卡片：`engine_sandbox` / `ubuntu1_cluster` / `herdr_agent`，每张显示在线状态、空闲槽、队列深度 | OFFLINE 集群禁用；显示名与节点信息由后端 `/research/clusters` 下发，前端不硬编码主机信息 |
| 股票池 | 下拉，默认 `useWorkbench().universe` | |
| 对比维度基线 | Chip 多选：Regime / 波动率分位 / 市值分层 / 申万行业 | 决定第 ④ 段热力图的行 |
| 验证预算 | 3 个紧凑数字框：CPCV `N/k`（默认 10/2）、试验数 `M`、Bootstrap `B`（默认 2000） | 每项旁带 TermTip |
| 重复检测 | 只读提示条 | 标题与想法和墓碑库做相似度匹配，命中则 `notice.warn`：「与已证伪 STD-0007（PBO 0.41）相似度 0.82」 |

- 底部：`[取消]` `[交给 Quant-Lead 起草]`（把当前草稿作为 `/派发集群研究` 发给 Bot，由 Bot 补全数学推演后返回 A2UI 确认卡）`[提交并派发]`（`btn pri`，调用 `POST /research/studies`）。
- quant-api 离线或 V3 接口 404：提交按钮禁用并提示「研究派发接口未上线」，不做假派发。

#### 3.3.2 集群健康状态条（ClusterStrip）

- 复用 TopBar `.hb` 芯片视觉：`pulse` 点 + 集群名 + `busy/total` 槽位 + `Q{queue}`；点击展开 `.pop` 显示节点明细表（节点、状态、CPU/GPU 占用、最近心跳、正在运行的 study）。
- 右端 SSE 连接状态（`LIVE / RECONNECTING / POLLING`）与手动刷新按钮。
- 轮询：`GET /research/clusters` 每 10s（与 Agent Home 心跳节奏一致）。

### 3.4 左栏：研究知识树 & 墓碑库 & 维度基线

**Tab 1 课题树（StudyTree）**

- 行高 40px，两行：第一行 `study_id`（mono 10.5）+ 状态徽章；第二行标题（ellipsis）+ 核心 OOS 指标（`SR 1.42 · DSR 0.97`）。
- 层级缩进 12px/级，最多展示 4 级，更深层折叠为「+3 代」。父子连线用 1px `var(--line2)` 竖线。
- 状态徽章映射（扩展 `triTone`）：`VERIFIED → pass`、`FALSIFIED → fail`、`INCONCLUSIVE → warn`、`RUNNING → info + .spin`、`QUEUED / DRAFT → mute`、`CANCELLED → mute 删除线`。
- 顶部筛选：状态 Seg（全部 / 进行中 / 已验证）+ 搜索框；按 `family_id` 分组标题行。
- 选中行 = `.xi.on` 视觉；键盘 `↑ ↓` 切换、`←` 折叠、`→` 展开。

**Tab 2 已证伪墓碑库（TombstoneList）**

- 每条：标题 + 证伪统计量（如 `PBO 0.41 > 0.25`、`BH q 0.18`、`CI [-0.004, 0.012] 跨零`）+ 证伪日期 + 「失败教训」一行摘要；左边框 2px `var(--up)`。
- 操作：`查看报告`（只读打开该研究）、`基于教训重开`（预填新建抽屉并自动写入「规避条件」）。
- 墓碑数据同时供新建抽屉重复检测使用。

**Tab 3 市场维度基线库（BaselinePanel）**

- 固定绑定当前 `snapshot_id`，分 4 组紧凑表（每行 22px）：
  - Regime：牛 / 熊 / 震荡，当前所处 Regime 高亮，显示划分口径 TermTip（`regime_classification`）；
  - 市值：沪深 300 / 中证 500 / 中证 1000 / 微盘 20D、60D 收益；
  - 波动率：低 / 中 / 高波分位组合收益；
  - 申万一级：涨幅前 5 / 后 5。
- 每行末尾 64×16 迷你序列线（新原语 `MiniLine`；现有 `Sparkline` 是区间位置计，不适用）。

### 3.5 右主区：6 段式投行研究报告

所有段落使用 `Panel`，标题带 TermTip，段落锚点 `id="sec-pipe|math|proof|dim|diag|art"`，URL `sec` 参数驱动滚动定位。

**① 执行流水线（PipelineStepper）**

```
HYPOTHESIS ─── MATH ─── FORMAL_VERIFY ─── BACKTEST/CPCV ─── DIAGNOSIS ─── PUBLISHED
  DONE 00:12   DONE 01:40   RUN 62% 03:05      WAIT           WAIT          WAIT
[ 日志 ▸ ]  ubuntu1_cluster · node-3 · job JOB-7f2a · 最近：Bootstrap 1240/2000 …
```

- 6 步等宽（586 / 6 ≈ 97px），每步：标签 9.5px mono + 状态符号（完成 `check` 图标 / 运行 `.spin` + 百分比 / 失败 `x` 红 / 待执行空心方块）+ 耗时。
- 步骤间连线：完成段 `var(--pass)`，运行段 `var(--sig)`，未开始 `var(--line2)`。
- 日志折叠区复用 `.code` 样式，`max-height: 180px`，自动跟随尾部（用户上滚时暂停跟随并显示「回到最新」）。
- 数据源：`useEventStream('/api/v1/jobs/{job_id}/events')`；事件 `{stage, status, progress_pct, log}`；SSE 断开 3 次后降级为每 2s 轮询 `GET /jobs/{job_id}`。

**② 数学推演与前提假设卡（MathCard）**

- 586px 下采用单列定义列表，避免双栏挤压：
  - 经济机制（段落，≤ 4 行，超出折叠）；
  - 公式推演：`FormulaBlock` 编号行 `(1) (2) (3)`，与 TermCard 同一渲染器；
  - 前提假设：`A1…An` 列表，每条带状态 Tag（已检验 PASS / 未检验 PENDING / 被证伪 FAIL，与证明链联动）；
  - 边界条件：列表；
  - 理论失效 Regime：Tag 组，点击滚动到第 ④ 段对应行并高亮。

**③ 形式化验证证明链（ProofLedger）**

| 灯 | 证明项（TermTip） | 统计量 | 阈值 | 误差 / 证伪量 | 状态 |
| :---: | :--- | :--- | :--- | :--- | :--- |
| ● | Prefix-Invariance 零前视 | max\|Δsignal\| | = 0 | 0.0 | PASS |
| ● | AST 因果证明 | 违规节点 | 0 | 0 | PASS |
| ● | Bootstrap 95% CI | RankIC CI | 下界 > 0 | [0.012, 0.035] | PASS |
| ● | CPCV / PBO / DSR | PBO · DSR | ≤0.25 · ≥0.95 | 0.18 · 0.93 | WARN |
| ● | BH-FDR 多重检验 | q-value | ≤ 0.05 | 0.031（m=24） | PASS |
| ● | 守恒律（权重 / 现金 / 归因闭合） | max\|ε\| | ≤ 1bp | 0.3bp | PASS |
| ● | PSD / Ledoit-Wolf | λmin · δ | ≥ −1e-10 | 2.1e-6 · 0.34 | PASS |
| ● | 参数敏感性悬崖 | 邻点最大衰减 | ≤ 30% | 41% @ lookback 15→20 | FAIL |

- 表格 `tbl dense`，行高 28px；首列 8px 方块灯（Token 色）。
- 每行可展开（chevron），展开区显示：精确数学误差、检验假设 H0/H1、样本量、证伪统计量的分布小图（复用 `CpcvHistogram` 或 `XYChart`）、证据引用（artifact 路径、job_id）。
- 行尾两个 `ib` 小按钮（仅 hover 行时显示，不占布局宽）：「问 Bot」→ `explainTerm`、「派发复研」→ 新建抽屉预填该项失败原因。
- 面板头汇总：`6/8 PASS · 1 WARN · 1 FAIL`，与 `<quant_canvas_context>` 的 `proofs` 字段一致。

**④ 跨维度稳定性热力图 & 维度基线对照（DimensionMatrix）**

```
              策略超额  基线收益   Δ超额   RankIC   样本日  贡献占比
Regime  牛     +6.2%    +18.4%   ···      0.041     212     38%
        熊     +3.1%    −14.2%            0.028     188     21%
        震荡   +5.0%    +1.3%             0.036     402     41%
波动率  低     ...
        中
        高
市值    大
        中
        小     +12.4%  ...                          ...     71%  ← DIM FLAG
行业    Top5 / Bottom5 （可展开）
```

- 新原语 `MatrixHeatmap`（行 × 列矩形矩阵；现有 `Heatmap` 只支持方阵且行标签宽 146px）：行标签 96px（分组名 + 桶名两级），6 个数值列各 ≈ 80px；行高 22px；12 行 + 表头 ≈ 300px。
- 着色：按列独立归一化，正值 `rgba(var(--heat-pos), α)`、负值 `rgba(var(--heat-neg), α)`（沿用现有 `--heat-pos / --heat-neg` Token）；数值始终以文字显示，不依赖颜色辨识。
- **单维依赖告警**：若任一桶「贡献占比」> 60%（`dimension_dependence` 阈值），该行右侧加 `Tag fail "DIM FLAG"`，面板头显示 `gs warn`：「超额 71% 来自小盘层，疑似被市值风格带偏」，并提供「派发市值中性复研」按钮。
- 「基线收益」列取自左栏 Tab 3 同一份基线数据，保证并排对照口径一致。

**⑤ 根因诊断 & 改进指导（DiagnosisCards）**

- 每条诊断一张卡（`gc` 视觉，左边框按严重度着色）：
  - 头：严重度 Tag（CRITICAL / MAJOR / MINOR）+ 结论一句话；
  - 证据绑定：证据 Chip（例如 `proof:param_cliff 41%`、`dim:小盘 71%`、`metric:turnover 26%`），点击滚动并高亮第 ③/④ 段对应行；
  - 改进指导：1–3 条可执行建议；
  - 按钮行：
    - `[基于此建议创建 V2 迭代子研究并派发集群]`（`btn sm pri`）→ 打开新建抽屉，预填 `parent_study_id`、标题 `… V2`、想法中写入建议、选中原集群；
    - `[把建议约束写入 /portfolio]`（仅当诊断携带 `constraints_patch`）→ 复用 `useExternalConstraintPatch()`，跳转 `/portfolio` 并触发现有 A2UI 闪烁横幅与 cvxpy 重算；
    - `[让 Quant-Lead 深入追问此问题]` → `dock.send` 附带 `study_id` 与诊断 id。
- 卡片纵向堆叠，586px 下按钮行可换行（`flex-wrap`），不截断文字。

**⑥ Artifact 与复现指纹（ArtifactCard）**

- `sigf` 键值网格：`study_id`、`parent_study_id`、`snapshot_id`、`fact_fingerprint`、`code_hash`、`job_id`、`engine`、`artifact_uri`、创建/完成时间。哈希类显示 `shortHash` + 复制按钮（复用 `CopyCmd` 视觉）。
- 按钮：`[导出 IC Memo（Markdown）]`（优先 `GET /research/studies/{id}/memo.md`；后端未提供时由前端 `memo.ts` 从报告对象生成并以 Blob 下载，文件名 `IC-Memo-{study_id}-{snapshot_id}.md`）、`[在 /models 打开关联实验]`、`[锁定该快照]`（调用现有 `setSnapshotLock`）。

### 3.6 数据契约 **[V3 契约]**

```ts
// lib/types.ts 新增 Q7 段
export type StudyStatus = 'DRAFT' | 'QUEUED' | 'RUNNING' | 'VERIFIED' | 'FALSIFIED' | 'INCONCLUSIVE' | 'CANCELLED';
export type StudyStage = 'HYPOTHESIS' | 'MATH' | 'FORMAL_VERIFY' | 'BACKTEST_CPCV' | 'DIAGNOSIS' | 'PUBLISHED';
export type EngineSandbox = 'engine_sandbox' | 'ubuntu1_cluster' | 'herdr_agent';
export type StudyKind = 'factor' | 'model' | 'portfolio_method' | 'execution_method';

export interface StudySummary {
  study_id: string; parent_study_id: string | null; family_id: string;
  title: string; kind: StudyKind; status: StudyStatus; stage: StudyStage;
  engine: EngineSandbox; universe_id: string; snapshot_id: string;
  oos_sharpe?: number | null; dsr?: number | null; pbo?: number | null;
  proofs_passed: number; proofs_total: number; job_id?: string | null;
  created_at: string; updated_at: string;
}
export interface ProofItem {
  key: 'prefix_invariance' | 'ast_causal' | 'bootstrap_ci' | 'cpcv_pbo_dsr' | 'bh_fdr' | 'conservation' | 'psd' | 'param_cliff' | string;
  label: string; status: 'pass' | 'warn' | 'fail' | 'pending';
  statistic: string; threshold: string; error: string;
  detail?: Record<string, unknown>; evidence_uri?: string | null;
}
export interface DimensionRow {
  dim: 'regime' | 'vol' | 'cap' | 'industry'; bucket: string;
  strategy_excess_pct: number; baseline_return_pct: number;
  rank_ic?: number | null; n_days: number; contribution_pct: number;
}
export interface DiagnosisItem {
  id: string; severity: 'critical' | 'major' | 'minor'; claim: string;
  evidence: { kind: 'proof' | 'dim' | 'metric'; ref: string; value: string }[];
  guidance: string[];
  constraints_patch?: Partial<OptConstraints> | null;
}
export interface StudyReport extends StudySummary {
  hypothesis: string;
  math: { mechanism: string; derivation: string[]; assumptions: { id: string; text: string; status: 'pass' | 'fail' | 'pending' }[]; boundaries: string[]; failure_regimes: string[] };
  stages: { stage: StudyStage; status: 'done' | 'running' | 'failed' | 'pending'; started_at?: string; ended_at?: string; progress_pct?: number }[];
  proofs: ProofItem[];
  dimensions: DimensionRow[];
  diagnosis: DiagnosisItem[];
  artifacts: { fact_fingerprint: string; code_hash?: string; artifact_uri?: string | null; memo_uri?: string | null };
}
export interface ClusterNode {
  engine: EngineSandbox; label: string; status: 'online' | 'degraded' | 'offline';
  slots_busy: number; slots_total: number; queue_depth: number; last_heartbeat: string;
}
```

| 方法 | 路径 | 用途 |
| :--- | :--- | :--- |
| GET | `/api/v1/research/studies?status=&family_id=` | 课题树 |
| POST | `/api/v1/research/studies` | 新建并派发，返回 `{ study: StudySummary, job: AsyncJobRecord }` |
| GET | `/api/v1/research/studies/{study_id}` | 6 段报告 |
| GET | `/api/v1/jobs/{job_id}/events` | **复用现有** SSE 推送流水线进度与日志 |
| GET | `/api/v1/research/tombstones` | 墓碑库 |
| GET | `/api/v1/research/baselines?snapshot_id=` | 维度基线 |
| GET | `/api/v1/research/clusters` | 集群健康 |
| GET | `/api/v1/research/studies/{study_id}/memo.md` | IC Memo（可选，前端有兜底） |

- 浏览器只访问同源 `/api/v1/*`（`next.config.ts` 已有 rewrite），集群派发（含 `herdr_agent`）全部由后端完成，前端不直连任何集群或 Agent 进程。
- MCP 侧建议新增 `quant_submit_study`（`approvalMode=ask` + `bg_run`），供 Bot 发起研究，与 V2 契约中高副作用工具的审批约定一致。

### 3.7 空态与错误态

| 场景 | 呈现 |
| :--- | :--- |
| quant-api 离线 | 页面顶部 `ApiNotice`；左栏与各段 `EmptyState "API OFFLINE · 无真实数据"` |
| `/research/*` 返回 404 | `notice.info`：「后端 V3 研究接口未上线（/api/v1/research/studies 404）」；新建按钮禁用 |
| 无任何研究 | 右主区显示引导：「新建研究」与「让 Bot 发起」两个按钮 + 3 行流程说明；不放示例数据 |
| 某段字段缺失 | 该段 `EmptyState "V3 FIELD PENDING"` 并写明缺失字段名 |

---

## 4. 现有 6 视图的投行级升级清单

### 4.1 `/alpha`

1. **TermTip 挂载**：8 个 KPI 标题；5 个预处理 Chip（`mad_winsorize / industry_neutral / size_neutral / barra_orthogonal / price_limit_rules`）；`zscore_rank`；「残余风格暴露」；`AST SAFE` 标签（`ast_safe`）；`PIT asof=ann_date`（`point_in_time`）；4 个面板标题（`monotonicity / rank_ic / ic_decay / max_corr_library`）；门禁条带失败项的 `b` 标签。
2. **Bootstrap 95% CI**：RankIC、ICIR、多空年化 KPI 副行改为 `[lo, hi]` + `CI` Tag（跨零 → `warn "CI 跨零"`）；IC 衰减柱图加误差须（`BarChart` 新增可选 `err?: [number, number][]`）。
3. **新面板「维度切片稳定性矩阵」**：插在 `gstrip` 与四图网格之间，全宽 844px：左 `MatrixHeatmap`（Regime 3 + Vol 3 + Cap 3 = 9 行 × RankIC / ICIR / 多空 / 胜率 / 样本日 / Δ全样本 6 列，高 228px），右 196px 摘要栏：最差切片、单维依赖度、`[派发集群深度研判此因子]`（打开 `/research` 新建抽屉，预填 `kind=factor`、因子表达式、当前预处理与股票池）。
4. **[V3 契约]** `FactorEvalResult` 可选新增：`rank_ic_ci95`、`icir_ci95`、`long_short_ci95`、`ic_decay_ci95`、`bootstrap_b`、`dimension_slices: DimensionRow[]`。

### 4.2 `/models`

1. **TermTip 挂载**：5 个 KPI；注册表表头 `IS SR / OOS SR / OOS/IS / PBO / DSR / OOS 超额 / M`；门禁区两个分节标题；Walk-Forward 图例 `Purge+Embargo`；负夏普路径行。
2. **新行 `g2`（Walk-Forward 面板之上）**：
   - 左「多重检验校正 · BH-FDR」：上半 `XYChart`（x = 排序序号 k，y = p 值，叠加 BH 临界线 `k/m·α`，被拒绝的点 `var(--sig)`，其余 `var(--tx3)`，高 150px）；下半紧凑表（实验、raw p、q、判定），家族按 `family_id` 划分，面板头显示 `m = 家族规模 · α = 0.05`。
   - 右「参数敏感性悬崖图」：每个参数一条 `XYChart` 小折线（x = 参数值，y = OOS Sharpe），选中点圆点标记，邻点最大衰减 > 30% 标红 `CLIFF`；多参数时用 `Seg` 切换。
   - 每张卡宽 (844 − 10) / 2 = 417px，高 260px。
3. 门禁判定 `verdict` 区新增两行 `VRow`：`BH-FDR q ≤ 0.05`、`参数悬崖 ≤ 30%`。
4. **[V3 契约]** `CpcvResult` 可选新增：`fdr: { alpha, family_id, family_size, items: { experiment_id, p_value, q_value, rejected }[] }`、`param_sensitivity: { param, values: number[], oos_sharpe: number[], selected_index, max_neighbor_drop_pct }[]`。

### 4.3 `/portfolio`

1. **TermTip 挂载**：5 个 KPI（ENC → `enc`）；5 个滑杆标签；目标函数 Seg（`opt_objective`）；求解状态 Tag（`solve_status`）；`PSD OK` 与 λmin（`psd`）；风险分解面板（`risk_decomposition`，并在「特质」段提及 `ledoit_wolf`）；龙卷风图例 `±0.5σ`（`style_exposure`）；持仓表头「冲击 bps」（`sqrt_impact`）、「流动性 / T+1 校验」（`t_plus_1`）；`CAP`。
2. **新行（行业偏离面板之后）**：`grid-template-columns: minmax(0,1fr) 280px`：
   - 左「AUM 策略容量与冲击衰减前沿」：`XYChart` 对数 x 轴（AUM 1 亿 → 1000 亿），左轴毛超额 / 净超额（两条线，中间冲击成本区域半透明填充 `var(--warn)` α 0.08），右轴 POV（虚线）；竖线标记「当前 AUM」与「容量上限（净超额 = 50% 毛超额或低于 hurdle）」；高 240px，宽 554px。
   - 右「形式化守恒徽章」`InvariantStrip`（纵向 5 枚）：Σw = 1、行业主动权重和 = 0、`max_constraint_violation ≤ 1e-6`（已有字段）、`|TE_pred − √(wᵀΣw)| < 1bp`、PSD λmin。
3. **[V3 契约]** `PortfolioOptResult` 可选新增：`capacity_frontier: { aum_cny: number[], gross_excess_pct: number[], net_excess_pct: number[], pov_pct: number[], current_aum_cny, capacity_aum_cny, hurdle_pct }`、`invariants: { key, label, value, tol, passed }[]`。

### 4.4 `/backtest`

1. **TermTip 挂载**：10 个 KPI（超额 Alpha 的 `NET/GROSS` Tag → `gross_vs_net`）；引擎 Seg；瀑布图 6 个分项标签；拒单表「失败原因」表头（`price_limit_rules / t_plus_1`）；Brinson 三效应行与「闭合误差」行；Barra 归因四分项；面板 Tag「真 Alpha 主导」（`barra_attribution`）。
2. **守恒证明徽章条**：KPI 网格下方新增一行 `.gstrip` 形态的 `InvariantStrip`（横向）：Brinson 闭合、Barra 闭合、现金守恒 `|NAV − (cash + Σpos·px)| ≤ 1e-8`、摩擦守恒 `gross − Σfriction = net`、T+1 违规 = 0。
3. **Capacity Frontier（回测口径）**：与 `/portfolio` 复用同一 `CapacityFrontier` 组件，置于摩擦/拒单 `bg2` 行之后、归因面板之前，全宽 844px，高 220px。
4. **[V3 契约]** `BacktestResult` 可选新增：`capacity_frontier`（同结构）、`invariants`；event 引擎改走 `async_mode=true` + `/jobs/{id}/events`，现有「计时中」提示升级为进度百分比。

### 4.5 `/data`

- **TermTip 挂载**：5 个 KPI（`coverage / freshness / fact_fingerprint` 等）；数据集矩阵表头「PIT 对账」（`point_in_time`）、「覆盖率」；7 张质量哨兵卡标题按 `check_id` 映射（`QA-01 → ohlcv_geometry`、`QA-02 → corp_action_adj`、`QA-03 → point_in_time`、`QA-04 → survivorship_bias`、`QA-05 → historical_constituents`、`QA-06 → sw_industry_pit`、`QA-07 → price_limit_rules`）；快照清单表头 `manifest sha256`；MCP 副作用 Tag `READ / ASK / BG_RUN`（SYS 域条目）。
- 哨兵卡 FAIL/WARN 时卡片右上角加「问 Bot」微按钮。

### 4.6 `/shadow`

- **TermTip 挂载**：5 个 KPI；「Perold IS 拆解」面板标题；`Authority Gate` 面板与 `SIGNAL-ONLY` Tag；衰减表头「60D RankIC / 历史分位 / 拥挤度」；状态列 `ACTIVE / DEGRADING / KILL-SWITCH`（`kill_switch`）；`risk_state` Tag。
- 衰减表「操作」列在现有「诊断」按钮旁新增「派发复研」（`DEGRADING / KILL-SWITCH` 时显示），预填 `kind=factor`、`parent_study_id`（若该因子有来源研究）与衰减指标。

### 4.7 `AgentHomeDock`

1. **快捷指令**：`QUICK_CMDS` 扩展为 5 条 + 取消，`.bcmds` 改 `repeat(3, minmax(0,1fr))`，标签缩短以保持两行高度：`/IC分层`、`/CPCV体检`、`/Barra诊断`、`/查术语`、`/派发研究`、`取消运行`。
2. **`/查术语`**：
   - 输入框为空时点击 → 打开 Glossary Drawer；
   - 输入 `/查术语 PBO` → 本地 `search()` 命中后：在线发 `explainTerm`；离线推送本地确定性术语卡（`termCard()`）。
   - 离线 `matchCommand` 增加 `/术语|是什么|含义|定义/` → `term`。
3. **`/派发集群研究`**：
   - 在线：以 `<quant_canvas_context>` + 当前视图异常项为上下文，请 Bot 起草研究并调用 `quant_submit_study`（审批 ask）；
   - 离线：直接打开新建抽屉并用画布上下文预填，不伪造派发。
   - 离线 `matchCommand` 增加 `/派发|复研|集群|研究/` → `study`。
4. **A2UI 研究结论卡**：`A2uiSurface` 新增自定义组件 `QuantStudyCard`（与现有 `QuantDiffGrid` 同级），字段 `study_id / title / status / stage / proofs_passed / proofs_total / key_metrics[] / verdict`；按钮 action `quant.open_study` → `router.push('/research?study=…')`。`applyQuantAction` 新增 `quant.open_study`、`quant.spawn_child_study`（打开新建抽屉预填）、`quant.explain_term`（打开抽屉定位词条）；`quant.open_view` 的白名单随 `VIEWS` 自动包含 `/research`。
5. **`<quant_canvas_context>` 透传 `/research`**：`buildCanvasContext` 新增分支：

```json
{
  "active_view": "/research",
  "universe": "CSI500",
  "snapshot_id": "…",
  "study_id": "STD-0015",
  "parent_study_id": "STD-0012",
  "stage": "FORMAL_VERIFY",
  "status": "RUNNING",
  "engine": "ubuntu1_cluster",
  "proofs": "6/8",
  "proof_fail": ["param_cliff"],
  "dim_flag": "cap:small 71%",
  "tearsheet_metrics": { "oos_sr": 1.31, "dsr": 0.93, "pbo": 0.18, "bh_q": 0.031, "ic_ci_lo": 0.012 }
}
```

   - `canvasChips` 新增 `study:` `stage:` `proofs:` 三枚；800 字符裁剪顺序：先裁 `tearsheet_metrics` 尾键，再裁 `proof_fail` 尾项，`study_id / stage / status` 永不裁剪。

### 4.8 `TopBar` / `SideNav`

- TopBar：版本标签改 `V3.0 PRO-QUANT`；新增术语搜索按钮（`search` 图标 + `⌘K`，`.ib` 加宽为 56px）；全局快捷键监听放在 `AppShell`（`keydown` 捕获，`metaKey||ctrlKey` + `k`）。宽度核算：1440 下 `.fld-bench` 已因 `max-width:1480px` 隐藏，右侧新增 56px 不挤压 PIT 锁字段。
- SideNav：`VIEWS` 首项为 `/research`，分区计数改为动态；`/research` 项右侧 `em` 显示运行中研究数（`RUNNING n`，来自课题树缓存，无数据时显示 `Q7`）。

---

## 5. 前端文件结构与组件拆分蓝图

### 5.1 新增文件

| 路径（相对 `apps/web/src`） | 职责 | 预估行数 |
| :--- | :--- | :---: |
| `lib/thresholds.ts` | 唯一阈值源：迁移 `AlphaView.THRESHOLDS/EXP_LIMIT`、`ModelsView.PBO_MAX/DSR_MIN/OOS_IS_MIN`、`BARRA_HARD_LIMIT`、闭合误差 1bp、特质占比 65%、PSD 容差；新增 BH-FDR、Cliff、CI、依赖度阈值 | 80 |
| `lib/glossary/types.ts` | `TermId` 联合类型、`GlossaryTerm`、`TermThreshold` | 60 |
| `lib/glossary/terms/{q1,q2,q3,q4,q5,q6,math}.ts` + `index.ts` | 85 条术语数据，按域拆分（单文件 < 400 行） | 7 × 150 |
| `lib/glossary/evaluate.ts` | `evaluate(term, value, override) → {verdict, gap}` | 70 |
| `lib/glossary/search.ts` | 子串 + 缩写评分，本页加权 | 60 |
| `lib/glossary/store.ts` | `useSyncExternalStore` 单例：active 卡、pinned、pageValues、drawer 开关 | 90 |
| `lib/use-floating.ts` | 浮层定位（翻转 / 夹取 / 避让 Dock / 滚动重定位） | 80 |
| `lib/use-event-stream.ts` | EventSource 封装：重连、Last-Event-ID、降级轮询 `/jobs/{id}` | 90 |
| `lib/research-api.ts` | `/research/*` 客户端（复用 `quantRequest`、`QuantApiError`） | 70 |
| `components/glossary/TermTip.tsx` | 轻量触发器（hover intent 180/140ms、focus、click pin） | 70 |
| `components/glossary/TermPopoverHost.tsx` | 单例 Portal，渲染 `TermCard`，Esc / 外部点击 | 90 |
| `components/glossary/TermCard.tsx` | 6 段卡片，Drawer 详情复用 | 150 |
| `components/glossary/FormulaBlock.tsx` | 零依赖公式微渲染器 | 60 |
| `components/glossary/GlossaryDrawer.tsx` | ⌘K 速查库（搜索、域筛选、列表 + 详情） | 180 |
| `components/Drawer.tsx` | 抽屉基座（Main 左缘滑出、veil、焦点圈定、Esc） | 60 |
| `components/proof/InvariantStrip.tsx` | 守恒/证明徽章（横向 / 纵向），供 P / B / R 复用 | 80 |
| `app/research/page.tsx` | `<Suspense><ResearchView /></Suspense>` | 10 |
| `components/views/ResearchView.tsx` | 页面容器：头部、ClusterStrip、两栏网格、URL 同步、`usePublishMetrics('research')` | 220 |
| `components/research/ClusterStrip.tsx` | 集群健康条 + 节点明细 `.pop` | 110 |
| `components/research/StudyTree.tsx` | 课题树（分组、缩进、键盘导航） | 180 |
| `components/research/TombstoneList.tsx` | 墓碑库 | 90 |
| `components/research/BaselinePanel.tsx` | 维度基线 4 组表 + `MiniLine` | 120 |
| `components/research/NewStudyDrawer.tsx` | 新建研究表单、重复检测、派发 | 230 |
| `components/research/report/PipelineStepper.tsx` | 6 步进度 + 日志折叠 | 140 |
| `components/research/report/MathCard.tsx` | 数学推演卡 | 100 |
| `components/research/report/ProofLedger.tsx` | 证明链表 + 行展开 | 170 |
| `components/research/report/DimensionMatrix.tsx` | 热力图 + 单维依赖告警 | 120 |
| `components/research/report/DiagnosisCards.tsx` | 诊断卡与三类动作 | 140 |
| `components/research/report/ArtifactCard.tsx` | 指纹与导出 | 90 |
| `components/research/memo.ts` | IC Memo Markdown 生成（后端缺省时兜底） | 90 |
| `components/research/CapacityFrontier.tsx` | P / B 共用容量前沿面板（内部用 `XYChart`） | 110 |
| `components/charts/XYChart.tsx` | 数值 x 轴（线性/对数）折线 + 散点 + 区域 + 竖线 + 双轴；服务容量前沿、BH-FDR 阶梯图、参数悬崖图 | 200 |
| `components/charts/MatrixHeatmap.tsx` | 行 × 列矩形热力图（分组行标签、按列归一化、数值常显） | 130 |
| `components/charts/MiniLine.tsx` | 64×16 序列迷你线 | 40 |

### 5.2 修改文件

| 路径 | 改动 |
| :--- | :--- |
| `lib/meta.ts` | `ViewKey` 增 `'research'`；`VIEWS` 首位插入 `/research`（`prd: 'Q7.3'`）；`viewFromPath` 自动生效 |
| `lib/workbench.tsx` | 新增 `studyId / setStudyId`；`DockController` 增 `explainTerm(q: TermQuery)`、`dispatchStudy(draft)`；`dock` memo 透传 |
| `lib/canvas-context.ts` | `CanvasInput` 增 `study?` 与 `termFocus?`；新增 research 分支与裁剪优先级；`canvasChips` 增三枚 |
| `lib/types.ts` | 新增 Q7 研究类型；Q2/Q3/Q4/Q5 结果类型追加可选 V3 字段（全部 `?:`，不破坏现有解析） |
| `lib/quant-api.ts` | 导出 `research` 命名空间或由 `research-api.ts` 复用 `quantRequest`；`cpcv / runBacktest` 增 `async_mode` 变体 |
| `app/globals.css` | 新增分区：`/* TermTip */ .tt-trg .tt .tt-h .tt-red .tt-plain .tt-fx .tt-now .tt-pit .tt-ft`；`/* Drawer */ .drw .drw-veil`；`/* Glossary */ .gdr .gdr-list .gdr-item`；`/* Research */ .rsg .cstrip .tree .tree-row .stp .stp-i .proof-row .diag`；z-index 刻度注释；全部仅用现有 Token |
| `components/AppShell.tsx` | 挂载 `TermPopoverHost`、`GlossaryDrawer`；注册 `⌘K` |
| `components/TopBar.tsx` | 版本 `V3.0`；术语搜索按钮 |
| `components/SideNav.tsx` | 分区计数动态化；`/research` 运行数徽标 |
| `components/icons.tsx` | 新增 `info`、`search`、`research`、`tree`、`tomb`、`cluster` 线性图标（24 网格、`stroke` 与现有一致） |
| `components/ui.tsx` | `triTone` 增 `verified → pass`、`falsified → fail`、`running/queued`；`Kpi` 不改签名（`label` 已是 `ReactNode`，直接传 `<TermTip>`） |
| `components/charts/BarChart.tsx` | 可选 `err?: [number, number][]` 误差须 |
| `components/AgentHomeDock.tsx` | `explainTerm / dispatchStudy` 实现；`matchCommand` 扩展；`applyQuantAction` 增 `open_study / spawn_child_study / explain_term`；注册新 DockController 方法 |
| `components/dock/constants.ts` | `QUICK_CMDS` 扩展与短标签；`QuickCmd` 类型同步到 `workbench.tsx` |
| `components/dock/A2uiSurface.tsx` | 新增 `QuantStudyCard` 组件分支 |
| `components/dock/local-diagnostics.ts` | 新增 `termCard()`、`studyRouteCard()` 本地确定性卡片构造 |
| `components/views/AlphaView.tsx` | TermTip 挂载；CI 副行；维度切片矩阵面板；阈值改引 `lib/thresholds.ts` |
| `components/views/ModelsView.tsx` | TermTip；BH-FDR 卡、悬崖卡；verdict 新增两行；阈值改引 |
| `components/views/PortfolioView.tsx` | TermTip；容量前沿 + 守恒徽章行 |
| `components/views/BacktestView.tsx` | TermTip；守恒徽章条；容量前沿；event 引擎异步进度 |
| `components/views/DataView.tsx` | TermTip（含 `check_id → termId` 映射表） |
| `components/views/ShadowView.tsx` | TermTip；「派发复研」按钮 |

### 5.3 零摩擦融合原则

1. **零新增运行时依赖**：`package.json` 依赖仍只有 `next / react / react-dom`；Popover、公式、热力图、对数轴、SSE 全部自研于现有 SVG 原语与 `ChartBox / useSize / nice()` 之上。
2. **数据获取**：请求/响应类一律 `useApi(key, fetcher, debounce)`（key 为 null 即离线禁用，沿用现有约定）；只有流式进度使用 `useEventStream`。
3. **状态**：跨页状态进 `useWorkbench`（`studyId`）；高频 UI 状态（TermTip 激活、Drawer 开关）进独立 `glossary/store`，避免每次 hover 触发整个 Workbench Context 重渲染。
4. **样式**：只写 `globals.css` 的 `@layer components`，类名短小（沿用 `.kpi/.gs/.gc` 命名风格），不在 JSX 写颜色字面量。
5. **组件体量**：单文件 ≤ 400 行；`ResearchView` 只做编排，六段报告各自独立文件。
6. **Bot 契约**：术语、研究的所有 Bot 文本只进最新 user 消息，不修改 system 前缀；离线一律走本地确定性卡片并标注 `LOCAL · DETERMINISTIC`。

### 5.4 1440px 验收清单

| 区域 | 宽度核算 | 验收点 |
| :--- | :--- | :--- |
| TermCard | 固定 360px，右界避让 Dock | Main 内任意触发点打开均不覆盖 Dock、不越出视口；底部触发点自动翻转 |
| Glossary / NewStudy Drawer | 560px 起点 x = 200 | 右缘 760 < Dock 左缘 1080，Dock 全程可见 |
| `/research` 两栏 | 248 + 10 + 586 = 844 | 586px 下：流水线 6 步每步 ≥ 92px；证明链 6 列无截断（统计量列 ellipsis + title） |
| `/research` 热力图 | 96 + 6 × 80 = 576 ≤ 586 | 数值列不换行 |
| `/alpha` 切片矩阵 | 844 − 196 − 10 = 638 热力图区 | 9 行 × 6 列，列宽 ≈ 90 |
| `/models` BH-FDR / Cliff | 各 417px | 表格 4 列 + 图 150px 高 |
| `/portfolio` 容量前沿 | 844 − 280 − 10 = 554 | 双轴刻度 42px 内边距后绘图区 ≈ 470px |
| Dock 快捷指令 | 360 − 20 内边距，三列 ≈ 105px | 6 个短标签两行，不溢出 |
| 双主题 | 全部 Token | 深/浅色切换后 Tag、热力图、公式块对比度一致 |

### 5.5 建议落地顺序

1. `lib/thresholds.ts` + 术语库 + TermTip/Popover/Drawer（无后端依赖，可先上线覆盖 6 视图）。
2. `meta / workbench / canvas-context / Dock` 的 `/research` 与 `/查术语` 接线。
3. `/research` 骨架 + 诚实空态（后端 V3 接口未上线时即可验收布局与交互）。
4. 随后端 V3 字段到位依次点亮：CI 与维度切片 → BH-FDR 与 Cliff → 容量前沿与守恒徽章 → 研究流水线 SSE。
