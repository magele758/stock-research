import type { GlossaryTerm } from '../types';
import { GATE_SPECS } from '../../thresholds';

export const MATH_TERMS: GlossaryTerm[] = [
  {
    id: 'newey_west_hac',
    zh: 'Newey-West HAC 方差',
    en: 'Newey-West HAC Variance',
    domain: 'MATH',
    aliases: ['HAC', 'Newey-West', 'NW', 'Bartlett kernel', 'long-run variance', '异方差自相关稳健'],
    plain: '对序列自相关与异方差都稳健的长期方差估计，用 Bartlett 核对各阶自协方差递减加权；是重叠标签下 IC 显著性的正确口径。',
    formula: [
      '\\hat{\\gamma}_{l} = \\frac{1}{T} \\sum_{t=l+1}^{T} (x_{t} - \\bar{x})(x_{t-l} - \\bar{x})',
      '\\hat{V}_{NW} = \\hat{\\gamma}_{0} + 2 \\sum_{l=1}^{L} (1 - \\frac{l}{L+1}) \\hat{\\gamma}_{l}',
      'L = \\lfloor 4 (T/100)^{2/9} \\rfloor'
    ],
    redline: 'lag = floor(4 (T/100)^{2/9})，重叠 h 日标签时 L >= h - 1（建议）',
    pitfalls: [
      'Bartlett 权重写成 1 - l/L：最高阶权重为 0，等价于少用一阶滞后',
      '样本较短且自相关很强时 HAC 仍会低估方差：可改用 Kiefer-Vogelsang 固定 b 渐近或块 bootstrap'
    ],
    related: ['nw_t_stat', 'bootstrap_ci', 'rank_ic', 'icir'],
    mounts: 'A NW t 卡相关 · R',
    source: 'builtin'
  },
  {
    id: 'bootstrap_ci',
    zh: 'Bootstrap 95% 置信区间',
    en: 'Bootstrap Confidence Interval',
    domain: 'MATH',
    aliases: ['bootstrap', 'CI', '95% CI', 'stationary bootstrap', 'block bootstrap', '置信区间', '自助法'],
    plain: '对时间序列做平稳块自助重抽样 B 次，取统计量分布的 2.5% 与 97.5% 分位作为置信区间；块结构保留了序列相关。',
    formula: [
      '\\ell \\sim \\text{Geometric}(1 / l),  \\quad l = \\max( h, \\lceil n^{1/3} \\rceil )',
      'CI_{95} = [ \\hat{\\theta}^{*}_{(0.025 B)}, \\hat{\\theta}^{*}_{(0.975 B)} ],  \\quad B \\ge 2000'
    ],
    symbols: ['\\ell: 随机块长（平稳自助法，Politis-Romano）', 'l: 平均块长', 'h: 标签期限'],
    redline: '下界 > 0（B >= 2000，平均块长 >= max(h, n^{1/3})，建议）',
    pitfalls: [
      'IID bootstrap 打散了自相关：对重叠标签或持仓收益会得到过窄的区间',
      '对 Sharpe 等比率统计量用百分位法在小样本下有偏：可改用 BCa 或 studentized bootstrap'
    ],
    related: ['newey_west_hac', 'hansen_spa', 'regime_consistency_score', 'long_short'],
    mounts: 'A KPI 副行 · R 证明链',
    source: 'builtin'
  },
  {
    id: 'bh_fdr',
    zh: 'BH-FDR 多重检验',
    en: 'Benjamini-Hochberg False Discovery Rate',
    domain: 'MATH',
    aliases: ['BH', 'FDR', 'BH-FDR', 'Benjamini-Hochberg', 'q-value', 'q 值', '多重检验'],
    plain: '同时检验大量因子或策略时，控制「被判为显著的结果中假发现的期望比例」，而不是对每个检验各自使用 0.05。',
    formula: [
      'p_{(1)} \\le p_{(2)} \\le ... \\le p_{(m)},  \\quad k^{*} = \\max( k : p_{(k)} \\le \\frac{k}{m} \\alpha )',
      'q_{(k)} = \\min_{j \\ge k} \\min( 1, \\frac{m p_{(j)}}{j} )'
    ],
    symbols: ['m: 检验总数', '\\alpha: 目标 FDR 水平'],
    gate: GATE_SPECS.bh_fdr,
    redline: 'q <= 0.05（合同硬门禁），0.05–0.10 观察',
    pitfalls: [
      'm 只计本批次检验数，忽略同一家族历史上的检验：q 值系统性偏低',
      'BH 在正相关（PRDS）下仍有效，但任意依赖结构须改用 BY 修正（阈值再除以 Σ 1/i）'
    ],
    related: ['hansen_spa', 'dsr', 'trial_count_m', 'nw_t_stat'],
    mounts: 'M 新卡 · R',
    source: 'builtin'
  },
  {
    id: 'hansen_spa',
    zh: 'Hansen SPA 检验',
    en: 'Hansen Superior Predictive Ability Test',
    domain: 'MATH',
    aliases: ['SPA', 'Hansen SPA', 'Reality Check', 'White RC', 'data snooping', '数据窥探检验'],
    plain: '在一整批候选策略中检验「最好的那个是否真的优于基准」，用自助法校正数据窥探；相比 White Reality Check，对明显劣质的候选不敏感。',
    formula: [
      'd_{k,t} = L_{0,t} - L_{k,t},  \\quad T^{SPA} = \\max( \\max_{k} \\frac{\\sqrt{n} \\bar{d}_{k}}{\\hat{\\omega}_{k}}, 0 )',
      '\\hat{\\mu}^{c}_{k} = \\bar{d}_{k} \\text{ if } \\frac{\\sqrt{n} \\bar{d}_{k}}{\\hat{\\omega}_{k}} \\ge -\\sqrt{2 \\ln \\ln n} \\text{ else } 0',
      'p = \\frac{#( b : T^{*}_{b} > T^{SPA} )}{B}'
    ],
    symbols: ['L_{0}, L_{k}: 基准与候选 k 的损失', '\\hat{\\omega}_{k}: 长期标准差（块 bootstrap 估计）'],
    gate: GATE_SPECS.hansen_spa,
    redline: 'SPA p <= 0.05（合同硬门禁），0.05–0.10 观察',
    pitfalls: [
      '候选集只放入最终几个策略：SPA 必须覆盖全部尝试过的配置，否则窥探校正失效',
      '用 IID 重抽样：须用平稳块自助法保留损失差分序列的自相关'
    ],
    related: ['bh_fdr', 'dsr', 'bootstrap_ci', 'trial_count_m'],
    mounts: 'M 新卡 · R 证明链',
    source: 'builtin'
  },
  {
    id: 'prefix_invariance',
    zh: '前缀不变性证明',
    en: 'Prefix Invariance',
    domain: 'MATH',
    aliases: ['prefix invariance', 'T1.PREFIX_INVARIANCE', 'truncation test', '截断不变性', '零前视证明'],
    plain: '在多个截断点 τ 截断数据后重新计算信号，截断点及之前的历史信号必须与全样本结果一致，从而经验地证明零前视。',
    formula: [
      '\\Delta = \\max_{\\tau \\in \\text{Cutoffs}} \\max_{t \\le \\tau, i} | f_{i,t}(D_{\\le \\tau}) - f_{i,t}(D_{full}) |',
      '\\Delta \\le 10^{-10}'
    ],
    gate: GATE_SPECS.prefix_invariance,
    redline: 'max|Δf| <= 1e-10（合同硬门禁），出现任一 witness key 即 FAIL',
    pitfalls: [
      '截断点只取样本末端：前视常来自全样本标准化或复权，须在样本中部多点截断',
      '缺失值处理不一致（全样本为 NaN 而截断样本被填充）：既可能误报前视，也可能掩盖真实前视'
    ],
    related: ['look_ahead_bias', 'ast_causal_proof', 'point_in_time', 'scale_invariance'],
    mounts: 'R 证明链 · A',
    source: 'builtin'
  },
  {
    id: 'ast_causal_proof',
    zh: 'AST 因果证明',
    en: 'AST Causality Proof',
    domain: 'MATH',
    aliases: ['causal proof', 'AST causality', 'temporal dependency', '时间下标检查', '因果性证明'],
    plain: '对因子表达式的依赖图做静态分析，证明每个节点引用的数据时间下标都不晚于 t，从语法层面排除未来函数。',
    formula: [
      '\\forall n \\in \\text{AST}: \\tau(n) \\le t',
      '\\tau(\\text{shift}(x, k)) = \\tau(x) - k,  \\quad \\tau(\\text{op}(x_{1}, ..., x_{r})) = \\max_{m} \\tau(x_{m})'
    ],
    symbols: ['\\tau(n): 节点 n 引用数据的最晚时间下标'],
    redline: '违规节点 = 0（建议门禁）',
    pitfalls: [
      '静态证明覆盖不了数据层前视（如未 PIT 的财务表）：须与前缀不变性互补',
      '自定义 Python 函数是黑盒：无法证明的节点应判 UNKNOWN 而非 PASS'
    ],
    related: ['ast_safe', 'prefix_invariance', 'look_ahead_bias'],
    mounts: 'R · A',
    source: 'builtin'
  },
  {
    id: 'scale_invariance',
    zh: '价格尺度保序性',
    en: 'Price Scale Invariance',
    domain: 'MATH',
    aliases: ['scale invariance', 'T2.SCALE_INVARIANCE', 'rank preservation', '尺度不变性', '保序性'],
    plain: '把全部价格乘以常数 a（0.1 或 10）后，因子截面排序应几乎不变；否则因子隐含依赖绝对价格水平，在复权口径切换时会失真。',
    formula: ['\\min_{a \\in (0.1, 10)} \\min_{t} \\rho_{S}( \\text{rank} f_{\\cdot,t}(a P), \\text{rank} f_{\\cdot,t}(P) ) \\ge 0.9999'],
    redline: 'Spearman ρ >= 0.9999（形式化校验 T2.SCALE_INVARIANCE）',
    pitfalls: [
      '价格绝对差类因子（如 close - open 未除以价格）天然不满足：应改写为收益或比率形式',
      '表达式中含绝对价格阈值（如 price > 5）：缩放后样本集合改变，造成排序突变'
    ],
    related: ['zscore_rank', 'corp_action_adj', 'prefix_invariance', 'conservation_law'],
    mounts: 'R 证明链 · A',
    source: 'builtin'
  },
  {
    id: 'conservation_law',
    zh: '守恒律',
    en: 'Conservation Invariants',
    domain: 'MATH',
    aliases: ['conservation', 'invariants', 'accounting identity', '会计恒等式', '守恒检查'],
    plain: '现金、权重、归因、摩擦在会计上必须闭合；任一项不守恒都说明计算链条中存在数据或口径错误。',
    formula: [
      '| \\sum_{i} w_{i} + w_{cash} - 1 | \\le 10^{-6}',
      'NAV_{t} = NAV_{t-1} (1 + r^{gross}_{t}) - \\text{Fees}_{t} - \\text{Impact}_{t}',
      '| (R^{p} - R^{b}) - \\sum \\text{Effects} | \\le 1 \\text{ bp}'
    ],
    redline: '各项 |ε| <= 容差：权重和 1e-6、约束违反 1e-6、归因闭合 1 bp',
    pitfalls: [
      '只在期末检查守恒：分红到账、费用计提时点错误会在期间累积后又相互抵消',
      '容差宽于数值误差量级：1e-2 的权重容差足以藏下 1% 的现金漏算'
    ],
    related: ['closure_error', 'perold_is', 'risk_decomposition', 'psd'],
    mounts: 'P · B · R',
    source: 'builtin'
  },
  {
    id: 'param_cliff',
    zh: '参数敏感性悬崖',
    en: 'Parameter Cliff',
    domain: 'MATH',
    aliases: ['param cliff', 'parameter sensitivity', 'robustness', '参数悬崖', '邻域衰减'],
    plain: '把最优参数在网格上挪动一格，性能若断崖式下跌，说明结果是「针尖上的点」，大概率是过拟合。',
    formula: [
      '\\text{Drop} = \\max_{\\theta \\in N(\\theta^{*})} \\frac{SR(\\theta^{*}) - SR(\\theta)}{| SR(\\theta^{*}) |}',
      '\\text{Drop} \\le 0.30'
    ],
    symbols: ['N(\\theta^{*}): 最优参数 \\theta^{*} 的网格邻点集合'],
    gate: GATE_SPECS.param_cliff,
    redline: '邻点衰减 <= 30%（合同硬门禁），30%–50% 观察',
    pitfalls: [
      '网格过粗时任何参数都显得陡峭，过细又会掩盖悬崖：步长应与参数的经济含义匹配',
      '只评估单参数邻域：窗口与阈值等多参数联动的悬崖须在二维邻域检查'
    ],
    related: ['pbo', 'dsr', 'trial_count_m', 'walk_forward'],
    mounts: 'M 新卡 · R',
    source: 'builtin'
  },
  {
    id: 'regime_classification',
    zh: 'Regime 划分',
    en: 'Regime Classification',
    domain: 'MATH',
    aliases: ['regime', 'market regime', 'bull bear', 'volatility regime', '市场状态', '牛熊', '大小盘'],
    plain: '用可复现的定量口径把每个交易日划入市场状态（牛 / 熊 / 震荡）、波动状态（低 / 中 / 高）与市值层（大 / 中 / 小），判定只使用前收盘及之前的数据。',
    formula: [
      '\\text{Market}_{t}: \\text{trend}^{60}_{t-1} \\text{ 与 } DD^{120}_{t-1} \\text{ 双阈值 + 迟滞} \\to \\text{BULL}, \\text{BEAR}, \\text{SIDEWAYS}',
      '\\text{Vol}_{t}: \\sigma^{20}_{t-1} \\text{ 在前 252 日分布中的 1/3、2/3 分位} \\to \\text{LOW}, \\text{MID}, \\text{HIGH}'
    ],
    symbols: ['trend^{60}: 60 日趋势收益', 'DD^{120}: 120 日回撤', '\\sigma^{20}: 20 日已实现波动率'],
    redline: '口径与快照绑定（建议），判定只用 t-1 及之前数据',
    pitfalls: [
      '用全样本分位划分波动状态：等于前视，且历史 regime 会随新数据改变',
      '没有迟滞阈值：状态在边界附近频繁翻转，每个状态的样本被切碎'
    ],
    related: ['regime_consistency_score', 'dimension_dependence', 'regime_shift', 'pit_snapshot_lock'],
    mounts: 'A 切片矩阵 · R 基线',
    source: 'builtin'
  },
  {
    id: 'dimension_dependence',
    zh: '单维依赖度',
    en: 'Single-Dimension Dependence',
    domain: 'MATH',
    aliases: ['dominance', 'single dimension dominance', 'bucket concentration', '单桶依赖', '集中贡献'],
    plain: '在某一切片维度（市场状态、波动、市值层、行业、风格）下，超额收益是否主要来自其中一个桶；单桶贡献过高说明策略是对单一环境的押注。',
    formula: [
      'c_{b} = \\sum_{t \\in b} \\ell_{t} a_{t}',
      '\\text{Dom} = \\max_{b} \\frac{\\max(0, c_{b})}{\\sum_{b} \\max(0, c_{b})} \\le 0.60'
    ],
    symbols: ['a_{t}: 日超额收益', '\\ell_{t}: 几何链接权重'],
    gate: GATE_SPECS.dimension_dependence,
    redline: '单桶贡献 <= 60%（合同硬门禁），50%–60% 观察',
    pitfalls: [
      '分母只含正贡献：其他桶为负时单桶占比可达 100% 但总超额很小，须同时看绝对值',
      '各桶样本天数差异很大时，贡献占比混合了时间占比与效率：应同时报告桶内年化超额'
    ],
    related: ['regime_consistency_score', 'regime_classification', 'ic_win_rate', 'barra_attribution'],
    mounts: 'A 切片矩阵 · R 热力图',
    source: 'builtin'
  },
  {
    id: 'regime_consistency_score',
    zh: 'Regime 一致性得分',
    en: 'Regime Consistency Score',
    domain: 'MATH',
    aliases: ['consistency score', 'regime consistency', '状态一致性', '跨状态稳健性'],
    plain: '在市场状态（牛 / 熊 / 震荡）各桶中，超额收益 95% bootstrap 置信区间下界大于 0 的桶所占比例；衡量 alpha 是否跨环境成立。',
    formula: [
      'S = \\frac{#( b \\in B_{qual} : CI^{lo}_{95}(b) > 0 )}{# B},  \\quad B = ( \\text{BULL}, \\text{BEAR}, \\text{SIDEWAYS} )',
      'b \\in B_{qual}: n_{b} \\ge 60,  \\quad n_{b} \\ge 0.6 \\cdot n^{match}_{b},  \\quad \\text{episodes}_{b} \\ge 3'
    ],
    symbols: ['n_{b}: 桶内有效超额观测天数', 'n^{match}_{b}: 桶内匹配交易日数', 'episodes_{b}: 进入该状态的独立区段数'],
    redline: '无合同硬门禁 · 建议 S >= 2/3；无合格桶时为 N/A 而非 0',
    pitfalls: [
      '某状态样本不足（如熊市区段 < 3）时不计入支持数但仍计入分母：得分偏保守，解读时须看合格桶数',
      '与 OOS/IS 比联用：OOS 恰落在单一 regime 时，亮眼的样本外表现可能只是 regime 运气'
    ],
    related: ['regime_classification', 'dimension_dependence', 'bootstrap_ci', 'oos_is_ratio'],
    mounts: 'R 热力图 · A 切片矩阵',
    source: 'builtin'
  }
];
