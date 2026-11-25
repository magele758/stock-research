# QuantLab Pro V3.0 量化研究缺口与形式化验证方案

审计基线为提交 `56d56b7` 的 `packages/quant-core/src/quant_core/` 与 `packages/quant-core/tests/`，沿关键入口交叉核对 `services/quant-api/src/quant_api/service.py`。V2.0 已具备确定性研究骨架。V3.0 首先要把数据可得性、试验登记和成交记账从约定升级为可重放、可拒绝的合同，再用样本外证据判断 Alpha。本文所有 V3 阈值是待版本化的首发策略，示例数值为测试用例，实盘和生产数据结论均待实证。

## 1. V2.0 实况与机构级 Gap

下表的机构名指公开可核实的研究实践，差距与验收项属于本项目设计。公开材料支持 WorldQuant 的规模化 Alpha 构建和模拟、GS QIS 的因子选股与受约束建仓、Two Sigma 的风险因子分解、Citadel EQR 的冲击建模和模拟反馈、D. E. Shaw 的系统化研究及数据基础设施、AQR 用实际成交估计成本与盈亏平衡规模。各家的内部数据合同、数值门槛、CPCV 或形式化实现均未知。 [WorldQuant BRAIN](https://www.worldquant.com/brain/) · [101 Formulaic Alphas](https://arxiv.org/abs/1601.00991) · [GSAM QIS](https://am.gs.com/public-assets/documents/a1cc7303-77d3-11ef-9a8e-ad76f6574855) · [Two Sigma Factor Lens](https://www.twosigma.com/wp-content/uploads/Introducing-the-Two-Sigma-Factor-Lens.10.18.pdf) · [Citadel EQR](https://www.citadel.com/careers/career-perspectives/inside-eqr-what-it-means-quantitative-researcher/) · [D. E. Shaw](https://www.deshaw.com/what-we-do) · [AQR 交易成本原始研究](https://www.aqr.com/Insights/Research/Working-Paper/Trading-Costs-of-Asset-Pricing-Anomalies)

**P0** 阻止候选升级或可执行性声明，**P1** 必须随 V3 交付，对应证据不足时相关结论为未知，**P2** 为后续增强。下文源码短路径以 `packages/quant-core/src/quant_core/` 为根。表中的测试指 `packages/quant-core/tests/`。

| 模块与公开对标 | V2.0 已实现及证据 | V3.0 差距与立即交付项 |
| --- | --- | --- |
| PIT 湖与数据接入 · D. E. Shaw、Citadel | `schemas.py:78–181` 定义 listing/issuer、行情、公司行动、财务修订、股票池及行业区间。 `lake/store.py:170–275,328–477` 用 Zstd Parquet、哈希清单、DuckDB、SQLite 注册快照及有效区间连接。财务可按 `published_at` 或同时按 `first_seen_at` 回放，取最后可见修订，撤回即移除。 `lake/qa_sentinel.py:102–621` 七项 QA 覆盖 OHLCV、复权连续性、财务时点、退市覆盖、权重、行业区间及涨跌停。 `lake/qlib_export.py:49–320` 缓存签名、二进制日频导出及目录替换。 `providers/base.py` 有限流、鉴权、重试及统一接口，Tushare 实现 A 股行情、复权、财报、成分与涨跌停，BaoStock 实现日线/复权/价格对账，SEC EDGAR 提取披露版本，Massive 提供美国行情及分红拆股。 | **P0** `load_bars` 丢弃 `available_at` 等，按有效期纳入成分却未限制 `universe_member.as_of_date`，行业连接未固定 taxonomy 且按 `valid_from` 去重。财务读未检 `report_period_end <= ann_date <= as_of_local_date`，公司行动只按 `ex_date` 范围读取。QA fail 和跳过 QA 仍可发布，重复 ID 可重写清单，读取未验 SHA，artifact 同 run/name 可覆盖旧内容（`store.py:141–152,236–275,635–688`、`registry.py:175–234`）。交付不可变清单、按时点与列的可得性、QA 隔离和证据哈希。**P1** 统一各源的单位、发布时间精度、回填来源和冲突仲裁，Qlib 产物附 PIT 能力清单。**P2** 多市场一致性与更细行情。 |
| Alpha Lab · WorldQuant、GS QIS | `factors/ast_guard.py:96–104,141–238,267–282` 以白名单编译表达式，禁止未来位移与居中 rolling，限定同证券时序及同日截面。 `factors/engine.py:123–412,444–552,603–825` 有内置因子、财务版本回放、次开盘至期限收盘标签、RankIC/ICIR、HAC、分层、换手、衰减、行业 IC 和库内比较。 `factors/preprocess.py:125–257` 逐日 MAD、行业/规模/Barra 回归、Z-score 或 rank。当前准入包括 RankIC≥.03、年化 ICIR≥1.5、HAC t≥3、至少 120 个成熟日且每日 IC 最少 50 标的、覆盖≥max(.80, 配置)、单边换手≤.15、分层单调性≥.90 且首尾正差、库内平均绝对日相关<.70。 | **P0** 内置函数不经过 AST；同 `trade_date` 可跨市场或跨决策时钟混排；盈利因子只按 `published_at` 回放，预计算财务列在无时间戳时也可使用（`engine.py:202–335,838–841`）。交付 T1/T2、可得性 DAG、冻结研究规范及成熟标签契约。**P1** 缺风格整日省略、rank 后残差可能重新暴露、四个内置因子的相关性库太小，顶层绝对均值与门禁平均绝对值口径不同且零可掩盖缺证据（`preprocess.py:230–256`、`engine.py:665–825`）。交付中性化后验检查及全库重叠样本相关。 |
| CPCV / DSR · 机构级可复现研究基准 | `validation/cpcv.py:32–78,81–186,198–383` 支持冻结的日期×候选收益矩阵、固定会话偏移 purge/embargo、15 组合/5 路径（N=6、k=2）、八等分块 CSCV PBO、独立冲突计数、DSR 及滚动选择。硬门槛 PBO≤.25、DSR≥.95、OOS/IS≥.50 且 IS>0、零冲突，walk-forward 至少有一折可算。 `schemas.py:356–414` 可记录试验计数和路径结果。 | **P0** 冻结矩阵选赢家，尚无按折拟合/转换；固定行偏移不能涵盖实际标签成熟日与缺交易日。服务 `service.py:419–510` 由分层 LS NAV 反推无执行成本的纸面收益、候选最多 12 个，基准只作对照，`trial_count_m` 可外传，登记可 upsert 且保存失败被吞掉。未知 PBO/DSR 顶层回填 0，负路径计数未设门槛。交付追加式 ledger、标签区间 oracle、可重放真实净收益及一次性盲测。**P1** Block bootstrap、BH/BY、SPA/WRC 与扰动试验，晋级需完整证据。 |
| Barra / cvxpy · Two Sigma、GS QIS、MSCI | `risk/barra_opt.py:132–509,607–909,951–1020,1088–1243` 有 CNE6 风格代理暴露、截面识别约束的 sqrt-cap WLS、近 253 个会话的 Ledoit–Wolf 因子协方差、行业/全局先验收缩的特异风险与 PSD 修复、long-only、行业与风格偏离、TE、单边换手和交易界，支持 CLARABEL 与 OSQP 后验复核、约束松弛和手数取整。 `schemas.py:420–524` 给暴露、目标权重及求解状态。 | **P0** 把求解后验验算、目标/实际持仓/现金和松弛版本独立存证。缺 NAV/手数/冲击数据时推定的连续组合只标研究目标；服务以基准当当前仓位（`service.py:571–582`）。**P1** 标记 CNE6 风格代理与描述符缺失，补风险预测校准、持仓 P&L 对账和换手/三类费用的同单位目标函数。**P2** 获得授权模型数据后做按版本对标与基准映射。 [MSCI Equity Factor Models](https://www.msci.com/data-and-analytics/factor-investing/equity-factor-models) |
| 微结构回测及归因 · Citadel、AQR | `backtest/engine.py:51–158,320–417,430–536,545–841` 信号下一交易日生效，支持双引擎、卖先买后、冻结停牌、派息/送股、现金与应收、独立 Brinson/Barra 和相对净值链接。 `backtest/microstructure.py:59–110,138–224` 有限价、T+1 可卖数量、整手、PoV、平方根冲击和显式费用。SCREENING 也近似计摩擦，但允许小数股。 | **P0** EVENT 以开盘为价格参考，却用全日量和 OHLC 判成交，属于日线估计；缺逐笔账本和时点化费用表，符号跨期映射还可能受未来同名 listing 影响。日终参考回放与真正事件仿真须区分，补账户/成交/归因可独立重建契约。**P1** 六档容量回放、延迟与未成交机会损益分解、成本拟合及压力场景。 |
| 影子盘 · Citadel 实盘反馈实践 | `shadow/monitor.py:80–109,146–330,333–533` 可读取同一面板上的审计字段，计算 Perold IS，配对模拟参考、检查成熟 IC 与拥挤，输出只读 manifest 与指纹。可比较证据须覆盖至少 95% 的订单和名义金额，`unknown/alert/kill` 状态禁发信号。 | **P0** 通常入口只供行情（`service.py:704–708`），普通快照缺成交和审批证据时不能支持影子实盘结论。改为显式 as-of、只读导入的独立持久订单/成交/审批链、按事件可得时间过滤、kill latch、同快照和过期检查。哈希提供内容完整性，审批权威需受信来源。**P1** 存在重叠的历史 IC 窗，库内 top-decile 重叠只是拥挤代理，补独立基准窗、逐候选成熟度、成本分桶和告警回放。 |

**接入能力与数据质量边界。** Tushare 日线的 `available_at` 取查询时点，财报入口仍返回源字段（`providers/tushare.py:114–147,189–208,307–321`）。BaoStock 价格对账走 inner join，未发现源之间单侧缺行（`providers/baostock.py:174–187`）。SEC EDGAR 当前把日期填成固定披露时刻，按过滤后的日期范围重编号且同报告止日可能混合会计上下文（`providers/sec_edgar.py:117–179`）。Massive 有首条前收与部分成交额的派生值，公司行动接口未遍历 `next_url`（`providers/massive.py:55–155`）。七项 QA 中，覆盖的现有展示多为表存在即 100%，退市检查与官方成分权重检查也各有口径边界（`lake/qa_sentinel.py:339–465,670–766`），因此 V3 需要独立会话和预期成员分母。影子 monitor 以 20 bp 的配对 IS 中位差做告警，缺成交或已审计权威时保持未知和无导出，以可复算性区分这一状态和真实的零冲击。

### 测试与可复现反例

- `test_lake_pit.py` 的 9 项覆盖黄金快照、初始/重述/撤回、退市及历史行业变更、公司行动连续性、涨跌停规则边界、QA 注错、Qlib 增量及回读、供应商 mock/凭证、注册表及产物往返。测试夹具用于验证行为，外部供应商、逐行迟到回放与快照不可变性仍需专门验证。
- `test_quant_engines.py` 的 30 项覆盖 AST、单次因子前缀对照、财务修订、SVD 正交、CPCV 路径和种子正负样本、Barra/CLARABEL/OSQP 和松弛、双引擎送股/派息/停牌、归因闭合、Perold 与只读授权。原有 39 项全部通过。复跑命令为 `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider packages/quant-core/tests`。
- 对临时六表快照的合成探针复现了迟到行情仍入当日面板、未来成分快照与同日 `load_universe_constituents` 矛盾、公告日不合法的财务行仍返回、迟公告公司行动进入 ex-date 读取、同 snapshot ID 重发后 SHA 改变，以及同 run/name 写入后旧 URI 读到新内容。这些是 V3 P0 负向验收样例。
- 非运行态依赖需纳入同一入口验收。服务用 `_bench_history` 月初取权重并在有行情的标的上重新归一化，`target_weights_from_scores` 用指数倾斜直接生成回测目标（`service.py:587–696`），因此 `optimize_portfolio` 通过并不能证明回测实际使用了 cvxpy 组合。V3 候选须声明 `target_generator` 和 `benchmark_policy_id`，两者进入证据哈希。

## 2. FormalVerificationEngine

### 2.1 输入、执行顺序与证据协议

`verify(ctx: VerificationContext) -> FormalProofBundle` 只读取已冻结的研究输入。 `ctx` 绑定快照内容、数据可得模式、研究规范、策略产物、日历与市场时区、基准和费用表版本、账本截点、门槛版本、随机种子及依赖环境。阶段为 `manifest → T1 → T2 → target/fills/attribution/T3 → OOS/T4 → dimension/capacity → diagnosis`。每个 checker 按固定顺序出具 PASS / FAIL / INSUFFICIENT / NOT_APPLICABLE；依赖失败时下游为 INSUFFICIENT 并列依赖 ID。策略必须检查现金/执行，纯因子可按 profile 免除执行项，免检仅由版本化 profile 声明。必检 FAIL 给 `reject`，必检缺证据、预期检查缺失或必检被免除给 `inconclusive`，全部通过才给 `pass`。这一合同的 pass 对应冻结研究证据，影子导出还须独立权威检查。

长任务沿用 `AsyncJobRecord` 与 `McpEnvelope`，摘要只返回哈希、verdict、失败 ID 和短预览。证据采用排序后的 Parquet/JSON 加内容 SHA，文件按内容寻址，快照与 ledger 用事务发布最终指针。各工件保留 `check_id, decision_at, listing_id, slice_id, candidate_id, fold_id` 中适用的键。确认阶段读取 hash，独立复算而非信任上次计算的 PASS 布尔值。

以下是合同的公共类型。 `schema_version` 控制结构，`policy_sha256` 绑定阈值、必检清单和单位，`code_sha256` 与 `env_sha256` 绑定评估代码和依赖环境。 `bundle_sha256` 按自定义带版本 canonical JSON 对 `exclude={"bundle_sha256"}` 摘要，UTC ISO 时间、键排序、禁止 NaN/Inf、浮点编码固定，同次重算用冻结的 `as_of`。外层 artifact SHA 直接对发布字节求值。

```python
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, FiniteFloat, model_validator

Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Key = Annotated[str, Field(min_length=1)]
Count = Annotated[int, Field(ge=0)]
Seed = Annotated[int, Field(ge=0, lt=2**64)]
Status = Literal["pass", "fail", "insufficient", "not_applicable"]
Verdict = Literal["pass", "reject", "inconclusive"]

class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

class EvidenceRef(Contract):
    uri: Key
    sha256: Sha256
    format: Literal["parquet", "json", "arrow"]
    row_count: Count

class Observation(Contract):
    metric: Key
    unit: Key
    value: FiniteFloat
    op: Literal["lt", "le", "gt", "ge", "eq"]
    threshold: FiniteFloat

    def passes(self) -> bool:
        match self.op:
            case "lt":
                return self.value < self.threshold
            case "gt":
                return self.value > self.threshold
            case "le":
                return self.value <= self.threshold
            case "ge":
                return self.value >= self.threshold
            case "eq":
                return self.value == self.threshold

class ProofCheckItem(Contract):
    check_id: Key
    suite: Literal["T1", "T2", "T3", "T4"]
    checker_version: Key
    kind: Literal["deductive", "invariant", "falsification", "statistical"]
    scope: Key
    status: Status
    reason_code: Key
    n_observed: Count
    dependencies: tuple[Key, ...] = ()
    comparators: tuple[Observation, ...] = ()
    evidence: tuple[EvidenceRef, ...] = Field(min_length=1)
    witness_keys: tuple[Key, ...] = ()

    @model_validator(mode="after")
    def check_verdict(self) -> Self:
        if self.status in ("pass", "fail"):
            if not self.comparators or self.n_observed == 0:
                raise ValueError("a verdict requires observed evidence and comparators")
            if all(metric.passes() for metric in self.comparators) != (self.status == "pass"):
                raise ValueError("verdict differs from comparisons")
        elif self.comparators:
            raise ValueError("unevaluated checks cannot carry gate comparisons")
        if self.status == "fail" and not self.witness_keys:
            raise ValueError("failure requires an addressable witness")
        return self

class FormalProofBundle(Contract):
    schema_version: Literal["3.0"] = "3.0"
    verification_id: Key
    experiment_id: Key
    candidate_id: Key
    snapshot_id: Key
    snapshot_manifest_sha256: Sha256
    as_of: AwareDatetime
    availability_mode: Literal["strict_public", "recorded_replay"]
    profile: Literal["factor", "strategy"]
    policy_sha256: Sha256
    derivation_sha256: Sha256
    ledger_cut_sha256: Sha256
    calendar_sha256: Sha256
    benchmark_sha256: Sha256
    code_sha256: Sha256
    env_sha256: Sha256
    seed: Seed
    inputs: tuple[EvidenceRef, ...] = Field(min_length=1)
    required_check_ids: tuple[Key, ...] = Field(min_length=1)
    checks: tuple[ProofCheckItem, ...] = Field(min_length=1)
    verdict: Verdict
    bundle_sha256: Sha256

    @model_validator(mode="after")
    def check_bundle(self) -> Self:
        by_id = {item.check_id: item for item in self.checks}
        if len(by_id) != len(self.checks) or len(set(self.required_check_ids)) != len(self.required_check_ids):
            raise ValueError("duplicate check ID")
        seen: set[str] = set()
        for item in self.checks:
            if not set(item.dependencies) <= seen:
                raise ValueError("unknown or cyclic dependency")
            if item.status in ("pass", "fail") and any(
                by_id[dep].status != "pass" for dep in item.dependencies
            ):
                raise ValueError("evaluated check has unresolved dependency")
            seen.add(item.check_id)
        statuses = [by_id[key].status if key in by_id else "insufficient"
                    for key in self.required_check_ids]
        expected = "reject" if "fail" in statuses else (
            "pass" if all(value == "pass" for value in statuses) else "inconclusive"
        )
        if self.verdict != expected:
            raise ValueError("invalid aggregate verdict")
        return self
```

首发 `factor` profile 要求 T1 全套、T2 全套、T3.PIT 及 T4 的该因子主统计与台账检查。`strategy` 另要求 T3.LEDGER/T3.ATTRIBUTION、净 OOS、容量所需的账务/行情证据，使用风险优化的策略增加 T3.RISK/T3.WEIGHTS，基础 long-only 也校验单纯形。具体检查集合来自 policy，根据探查用途和晋级用途分别发版；晋级失败如被标成探查 PASS，消费端仍按晋级 policy SHA 拒绝。

Pydantic 验结构和逻辑，加载层验字节、阈值清单和方法版本，并拒绝路径逃逸、相同键的不同内容和错误依赖身份。证据缺失仍输出机器可读的缺失清单 artifact。T1 结构性检查可用缺少证明的节点数，T3 可用最大残差，T4 用估计量及预登记阈值，诊断通过 `witness_keys` 回到具体行或统计结果。

### 2.2 T1 · 因果性与前缀不变性

**数据合同。** 以交易所日历、IANA 时区和 UTC 事件时间与同刻稳定序列定义决策时钟 $t$。日期只作本地业务键，时间戳同刻的事件另有序列排序。令 $P_t D$ 为从原始不可变版本流投影出的当时可见数据，加上当时已知配置、日历和已发布模型。$\mathcal F_t=\sigma(P_tD)$。原始行情、复权、股票池、申万分类、基准、费率、公司行动、信号与模型均保留 `effective_from/effective_to`（使用半开区间）、`public_at`、`observed_at`、稳定实体键、版本、来源和质量；同一行情记录中的开价、收价、总量可有不同可得时点。 `strict_public` 要求可信公开时间不晚于 $t$，`recorded_replay` 还要求不可变实收时间不晚于 $t$。财务映射现有 `published_at/first_seen_at`，公司行动映射 `announced_at` 及修订信息，指数和行业同时核对生效日与发布时间。只有日期的源按交易所本地日终作为保守上界或判不可用，禁止机械填 UTC 开盘。历史重建与实收回放分别出具结果。

- **T1.OPERATOR** 使用当前 AST 守卫作为语法前置。扩展受限表达式中间表示，给每节点 `operator_id, scope, inputs, latest_knowledge_at, fit_cutoff`。常量从冻结配置读取，点算子取输入可得时点的最大值，尾随窗口仅访问单证券当前或过去会话，同截面聚合以 `market, decision_at, universe_id` 为键并检查所有参与列与成员在 $t$ 可见。跨市场因子要声明共同 UTC 决策时钟，早开市场无法引用晚收市场同日收盘。fit 产物仅在训练特征与标签均成熟、模型实际可用后进入信号。独立验证器按 DAG 拓扑做结构归纳并核查叶节点的版本与可见时点，由此证明闭合算子图在合同前提下 $f_t$ 是 $\mathcal F_t$-可测的。内置自定义 Python 须注册经验证的算子语义、改写为受限图或只提供动态证据；决策进程只能读授权投影，禁用隐式全局数据、系统时钟、网络、自由文件读取和未绑定缓存。
- **T1.PREFIX** 全量路径从原始版本流重建每一决策时点，前缀路径从 $P_\tau D$ 独立重建。同一历史起点与 warm-up、同一策略/模型版本策略、确定性浮点顺序和因果种子派生，隔离本地缓存。比较全量结果限制于 $t\le\tau$ 与前缀结果的 `(market, listing_id, decision_at, output_id)` 键、可用掩码、权重/信号和诊断状态；异常也比较。浮点值统一 Float64 或预声明无量纲输出尺度，有限项按绝对误差

  $$
  \max_{t\le\tau,i}\left|f_i(t;P_\tau D)-f_i(t;D)\right|\le 10^{-10}.
  $$

  键、状态、分类值、null/NaN 掩码必须完全一致，Inf 永远失败。全空、截断均在 warm-up、或无有效对照则证据不足。不得从已计算面板或按最终快照预处理的特征做截断。标签与未来成交仅走单独的评价和事后回放通道，晋级还需验证训练标签的成熟边界。
- **T1.SUFFIX** 在相同前缀上追加、删去或编辑未来报价、成员、行业、财务重述、晚到公告和同名复上市，重跑全路径。固定 CI 样例须覆盖全样本均值/标准化、`center=True`、全期 group transform、跨市场同日、换仓边界和全样本拟合。发现反例存 `cutoff, listing_id, decision_at, output_id, prefix_value, full_value, first_bad_dependency`。研究运行至少用固定事件边界（开/收、发布、财报修订、分池及 fold 边界）加 seeded 分层抽样 32 个额外截点（不足则全取），发布前对小样本穷举所有决策边界。保存 seed、截点、扰动类型与分支覆盖；已有反例是 CN 07:00 UTC close=10 与 US 21:00 UTC close=100 的同日截面，`close - mean(close)` 给 CN 的 raw 为 -45，按 UTC 前缀重算为 0。

对所有输入与所有截断成立的**精确**前缀不变性，等价于确定性输出仅依赖可见前缀，前提是 $P_t$ 恰好保留全部可读信息。有限次试验只能证伪或确认已测样本，不能承诺 100% 捕获全部泄漏。从 $N$ 个候选截点均匀无放回抽 $k$ 个、固定数据中有 $m$ 个能暴露某缺陷时，单纯该抽样的漏检概率为 $\binom{N-m}{k}/\binom{N}{k}$。若违规影响只出现在一个从未抽到的分支、所有后缀在该样本恰好抵消、差异小于容差，或两个路径共享已泄漏的上游值，动态检验都可能通过。V3 用归纳证明加运行时受限输入验证覆盖结构性因果性，并用前后缀反例检验集成。未获可信来源、未封闭的算子和无法证明的系统隐式依赖返回 INSUFFICIENT。日线 EVENT 使用整日成交信息，输出只能声明日终估计成交；若要证明盘中订单的适应性，需接可观测事件流并核对逐单决策与成交时钟。

### 2.3 T2 · 截面不变性、鲁棒性与有效覆盖

| 检查 ID | 可执行口径 | 验收与证据 |
| --- | --- | --- |
| T2.EQUIVARIANCE | 对 `MathDerivationSpec` 声明的列组实施一致单位换算 $x'=a x+b$，$a>0$，同一价格组全部换算，不能只缩放 close。声明的合同为 `invariant/positive_affine/rank_preserving`，分别对 $f'=f$、$f'=a f+b$、同股截面平均秩/平局集合验算。对不同单位使用规范化误差（冻结尺度），输出同步检查行置换与同刻完整键。 | 系数初值 $a\in\{.1,10\}, b\in\{-1,1\}$ 乘预声明单位尺度，跨域输入由工件记原因。Z-score 在原始值正比例与平移、方差非零及相同截面下应不变。价格收益比只声明比例变换，模型若具固定金额阈值则同步变换阈值或排除平移。通过标准化误差≤$10^{-10}$ 且秩/掩码合同成立。 |
| T2.STRESS | 注入单变量极端值（分位数外 20 个 MAD）、将某 listing 标停牌、随机屏蔽 5%/10% 字段、由行业整块缺失模拟 MNAR，检查 stale、Inf、重复/重排行、同 ID 异单位、全常量列和全停牌。留出污染标的，在共同未污染样本比较平均秩 Spearman、有效样本及实际被裁剪比例。 | 成交模式停牌/未明状态禁止通过交易筛选，缺失原因和有效标的数须可查。常量、回归不可识别、极小样本导致指标不可定义时返回 null 与 reason。初始鲁棒性合同为共同样本 $\rho\ge .90$、单日覆盖下降不超过 .10，按假设与版本显式覆盖。 |
| T2.COVERAGE | 分母取当时实际应纳入且符合预登记市场状态的独立成员列表，包括原始报价缺失的成员。分子为通过时点、质量、有限值与版本检查的唯一 listing。报告 $N_{\mathrm{valid},t}/N_{\mathrm{eligible},t}$、$N_{\mathrm{eligible},t}/N_{\mathrm{universe},t}$、基准权重覆盖、逐日缺失归因和日期缺口。 | 基线 120 个成熟会话，最少 50 个有限标签配对用于全池 RankIC，至少 95% 应评价会话的因子覆盖≥.80。窄池或切片需自己冻结门槛。回归后查 $X^T W e$ 的规范化残差≤$10^{-8}$，保存有效秩、条件数、遗漏列与预处理阶段。若最终 rank 改变中性暴露，对最终值再验。 |

### 2.4 T3 · 数学守恒、风险与成交后验

**T3.PIT。** 对消费行及接入拒绝行独立重算时钟。金融基本事实须逐行满足 `report_period_end <= ann_date <= local_date(decision_at)`、`published_at <= decision_at`，公开时点的本地日与公告证据一致，实收回放额外检查 `published_at <= first_seen_at <= decision_at` 或有可审计的预接收封存规则。按发行人/指标/期间/会计口径/币种重放版本、有序且同序不可矛盾，最后可见撤回是 tombstone；当前系统的数据质量状态映射为 `observed/reconstructed/stale/unknown`。证据报告 `violation_count=0` 及期望键召回率。成分和标识解析必须共用 as-of 入口，预先公布的未来成分仅能作未来计划，不能加入当前投资池。固定 taxonomy 和同市场会话，禁用借全局排序或 ROW_NUMBER 掩盖坏键。

**T3.RISK / T3.WEIGHTS。** 对 solver 实际使用的 $X,F,s,w,b,w_0$ 用稳定 listing 顺序独立复算。协方差 $\Sigma=XFX^\top+\operatorname{diag}(s)$，要求有限、对称和 PSD（$\lambda_{\min}\ge0$）；数值比较容差 $\tau=10^{-10}\max(1,\|\Sigma\|_2)$，并核对 $F$ 也 PSD、$s_i\ge0$。需严格正定的调用单独做 Cholesky 和条件数检查。保留修复前后最小特征值、相对 Frobenius 修复幅度、估计期样本量与缺描述符掩码，修复后的 PSD 不代表估计合格。风险份额使用 $c_j=\beta_j(F\beta)_j$、$\beta=X^\top(w-b)$，特异项 $\sum_i s_i(w_i-b_i)^2$，独立核对加总到 $TE^2$，允许贡献为负。样本外报告滚动期的预测/实现 TE 比及超出预测方差事件，以免用正定替代风险校准。

完全投资目标 $\sum_i w_i=1$，允许现金时把 $w_{\mathrm{cash}}$ 纳入单纯形，$\min_i w_i\ge0$，预算误差≤$10^{-8}$。成交后费用以同一净 NAV 为权重分母，仓位锁、行业/风格/单股上限、PoV、整手、原 TE 锥、实际单边换手 $\frac12\|w-w_0\|_1$ 分项核对，手续费和现金按账户账本校验。申请约束与有效约束分别保存，自动放宽仅能产出新 policy 下的研究结果，原门禁保留 FAIL。若声明最优，输出可独立验算的原始/对偶可行性、互补与 KKT 残差（MAX_SHARPE 子问题保存比值与锥可行性上下界），否则只声明可行。优化频率须与收益、风险和成本同单位。成本前置的凸替代可取单期 $\max_w a^T\Delta w-\gamma(w-b)^T\Sigma(w-b)-\sum_i[k_i|\Delta w_i|+\eta_i|\Delta w_i|^{3/2}]-\lambda\sum_i|\Delta w_i|$，其中 $\Delta w=w-w_0$、$\eta_i=Y_i\sigma_i\sqrt{A/(P_iADV_i)}$，由 `cvxpy.power(abs(delta), 1.5)` 表达，选支持相应锥的求解器。受 PoV、手数和交易日规则限制的组合使用执行回放复核。年化卡片依实际会话和再平衡频率计算。

**T3.LEDGER。** 新增 `Decision, ParentOrder, Fill, JournalPosting, PositionLot, DailyMark, CorporateEvent` 工件，使用 `(run_id, event_id, listing_id, currency, event_at, seq)` 的适用子集和稳定订单关系。金额用 Decimal 或最小货币单位整数，股数用整数及明确零股/拆股规则。逐事件借贷相等并核对：

$$
\begin{aligned}
\Delta q_i &= \sum_f q_f^{\mathrm{signed}} + \Delta q_i^{\mathrm{corp}},\\
\Delta AR &= \text{ex-accrual}-\text{settlement},\\
\Delta Cash &= -\sum_f q_f^{\mathrm{signed}}P_f-\sum_f fees_f+\text{settlement}+\text{external flow},\\
NAV &= Cash+AR+\sum_i q_i P_i^{\mathrm{raw\,mark}}.
\end{aligned}
$$

现金/应收按约定分位舍入后逐币种精确对账，另由日初 NAV、逐项现金流与市值变化独立复建日终 NAV。公司行动区分权益登记、除权、到账和可卖锁，检测重复入账、冻结标的错误成交、超卖、负现金与陈旧 mark。执行冲击已经进入 $P_f$，机会损益单列，费用分项中现金另扣显式费用。对以开盘为参考价的日线估算单列日终可用标签，禁止把事后可用值喂回策略。复现样例是历史只传 symbol 的信号在附加以后同名 listing 后会报歧义，故 V3 信号必带 `listing_id`。影子同样用独立账本核对
$IS=s[\sum_f q_f(P_f-P_{\mathrm{dec}})+(Q-\sum_f q_f)(P_{\mathrm{end}}-P_{\mathrm{dec}})]+\sum_f fee_f$，并和 delay / liquidity / unfilled / fees 相加，$s=+1$ 买、$-1$ 卖，$q_f\ge0$。 [Perold](https://doi.org/10.3905/jpm.1988.409150)

**T3.ATTRIBUTION。** 在互斥完备行业分组（含现金）上，以期初持仓、总收益含权息计算 Brinson-Fachler。设 $P_g,B_g$ 为组内策略和基准回报，$B$ 为全基准回报，$w_{P,g},w_{B,g}$ 为期初组权重：

$$
A_g=(w_{P,g}-w_{B,g})(B_g-B),\quad
S_g=w_{B,g}(P_g-B_g),\quad
I_g=(w_{P,g}-w_{B,g})(P_g-B_g).
$$

空基准组采用预声明代理，零权重时仍需可复核组件归属，行业总和与股票层的期初主动回报核对。另基于成交和权益日志独立计算交易时差、权益差及执行费用，再用指数与同成分重建基准之差作显式 bridge。Barra 以点积独立核对风格、行业、特异及费用；当前将时差和基准 bridge 合入 selection/specific，V3 产物单列以定位偏差。日频恒等式和累计闭合均要求绝对误差≤$10^{-4}$（回报小数，即 1 bp），存 `expected, actual, residual_return, residual_bp`。令 $p_t,b_t$ 为日净策略和基准收益，$k_t=[\log(1+p_t)-\log(1+b_t)]/(p_t-b_t)$，相等用 $1/(1+b_t)$。累计 $P_*,B_*$ 同法求 $k_*$，用 $L_t=k_t/[k_*(1+B_*)]$ 链接至 $(1+P_*)/(1+B_*)-1$。凡 $p_t$ 或 $b_t\le-1$ 时停用对数链接并给可复核失败。 [Brinson 与 Fachler](https://doi.org/10.3905/jpm.1985.409005)

### 2.5 T4 · 统计证据、搜索偏差与参数悬崖

**统一 estimand。** 在冻结 OOS 且经过成熟度判定的每日序列上，$IC_t=\operatorname{corr}_{\rm Spearman}(f_{i,t},r_{i,t:t+h})$，使用平均秩处理并列。报告 $\overline{IC}$ 及 V2 可比的 $ICIR=\sqrt{252}\,\overline{IC}/sd(IC)$，重叠标签的推断由 HAC/Bootstrap 描述。设 $p_t,b_t,r_{f,t}$ 为净策略、基准和无风险日收益，年化相对净超额 $\alpha_{\rm ann}=\operatorname{expm1}\{252\overline{[\log(1+p_t)-\log(1+b_t)]}\}$，$SR=\sqrt{252}\,\overline{(p-r_f)}/sd(p-r_f)$，$IR=\sqrt{252}\,\overline{(p-b)}/sd(p-b)$。收益使用小数，百分数和 bp 仅在输出边界转换。初始发布只对同一市场会话、币种和费用合同计算，未给无风险基准时标 Sharpe 缺证据。缺价格不要前向填成零实际收益，策略账本已判定持有现金才用对应现金回报。$sd=0$ 或支持样本不足返回 null。

**T4.HAC / T4.BOOTSTRAP。** 保留现有 Bartlett Newey-West，并把长程方差、lag、有效样本、均值 SE 和 CI 存档。对连续日历用 $\hat v=\hat\gamma_0+2\sum_{\ell=1}^L(1-\ell/(L+1))\hat\gamma_\ell$，$L=\max(h-1,\lfloor4(n/100)^{2/9}\rfloor)$ 并受可用会话上界约束。因子日的有效位 $m_t$ 和 $m_t IC_t$ 在交易日历上配对，以比率估计 $\hat\mu=\sum m_t IC_t/\sum m_t$，HAC 输入 influence series $m_t(IC_t-\hat\mu)/\overline m$，缺 IC 日不伪装为真实 0，继续保留真实时间间隔。HAC 带宽敏感性随证据发布。 [Newey 与 West](https://doi.org/10.2307/1913610)

Stationary Block Bootstrap 在长度 $n$ 的原交易日历上统一生成日期索引。首个索引均匀抽取，后续以 $1/\ell$ 概率均匀重启，其余取上一索引 $+1\bmod n$，期望块长 $\ell$。同一 draw 对 IC 与掩码、净策略/基准/risk-free、全部候选及切片贡献共用索引，保留同日横截面和候选相关。首发用 $\ell=\max(h,\lceil n^{1/3}\rceil)$、对 $\max(h,\lceil\ell/2\rceil)$ 和 $2\ell$ 做敏感性，须有至少 120 会话、至少 10 个期望块及预登记的有效覆盖；不足或在明显结构断点下给分时段结果及 INSUFFICIENT。以 `numpy.random.Generator(PCG64)` 批量生成，按预登记固定次数，原始分布重算目标统计量取 2.5%/97.5% 分位。零阈检验分别对 IC、策略超无风险收益或 log 相对收益做中心化，重采样后重算 RankIC 均值、ICIR、Sharpe、几何净超额统计量，按登记方向取尾，双侧取绝对值尾。非零阈与两策略差使用相应的中心化统计根及配对 draw，预登记近似口径，不能以 CI 覆盖规则替代多重校正。报告 $p=(1+\#\{T_b^*\text{ 至少同样极端}\})/(B+1)$、seed、块长、原始/有效 draw 数和 Monte Carlo 误差。 [Politis 与 Romano](https://doi.org/10.1080/01621459.1994.10476870) · [Ledoit 与 Wolf 的 Sharpe 检验](https://www.ledoit.net/jef2008_abstract.htm)

**T4.CPCV / PBO / DSR。** 每个 observation 登记 `feature_cutoff, label_start, label_end, label_known_at, market_session_id`，以实际标签区间（共享端点报价也冲突）对测试区间做独立区间扫描， 再于连续测试块的最后标签端点之后 embargo。预处理、风格投影、收益标定、模型选择在获准的训练/内验证 fold 重拟合，训练标签及产物可得性写入 `FoldManifest`。CPCV 是组合切分稳健性试验，某些 fold 可以用相对测试较晚的训练，实际可部署证据由逐日期因果走步与最后隔离窗口给出。已冻结策略矩阵可保留 V2 的路径分配方法，保存每路径逐日净收益及换策略的过渡成本，不能无成本拼接仓位。压力路径报告分位数、负复利路径占比，首发最多 .20；与 V2 负 Sharpe 路径计数分别命名。至少给一段连续、从未参与调参的确认 OOS，固定样本不足时证据不足。

预测候选 CSCV 的 PBO 用同窗、共同日历、净收益列，保留去除精确重复列、胜者索引、OOS 秩和平分规则；无两个可比非退化候选时 PBO 缺证据。DSR 使用同频率日 Sharpe $s$、样本量 $n$、偏度 $\gamma_3$、普通峰度 $\gamma_4$，可复用当前公式：
$$
s_0=\sigma_{s,\mathrm{trials}}\left[(1-\gamma)\Phi^{-1}(1-1/M)+\gamma\Phi^{-1}(1-1/(eM))\right],\qquad
DSR=\Phi\left(\frac{(s-s_0)\sqrt{n-1}}{\sqrt{1-\gamma_3s+(\gamma_4-1)s^2/4}}\right).
$$

$\gamma$ 为 Euler 常数，$M=1$ 用预先声明的基准 Sharpe，默认 0。候选同窗输入、收益频率、方差估计样本与可见性写入 artifact。$\sigma_{s,\mathrm{trials}}$ 来自同口径试验日 Sharpe，完整台账计数为主，依赖性修正后的有效试验数可列敏感性。缺完整可比历史时不把幸存候选的离散度当完整估计，超出 DSR 近似假设的序列联合报告 HAC/Bootstrap，路径互相关时不可把每条路径作为独立 p 值。首发沿用 PBO≤.25、DSR≥.95、OOS/IS≥.50 与零 purge 冲突，负路径按新口径另报。 [DSR 原文](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf) · [PBO/CSCV 原文](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)

**T4.LEDGER / FDR。** 保留 `ExperimentRecord` 作兼容投影。新增追加式 `trial_reserved, evidence_committed, terminal`，每条含全局单调 `seq`、`trial_id, parent_trial_id, definition_sha256, data_view_sha256, search_family_id, holdout_id, primary_metric_id, direction, status, evidence_sha256` 与事件时点。看任何结果前原子登记假设及参数，每个影响结果的变体、筛选过的候选、已淘汰/中断的试验均计入 $M_{\text{total}}$，相同内容和环境的执行重试重用 ID，保留事件。发布者绑定期末 ledger cut 哈希，截止后的条目进入下一确认周期。迁移对历史用只读枚举及 `legacy_completeness` 标注，无法还原历史母数时阻止声明历史全量 FDR，主线上另开真正锁定的确认窗口。

同一确认窗按冻结历史台账为每个登记的主假设分配一个槽位，$M=M_{\text{total}}$。没有有效确认 p 的槽位以 1 进入整批校正，原始 p 留 null，不把已有探索 p 搬到确认窗口。固定一项主要检验与方向，若研究计划允许从多个端点中挑赢家则登记每个独立测试槽位或事先做组内调整。排序计算 $q_{(i)}^{BH}=\min(1,\min_{j\ge i} M p_{(j)}/j)$，文档的 q-value 指 BH 调整 p。独立或正回归依赖（PRDS）下用 BH，依赖未知用 BY（以 $M\sum_{j=1}^M1/j$ 替换 $M$）控制当期批次，默认 $q\le .05$ 为主门禁，BH 仍报告。反复查看同一确认窗会消耗推断有效性，每次确认冻结批次且限定一次解盲；跨滚动确认批次若要承诺全时段 FDR，须另实现明确的在线 FDR/全局预算，不能把重复的批内 .05 解释成永久承诺。最低 p 分辨率 $1/(B+1)$，预登记的算力至少可测试最严格秩 1 门槛（BH 约需 $B+1\ge M/.05$，BY 加 harmonic factor），否则报分辨率不足并保留结果。 [Benjamini–Hochberg](https://doi.org/10.2307/2346101) · [Benjamini–Yekutieli](https://doi.org/10.1214/aos/1013699998) · [Harvey、Liu 与 Zhu](https://academic.oup.com/rfs/article-abstract/29/1/5/1843824)

**T4.SPA / WHITE_RC。** 对同一确认窗、事先登记的可比候选，构造 $d_{t,j}=\ell_{t,0}-\ell_{t,j}$，净收益型损失可预设为 $-\log(1+r^{net})$，统一 benchmark 和费用。检验 $H_0:\max_j E[d_j]\le0$。White RC 使用 $T=\max_j\sqrt n\,\bar d_j$，所有列零中心后联合重采样。SPA 用 $\max(0,\max_j\sqrt n\,\bar d_j/\hat\omega_j)$，$\hat\omega_j$ 来自同一 Stationary Bootstrap 长程方差（或直接 $\sqrt{n\operatorname{Var}_*(\bar d_j^*)}$ 的冻结先导 draw），一致重心为 $\hat\mu_j=\bar d_j 1\{\sqrt n\bar d_j/\hat\omega_j<-\sqrt{2\log\log n}\}$。对每个 draw 用 $d_{I,j}-\bar d_j+\hat\mu_j$，保持候选相关。不把证据不足列临时从家族消隐，事先区分不可能观测的列、完全同基准的常量零列与其他退化列，并记原因。主门禁 SPA≤.05，White RC 作为按同基准的保守敏感性证据。两个都是家族全局检验，入库还须过单个候选 FDR。保存损失、列映射、方差、共同索引及失败/排除工件。 [Hansen SPA](https://doi.org/10.1198/073500105000000063) · [White Reality Check](https://doi.org/10.1111/1468-0262.00152)

**T4.CLIFF。** 预登记中心及每个可扰动数值参数的 ±10%、±20%，整数窗口 half-up 取整、合法域去重，零值超参用预声明绝对步长。另在最关键两参数上取四个 ±20% 角点以覆盖交互；对 `daily/weekly/monthly` 使用预声明相邻枚举。各扰动共享 OOS 时间、基准、初始规则和统一 block 索引，按 fold 重新 fit，实际结果不能反馈进已冻结的确认窗选参。主性能 $S$ 用年化净超额（策略）或方向对齐 RankIC（因子），对参数相对位移 $u$ 存 $g=(S(u)-S(0))/|u|$ 和 $D=(S(0)-S(u))/\max(|S(0)|,\epsilon_S)$，$\epsilon_S$ 为规范里的经济意义下限。主基线低于最小效果时直接给不足/失败而不解释百分比。首发判 cliff 为两个相邻扰动点 $D>0.50$ 且配对 95% 区间确认绝对衰减超过预登记尺度，符号翻转另列，缺有效双侧邻点给边界证据不足。汇报最陡梯度、敏感参数、样本量、变体 ID、热力表与费用差分。参数审计失败可以支持下一轮假设，不能在同一确认窗挽救已失败候选。

## 3. MathDerivationSpec · 把研究假设变成能证伪的合同

`FactorHypothesisRequest` 当前只存说明与表达式。V3 提交必须先冻结原始假设、实际会运行的表达式或注册策略实现、数据依赖、效果区间、原假设、失效市场状态及证据接受阈值，生成 `derivation_sha256` 和 `trial_id`。LaTeX 是可读表示，执行端只认已验证 DSL 或注册的有类型 DAG，按 AST/DAG 的规范序列化生成执行 hash。注册的内置因子描述需要与真实分支一致，例如 `alpha_vp_corr_20d` 实际有复权价逻辑，`alpha_earn_sue_pit` 的现金质量用正权益条件，展示公式不能代替可执行版本。阈值、单位和标签端点改变均生成新试验。

以下定义复用 `Contract` 等公共类型。`metric_id`、`unit`、`slice_id` 对版本化的指标和切片目录外键校验。除数零、负数取 log、warm-up、标签熟化、缺字段策略必须由目录中的算子和观测合同驱动。

```python
PredicateOp = Literal["lt", "le", "gt", "ge", "eq"]

class MetricPredicate(Contract):
    metric_id: Key
    slice_id: Key = "all"
    unit: Key
    op: PredicateOp
    threshold: FiniteFloat
    min_sessions: Annotated[int, Field(gt=0)]

class FormulaSpec(Contract):
    latex: Key
    executable_kind: Literal["factor_dsl", "registered_dag"]
    executable: Key
    implementation_sha256: Sha256
    input_schema_sha256: Sha256
    output_unit: Key

class InputTransformSpec(Contract):
    columns: tuple[Key, ...] = Field(min_length=1)
    operation: Literal["scale", "shift", "positive_affine"]
    expected_relation: Literal["invariant", "positive_affine", "rank_preserving"]
    unit_scale: Annotated[FiniteFloat, Field(gt=0)]

class AssumptionSpec(Contract):
    assumption_id: Key
    rationale: Key
    predicate: MetricPredicate
    gate: Literal["required", "diagnostic"]

class EffectSpec(Contract):
    metric_id: Key
    slice_id: Key = "all"
    unit: Key
    direction: Literal["positive", "negative", "two_sided"]
    min_abs_effect: Annotated[FiniteFloat, Field(gt=0)]
    plausible_low: FiniteFloat
    plausible_high: FiniteFloat

    @model_validator(mode="after")
    def validate_effect(self) -> Self:
        if self.plausible_low > self.plausible_high:
            raise ValueError("reversed effect range")
        if self.direction == "positive" and self.plausible_low < self.min_abs_effect:
            raise ValueError("effect range violates positive hypothesis")
        if self.direction == "negative" and self.plausible_high > -self.min_abs_effect:
            raise ValueError("effect range violates negative hypothesis")
        if self.direction == "two_sided" and max(
            abs(self.plausible_low), abs(self.plausible_high)
        ) < self.min_abs_effect:
            raise ValueError("effect range is smaller than minimum absolute effect")
        return self

class FailureModeSpec(Contract):
    failure_mode_id: Key
    economic_rationale: Key
    activation: MetricPredicate
    expected_when_active: MetricPredicate

class MathDerivationSpec(Contract):
    schema_version: Literal["3.0"] = "3.0"
    hypothesis_id: Key
    economic_hypothesis: Key
    universe_id: Key
    benchmark_policy_id: Key
    decision_clock_id: Key
    label_spec_id: Key
    formula: FormulaSpec
    assumptions: tuple[AssumptionSpec, ...] = Field(min_length=1)
    primary_effect: EffectSpec
    secondary_effects: tuple[EffectSpec, ...] = ()
    failure_modes: tuple[FailureModeSpec, ...] = Field(min_length=1)
    transforms: tuple[InputTransformSpec, ...] = ()
    preregistered_at: AwareDatetime
```

**证据判定。** 从 proof/result artifacts 按 `(metric_id,slice_id,unit)` 取数，查样本和指标版本。输入条件例如 `PIT.violations eq 0` 用确定性比较；有不确定性的阈值至少同时记点估计、95% CI 与支持日数。CI 完全落在符合区间给 `supported`，完全落在反方向或达不到经济下限给 `violated`，跨阈给 `uncertain`。有向效果要求 CI 通过相应阈值且 T4 多重检验通过，双侧要求 CI 在零两侧的经济意义区间之一。超出预期量级区间给幅度偏离并保留方向结果。状态规则以 activation 的可观测标签筛选，未触发标 `not_observed`，触发但日期不足标 `insufficient`，满足前提后逐项比较预测表现。所有状态保存 `assumption_id/failure_mode_id, observed_value, interval, unit, proof_check_ids, evidence_ref, slice_id`。经济理由可结构化回读，结论只适用于映射到可观测指标的推论。

例如某流动性供给假设预登记在正常时段 5 日 RankIC≥.03，且低流动性/高波环境中扣费后净超额≤0。执行定义可用同证券滞后报价输入，声明报价与复权时钟、正价格与最小月度覆盖。若全样本有正 Alpha 而高波低流动性样本不足，主效应与失效预测各自保留证据状态。相反，若高波低流动性净超额 CI 全为正，失效预测被证伪，保留原假设与新解释的修订链。

## 4. DimensionAnalysis · 共享的市场基线与集中度证据

**基线独立于候选。** 创建版本化 `DimensionBaseline`，键为 `(baseline_id, market, universe_id, benchmark_id, decision_at, listing_id?)`。绑定快照 SHA、交易会话、`SW2021` taxonomy、边界策略、特征代码、最早可用时间及各层缺失规则。同一候选比较组用同一基线。市场态由决策时已知的基准总收益和日历驱动，资产态以信号或成交前已知的资产元数据标记。收盘后状态从未来可交易会话起使用。窗口仅向后，计算阈值时排除待分类观测，滚动分位等值按固定规则分段，记录多人同值时的实际占比。基线先生成并通过 T1，再与收益、持仓及成本按键连接。

| 维度 | V3.0 首发定义与实现 | 分解口径 |
| --- | --- | --- |
| 牛 / 熊 / 震荡 | 以基准 60 会话总收益 $R_{60}$ 和 120 会话高水位回撤 $DD_{120}$。优先熊态触发 $R_{60}\le-.08$ 或 $DD_{120}\le-.15$，牛态触发 $R_{60}\ge.08$ 且 $DD_{120}>-.10$。未触发时，已是熊且满足 $R_{60}<-.03$ 或 $DD_{120}\le-.10$ 之一时维持熊，已是牛且 $R_{60}>.03$ 且 $DD_{120}>-.12$ 时维持牛，其余震荡。两条 trigger 优先于维持规则，warm-up 不足为 Unknown。 | 每日唯一状态、记录转换原因。不同历史的候选共享同键，不按策略曲线事后改状态。阈值作为首发项目配置。 [Two Sigma 公开市场状态研究](https://www.twosigma.com/articles/a-machine-learning-approach-to-regime-modeling/) |
| 市场波动低 / 中 / 高 | 近 20 交易日基准日回报标准差，基准为该时刻前 252 个已知的同指标历史值的 1/3 与 2/3 分位，至少 126 个有效历史值，否则 Unknown。分位重合时依预声明等值规则归中档并标退化。 | 单日全池共享一个波动状态，跨市场各自基线。若另研究个股低/高波，另定义 `asset_vol`，保留 key 区分。 |
| 大 / 中 / 小盘，高 / 低换手 | 自由流通市值在当时合格截面按日 1/3、2/3 分位。换手以 20 会话换手率中位数按日横截面二分，缺 `turnover_rate` 时可用 PIT 自由流通股数与成交股数重建并记来源；金额 ADV 列另供容量。同值归中盘或按登记的中位边界固定分组。 | 分组以可见成员与稳定 listing 键，任一分桶变量不可信即 Unknown。统计边际维度的同时生成市值×市场波动的互斥交叉单元，用于定位小盘高波集中度。 |
| 申万一级与 Barra 风格 | 申万使用当时有效且已发布的 `taxonomy=SW2021` 和稳定行业代码。风格以滞后 $X_{t-1}$ 与当期持有权重求主动暴露 $X^\top(w-b)$、风格贡献 $\beta_j f_{j,t}$，报告标记为 CNE6 风格代理的字段与缺数据率。 | 行业支持互斥贡献和分桶收益，风格因子之间可重叠且贡献可能为负。风格 beta 是否主导，另做期初暴露、行业/风格中性 challenger 和残差 OOS。 |

**指标与样本。** `DimensionAnalysis` 输入 `baseline_id, proof_bundle_sha256, return_run_id, factor_panel_id, partition_id, label_spec_id`，输出 `DimensionSliceResult`，字段含 `slice_id, predicate_version, session_count, episode_count, name_session_count, coverage, rank_ic_mean, sharpe_ann, win_rate, net_excess_ann, linked_contribution, ci95, status, evidence_refs`。时间切片用状态日的全池 RankIC 和日策略收益，横截面切片先用同一组内拥有成熟标签的股票算每日 RankIC，再在有效日上聚合。横截面 Sharpe、胜率、净超额使用日策略与基准两侧的独立期初组资本及对应净 P&L 计算，含归属该组的执行成本，分别除以各自期初组资本；需要两侧期初资本为正、资本轨迹面向同一持有期。否则对应比率为 null，仍可报告可加总贡献。胜率为主动收益为正的有效日比例，净超额及 Sharpe 沿用 `2.5` 的年化定义，时间子样本标为「状态内年化」。Bootstrap 用完整日历联合抽样再掩码，避免拼接不同状态时段视为相邻。初始每切片至少 60 个有效会话、60% 合格会话覆盖，RankIC 每日至少 20 对，时间状态至少经历 3 个不相连的事件段。未达标仍报样本和点估计（如可定义），不进入确定性结论和 ConsistencyScore 计数。

**Regime Consistency Score。** 对某个预登记、互斥完备的时间状态集合 $G$ 及主效应方向，定义方向对齐后的效应 $\theta_g$、经济下限 $\delta_g$。将证据合格的单元记为 $V$，$C=|V|/|G|$。发布
$$
RCS=C\,\frac{1}{|V|}\sum_{g\in V}1\{\operatorname{CI}_{95\%,L}(\theta_g)>\delta_g\},
$$
空 $V$ 时返回 null。逐主状态等权，防止牛市占用多数会话主导结论，同时展示 $C$、状态下行尾部、方向冲突、按期划分的留一状态分析和 pooled 指标。若预期失效状态由研究规范声明，单独展示其证伪结果和剔除它的支持域 RCS，保留全状态 RCS。

**Dimension Dominance Warning。** 日贡献保存在 `(decision_at, listing_id, contribution_type)`，股票按当时的状态和其标签映射到互斥交叉组，未分配 bridge 或现金有显式单元。令 $C_g=\sum_t L_t c_{g,t}$，同一 partition 强制 $\sum_g C_g=\alpha_{\rm cumulative}$（误差≤1 bp）。集中度取 $D_g=\max(C_g,0)/\sum_h\max(C_h,0)$，各组正贡献和须超过预设 1 bp。另报 signed share $C_g/\sum_h C_h$ 和负贡献抵消量，总净接近零时 signed share 为 null。不同分区的边际份额不可加。最大单元 $D_g\ge.70$、证据覆盖达标且联合日期 bootstrap 的最大值校正后下界≥.50，触发 `DIMENSION_DOMINANCE`；若删去该单元后其他贡献的净额非正或 RCS<.50，提高等级。校正可由共用 Bootstrap draw 对所有 $D_g-\hat D_g$ 的最大绝对值取 95% 分位建立同时带。若有 +82、+18 和 −20 bp 三组，第一组贡献了 82% 的正贡献，总净为 +80 bp。报告应写「正贡献 82% 来自小盘×高波，剔除此单元后为 −2 bp」，并附 CI、分区、样本和来源 SHA。该数字是闭合测试用例。检验替换交易风格需要用原信号屏蔽该单元并重跑，不能从归因的代数删除推出交易反事实。

## 5. Capacity & Market Impact Frontier · 六档净超额与 IR

### 5.1 单位一致的执行模型

以同一研究策略、确认 OOS 日期、基准、信号时钟、初始持仓规则及成本校准版本为条件，分别以 **1,000 万、5,000 万、1 亿、5 亿、10 亿、30 亿 RMB** 启动独立账户。每档用该账户成交后的仓位与当时 NAV $A_t$ 推导母单目标 $Q_{i,t}=A_t|\Delta w_{i,t}|/P_{i,t}^{ref}$（股）。$P^{ref}$ 是下单时可见的参考价，$ADV_{i,t}$ 与 $Q$ 同为股，以前 20 个可用会话估计并记录缺报和停牌政策，$\sigma_{i,t}$ 是同频前置日波动。成交交给版本化的整手、T+1、现金、板价、停牌、母单寿命及订单排序规则。

$$
\frac{\Delta p}{p}=Y_{i,t}\sigma_{i,t}\sqrt{\frac{Q_{i,t}}{ADV_{i,t}}},\qquad
\sum_{\text{该标的该窗口}}\operatorname{fill\_shares}
\le \rho_{i,t}\operatorname{observed\_market\_shares}_{i,t}.
$$

计划时的 PoV 使用可得量或滞后预测值，验证时使用实际成交节奏内已知的市场量，所有共用成交容量的母单累加。只有日线时可使用全日量作为日终上界，结果标签 `daily_bar_replay`。保存已知的 partial fill、撤单、超限、未成机会成本和等待时长。未成交母单不计已执行现金成本，未成导致的持仓差通过回放自然影响收益。冲击价格偏移以买正卖负施加，不能因限价截断后无约束全额成交。支持至少显式的买卖佣金、适用税费、过户费、半价差和冲击，逐日有效的费用表覆盖合约级最低费与币种。显收费按实际成交名义额或条款计，冲击计入成交价，另列分解不重复扣减。

$Y$ 用冻结期之前、只读导入的真实父单/成交估计。响应为签名后的平均执行价与到达价差，先按独立标记拆费、半价差和延迟，再以 $\sigma\sqrt{Q/ADV}$ 为自变量做稳健回归。按买卖方向、流动性、成交时段或状态分桶，样本稀疏向总体收缩并保存覆盖、残差、留出误差及 CI。拟合平均执行冲击可直接取 $Y_{\rm avg}$。若 $Y$ 代表终点冲击且假设等价的连续平方根轨迹，积分平均为 $2Y/3$，两参数口径须有独立类型字段。V2 的 `impact_coeff_y=.45` 可当未校准场景参数，`calibration_status=uncalibrated` 只支持情景分析。缺 ADV、波动、规则或区间外 PoV 时不得把影响置零。报告 base、保守 Y（如校准 P90）、ADV 下调 30% 与联合压力，并明确未匹配数据比例。 [平方根冲击原始研究](https://doi.org/10.1103/PhysRevX.1.021006) · [基于母单的适用范围研究](https://arxiv.org/abs/1412.2152) · [AQR 的成交成本与规模研究](https://www.aqr.com/insights/research/working-paper/trading-costs-of-asset-pricing-anomalies)

### 5.2 解析对照、六档运行与容量根

固定 $\Delta w$、无 PoV/整手/价格限制且全成时，单日冲击金额为 $\sum_i P_i Q_i Y_i\sigma_i\sqrt{Q_i/ADV_i}$，故
$$
\frac{C_{\rm imp}(A)}{A}=\sqrt A \sum_i\frac{Y_i\sigma_i|\Delta w_i|^{3/2}}{\sqrt{P_iADV_i}}=\kappa\sqrt A.
$$
在年化小收益线性近似和固定摩擦 $c$ 下，$\alpha_{\rm net}(A)\approx\alpha_g-c-\kappa\sqrt A$，$\kappa>0$ 且 $\alpha_g>c$ 时解析 $A_{\rm BE}\approx[(\alpha_g-c)/\kappa]^2$。这给端到端单位测试。例如名义资金放大 4 倍时单位资金冲击为原来 2 倍，冲击金额为 8 倍。生产回放中 $w$, 成交比例和路径均随 AUM 变动，分档以账户净 NAV 与基准计算 `2.5` 的 $\alpha_{\rm ann}$ 和 $IR$，双输出可选冻结目标与相同规则的容量感知优化版本，分别建曲线。

工件 `CapacityFrontier` 绑定 `root_run_id, snapshot_manifest_sha256, holdout_id, execution_model_id, calibration_id, threshold_policy_id`。每个 `CapacityPoint` 存 `aum_rmb, net_excess_ann, ir_ann, net_excess_ci95, ir_ci95, gross_excess_ann, impact_bps, explicit_cost_bps, turnover, fill_rate_notional, p95_pov, overlap_days, median_delay, bottleneck_listing_ids, valid_status, evidence_ref`。年化或占成交成本分母在字段表声明，压力场景有独立 case ID。把六档连线和 $A/A_0$、$\alpha_{\rm ann}(A)-\alpha_{\rm ann}(A_0)$、$IR(A)-IR(A_0)$ 一起交付，日期块在所有档位共享，并按同一 bootstrap 路径重算阈值或区间。

- **Breakeven AUM** 是从基准 $A_0=10^7$ 元起的连续有效区间内，年化净超额首次达到或跌过 0 的规模。**Optimal Capacity** 是首次达到或跌过 $0.5\,\alpha_{\rm ann}(A_0)$ 的规模，定义为 50% 净超额衰减阈值。
- 若基准超额弱于 0 或 CI 不支持正值，给 `baseline_non_positive` 或 `baseline_uncertain`。在各档寻找第一个越界括号，按对数规模中点重新运行精化，首发到区间宽度≤5% 或最多 12 次。局部可证单调且连续时才对重放值用 `scipy.optimize.brentq`。整手跳变、回升或噪声导致无法证单调时报告首个可观测 crossing 区间及后续回升，状态 `interval/non_monotone`。高于 30 亿仍未越界时报告右删失，首次容量不可实施则给 `feasibility_limited` 并保留前一个可行规模。Bootstrap 中删失保留比例，不能只用有根的 draw 构造 CI。
- 容量输出另存运营上限。初始规则为名义成交率≥.95、PoV 零违规、市场日覆盖≥.95、成交延迟 p95 不超过预登记的母单寿命。将净收益阈值、执行阈值和风险阈值与各自 CI 联合报告。数据只支持有界情景时输出范围和最坏场景，不颁发可交易容量证明。

## 6. DiagnosisAndGuidanceEngine · 规则树和行动证据

`diagnose(bundle, derivation_outcomes, dimension, frontier, ledger_cut) -> tuple[DiagnosisItem, ...]` 读取同一 `snapshot_manifest_sha256/holdout_id` 的证据。每个 `DiagnosisItem` 有 `root_cause, rule_id, rule_version, severity, status, proof_check_ids, observation_refs, observed_values, slice_ids, suggested_configs, acceptance_check_ids, counterexample_keys`。输出使用固定模板与已登记单位，规则参数放在 policy，LLM 至多渲染表述。按 P0 integrity → 数据证据 → 统计充分性 → 执行归因 → 稳定性/共线顺序执行，记录所有满足条件的规则。前驱缺证据时只给有证据的诊断及阻塞原因，已知前视和账务不平优先阻止下游效果解释。

| 根因及规则 | 触发证据，阈值为首发值 | 下一步试验和验收 |
| --- | --- | --- |
| 前视泄漏 `D01` | `T1.PREFIX / SUFFIX / OPERATOR` 失败且见键、掩码或最大差值，或 `T3.PIT.violation_count>0`。分别列首个违法输入的公开/实收时点与决策时点。 | 自动生成最小复现场景并定位到操作符、join 或字段；把全样本变换改为尾随或决策时刻截面，财报改完整版本流。重放反例和全量 T1/T3，修正后新登记。 |
| 风格 Beta 伪装 `D02` | T3 归因闭合，风格主动暴露越限或集中，正净超额中风格贡献至少 .60，且同折同成本的中性 challenger 的残差 OOS 主效应 CI 不满足最小效果。两个条件的切片及 benchmark 相同。 | 开 `neutralize_size=True`、`neutralize_industry=True`、`orthogonalize_barra=True`，按证据指定 Size/Beta/Residual Vol/申万约束，用 `standardize="zscore"` 保留线性残差性质，再查正交与成本。对仓位可把 `max_size_z`、`max_style_z` 降至原值一半并重新验原 TE 和 turnover 合同。 |
| 多重检验过拟合 `D03` | `T4.LEDGER` 完整前提下，探索样本过门但确认的 BH/BY q>.05、DSR<.95 或 SPA>.05，结合高 PBO / `T4.CLIFF` 给依据。单纯统计不显著记「过拟合风险」且需区分 power 不足。 | 去掉数据驱动筛选，预注册一条经济主假设及邻域，减小搜索自由度，开新隔离窗。合法证据反向或稳定达不到经济下限时入墓碑；缺历史台账时先补齐或留作 `insufficient`。 |
| 换手冲击吞噬 `D04` | 毛超额为正，含费用回放净超额非正或可归属执行摩擦吞噬毛超额≥.50，容量/成交日志和 `T3.LEDGER` 对账通过。区分佣金、价差和冲击。 | 固定基准，比较 `turnover_lambda=0.60/0.90/1.20`（使用规范化同周期目标），`rebalance_freq=weekly/monthly`，降低 `max_pov` 做压力对照。在净超额、IR、成交率及 exposure 硬约束全部通过时保留候选，记录单位成本分解。 |
| 单一 Regime 依赖 `D05` | `DIMENSION_DOMINANCE` 报警并有低 RCS/留一贡献非正支持，字段包含主状态、样本、CI 与状态标签决策时间。 | 复核假设失效区，冻结时点可知的状态门控、分桶中性，或限定研究适用域。用新登记和留一整段状态回测复验净超额、覆盖率和原交易成本。 |
| 流动性容量不足 `D06` | 目标资金规模违反 PoV/成交率合同、保守场景容量下界，或低流动性 slices 上净超额 CI 上界≤0。未校准时原因记数据不足。 | 测试提高 PIT ADV 门槛、`max_pov` 和单标的持仓上限按资金反算、批量隔日调仓或降低频率。重新计算六档并在目标 AUM 验成交率/净超额区间。 |
| 因子库高度共线 `D07` | 同池同样本的成熟 OOS 每日绝对 Rank 相关均值≥.70、成对覆盖≥.80；再检条件数或去重后的增量 RankIC/IR 的 CI。保留最大相关 peer 与重复子簇。 | 用 train-only 加权 QR/SVD 对 peer 或行业风格增量正交，冻结 PCA 载荷或直接去冗余，以验证期和独立 OOS 复核。数值秩不足或增量证据反向时按 `COLLINEAR` 墓碑归档。 |

墓碑追加 `candidate_id, definition_sha256, parent_trial_id, reason_code, proof_bundle_sha256, ledger_seq, terminal_at`，使用 `(candidate_id, definition_sha256, proof_bundle_sha256, reason_code)` 幂等约束。明示状态 `falsified/rejected/inconclusive`，证据不足保留观察状态。每项建议包含可调用的参数配置、对应复验 check IDs 和应比较的前后指标，修复后的研究结果以新 proof bundle 重新晋级。

## 7. 交付顺序、接口迁移与验收门槛

| 依赖顺序 | 实现位置建议与迁移 | 可验收的完成条件 |
| --- | --- | --- |
| P0 · 1 数据合同与账本 | 在 `lake/store.py` 增加时点读取和不可变 manifest。`load_pit_financials` 保留单点用途，新增完整 `load_financial_events` 或批量时点投影。当前 `service.py:97–102,276–279` 在运行末尾只读一次最新财务观测，核心盈利因子之后无法恢复早期修订或已撤回组。接入层至少核对四源版本冲突、币种、财务季度/YTD/年报上下文与披露时间。SEC 适配器按日期构造发布时点且路径先选 tag 后枚举修订（`providers/sec_edgar.py:117–179`），时间精度应显式标注。`lake/registry.py` 新增追加式 ledger 和内容寻址，兼容输出旧投影。 | 拒绝同名不同内容，读文件改一字节报错，临时写故障恢复后不会把未完成快照选为 latest；重复/并发预约只产生一个 trial。六个 PIT 反例全部转负向测试。Qlib 同一成分重新入池保留缺口，对 float32 导出另用可解释容差，T1 Float64 由规范工件提供。 |
| P0 · 2 因果、推导、账务 | 在 `validation/` 挂 proof engine 与 checker，`schemas.py` 增设 3.0 契约并保持旧入口读兼容，`factors/` 输出 DSL/DAG 和行级 lineage，`backtest/` 输出事件与归因证据。按快照 SHA、版本、cutoff 与 prefix 输入哈希隔离缓存。 | 执行金标 fixture 和定向变异测试，所有预植入 leakage、错位列、错现金、重复成交换手、PSD 负值及未授权放宽产生可寻址 counterexample。正例有限总场景可穷举，固定种子重复评估得到相同证据语义与 hash。 |
| P0/P1 · 3 净日收益与统计晋级 | `validation/cpcv.py` 保留冻结收益研究，延伸为带实际标签范围、fit/inference replay 和净组合路径的接口。服务从执行净回报工件构造候选而非读取分层 NAV；终态、cut 与 proof 在同事务提交。 | 小矩阵手算 CSCV、固定会话 split、HAC、bootstrap、BH/BY、SPA/WRC 及调整 p 分辨率。用事先定义的序列相关零效应、单个强效应和针尖峰值夹具验校准与检出，区分工程可复算与有限样本统计功效，设定 Monte Carlo 误差带再做覆盖率断言。 |
| P0/P1 · 4 维度、容量、诊断与影子 | 维度产物、容量回放、诊断引擎可复用主 proof/事件工件，无需重复构建事实。`shadow/monitor.py` 接只读审计事件和显式 as-of，审批用持久受信记录绑定 candidate/snapshot/order、权限版本、期限及撤销，kill latch 持久化；只读导出保留现有合约。 | +82/+18/−20 bp 分解闭合，缺行情分母仍正确。六档容量排序、冲击 4×/2×/8× 金标、非单调与尾部删失、低覆盖及校准缺失状态均通过。故障输入可命中 7 个诊断，缺证据与依赖链返回明确状态。影子对重复 fill、过期审批、快照不符与连续时间的 kill 状态做负向回放。 |

交付成本主要由候选数、fold 数、前缀数、会话和标的数决定。DuckDB/Polars 先做按日历裁剪、候选批量扫描与稳定键 join，NumPy/SciPy 按 draw chunk 重用 bootstrap 交易日索引，先出算力与预计 p 分辨率，再调度异步任务。初期将截断回放限制在变异 fixtures 和记录的分层随机截点，可复用经过验证的原始前缀视图。禁止复用全样本 fit/聚合状态。逐笔账本采用分 run 分期 Parquet 并在摘要只存 ref。测试既覆盖核心函数，也从实际 hypothesis/CPCV/backtest/shadow 服务入口做完整集成回放。

**Must-Have** 为同源时点重放、不可变登记、独立守恒验算、确认窗口和只读影子权限。**Should-Have** 为跨维度基线、参数稳定区间、校准容量曲线与可配置诊断。应避免的做法包括按回测末日一次性提取财报、用 paper LS 冒充净执行收益、用修复后 PSD 或求解器状态代替约束独立验算，以及用截面/容量的单一高点支撑晋级。
