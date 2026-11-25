import type { GlossaryTerm } from '../types';
import { GATE_SPECS } from '../../thresholds';

export const Q6_TERMS: GlossaryTerm[] = [
  {
    id: 'implementation_shortfall',
    zh: '执行落差',
    en: 'Implementation Shortfall',
    domain: 'Q6',
    prd: 'Q6.1',
    aliases: ['IS', 'implementation shortfall', 'paper vs shadow', '执行缺口', '执行损耗'],
    plain: '理论纸面组合（以决策价全部成交）与实际可成交影子组合之间的收益差，衡量从信号到持仓的全部损耗。',
    formula: [
      'IS = R^{paper} - R^{shadow}',
      'IS_{bp} = 10^{4} \\cdot \\frac{\\sum_{i} s_{i} q_{i} (P^{exec}_{i} - P^{dec}_{i}) + \\text{Fees} + \\text{OppCost}}{\\text{Notional}}'
    ],
    symbols: ['P^{dec}: 决策价（信号生成时的收盘价）', 's_{i}: 买入为 +1，卖出为 -1'],
    gate: GATE_SPECS.implementation_shortfall,
    redline: '|IS| <= 50 bp（窗口口径，建议门禁）',
    pitfalls: [
      '决策价取 t+1 开盘价：开盘跳空被排除在 IS 之外，系统性低估执行成本',
      '未成交部分不计机会成本：涨停买不进的股票若随后继续上涨，损失不会体现在成交价中'
    ],
    related: ['perold_is', 'open_gap', 'fill_rate', 'gross_vs_net'],
    mounts: 'S KPI',
    source: 'builtin'
  },
  {
    id: 'perold_is',
    zh: 'Perold IS 拆解',
    en: 'Perold IS Decomposition',
    domain: 'Q6',
    prd: 'Q6.1',
    aliases: ['Perold', 'IS decomposition', 'shortfall attribution', '执行落差分解'],
    plain: '按 Perold (1988) 框架把执行落差拆为延迟（开盘跳空）、执行（流动性冲击）、机会成本（涨跌停未成交）与显性费用，分项之和必须等于总 IS。',
    formula: [
      'IS = \\text{Delay} + \\text{Execution} + \\text{Opportunity} + \\text{Fees}',
      '\\text{Delay} = \\sum_{i} s_{i} q^{fill}_{i} (P^{arr}_{i} - P^{dec}_{i}),  \\quad \\text{Execution} = \\sum_{i} s_{i} q^{fill}_{i} (P^{exec}_{i} - P^{arr}_{i})',
      '\\text{Opportunity} = \\sum_{i} s_{i} (q_{i} - q^{fill}_{i}) (P^{end}_{i} - P^{dec}_{i})'
    ],
    symbols: ['P^{arr}: 到达价（t+1 开盘价）', 'P^{end}: 评估期末价'],
    redline: '分项和 = 总 IS（建议门禁，残差 <= 1 bp）',
    pitfalls: [
      '各分项使用不同基准价（有的取开盘价、有的取 VWAP）：加总后不闭合',
      '把涨跌停未成交的机会成本归入流动性冲击：误导容量判断'
    ],
    related: ['implementation_shortfall', 'open_gap', 'liquidity_impact', 'limit_queue', 'conservation_law'],
    mounts: 'S 面板',
    source: 'builtin'
  },
  {
    id: 'open_gap',
    zh: '开盘跳空损耗',
    en: 'Open Gap Cost',
    domain: 'Q6',
    prd: 'Q6.1',
    aliases: ['gap cost', 'overnight gap', 'delay cost', '隔夜跳空', '延迟成本'],
    plain: '信号在 t 日收盘后生成、t+1 日开盘才能成交，两者之间的价格变化；动量与事件驱动类信号受影响最大。',
    formula: [
      '\\text{Gap}_{i} = s_{i} \\cdot \\frac{P^{open}_{i,t+1} - P^{close}_{i,t}}{P^{close}_{i,t}}',
      '\\text{GapCost} = \\sum_{i} | \\Delta w_{i} | \\cdot \\text{Gap}_{i}'
    ],
    redline: '无硬门禁 · 计入 Perold Delay 分项',
    pitfalls: [
      '盘后公告类信号的 alpha 大部分在集合竞价中兑现：回测以收盘价成交会严重高估',
      '除权除息日未复权：机械跳空被误算为执行成本'
    ],
    related: ['perold_is', 'implementation_shortfall', 'ic_decay', 't_plus_1'],
    mounts: 'S KPI',
    source: 'builtin'
  },
  {
    id: 'liquidity_impact',
    zh: '流动性冲击',
    en: 'Liquidity Impact',
    domain: 'Q6',
    prd: 'Q6.1',
    aliases: ['liquidity cost', 'slippage', 'execution cost', '滑点', '执行成本'],
    plain: '下单量相对盘口深度与成交量过大，使成交均价劣于到达价的损耗；对应 Perold 拆解中的执行分项。',
    formula: [
      '\\text{Slip}_{i} = s_{i} \\cdot \\frac{P^{exec}_{i} - P^{arr}_{i}}{P^{arr}_{i}}',
      '\\mathbb{E}[\\text{Slip}_{i}] \\approx Y \\sigma_{i} \\sqrt{Q_{i} / ADV_{i}}'
    ],
    redline: '无硬门禁 · 用于校准平方根冲击参数 Y',
    pitfalls: [
      '到达价取前收盘价：把开盘跳空混入滑点，冲击模型被错误放大',
      '只用已成交部分统计滑点：因 POV 限制未成交的大单，其潜在冲击被忽略'
    ],
    related: ['sqrt_impact', 'pov', 'perold_is', 'capacity_frontier'],
    mounts: 'S KPI',
    source: 'builtin'
  },
  {
    id: 'limit_queue',
    zh: '涨跌停排队',
    en: 'Limit-up / Limit-down Queue',
    domain: 'Q6',
    prd: 'Q6.1',
    aliases: ['limit queue', 'limit lock', '涨停排队', '跌停排队', '封板', '一字板'],
    plain: '股票封涨停时买单排队难以成交、封跌停时卖单无法卖出，由此产生的机会损失；在小盘与题材股上尤为显著。',
    formula: [
      'P_{i,t} = P^{up}_{i,t} \\text{ 且全天封板} \\Rightarrow q^{fill,buy}_{i,t} = 0',
      '\\text{OppLoss} = \\sum_{i \\in \\text{blocked}} s_{i} (q_{i} - q^{fill}_{i}) (P^{end}_{i} - P^{dec}_{i})'
    ],
    redline: '无硬门禁 · 计入 Perold Opportunity 分项',
    pitfalls: [
      '只判断收盘价是否等于涨停价：盘中开板时段仍可能成交，应结合分钟数据估计排队成交概率',
      '跌停卖不出会被迫继续持有：后续回撤须计入策略，而不能视为订单取消'
    ],
    related: ['price_limit_rules', 'perold_is', 'fill_rate', 't_plus_1'],
    mounts: 'S KPI',
    source: 'builtin'
  },
  {
    id: 'fill_rate',
    zh: '成交率',
    en: 'Fill Rate',
    domain: 'Q6',
    prd: 'Q6.1',
    aliases: ['fill rate', 'fill ratio', '成交比例', '完成率'],
    plain: '影子组合实际成交名义金额占目标调仓名义金额的比例；过低意味着纸面组合与可持有组合持续分离。',
    formula: ['\\text{FillRate} = 100 \\cdot \\frac{\\sum_{i} | q^{fill}_{i} | P_{i}}{\\sum_{i} | q^{target}_{i} | P_{i}}'],
    gate: GATE_SPECS.fill_rate,
    redline: '成交率 >= 95%（建议门禁）',
    pitfalls: [
      '只统计买单：跌停卖不出在下跌阶段更常见，应分买卖方向报告',
      '未成交订单次日自动重发会让成交率看似正常，但成交价已严重偏离决策价'
    ],
    related: ['limit_queue', 'pov', 'implementation_shortfall', 'capacity_frontier'],
    mounts: 'S KPI',
    source: 'builtin'
  },
  {
    id: 'crowding_index',
    zh: '拥挤度',
    en: 'Factor Crowding Index',
    domain: 'Q6',
    prd: 'Q6.2',
    aliases: ['crowding', 'crowdedness', '因子拥挤', '拥挤指数'],
    plain: '同类资金扎堆同一因子的程度，取值 0–1；缺少直接拥挤数据时，用该因子 Top 10% 与同库因子 Top 10% 的最大重合度代理。',
    formula: [
      'C_{t} = \\text{median}_{i}( c_{i,t} ),  \\quad c_{i,t} \\in [0, 1]',
      '\\hat{C}^{proxy}_{t} = \\max_{j \\ne f} \\frac{#( \\text{Top}_{10}(f) \\text{ 与 } \\text{Top}_{10}(j) \\text{ 重合} )}{\\lceil N / 10 \\rceil}'
    ],
    symbols: ['N: 两因子共同覆盖的股票数（>= 50）'],
    gate: GATE_SPECS.crowding_index,
    redline: '拥挤度 <= 0.80（建议门禁）；>= 0.95 且 IC 衰减时触发 KILL-SWITCH',
    pitfalls: [
      '重合度代理只反映库内因子之间的拥挤：市场上同类产品的持仓拥挤无法直接观测',
      '拥挤度高不等于立即失效：须与 IC 历史分位联合判定，单独据此停用会错失收益'
    ],
    related: ['hist_percentile', 'kill_switch', 'max_corr_library', 'regime_shift'],
    mounts: 'S 表头',
    source: 'builtin'
  },
  {
    id: 'hist_percentile',
    zh: '历史分位',
    en: 'Historical Percentile',
    domain: 'Q6',
    prd: 'Q6.2',
    aliases: ['percentile', 'hist percentile', 'IC percentile', '历史百分位'],
    plain: '当前 60 日滚动 RankIC 在自身历史滚动值分布中的位置；参考分布冻结在当前 60 日窗口之前。',
    formula: [
      'P_{t} = 100 \\cdot \\frac{#( IC^{60}_{s} < IC^{60}_{t} ) + 0.5 \\cdot #( IC^{60}_{s} = IC^{60}_{t} )}{N_{ref}}',
      's \\le t - 60,  \\quad N_{ref} \\ge 20'
    ],
    gate: GATE_SPECS.hist_percentile,
    redline: '>= 20% 分位（建议门禁）；连续 5 日 IC <= 0 且低于 10% 分位触发 DEGRADING',
    pitfalls: [
      '参考窗口与当前 60 日窗口重叠：当前值污染历史分布，分位被拉向中位',
      '参考样本不足 20 个窗口时分位没有统计意义：应标记 N/A 而非 0'
    ],
    related: ['crowding_index', 'kill_switch', 'rank_ic', 'regime_shift'],
    mounts: 'S 表头',
    source: 'builtin'
  },
  {
    id: 'kill_switch',
    zh: '熔断',
    en: 'Kill-Switch & DEGRADING',
    domain: 'Q6',
    prd: 'Q6.2',
    aliases: ['kill switch', 'KILL-SWITCH', 'DEGRADING', 'circuit breaker', '停用', '衰减预警'],
    plain: '因子衰减到阈值以下时先标记 DEGRADING，叠加高拥挤度或执行、新鲜度、授权、对账硬检查失败时自动熔断停用。',
    formula: [
      '\\text{DEGRADING}: \\forall j \\in 0..4: IC^{60}_{t-j} \\le 0 \\text{ 且 } IC^{60}_{t-j} < Q_{0.10}(\\text{hist})',
      '\\text{KILL}: \\text{硬检查失败} \\text{ 或 } (\\text{DEGRADING 且 } C_{t} \\ge 0.95)'
    ],
    redline: '规则门禁：DEGRADING 或拥挤度 >= 0.80 告警；DEGRADING 且拥挤度 >= 0.95 或任一硬检查失败即 KILL-SWITCH',
    pitfalls: [
      '只用单日 IC 判定：日度 IC 噪声极大，须用滚动窗口加连续天数确认',
      '熔断后自动恢复：恢复应走研究复核与审批，而非 IC 回升一天就重新启用'
    ],
    related: ['hist_percentile', 'crowding_index', 'authority_gate', 'regime_shift'],
    mounts: 'S 状态列',
    source: 'builtin'
  },
  {
    id: 'authority_gate',
    zh: '权限闸门',
    en: 'Authority Gate',
    domain: 'Q6',
    prd: 'Q6.3',
    aliases: ['authority gate', 'SIGNAL-ONLY', 'broker_connected', 'research isolation', '研究交易隔离'],
    plain: '研究系统只产出调仓信号文件，与券商下单通道物理隔离；任何下单、账户、持仓类接口一律不可达。',
    formula: [
      '\\text{BrokerConnected} = \\text{false}',
      '#( r \\in \\text{Routes} : r \\text{ 匹配交易类关键词} ) = 0,  \\quad \\text{Method}(r) \\in ( \\text{GET}, \\text{POST} )'
    ],
    redline: 'broker_connected = false（代码类型约束），交易类路径 / 工具名匹配数 = 0',
    pitfalls: [
      '信号文件被下游脚本自动读取并下单：实际打通了闸门，须人工签核与离线传递',
      '工具名不含交易关键词，但参数可透传到外部执行服务：同样违反隔离'
    ],
    related: ['mcp_side_effect', 'kill_switch', 'cluster_dispatch', 'ic_memo'],
    mounts: 'S 面板 · SIGNAL-ONLY Tag',
    source: 'builtin'
  },
  {
    id: 'regime_shift',
    zh: '状态切换',
    en: 'Regime Shift',
    domain: 'Q6',
    prd: 'Q6.2',
    aliases: ['regime shift', 'regime change', 'structural break', '风格切换', '结构突变'],
    plain: '市场状态（牛熊、波动、大小盘风格）突变，使因子收益分布发生结构性改变，历史 IC 不再代表未来。',
    formula: [
      '\\text{Shift}_{t}: g_{t} \\ne g_{t-1},  \\quad g_{t} = \\text{Regime}(D_{\\le t-1})',
      'Z_{t} = \\frac{IC^{60}_{t} - \\mu^{hist}_{IC}}{\\sigma^{hist}_{IC}}'
    ],
    redline: '无硬门禁 · 状态切换后须复核 regime 一致性与历史分位',
    pitfalls: [
      '用包含当日的数据判定 regime：分类本身产生前视，应只基于前收盘',
      '缺少迟滞阈值：状态在边界附近频繁翻转，把短期噪声误当 regime 切换'
    ],
    related: ['regime_classification', 'regime_consistency_score', 'hist_percentile', 'kill_switch', 'dimension_dependence'],
    mounts: 'S · R 维度热力图',
    source: 'builtin'
  }
];
