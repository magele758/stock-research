import type { GlossaryTerm } from '../types';
import { GATE_SPECS } from '../../thresholds';

export const Q4_TERMS: GlossaryTerm[] = [
  {
    id: 'barra_cne6',
    zh: 'Barra CNE6 风险模型',
    en: 'Barra CNE6 China Equity Model',
    domain: 'Q4',
    prd: 'Q4.1',
    aliases: ['CNE6', 'CNE5', 'Barra', 'risk model', '多因子风险模型'],
    plain: '用国家因子、行业因子、风格因子（规模、贝塔、动量、波动、流动性、价值、成长、杠杆、盈利等）与特质收益解释每只股票的收益与风险。',
    formula: [
      'r_{i} = f_{c} + \\sum_{k} X^{ind}_{i,k} f_{k} + \\sum_{s} X^{sty}_{i,s} f_{s} + u_{i}',
      'V = X F X^{T} + \\Delta'
    ],
    symbols: ['F: 因子收益协方差矩阵', '\\Delta: 特质方差对角阵', 'f_{c}: 国家因子收益'],
    redline: '无硬门禁 · 风险模型本体（风格暴露与 PSD 另有门禁）',
    pitfalls: [
      '风格暴露未按市值加权做截面标准化：基准暴露不为 0，主动暴露被系统性偏移',
      '特质方差用短窗口估计时小盘股特质波动被低估：优化器会过配小盘股'
    ],
    related: ['style_exposure', 'risk_decomposition', 'ledoit_wolf', 'barra_attribution', 'psd'],
    mounts: 'P 面板标题',
    source: 'builtin'
  },
  {
    id: 'style_exposure',
    zh: '主动风格暴露',
    en: 'Active Style Exposure',
    domain: 'Q4',
    prd: 'Q4.1',
    aliases: ['style exposure', 'active exposure', 'factor exposure', '风格暴露', '风格偏离'],
    plain: '组合相对基准在某个 Barra 风格上的暴露差，以截面标准差为单位；超限说明收益主要来自风格押注而非选股。',
    formula: [
      'z_{s} = \\sum_{i} (w^{p}_{i} - w^{b}_{i}) X_{i,s}',
      '| z_{s} | \\le 0.5 \\sigma  \\quad \\forall s'
    ],
    gate: GATE_SPECS.style_exposure,
    redline: '|z| <= 0.50σ（合同硬门禁），0.30–0.50σ 观察',
    pitfalls: [
      '只约束调仓日暴露，不监控持有期漂移：动量等快速变化的风格会在持有期内越界',
      '基准成分权重不是 PIT：主动暴露的基线本身就是错的'
    ],
    related: ['barra_cne6', 'residual_exposure', 'tracking_error', 'opt_objective', 'barra_attribution'],
    mounts: 'P 龙卷风 · 门禁',
    source: 'builtin'
  },
  {
    id: 'industry_active_dev',
    zh: '行业主动偏离',
    en: 'Industry Active Weight',
    domain: 'Q4',
    prd: 'Q4.2',
    aliases: ['active weight', 'sector deviation', 'industry deviation', '行业偏离', '行业超低配'],
    plain: '组合在某个申万一级行业的权重减去基准在该行业的权重；限制偏离可避免把行业轮动押注误当成选股能力。',
    formula: [
      'a_{k} = \\sum_{i \\in k} w^{p}_{i} - \\sum_{i \\in k} w^{b}_{i}',
      '| a_{k} | \\le 0.03  \\quad \\forall k'
    ],
    redline: '|偏离| <= 3%（代码默认约束）',
    pitfalls: [
      '用最新申万归属计算历史偏离：被重新分类的股票让历史约束失真',
      '基准权重很小的行业（< 0.5%）上 ±3% 等于数倍超配：可增加相对比例约束'
    ],
    related: ['sw_industry_pit', 'brinson_allocation', 'industry_neutral', 'tracking_error'],
    mounts: 'P 滑杆 · 柱图',
    source: 'builtin'
  },
  {
    id: 'tracking_error',
    zh: '跟踪误差',
    en: 'Tracking Error',
    domain: 'Q4',
    prd: 'Q4.2',
    aliases: ['TE', 'tracking error', 'active risk', '主动风险'],
    plain: '组合相对基准超额收益的年化标准差；事前 TE 由风险模型预测，事后 TE 由实现收益计算。',
    formula: [
      'TE_{post} = \\sigma(r^{p}_{t} - r^{b}_{t}) \\cdot \\sqrt{252}',
      'TE_{ante} = \\sqrt{(w^{p} - w^{b})^{T} V (w^{p} - w^{b})}'
    ],
    redline: '<= 目标 TE（默认 5%，代码）',
    pitfalls: [
      '事前 TE 系统性低于事后 TE（协方差估计误差、风格漂移）：须持续监控两者比值',
      '基准用价格指数而非全收益指数：中证 500 两者每年相差约 1-2%，超额与 TE 均失真'
    ],
    related: ['information_ratio', 'risk_decomposition', 'style_exposure', 'ledoit_wolf'],
    mounts: 'P KPI · 滑杆',
    source: 'builtin'
  },
  {
    id: 'information_ratio',
    zh: '信息比率',
    en: 'Information Ratio',
    domain: 'Q4',
    prd: 'Q4.2',
    aliases: ['IR', 'information ratio', '信息比'],
    plain: '年化超额收益除以跟踪误差，衡量每承担一单位主动风险换来多少超额收益。',
    formula: [
      'a_{t} = r^{p}_{t} - r^{b}_{t},  \\quad IR = \\frac{\\bar{a}}{\\sigma(a)} \\sqrt{252}',
      'IR \\approx IC \\cdot \\sqrt{BR} \\cdot TC  \\quad (\\text{主动管理基本定律})'
    ],
    symbols: ['BR: 每年独立下注次数（广度）', 'TC: 转移系数'],
    gate: GATE_SPECS.information_ratio,
    redline: 'IR >= 0.50（建议门禁）',
    pitfalls: [
      '在 GROSS 口径报告 IR：A 股高换手策略扣费后 IR 常下降 30%-50%',
      '按股票数 × 调仓次数计算广度 BR：因子间相关与信号持续性让独立下注数远小于名义值'
    ],
    related: ['tracking_error', 'icir', 'calmar', 'gross_vs_net'],
    mounts: 'P KPI · B KPI',
    source: 'builtin'
  },
  {
    id: 'turnover_lambda',
    zh: '换手惩罚 λ',
    en: 'L1 Turnover Penalty',
    domain: 'Q4',
    prd: 'Q4.2',
    aliases: ['lambda', 'turnover penalty', 'transaction cost penalty', 'L1 penalty', '换手惩罚'],
    plain: '优化目标中对调仓量的 L1 惩罚系数；λ 越大越不愿调仓，以少量 alpha 换取更低的交易成本。',
    formula: ['\\max_{w} \\alpha^{T} w - \\frac{\\kappa}{2} w^{T} V w - \\lambda \\sum_{i} | w_{i} - w_{0,i} |'],
    symbols: ['w_{0}: 当前持仓（价格漂移后）', '\\kappa: 风险厌恶系数'],
    redline: '无硬门禁 · 由单边换手 <= 15% 与净 IR 共同约束',
    pitfalls: [
      'λ 与 α 量纲不一致（α 为 z 分数而 λ 按成本 bp 设定）：惩罚强度失真',
      '对称 L1 忽略 A 股买卖成本不对称：卖出另有 0.05% 印花税，宜按买卖方向分设'
    ],
    related: ['turnover_oneway', 'opt_objective', 'stamp_duty', 'sqrt_impact'],
    mounts: 'P 滑杆',
    source: 'builtin'
  },
  {
    id: 'enc',
    zh: '有效持股数',
    en: 'Effective Number of Constituents',
    domain: 'Q4',
    prd: 'Q4.2',
    aliases: ['ENC', 'effective N', 'inverse HHI', '1/HHI', '集中度'],
    plain: '权重平方和的倒数，衡量组合实际相当于分散在多少只等权股票上；比名义持股数更真实地反映集中度。',
    formula: ['ENC = \\frac{1}{\\sum_{i} w_{i}^{2}} = \\frac{1}{HHI}'],
    gate: GATE_SPECS.enc,
    redline: 'ENC >= 30（建议门禁）',
    pitfalls: [
      '名义持股 200 只但前 10 大占 50% 权重时，ENC 可能不足 30',
      'ENC 不反映相关性：30 只同行业股票的分散效果远不如 30 只跨行业股票'
    ],
    related: ['opt_objective', 'risk_decomposition', 'capacity_frontier'],
    mounts: 'P KPI',
    source: 'builtin'
  },
  {
    id: 'ledoit_wolf',
    zh: 'Ledoit-Wolf 收缩',
    en: 'Ledoit-Wolf Shrinkage Covariance',
    domain: 'Q4',
    prd: 'Q4.1',
    aliases: ['LW', 'Ledoit Wolf', 'shrinkage', 'covariance shrinkage', '收缩估计'],
    plain: '把噪声很大的样本协方差向结构化目标（常相关或单因子矩阵）收缩，收缩强度由解析公式最优选取，降低优化器对估计误差的放大。',
    formula: [
      '\\hat{\\Sigma} = \\delta F + (1 - \\delta) S',
      '\\delta^{*} = \\max( 0, \\min( 1, \\frac{\\hat{\\pi} - \\hat{\\rho}}{\\hat{\\gamma} T} ) )'
    ],
    symbols: ['S: 样本协方差', 'F: 收缩目标', '\\hat{\\pi}, \\hat{\\rho}, \\hat{\\gamma}: 估计误差、目标相关项与目标偏离'],
    redline: '收缩强度 δ ∈ [0, 1]；收缩后须通过 PSD 检查',
    pitfalls: [
      '股票数接近或超过样本长度（500 只股票用 250 日数据）时样本协方差奇异：不收缩会让优化器集中于伪低风险组合',
      '在 Barra 结构化协方差之上再做 LW 收缩：重复收缩会低估风险'
    ],
    related: ['psd', 'barra_cne6', 'risk_decomposition', 'tracking_error'],
    mounts: 'P 风险分解 · R',
    source: 'builtin'
  },
  {
    id: 'psd',
    zh: '半正定',
    en: 'Positive Semi-Definite',
    domain: 'Q4',
    prd: 'Q4.1',
    aliases: ['PSD', 'lambda min', 'min eigenvalue', 'positive semidefinite', '最小特征值'],
    plain: '协方差矩阵的最小特征值不能为负；否则优化器可以构造出「负方差」组合，得到虚假的零风险解。',
    formula: [
      '\\lambda_{min}(\\hat{\\Sigma}) \\ge -10^{-10}',
      'w^{T} \\hat{\\Sigma} w \\ge 0  \\quad \\forall w'
    ],
    gate: GATE_SPECS.psd,
    redline: 'λmin >= -1e-10（代码门禁）',
    pitfalls: [
      '对缺失值做成对删除（pairwise）估计协方差：最容易产生非 PSD 矩阵',
      '直接把负特征值截断为 0 会改变总方差：应做特征值修复后重缩放迹，或投影到最近 PSD 矩阵'
    ],
    related: ['ledoit_wolf', 'barra_cne6', 'solve_status', 'conservation_law'],
    mounts: 'P Tag · R 证明链',
    source: 'builtin'
  },
  {
    id: 'risk_decomposition',
    zh: '风险分解',
    en: 'Risk Decomposition',
    domain: 'Q4',
    prd: 'Q4.1',
    aliases: ['risk attribution', 'MCTR', 'risk contribution', '风险贡献', '边际风险贡献'],
    plain: '把预测跟踪误差拆为风格、行业、特质三部分及各股边际贡献；特质占比低说明主动风险主要来自因子押注。',
    formula: [
      'TE^{2} = a^{T} X F X^{T} a + a^{T} \\Delta a,  \\quad a = w^{p} - w^{b}',
      'RC_{i} = a_{i} \\frac{(V a)_{i}}{TE},  \\quad \\sum_{i} RC_{i} = TE'
    ],
    redline: '特质风险占比 >= 50%（建议）',
    pitfalls: [
      '风格与行业之间的协方差交叉项未显式分配：各分项之和不等于总风险',
      '只看方差占比不看边际贡献：少数大权重股票的 RC 可能主导特质风险'
    ],
    related: ['barra_cne6', 'tracking_error', 'style_exposure', 'conservation_law'],
    mounts: 'P 面板',
    source: 'builtin'
  },
  {
    id: 'opt_objective',
    zh: '优化目标',
    en: 'Optimization Objective',
    domain: 'Q4',
    prd: 'Q4.2',
    aliases: ['objective', 'mean-variance', 'max sharpe', 'min variance', 'risk parity', '均值方差', '风险平价'],
    plain: '组合优化器的目标函数选择：最大夏普、均值方差、最小方差、风险平价在对收益预测的依赖度与稳健性之间各有取舍。',
    formula: [
      '\\max_{w} \\alpha^{T} w - \\frac{\\kappa}{2} w^{T} V w - \\lambda \\sum_{i} | w_{i} - w_{0,i} |',
      '\\text{s.t. } \\sum_{i} w_{i} = 1,  \\quad 0 \\le w_{i} \\le u_{i},  \\quad | X^{T}_{s} (w - w^{b}) | \\le 0.5'
    ],
    redline: '无硬门禁 · 约束须满足：权重和偏差 <= 1e-6，约束违反 <= 1e-6',
    pitfalls: [
      '最大夏普对 α 估计误差极度敏感，常导致集中持仓：α 应先做收缩',
      'A 股多头约束下 w_i >= 0 大量触发，负向 alpha 信息几乎全部丢失，转移系数 TC 显著降低'
    ],
    related: ['turnover_lambda', 'solve_status', 'style_exposure', 'enc'],
    mounts: 'P Seg',
    source: 'builtin'
  },
  {
    id: 'solve_status',
    zh: '求解状态',
    en: 'Solver Status',
    domain: 'Q4',
    prd: 'Q4.2',
    aliases: ['solve status', 'OPTIMAL', 'RELAXED_OPTIMAL', 'INFEASIBLE', 'cvxpy status'],
    plain: '凸优化器的终止状态：OPTIMAL 为满足全部约束的最优解；RELAXED_OPTIMAL 表示放松了部分约束才有解，须逐项审阅。',
    formula: [
      '\\text{gap} = \\frac{| p^{*} - d^{*} |}{\\max(1, | p^{*} |)} \\le \\epsilon_{tol}',
      '\\max_{j} \\max( 0, g_{j}(w^{*}) ) \\le 10^{-6}'
    ],
    symbols: ['p^{*}, d^{*}: 原问题与对偶问题最优值', 'g_{j}: 第 j 个不等式约束'],
    redline: '状态 = OPTIMAL（代码 Tag），约束违反 <= 1e-6',
    pitfalls: [
      '把 OPTIMAL_INACCURATE 当作 OPTIMAL 使用：数值精度不足时约束可能已被违反',
      'RELAXED_OPTIMAL 未记录放松了哪条约束：审阅者无法判断风险是否越界'
    ],
    related: ['opt_objective', 'psd', 'style_exposure', 'conservation_law'],
    mounts: 'P 求解行',
    source: 'builtin'
  },
  {
    id: 'capacity_frontier',
    zh: '策略容量前沿',
    en: 'Strategy Capacity Frontier',
    domain: 'Q4',
    prd: 'Q4.2',
    aliases: ['capacity', 'AUM capacity', 'half-alpha AUM', '容量', '策略容量'],
    plain: '管理规模放大时冲击成本随之上升、逐步吞噬毛超额的曲线；净超额降到毛超额一半时的规模定义为策略容量。',
    formula: [
      'net(AUM) = gross - impact(AUM) - fees',
      'impact(AUM) = \\sum_{i} TO_{i} \\cdot Y \\sigma_{i} \\sqrt{\\frac{w_{i} \\cdot AUM}{ADV_{i}}}',
      'AUM^{cap}: net(AUM^{cap}) = 0.5 \\cdot gross'
    ],
    symbols: ['ADV_{i}: 日均成交额', 'Y: 冲击系数'],
    redline: '容量点：净超额 = 50% 毛超额（建议）',
    pitfalls: [
      '用股票池平均 ADV 估算：中证 1000 尾部股票日成交额可能不足千万元，容量被高估一个数量级',
      '忽略 POV 上限与涨跌停不可成交：冲击模型之外还有硬性成交约束'
    ],
    related: ['sqrt_impact', 'pov', 'turnover_oneway', 'liquidity_impact'],
    mounts: 'P · B 新面板',
    source: 'builtin'
  }
];
