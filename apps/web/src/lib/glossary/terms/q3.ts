import type { GlossaryTerm } from '../types';
import { GATE_SPECS } from '../../thresholds';

export const Q3_TERMS: GlossaryTerm[] = [
  {
    id: 'sharpe_is_oos',
    zh: '样本内 / 样本外夏普',
    en: 'IS vs OOS Sharpe Ratio',
    domain: 'Q3',
    prd: 'Q3.1',
    aliases: ['Sharpe', 'SR', 'IS Sharpe', 'OOS Sharpe', 'in-sample', 'out-of-sample', '样本内夏普', '样本外夏普'],
    plain: '训练期（样本内）与模型从未见过的测试期（样本外）上的年化风险调整收益；只有样本外夏普才有决策意义。',
    formula: [
      'SR = \\frac{\\mathbb{E}[r_{t} - r_{f}]}{\\sigma(r_{t})} \\cdot \\sqrt{252}',
      '\\sigma(\\hat{SR}) \\approx \\sqrt{\\frac{1 + \\hat{SR}^{2} / 2}{T}}  \\quad (\\text{IID 正态，未年化})'
    ],
    redline: '无硬门禁 · 描述性指标，由 OOS/IS 比与 DSR 判定',
    pitfalls: [
      '样本外期被用于调参或挑模型后就不再是样本外：holdout 必须一次性锁定',
      '日收益存在自相关时 √252 年化会误估夏普：需按 Lo (2002) 做自相关修正'
    ],
    related: ['oos_is_ratio', 'dsr', 'walk_forward', 'cpcv'],
    mounts: 'M KPI · 表头',
    source: 'builtin'
  },
  {
    id: 'oos_is_ratio',
    zh: 'OOS/IS 比',
    en: 'OOS-to-IS Performance Ratio',
    domain: 'Q3',
    prd: 'Q3.2',
    aliases: ['OOS/IS', 'IS/OOS ratio', 'degradation ratio', '样本外保留率'],
    plain: '样本外表现与样本内表现之比，衡量模型从训练期到未见数据保留了多少；过低说明样本内表现主要来自拟合噪声。',
    formula: ['\\text{Ratio} = \\frac{SR_{OOS}}{SR_{IS}},  \\quad SR_{IS} > 0'],
    gate: GATE_SPECS.oos_is_ratio,
    redline: 'OOS/IS >= 0.50（合同硬门禁），0.40–0.50 观察',
    pitfalls: [
      'SR_IS 接近 0 或为负时比值无意义甚至符号反转：应直接判 FAIL 而非报告比值',
      '比值 > 1 不代表更好：常见于 OOS 恰逢有利 regime，须结合 regime 一致性检验'
    ],
    related: ['sharpe_is_oos', 'pbo', 'walk_forward', 'regime_consistency_score'],
    mounts: 'M KPI · 表头',
    source: 'builtin'
  },
  {
    id: 'cpcv',
    zh: '组合清除交叉验证',
    en: 'Combinatorial Purged Cross-Validation',
    domain: 'Q3',
    prd: 'Q3.2',
    aliases: ['CPCV', 'combinatorial purged CV', 'Purged K-Fold', '组合交叉验证'],
    plain: '把时间序列切成 N 组，任取 k 组做测试、其余训练（训练集做 purge 与 embargo），拼出多条完整的样本外路径，得到夏普的分布而非单点。',
    formula: [
      'C(N, k) = \\frac{N!}{k! (N-k)!}',
      '\\varphi(N, k) = \\frac{k}{N} C(N, k)  \\quad (\\text{完整 OOS 路径数})'
    ],
    symbols: ['N: 时间分组数', 'k: 每次切分的测试组数'],
    gate: GATE_SPECS.cpcv,
    redline: 'OOS 路径数 >= 15（建议门禁），且每条路径均完成 purge + embargo',
    pitfalls: [
      'N 过大导致每个测试块太短（不足标签窗口数倍），purge 后训练样本所剩无几',
      '把 φ 条路径当独立样本做显著性检验：路径之间共享测试块，高度相关'
    ],
    related: ['purge', 'embargo', 'pbo', 'negative_paths'],
    mounts: 'M 面板 · R',
    source: 'builtin'
  },
  {
    id: 'purge',
    zh: '清除',
    en: 'Purging',
    domain: 'Q3',
    prd: 'Q3.2',
    aliases: ['purge', 'purging', 'purged k-fold', 'label overlap removal', '样本清除'],
    plain: '从训练集中删除标签区间与测试集标签区间重叠的样本，避免训练集「看到」测试期的收益。',
    formula: [
      't_{i,1} \\ge t^{test}_{0} \\text{ 且 } t_{i,0} \\le t^{test}_{1} \\Rightarrow \\text{从训练集删除 } i',
      '\\text{Conflicts} = #( i \\in \\text{Train} : \\text{标签区间与测试区间重叠} ) = 0'
    ],
    symbols: ['[t_{i,0}, t_{i,1}]: 样本 i 的标签观测区间（如 t+1 至 t+h）', '[t^{test}_{0}, t^{test}_{1}]: 测试块标签区间'],
    gate: GATE_SPECS.purge,
    redline: 'Purge 冲突 = 0（代码门禁）',
    pitfalls: [
      '只按特征日期 purge，忽略标签向后延伸 h 日：20 日收益标签须清除测试块前至少 20 个交易日的训练样本',
      '多期限标签混用时按最短期限 purge：长期限标签仍然泄漏'
    ],
    related: ['embargo', 'cpcv', 'look_ahead_bias', 'pbo'],
    mounts: 'M KPI',
    source: 'builtin'
  },
  {
    id: 'embargo',
    zh: '禁运期',
    en: 'Embargo',
    domain: 'Q3',
    prd: 'Q3.2',
    aliases: ['embargo', 'embargo period', 'gap', '隔离期'],
    plain: '在每个测试块之后再空出一段时间不用于训练，阻断特征序列相关造成的后向泄漏。',
    formula: [
      'h_{emb} = \\lceil \\epsilon \\cdot T \\rceil,  \\quad h_{emb} \\ge h_{label}',
      '\\text{Train} \\text{ 剔除 } (t^{test}_{1}, t^{test}_{1} + h_{emb}]'
    ],
    symbols: ['\\epsilon: 禁运比例（常用 0.01）', 'h_{label}: 标签窗口长度'],
    redline: 'Embargo >= 标签窗口（建议）',
    pitfalls: [
      '只设比例 ε 不核对绝对天数：样本较短时 1% 可能只有 2-3 天，小于 20 日标签窗口',
      '特征含长窗口（如 250 日动量）时，仅按标签窗口设 embargo 不足以阻断特征持续性泄漏'
    ],
    related: ['purge', 'cpcv', 'walk_forward'],
    mounts: 'M KPI 副行 · WF 图例',
    source: 'builtin'
  },
  {
    id: 'pbo',
    zh: '过拟合概率',
    en: 'Probability of Backtest Overfitting',
    domain: 'Q3',
    prd: 'Q3.2',
    aliases: ['PBO', 'CPCV PBO', 'CSCV', 'backtest overfitting', '回测过拟合概率'],
    plain: '把所有参数组合在样本内挑出的冠军，拿到样本外排名跌到中位数以下的概率。越高越像是在拟合噪声。',
    formula: [
      '\\lambda_{c} = \\ln \\frac{\\bar{\\omega}_{c}}{1 - \\bar{\\omega}_{c}},  \\quad \\bar{\\omega}_{c} = \\frac{\\text{rank}_{OOS}(n^{*}_{c})}{N + 1}',
      'PBO = \\int_{-\\infty}^{0} f(\\lambda) d\\lambda \\approx \\frac{#( c : \\lambda_{c} \\le 0 )}{# C}'
    ],
    symbols: ['N: 参数组合数', 'n^{*}_{c}: 第 c 次切分的样本内最优组合', 'C: CSCV 切分集合'],
    gate: GATE_SPECS.pbo,
    redline: 'PBO <= 0.25（合同硬门禁），0.20–0.25 观察',
    pitfalls: [
      '只报告 PBO 不报告试验次数 M：M 越大 PBO 越容易虚低',
      'purge / embargo 过短会让 PBO 系统性偏乐观'
    ],
    related: ['dsr', 'cpcv', 'purge', 'trial_count_m', 'bh_fdr'],
    mounts: 'M KPI · 门禁 · R 证明链',
    source: 'builtin'
  },
  {
    id: 'dsr',
    zh: '紧缩夏普',
    en: 'Deflated Sharpe Ratio',
    domain: 'Q3',
    prd: 'Q3.2',
    aliases: ['DSR', 'deflated sharpe', 'PSR', '紧缩夏普比率', '通缩夏普'],
    plain: '扣除多重试验带来的选择偏差与收益非正态性之后，观测夏普仍显著大于 0 的概率。',
    formula: [
      'DSR = \\Phi( \\frac{(\\hat{SR} - SR_{0}) \\sqrt{T - 1}}{\\sqrt{1 - \\gamma_{3} \\hat{SR} + \\frac{\\gamma_{4} - 1}{4} \\hat{SR}^{2}}} )',
      'SR_{0} = \\sqrt{V[\\hat{SR}_{m}]} ( (1 - \\gamma) \\Phi^{-1}(1 - \\frac{1}{M}) + \\gamma \\Phi^{-1}(1 - \\frac{1}{M e}) )'
    ],
    symbols: [
      '\\hat{SR}: 未年化夏普（与 T 同频）',
      '\\gamma_{3}, \\gamma_{4}: 收益偏度与峰度（非超额峰度）',
      'M: 独立试验次数；\\gamma \\approx 0.5772 为 Euler-Mascheroni 常数'
    ],
    gate: GATE_SPECS.dsr,
    redline: 'DSR >= 0.95（合同硬门禁），0.90–0.95 观察',
    pitfalls: [
      '代入年化 SR 而 T 为日数：频率不一致会让 DSR 虚高到接近 1',
      'M 只计最终提交的配置而不计全部尝试：SR_0 被低估，DSR 虚高'
    ],
    related: ['pbo', 'trial_count_m', 'sharpe_is_oos', 'bh_fdr', 'hansen_spa'],
    mounts: 'M KPI · 门禁 · R 证明链',
    source: 'builtin'
  },
  {
    id: 'trial_count_m',
    zh: '试验次数',
    en: 'Number of Trials M',
    domain: 'Q3',
    prd: 'Q3.2',
    aliases: ['M', 'trial count', 'number of trials', 'multiple testing', '试验数', '尝试次数'],
    plain: '为得到当前结果一共尝试过多少种因子、参数与模型配置；它决定多重检验惩罚的强度，是 DSR、PBO、BH-FDR 的共同输入。',
    formula: [
      '\\mathbb{E}[ \\max_{m \\le M} \\hat{SR}_{m} ] \\approx \\sqrt{V[\\hat{SR}]} ( (1 - \\gamma) \\Phi^{-1}(1 - \\frac{1}{M}) + \\gamma \\Phi^{-1}(1 - \\frac{1}{M e}) )',
      'M_{eff} \\approx \\bar{\\rho} + (1 - \\bar{\\rho}) M'
    ],
    symbols: ['\\bar{\\rho}: 试验收益序列的平均相关', 'M_{eff}: 有效独立试验数'],
    redline: '必须申报（建议门禁）：未申报 M 的 DSR / PBO 不予入库',
    pitfalls: [
      '只申报本次研究的 M：同一研究家族历史上的尝试与墓碑记录都应累计',
      '把高度相关的参数网格当独立试验：M 过大会过度惩罚，应估计有效试验数 M_eff'
    ],
    related: ['dsr', 'pbo', 'bh_fdr', 'study_lineage', 'falsified_tombstone'],
    mounts: 'M KPI 副行 · 表头',
    source: 'builtin'
  },
  {
    id: 'walk_forward',
    zh: '滚动样本外',
    en: 'Walk-Forward Analysis',
    domain: 'Q3',
    prd: 'Q3.2',
    aliases: ['WF', 'walk forward', 'rolling OOS', 'expanding window', '滚动回测'],
    plain: '按时间顺序滚动：用过去窗口训练、紧接的下一段测试，再整体向前推进，模拟真实的定期再训练流程。',
    formula: [
      '\\text{Train}_{j} = [t_{j} - W, t_{j}),  \\quad \\text{Test}_{j} = [t_{j} + h_{emb}, t_{j} + h_{emb} + S)',
      '\\bar{IR}_{OOS} = \\frac{1}{J} \\sum_{j=1}^{J} IR^{OOS}_{j} \\ge 0'
    ],
    symbols: ['W: 训练窗口长度', 'S: 测试步长', 'J: 滚动折数'],
    redline: 'OOS IR 均值 >= 0（代码 Tag 逻辑）',
    pitfalls: [
      '只有一条路径：结果高度依赖切分起点，应配合 CPCV 获得路径分布',
      '反复调优窗口长度 W 等于在 OOS 上做参数选择：须计入试验次数 M'
    ],
    related: ['cpcv', 'embargo', 'oos_is_ratio', 'sharpe_is_oos'],
    mounts: 'M 面板',
    source: 'builtin'
  },
  {
    id: 'negative_paths',
    zh: '负夏普路径占比',
    en: 'Negative Sharpe Path Ratio',
    domain: 'Q3',
    prd: 'Q3.2',
    aliases: ['negative paths', 'negative path ratio', 'path loss ratio', '亏损路径占比'],
    plain: 'CPCV 生成的多条样本外路径中夏普为负的比例；比单条回测更能反映策略是否稳健。',
    formula: ['\\text{NegRatio} = \\frac{#( p : SR^{OOS}_{p} < 0 )}{\\varphi(N, k)}'],
    gate: GATE_SPECS.negative_paths,
    redline: '负路径占比 <= 20%（建议门禁）',
    pitfalls: [
      '路径数少于 15 时比例颗粒度过粗：一条路径就能改变 5-20 个百分点',
      '路径之间共享测试块，负路径往往集中在同一 regime：应结合 regime 一致性解读'
    ],
    related: ['cpcv', 'pbo', 'regime_consistency_score', 'sharpe_is_oos'],
    mounts: 'M 门禁',
    source: 'builtin'
  }
];
