import type { GlossaryTerm } from '../types';
import { GATE_SPECS } from '../../thresholds';

const BRINSON_REDLINE = '无硬门禁 · 各效应之和须满足闭合误差 |ε| <= 1 bp';

export const Q5_TERMS: GlossaryTerm[] = [
  {
    id: 't_plus_1',
    zh: 'T+1 交收约束',
    en: 'T+1 Settlement Constraint',
    domain: 'Q5',
    prd: 'Q5.2',
    aliases: ['T+1', 'T plus 1', 'settlement', '次日交收', '当日不可卖'],
    plain: 'A 股当天买入的股票最早下一个交易日才能卖出；回测须锁定当日买入仓位，日内反转类信号无法实现。',
    formula: [
      '\\text{Sellable}_{i,t} = h^{settled}_{i,t-1} - q^{sold}_{i,t}',
      'q^{sell}_{i,t} \\le \\text{Sellable}_{i,t}'
    ],
    symbols: ['h^{settled}_{i,t-1}: 截至上一交易日已交收持仓'],
    redline: '违规 = 0（建议门禁）',
    pitfalls: [
      '向量化回测以日收益乘权重，隐含了当日买入当日可卖',
      '部分 ETF 与可转债为 T+0：混合资产回测须按品种区分交收规则'
    ],
    related: ['price_limit_rules', 'limit_queue', 'gross_vs_net', 'open_gap'],
    mounts: 'B 拒单 · P 持仓 Tag',
    source: 'builtin'
  },
  {
    id: 'pov',
    zh: '成交量参与率',
    en: 'Percentage of Volume',
    domain: 'Q5',
    prd: 'Q5.2',
    aliases: ['POV', 'participation rate', 'max_pov', '参与率'],
    plain: '单只股票下单量占当日（或执行时段）市场成交量的比例上限；超出部分须延后成交或放弃。',
    formula: [
      'POV_{i,t} = \\frac{q_{i,t}}{V_{i,t}} \\le POV_{max}',
      'q^{fill}_{i,t} = \\min( q^{target}_{i,t}, POV_{max} \\cdot V_{i,t} )'
    ],
    redline: '<= max_pov（代码配置）',
    pitfalls: [
      '用全天成交量约束开盘成交：开盘集合竞价成交量通常只占全天的几个百分点',
      '未处理停牌与一字板：成交量为 0 或极低时应直接判为不可成交'
    ],
    related: ['sqrt_impact', 'capacity_frontier', 'fill_rate', 'liquidity_impact'],
    mounts: 'B 摩擦 · P 容量',
    source: 'builtin'
  },
  {
    id: 'sqrt_impact',
    zh: '平方根冲击',
    en: 'Square-root Market Impact',
    domain: 'Q5',
    prd: 'Q5.2',
    aliases: ['sqrt impact', 'square root law', 'market impact', '冲击成本', '平方根律'],
    plain: '市场冲击成本与下单量占日均成交量比例的平方根成正比、与波动率成正比；是容量与净值估算的核心模型。',
    formula: [
      'I_{i} = Y \\cdot \\sigma_{i} \\cdot \\sqrt{\\frac{Q_{i}}{ADV_{i}}}',
      '\\text{Cost}_{i} = I_{i} \\cdot | Q_{i} | \\cdot P_{i}'
    ],
    symbols: ['Y: 校准系数（经验量级约 0.5-1）', '\\sigma_{i}: 日波动率', 'Q_{i}/ADV_{i}: 参与比例'],
    redline: '无硬门禁 · 参数 Y 须用影子或实盘成交校准',
    pitfalls: [
      '直接套用海外文献的 Y：A 股小盘股盘口深度与最小价位不同，须本地校准',
      '平方根律刻画单次执行完成后的冲击：日内多次拆单与跨日累积冲击会被低估'
    ],
    related: ['capacity_frontier', 'pov', 'liquidity_impact', 'gross_vs_net'],
    mounts: 'B 瀑布 · P 冲击列',
    source: 'builtin'
  },
  {
    id: 'stamp_duty',
    zh: '印花税等摩擦',
    en: 'Stamp Duty & Fees',
    domain: 'Q5',
    prd: 'Q5.2',
    aliases: ['stamp duty', 'commission', 'transaction fees', '印花税', '佣金', '过户费'],
    plain: 'A 股卖出征收 0.05% 印花税（2023-08-28 起由 0.1% 减半），另有双边佣金与过户费；是高换手策略的主要固定成本。',
    formula: [
      'c^{sell} = 0.0005 + c_{comm} + c_{tr},  \\quad c^{buy} = c_{comm} + c_{tr}',
      '\\text{Fee}_{t} = \\sum_{i} ( c^{buy} B_{i,t} + c^{sell} S_{i,t} )'
    ],
    symbols: ['c_{comm}: 佣金率（常见万 1 至万 3，单笔最低 5 元）', 'c_{tr}: 过户费 0.001%（双边）', 'B, S: 买入与卖出成交额'],
    redline: '无硬门禁 · 历史回测须按 PIT 税率（2023-08-28 前卖出 0.1%）',
    pitfalls: [
      '全期使用 0.05% 印花税：2023-08-28 前为 0.1%，历史成本被低估',
      '忽略单笔最低佣金 5 元：小资金或碎单调仓时实际费率远高于名义费率'
    ],
    related: ['gross_vs_net', 'turnover_oneway', 'turnover_lambda', 'sqrt_impact'],
    mounts: 'B 瀑布',
    source: 'builtin'
  },
  {
    id: 'gross_vs_net',
    zh: '毛 / 净口径',
    en: 'GROSS vs NET',
    domain: 'Q5',
    prd: 'Q5.1',
    aliases: ['gross', 'net', 'after cost', 'GROSS', 'NET', '毛收益', '净收益'],
    plain: '向量化快筛通常未扣真实摩擦（GROSS）；事件驱动引擎扣除费税、冲击、T+1 与涨跌停约束后的才是可决策的净值（NET）。',
    formula: [
      'R^{net} = R^{gross} - \\text{Fees} - \\text{Impact} - \\text{Opportunity}',
      '\\text{CostDrag} = R^{gross} - R^{net}'
    ],
    redline: '晋级必须使用 NET 口径（建议门禁）',
    pitfalls: [
      'GROSS 与 NET 结果混放在同一排行榜：高换手策略在 GROSS 下排名虚高',
      'NET 只扣费税不扣冲击与不可成交损失：仍然偏乐观'
    ],
    related: ['stamp_duty', 'sqrt_impact', 'implementation_shortfall', 'capacity_frontier'],
    mounts: 'B KPI Tag · 引擎 Seg',
    source: 'builtin'
  },
  {
    id: 'brinson_allocation',
    zh: '配置效应',
    en: 'Brinson Allocation Effect',
    domain: 'Q5',
    prd: 'Q5.3',
    aliases: ['allocation', 'allocation effect', 'Brinson-Fachler', 'BF', '行业配置效应'],
    plain: '超配跑赢基准整体的行业、低配跑输的行业所带来的超额收益（Brinson-Fachler 口径）。',
    formula: ['A_{k} = (w^{p}_{k} - w^{b}_{k}) (R^{b}_{k} - R^{b})'],
    symbols: ['w_{k}: 行业 k 权重', 'R^{b}_{k}: 基准在行业 k 内的收益', 'R^{b}: 基准总收益'],
    redline: BRINSON_REDLINE,
    pitfalls: [
      '用 BHB 口径（R^{b}_{k} 不减 R^{b}）：超配一个绝对收益为正但跑输基准的行业，也会显示正配置效应',
      '单期效应直接相加做多期归因不闭合：须用 Carino 或 Menchero 链接系数'
    ],
    related: ['brinson_selection', 'brinson_interaction', 'closure_error', 'industry_active_dev'],
    mounts: 'B 归因表',
    source: 'builtin'
  },
  {
    id: 'brinson_selection',
    zh: '选股效应',
    en: 'Brinson Selection Effect',
    domain: 'Q5',
    prd: 'Q5.3',
    aliases: ['selection', 'selection effect', 'stock selection', '行业内选股'],
    plain: '在行业内选出比基准成分更好的股票带来的超额，按基准行业权重计量。',
    formula: ['S_{k} = w^{b}_{k} (R^{p}_{k} - R^{b}_{k})'],
    symbols: ['R^{p}_{k}: 组合在行业 k 内的收益'],
    redline: BRINSON_REDLINE,
    pitfalls: [
      '行业内只持有 1-2 只股票时，选股效应主要是个股特质噪声',
      '选股效应包含行业内风格暴露（如小市值）：须用 Barra 归因进一步拆分'
    ],
    related: ['brinson_allocation', 'brinson_interaction', 'barra_attribution', 'closure_error'],
    mounts: 'B 归因表',
    source: 'builtin'
  },
  {
    id: 'brinson_interaction',
    zh: '交互效应',
    en: 'Brinson Interaction Effect',
    domain: 'Q5',
    prd: 'Q5.3',
    aliases: ['interaction', 'interaction effect', 'cross term', '交叉项'],
    plain: '既超配又选得好（或既低配又选得差）产生的交叉项；常被并入选股效应，从而夸大或掩盖选股能力。',
    formula: [
      'I_{k} = (w^{p}_{k} - w^{b}_{k}) (R^{p}_{k} - R^{b}_{k})',
      '\\sum_{k} (A_{k} + S_{k} + I_{k}) = R^{p} - R^{b}'
    ],
    redline: BRINSON_REDLINE,
    pitfalls: [
      '把交互项并入选股效应：超配行业内的选股贡献被放大',
      '基准在某行业权重为 0 时，该行业的全部收益都落入交互项'
    ],
    related: ['brinson_allocation', 'brinson_selection', 'closure_error'],
    mounts: 'B 归因表',
    source: 'builtin'
  },
  {
    id: 'closure_error',
    zh: '闭合误差',
    en: 'Attribution Closure Error',
    domain: 'Q5',
    prd: 'Q5.3',
    aliases: ['closure', 'closure error', 'attribution residual', 'epsilon', '闭合残差', '归因残差'],
    plain: '各归因分项之和与组合实际总超额之间的差；超过 1 bp 说明归因口径、数据或链接方法有误。',
    formula: [
      '\\varepsilon = (R^{p} - R^{b}) - \\sum_{k} (A_{k} + S_{k} + I_{k})',
      '| \\varepsilon | \\le 1 \\text{ bp}'
    ],
    gate: GATE_SPECS.closure_error,
    redline: '|ε| <= 1 bp（合同硬门禁）',
    pitfalls: [
      '组合收益含现金、费用与交易时点差，而归因只用期初权重乘期间收益：差额被藏进 ε',
      '多期累积时算术相加不闭合：应先做几何链接再检验'
    ],
    related: ['conservation_law', 'brinson_interaction', 'barra_attribution', 'brinson_allocation'],
    mounts: 'B 表底 · R 证明链',
    source: 'builtin'
  },
  {
    id: 'barra_attribution',
    zh: '真假 Alpha 甄别',
    en: 'Barra Return Attribution',
    domain: 'Q5',
    prd: 'Q5.3',
    aliases: ['Barra attribution', 'specific alpha', 'factor attribution', '收益分解', '特质收益', '风格归因'],
    plain: '用 Barra 风险模型把超额拆成特质 Alpha、风格 Beta、行业与成本；特质占比低说明所谓 alpha 其实是风格押注。',
    formula: [
      'R^{p} - R^{b} = \\sum_{s} z_{s} f_{s} + \\sum_{k} a_{k} f_{k} + \\sum_{i} a_{i} u_{i} - \\text{Cost}',
      '\\text{SpecificShare} = 100 \\cdot \\frac{\\sum_{i} a_{i} u_{i}}{R^{p} - R^{b}}'
    ],
    symbols: ['z_{s}: 主动风格暴露', 'a_{k}, a_{i}: 行业与个股主动权重', 'u_{i}: 特质收益'],
    gate: GATE_SPECS.barra_attribution,
    redline: '特质 Alpha 占比 >= 65%（代码门禁）',
    pitfalls: [
      '超额为负或接近 0 时占比失去意义：应同时报告各分项的绝对 bp',
      '风格暴露取期末值而非期初值：特质收益会吸收持有期内的风格漂移'
    ],
    related: ['barra_cne6', 'style_exposure', 'closure_error', 'top_excess'],
    mounts: 'B 面板 Tag',
    source: 'builtin'
  },
  {
    id: 'max_drawdown',
    zh: '最大回撤',
    en: 'Maximum Drawdown',
    domain: 'Q5',
    prd: 'Q5.3',
    aliases: ['MDD', 'max drawdown', 'drawdown', 'underwater', '水下曲线'],
    plain: '净值从历史最高点到其后最低点的最大跌幅；水下曲线展示每天距前高的回撤深度与持续时间。',
    formula: [
      'DD_{t} = \\frac{NAV_{t}}{\\max_{s \\le t} NAV_{s}} - 1',
      'MDD = \\min_{t} DD_{t}'
    ],
    redline: '无硬门禁 · 描述性指标（由 Calmar 约束）',
    pitfalls: [
      '只报告回撤幅度不报告修复时长：同样 -20%，3 个月修复与 2 年修复的风险含义完全不同',
      'MDD 是路径依赖的单一极值，样本越长越大：不同长度的回测之间不可直接比较'
    ],
    related: ['calmar', 'excess_drawdown', 'regime_shift'],
    mounts: 'B KPI · 水下图',
    source: 'builtin'
  },
  {
    id: 'calmar',
    zh: 'Calmar 比率',
    en: 'Calmar Ratio',
    domain: 'Q5',
    prd: 'Q5.3',
    aliases: ['Calmar', 'MAR', 'return over max drawdown'],
    plain: '年化收益除以最大回撤的绝对值，衡量每承受一单位最大回撤换来多少收益。',
    formula: ['Calmar = \\frac{R_{ann}}{| MDD |}'],
    gate: GATE_SPECS.calmar,
    redline: 'Calmar >= 1.0（建议门禁）',
    pitfalls: [
      '传统定义使用最近 36 个月：与全样本口径混用会让长回测 Calmar 偏低',
      'A 股多头策略的 MDD 往往由 2015、2018、2024 年初等系统性下跌主导：应同时看超额回撤'
    ],
    related: ['max_drawdown', 'excess_drawdown', 'information_ratio'],
    mounts: 'B KPI',
    source: 'builtin'
  },
  {
    id: 'excess_drawdown',
    zh: '超额回撤',
    en: 'Excess (Relative) Drawdown',
    domain: 'Q5',
    prd: 'Q5.3',
    aliases: ['relative drawdown', 'active drawdown', '相对回撤', '超额最大回撤'],
    plain: '超额净值（组合相对基准的累积超额）从高点到低点的最大跌幅；是指数增强产品最受关注的风险指标。',
    formula: [
      'E_{t} = \\prod_{s \\le t} \\frac{1 + r^{p}_{s}}{1 + r^{b}_{s}}',
      'EDD = \\min_{t} ( \\frac{E_{t}}{\\max_{s \\le t} E_{s}} - 1 )'
    ],
    redline: '无硬门禁 · 描述性指标',
    pitfalls: [
      '用超额收益简单累加构造超额净值：长期会与几何口径显著偏离',
      '超额回撤集中在风格切换期（如 2024 年初小微盘踩踏）：须配合 regime 切片分析'
    ],
    related: ['max_drawdown', 'tracking_error', 'regime_shift', 'calmar'],
    mounts: 'B KPI',
    source: 'builtin'
  }
];
