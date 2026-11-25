import type { GlossaryTerm } from '../types';
import { GATE_SPECS } from '../../thresholds';

export const Q2_TERMS: GlossaryTerm[] = [
  {
    id: 'rank_ic',
    zh: 'RankIC 均值',
    en: 'Rank Information Coefficient',
    domain: 'Q2',
    prd: 'Q2.3',
    aliases: ['RankIC', 'Rank IC', 'IC', 'Spearman IC', '信息系数', '秩相关 IC'],
    plain: '每天把因子值排名与下一期收益排名做 Spearman 相关，再对时间求均值；衡量因子截面排序的预测力。',
    formula: [
      'IC_{t} = \\rho_{S}( \\text{rank}(f_{\\cdot,t}), \\text{rank}(r_{\\cdot,t+1 \\to t+1+h}) )',
      '\\bar{IC} = \\frac{1}{T} \\sum_{t=1}^{T} IC_{t}'
    ],
    symbols: ['\\rho_{S}: Spearman 秩相关', 'h: 预测期（1D / 5D / 20D）'],
    gate: GATE_SPECS.rank_ic,
    redline: 'RankIC >= 0.03（合同硬门禁），0.02–0.03 观察',
    pitfalls: [
      '收益起点取 t 日收盘：信号在 t 日收盘后生成，只能 t+1 开盘成交，标签应从 t+1 开盘计起',
      '多期限重叠标签（h > 1）直接对日频 IC 做普通 t 检验会严重高估显著性，须用 Newey-West'
    ],
    related: ['icir', 'nw_t_stat', 'ic_win_rate', 'ic_decay'],
    mounts: 'A KPI · K',
    source: 'builtin'
  },
  {
    id: 'icir',
    zh: 'ICIR 年化',
    en: 'IC Information Ratio (Annualized)',
    domain: 'Q2',
    prd: 'Q2.3',
    aliases: ['ICIR', 'IC IR', 'IR of IC', 'IC 信息比率'],
    plain: 'IC 均值除以 IC 标准差再年化；衡量预测力是否稳定，比单看 IC 均值更能区分偶然与持续。',
    formula: [
      'ICIR_{ann} = \\frac{\\bar{IC}}{\\sigma(IC)} \\cdot \\sqrt{252}',
      'ICIR^{(h)}_{ann} = \\frac{\\bar{IC}}{\\sigma(IC)} \\sqrt{252 / h}  \\quad (\\text{非重叠 h 日样本})'
    ],
    gate: GATE_SPECS.icir,
    redline: 'ICIR 年化 >= 1.50（合同硬门禁），1.00–1.50 观察',
    pitfalls: [
      '对重叠 h 日标签仍乘 √252：重叠样本的 σ(IC) 被低估，ICIR 虚高可达约 √h 倍',
      '样本期短于 2 年时 ICIR 置信区间极宽，须配合 bootstrap CI 一并报告'
    ],
    related: ['rank_ic', 'nw_t_stat', 'bootstrap_ci', 'information_ratio'],
    mounts: 'A KPI',
    source: 'builtin'
  },
  {
    id: 'nw_t_stat',
    zh: 'NW t 统计量',
    en: 'Newey-West t-statistic',
    domain: 'Q2',
    prd: 'Q2.3',
    aliases: ['NW t', 'Newey-West t', 'HAC t', 't-stat', 't 值'],
    plain: '用对自相关与异方差稳健的 Newey-West 标准误检验 IC 均值是否显著异于 0；重叠标签下普通 t 值会严重虚高。',
    formula: [
      't_{NW} = \\frac{\\bar{IC}}{\\sqrt{\\hat{V}_{NW} / T}}',
      '\\hat{V}_{NW} = \\hat{\\gamma}_{0} + 2 \\sum_{l=1}^{L} (1 - \\frac{l}{L+1}) \\hat{\\gamma}_{l},  \\quad L = \\lfloor 4 (T/100)^{2/9} \\rfloor'
    ],
    symbols: ['\\hat{\\gamma}_{l}: IC 序列 l 阶自协方差', 'L: Bartlett 核截断滞后阶数'],
    gate: GATE_SPECS.nw_t_stat,
    redline: 'NW t >= 3.00（合同硬门禁，多重检验口径），2.00–3.00 观察',
    pitfalls: [
      'h 日重叠标签时滞后阶数至少取 h - 1：自动带宽 L 小于 h - 1 会低估方差',
      't >= 2 只适用于单次检验；在因子挖掘的多重检验情境下须提高到 3 并配合 BH-FDR'
    ],
    related: ['newey_west_hac', 'rank_ic', 'icir', 'bh_fdr'],
    mounts: 'A KPI',
    source: 'builtin'
  },
  {
    id: 'ic_win_rate',
    zh: 'IC 胜率',
    en: 'IC Hit Rate',
    domain: 'Q2',
    prd: 'Q2.3',
    aliases: ['IC win rate', 'hit rate', 'hit ratio', 'IC 正比例'],
    plain: 'IC 为正的交易日占比；与 IC 均值一起看，均值高但胜率低说明收益来自少数极端交易日。',
    formula: ['\\text{HitRate} = \\frac{#( t : IC_{t} > 0 )}{T}'],
    gate: GATE_SPECS.ic_win_rate,
    redline: 'IC 胜率 >= 55%（建议门禁）',
    pitfalls: [
      '负向因子（IC 均值 < 0）须先翻转符号再算胜率，否则会被误判为失效',
      '胜率对 IC 幅度不敏感：胜率 60% 但亏损日 IC 绝对值更大时，整体仍可能为负'
    ],
    related: ['rank_ic', 'icir', 'dimension_dependence'],
    mounts: 'A KPI 副行',
    source: 'builtin'
  },
  {
    id: 'ic_decay',
    zh: 'IC 滞后衰减',
    en: 'IC Decay by Lag',
    domain: 'Q2',
    prd: 'Q2.3',
    aliases: ['IC decay', 'signal decay', 'lagged IC', '信号衰减'],
    plain: '把信号延迟 k 日再与收益计算 IC，观察预测力随延迟衰减的速度；决定执行延迟容忍度与调仓频率。',
    formula: [
      'IC(k) = \\mathbb{E}_{t}[ \\rho_{S}( f_{\\cdot,t}, r_{\\cdot,t+k+1 \\to t+k+1+h} ) ]',
      '\\text{Retention}(k) = IC(k) / IC(0),  \\quad \\text{Retention}(5) \\ge 0.5'
    ],
    redline: 'Lag5 保留率 >= 50%（建议）',
    pitfalls: [
      '只看 lag 0：若 lag 1 骤降，说明 alpha 主要来自 t+1 开盘跳空，实盘集合竞价难以获取',
      '衰减曲线非单调（lag 3 高于 lag 1）多为周内效应或数据对齐错误，而非真实信号'
    ],
    related: ['half_life', 'rank_ic', 'turnover_oneway', 'open_gap'],
    mounts: 'A 面板',
    source: 'builtin'
  },
  {
    id: 'half_life',
    zh: 'IC 半衰期',
    en: 'Signal Half-life',
    domain: 'Q2',
    prd: 'Q2.3',
    aliases: ['half-life', 'HL', 'decay half life', '半衰期'],
    plain: 'IC 衰减到初始一半所需的天数；半衰期应不短于调仓周期，否则调仓时信号已大半失效。',
    formula: ['IC(k) \\approx IC(0) e^{-\\lambda k}', 'HL = \\frac{\\ln 2}{\\lambda}'],
    redline: 'HL >= 调仓周期（建议）',
    pitfalls: [
      '对噪声较大的 IC(k) 直接做指数拟合，λ 可能为负或极不稳定：需对 k 加权并给出 bootstrap 区间',
      '半衰期远长于调仓周期时提高调仓频率只会增加换手成本，不会增加 alpha'
    ],
    related: ['ic_decay', 'turnover_oneway', 'turnover_lambda', 'bootstrap_ci'],
    mounts: 'A KPI',
    source: 'builtin'
  },
  {
    id: 'monotonicity',
    zh: '分层单调性',
    en: 'Decile Monotonicity',
    domain: 'Q2',
    prd: 'Q2.3',
    aliases: ['monotonicity', 'quantile monotonicity', 'decile spread', '单调性', '分组单调'],
    plain: '按因子值分 10 组，检验 Q1 到 Q10 的平均收益是否随组号单调递增；单调性差说明因子只在尾部有效或存在非线性。',
    formula: ['\\rho_{mono} = \\rho_{S}( (1, 2, ..., 10), (\\bar{r}_{Q1}, \\bar{r}_{Q2}, ..., \\bar{r}_{Q10}) )'],
    gate: GATE_SPECS.monotonicity,
    redline: 'Spearman ρ >= 0.90（合同硬门禁），0.70–0.90 观察',
    pitfalls: [
      '分组用全样本分位点而非逐日截面分位：既引入前视，又让组间股票数不均',
      '只有 10 个点的 Spearman 对单组噪声非常敏感：须结合多空收益与 bootstrap CI 判断'
    ],
    related: ['long_short', 'top_excess', 'rank_ic'],
    mounts: 'A 面板 Tag',
    source: 'builtin'
  },
  {
    id: 'long_short',
    zh: '多空收益',
    en: 'Long-Short Return (Q10 - Q1)',
    domain: 'Q2',
    prd: 'Q2.3',
    aliases: ['long short', 'L/S', 'Q10-Q1', 'spread return', '多空组合'],
    plain: '做多因子最高组、做空最低组的年化收益；衡量因子整体区分度，但 A 股融券受限，实盘难以完整获取。',
    formula: [
      'r^{LS}_{t} = \\bar{r}_{Q10,t} - \\bar{r}_{Q1,t}',
      'R^{LS}_{ann} = ( \\prod_{t=1}^{T} (1 + r^{LS}_{t}) )^{252/T} - 1'
    ],
    redline: '> 0 且 95% bootstrap CI 不跨 0（建议）',
    pitfalls: [
      '空头腿收益主要来自小市值、ST 与流动性枯竭股票，券源不足时无法实现',
      '未扣双边成本：多空组合换手约为单边多头的两倍，成本侵蚀更显著'
    ],
    related: ['top_excess', 'monotonicity', 'bootstrap_ci', 'gross_vs_net'],
    mounts: 'A KPI',
    source: 'builtin'
  },
  {
    id: 'top_excess',
    zh: 'Top 组超额',
    en: 'Top-Decile Excess Return',
    domain: 'Q2',
    prd: 'Q2.3',
    aliases: ['top excess', 'long-only excess', 'Q10 excess', '多头超额'],
    plain: 'A 股融券受限，真正能拿到的是多头组合相对基准（如中证 500）的超额收益。',
    formula: [
      'r^{top}_{t} = \\bar{r}_{Q10,t} - r^{b}_{t}',
      'R^{top}_{ann} = 252 \\cdot \\frac{1}{T} \\sum_{t=1}^{T} r^{top}_{t}'
    ],
    redline: '> 0（建议），且须在 NET 口径下成立',
    pitfalls: [
      '基准错配：股票池为中证 1000 却对比沪深 300，超额主要是市值风格 beta',
      'Top 组等权而基准市值加权，超额中混入小盘溢价：须用 Barra 归因甄别'
    ],
    related: ['long_short', 'barra_attribution', 'size_neutral', 'gross_vs_net'],
    mounts: 'A KPI',
    source: 'builtin'
  },
  {
    id: 'turnover_oneway',
    zh: '单边日换手',
    en: 'One-way Daily Turnover',
    domain: 'Q2',
    prd: 'Q2.3',
    aliases: ['turnover', 'one-way turnover', 'daily turnover', 'TO', '换手率'],
    plain: '每天需要换掉的持仓权重比例（只算买入或卖出一侧）；直接决定交易成本与策略容量。',
    formula: [
      'TO_{t} = \\frac{1}{2} \\sum_{i} | w_{i,t} - w^{-}_{i,t} |',
      'w^{-}_{i,t} = \\frac{w_{i,t-1} (1 + r_{i,t})}{\\sum_{j} w_{j,t-1} (1 + r_{j,t})}'
    ],
    symbols: ['w^{-}_{i,t}: 价格漂移后的调仓前权重'],
    gate: GATE_SPECS.turnover_oneway,
    redline: '单边日换手 <= 15%（合同硬门禁），15%–20% 观察',
    pitfalls: [
      '用 t-1 目标权重而非漂移后权重计算：会把价格涨跌误算成交易',
      '日换手 15% 意味着年化约 37.8 倍单边换手，按单边综合成本约 0.15% 估算，年成本可超 5%'
    ],
    related: ['turnover_lambda', 'half_life', 'stamp_duty', 'sqrt_impact'],
    mounts: 'A KPI',
    source: 'builtin'
  },
  {
    id: 'max_corr_library',
    zh: '库内最大相关',
    en: 'Max Correlation to Library',
    domain: 'Q2',
    prd: 'Q2.3',
    aliases: ['MaxCorr', 'max corr', 'factor correlation', '因子相关性', '冗余度'],
    plain: '新因子与因子库中已入库因子的最大相关系数；与已有因子太像，说明没有增量信息。',
    formula: ['\\text{MaxCorr} = \\max_{j \\in \\text{Lib}} | \\frac{1}{T} \\sum_{t} \\rho_{S}( f_{\\cdot,t}, g^{(j)}_{\\cdot,t} ) |'],
    symbols: ['g^{(j)}: 库内第 j 个因子（同口径预处理后）'],
    gate: GATE_SPECS.max_corr_library,
    redline: 'MaxCorr < 0.70（合同硬门禁），0.50–0.70 观察',
    pitfalls: [
      '只比因子值相关、不比 IC 序列相关：因子值低相关但 IC 时序高度同步，仍是同一种 alpha',
      '比较前未做相同预处理（去极值、中性化）：风格共同成分会抬高相关性'
    ],
    related: ['barra_orthogonal', 'falsified_tombstone', 'crowding_index', 'rank_ic'],
    mounts: 'A KPI · 相关矩阵',
    source: 'builtin'
  },
  {
    id: 'mad_winsorize',
    zh: 'MAD 去极值',
    en: 'MAD Winsorization',
    domain: 'Q2',
    prd: 'Q2.2',
    aliases: ['MAD', 'winsorize', 'median absolute deviation', '去极值', '缩尾'],
    plain: '用截面中位数与中位数绝对偏差确定截断边界，把离群值压回边界；比均值加减 3 倍标准差更抗极端值。',
    formula: [
      's_{t} = 1.4826 \\cdot \\text{median}_{i}( | f_{i,t} - \\tilde{f}_{t} | )',
      'f^{w}_{i,t} = \\min( \\max( f_{i,t}, \\tilde{f}_{t} - k s_{t} ), \\tilde{f}_{t} + k s_{t} )'
    ],
    symbols: ['\\tilde{f}_{t}: 截面中位数', 'k: 截断倍数（代码默认 mad_k = 3.5）', '1.4826: 使 MAD 在正态下与 σ 一致'],
    redline: '无硬门禁 · 预处理参数 k = 3.5（代码默认）',
    pitfalls: [
      '去极值必须逐日截面进行：用全样本中位数与 MAD 截断是隐性前视',
      '离散型因子（如分析师覆盖数）MAD 可能为 0：需回退到 IQR 或标准差，否则全部值被压到中位数'
    ],
    related: ['zscore_rank', 'industry_neutral', 'coverage'],
    mounts: 'A Chip',
    source: 'builtin'
  },
  {
    id: 'industry_neutral',
    zh: '行业中性化',
    en: 'Industry Neutralization',
    domain: 'Q2',
    prd: 'Q2.2',
    aliases: ['industry neutral', 'sector neutral', '行业中性', '行业哑变量回归'],
    plain: '把因子对申万一级行业哑变量做截面回归取残差，剔除行业轮动带来的伪 alpha。',
    formula: [
      'f_{i,t} = \\sum_{k} \\beta_{k,t} D_{i,k,t} + \\varepsilon_{i,t}',
      'f^{neu}_{i,t} = \\hat{\\varepsilon}_{i,t}'
    ],
    symbols: ['D_{i,k,t}: 股票 i 当日是否属于申万一级行业 k（PIT）'],
    redline: '残余行业暴露 |z| <= 0.30σ（代码）',
    pitfalls: [
      '用最新而非 PIT 的申万行业归属构造哑变量：引入前视',
      '小行业（成分 < 5 只）残差几乎被压为 0：建议合并小行业或使用加权回归'
    ],
    related: ['sw_industry_pit', 'size_neutral', 'residual_exposure', 'barra_orthogonal'],
    mounts: 'A Chip',
    source: 'builtin'
  },
  {
    id: 'size_neutral',
    zh: '市值中性化',
    en: 'Size Neutralization',
    domain: 'Q2',
    prd: 'Q2.2',
    aliases: ['size neutral', 'log mcap', 'Size 中性', '市值中性'],
    plain: '把因子对对数流通市值（通常与行业一起）做截面回归取残差，剔除 A 股显著的小盘溢价。',
    formula: ['f_{i,t} = \\alpha_{t} + \\beta_{t} \\ln(MV_{i,t}) + \\sum_{k} \\gamma_{k,t} D_{i,k,t} + \\varepsilon_{i,t}'],
    symbols: ['MV_{i,t}: 流通市值'],
    redline: 'Size 残余暴露 |z| <= 0.30σ（代码）',
    pitfalls: [
      '只做线性 ln(MV) 中性：非线性市值（Barra Mid-Cap）仍会残留，中证 1000 股票池尤其明显',
      '用总市值而非流通市值：大比例限售股公司的可交易市值被高估'
    ],
    related: ['industry_neutral', 'barra_orthogonal', 'residual_exposure', 'style_exposure'],
    mounts: 'A Chip · Meter',
    source: 'builtin'
  },
  {
    id: 'barra_orthogonal',
    zh: 'Barra 风格正交',
    en: 'Barra Style Orthogonalization',
    domain: 'Q2',
    prd: 'Q2.2',
    aliases: ['orthogonalization', 'style neutral', 'Barra neutral', '风格正交', '正交化'],
    plain: '把因子对 Barra CNE6 风格与行业暴露做加权截面回归，只保留残差，确保剩下的是风格模型解释不了的增量信息。',
    formula: [
      '\\hat{\\beta}_{t} = (X_{t}^{T} W_{t} X_{t})^{-1} X_{t}^{T} W_{t} f_{t}',
      'f^{orth}_{t} = f_{t} - X_{t} \\hat{\\beta}_{t}'
    ],
    symbols: ['X_{t}: 风格 + 行业暴露矩阵', 'W_{t}: 回归权重（常用 √流通市值）'],
    redline: '残余风格暴露 |z| <= 0.30σ（代码）',
    pitfalls: [
      '正交掉动量、反转等风格后 IC 大幅下降：说明原因子本质是风格 beta，不应以原始 IC 入库',
      '风格暴露与因子不是同一时点（如月频 Barra 对日频因子）：残差中仍残留风格'
    ],
    related: ['barra_cne6', 'residual_exposure', 'max_corr_library', 'barra_attribution'],
    mounts: 'A Chip',
    source: 'builtin'
  },
  {
    id: 'zscore_rank',
    zh: '截面标准化',
    en: 'Z-Score vs Rank Normalization',
    domain: 'Q2',
    prd: 'Q2.2',
    aliases: ['z-score', 'zscore', 'rank', 'rank gauss', 'normalization', '标准化', '排序标准化'],
    plain: '把每日截面因子值变换到可比尺度：Z-Score 保留幅度信息；Rank 更抗极值，且对价格尺度保序。',
    formula: [
      'z_{i,t} = \\frac{f_{i,t} - \\mu_{t}}{\\sigma_{t}}',
      'u_{i,t} = \\frac{\\text{rank}(f_{i,t}) - 0.5}{N_{t}} - 0.5,  \\quad g_{i,t} = \\Phi^{-1}(u_{i,t} + 0.5)'
    ],
    symbols: ['N_{t}: 当日有效截面股票数', 'g_{i,t}: rank-gauss 变换'],
    redline: '无硬门禁 · 方法选择须与入库口径一致',
    pitfalls: [
      'Z-Score 前未去极值：一个离群值就能压缩其余样本的方差',
      'Rank 丢失幅度信息：对强弱差异很大的事件型因子，会削弱头部信号'
    ],
    related: ['mad_winsorize', 'scale_invariance', 'rank_ic'],
    mounts: 'A Seg',
    source: 'builtin'
  },
  {
    id: 'residual_exposure',
    zh: '残余风格暴露',
    en: 'Residual Style Exposure',
    domain: 'Q2',
    prd: 'Q2.2',
    aliases: ['residual exposure', 'EXP_LIMIT', 'style leak', '残余暴露', '风格泄漏'],
    plain: '预处理（去极值、中性化、正交化）之后，因子残差与各风格 / 行业暴露之间仍存在的加权相关程度；超限即风格泄漏。',
    formula: [
      'e_{k,t} = \\frac{| (\\sqrt{W} X_{k})^{T} (\\sqrt{W} \\varepsilon_{t}) |}{| \\sqrt{W} X_{k} | \\cdot | \\sqrt{W} \\varepsilon_{t} |}',
      'e_{k} = \\max_{t} e_{k,t} \\le 0.30'
    ],
    symbols: ['X_{k}: 第 k 个风格或行业暴露列', '\\varepsilon_{t}: 预处理后的因子残差'],
    gate: GATE_SPECS.residual_exposure,
    redline: '|z| <= 0.30σ（代码门禁 EXP_LIMIT）',
    pitfalls: [
      '只检查参与回归的风格：未纳入设计矩阵的风格（如非线性市值、Beta）可能仍高度暴露',
      '用日均值而非最大值汇总：个别交易日的严重泄漏会被平均掉'
    ],
    related: ['barra_orthogonal', 'industry_neutral', 'size_neutral', 'style_exposure'],
    mounts: 'A Meter 标题',
    source: 'builtin'
  },
  {
    id: 'ast_safe',
    zh: 'AST 安全检查',
    en: 'AST Safety Check',
    domain: 'Q2',
    prd: 'Q2.1',
    aliases: ['AST', 'AST safe', 'SAFE', 'expression sandbox', '静态扫描', '表达式白名单'],
    plain: '把因子表达式解析为抽象语法树做静态扫描：只放行白名单算子，拦截负向位移（未来函数）与文件、网络、eval 等危险调用。',
    formula: [
      '\\forall n \\in \\text{AST}: \\text{op}(n) \\in \\text{Whitelist}',
      '\\forall \\text{shift}(x, k) \\in \\text{AST}: k \\ge 0  \\quad (\\text{禁止 } k < 0 \\text{ 的未来位移})'
    ],
    redline: '状态 = SAFE（代码），任一 UNSAFE 节点直接拒绝执行',
    pitfalls: [
      '只靠正则黑名单：getattr 与 __import__ 字符串拼接、Unicode 同形字都能绕过，必须在 AST 层做白名单',
      '静态安全不等于因果正确：窗口对齐错误、全样本统计等需 AST 因果证明与前缀不变性兜底'
    ],
    related: ['ast_causal_proof', 'look_ahead_bias', 'prefix_invariance', 'mcp_side_effect'],
    mounts: 'A 状态栏 Tag',
    source: 'builtin'
  }
];
