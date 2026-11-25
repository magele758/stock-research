import type { GlossaryTerm } from '../types';
import { GATE_SPECS } from '../../thresholds';

export const Q1_TERMS: GlossaryTerm[] = [
  {
    id: 'point_in_time',
    zh: 'PIT 时点数据',
    en: 'Point-in-Time Data',
    domain: 'Q1',
    prd: 'Q1.1',
    aliases: ['PIT', 'point in time', 'as-of', 'asof', '时点数据', '公告日对齐'],
    plain: '任何时点 t 只能使用当时已经公开的信息：财报按公告日（ann_date）而非报告期入表，修订值按修订发布日生效。',
    formula: [
      'X_{i,t} = f( r \\in R_{i} \\mid \\text{AnnDate}_{r} \\le t )',
      '\\text{Violations} = #( r : \\text{AnnDate}_{r} > t_{use} ) = 0'
    ],
    symbols: ['R_{i}: 股票 i 的全部财报与公告记录', 't_{use}: 该记录被因子引用的交易日'],
    redline: '公告日晚于使用日的违规记录 = 0（建议门禁）',
    pitfalls: [
      '用报告期（end_date）对齐财报：一季报按 3/31 入表，实际 4 月底才披露，制造约 1 个月前视',
      '只保留最新修订版财务数据：业绩快报、正式年报与会计差错更正必须按各自发布日分版本存储'
    ],
    related: ['look_ahead_bias', 'pit_snapshot_lock', 'prefix_invariance', 'fact_fingerprint'],
    mounts: 'D 对账列 · A 编辑器 PIT asof · T PIT 锁',
    source: 'builtin'
  },
  {
    id: 'survivorship_bias',
    zh: '幸存者偏差',
    en: 'Survivorship Bias',
    domain: 'Q1',
    prd: 'Q1.3',
    aliases: ['QA-04', 'survivorship', 'delisting bias', '生存偏差', '退市偏差'],
    plain: '只用今天仍在交易的股票回测，会悄悄剔除历史上退市、摘牌的输家，系统性高估收益与夏普。',
    formula: [
      '\\text{Bias} = \\bar{R}_{alive} - \\bar{R}_{all}',
      '\\text{InclusionRate} = \\frac{#\\text{Delisted in Universe}_{t}}{#\\text{Delisted}_{t}} = 1'
    ],
    symbols: ['\\bar{R}_{alive}: 仅存续股票样本的平均收益', '\\bar{R}_{all}: 含退市股票的全样本平均收益'],
    redline: '退市股纳入率 = 100%（建议门禁），退市日收益按最后交易价或清算价计入',
    pitfalls: [
      '退市股票只保留到最后交易日却不计退市损失：退市整理期跌幅与摘牌后价值归零必须计入收益',
      '用当前 ST / 非 ST 状态过滤历史股票池：过滤条件本身也必须按 PIT 取值'
    ],
    related: ['historical_constituents', 'point_in_time', 'look_ahead_bias', 'coverage'],
    mounts: 'D 哨兵 · R 证明链',
    source: 'builtin'
  },
  {
    id: 'look_ahead_bias',
    zh: '前视偏差',
    en: 'Look-ahead Bias',
    domain: 'Q1',
    prd: 'Q1.3',
    aliases: ['lookahead', 'look ahead', 'future leak', 'leakage', '未来函数', '数据泄漏'],
    plain: '计算 t 日信号时用到了 t 日收盘后乃至未来才可得的信息，回测收益来自「偷看答案」，实盘必然消失。',
    formula: [
      's_{t} = f(D_{\\le t}),  \\quad \\forall k > 0: \\partial s_{t} / \\partial D_{t+k} = 0',
      '\\max_{\\tau} \\max_{t \\le \\tau} | f_{t}(D_{\\le \\tau}) - f_{t}(D_{full}) | \\le 10^{-10}'
    ],
    redline: '违规 = 0：前缀不变性 max|Δf| <= 1e-10 且 AST 因果证明零违规节点',
    pitfalls: [
      '用 t 日收盘价生成信号又以 t 日收盘价成交：A 股应以 t+1 开盘价或 VWAP 成交',
      '全样本标准化、全样本去极值或用全样本训练的行业 / 风格模型，都属于隐性前视'
    ],
    related: ['point_in_time', 'prefix_invariance', 'ast_causal_proof', 'ast_safe'],
    mounts: 'A AST 标签 · R 证明链',
    source: 'builtin'
  },
  {
    id: 'corp_action_adj',
    zh: '复权与公司行动',
    en: 'Corporate Action Adjustment',
    domain: 'Q1',
    prd: 'Q1.3',
    aliases: ['QA-02', 'adj factor', 'adjustment factor', '复权', '前复权', '后复权', '除权除息'],
    plain: '分红、送转、配股会让价格在除权日机械跳空，必须用复权因子把价格序列还原成可比的总收益口径。',
    formula: [
      'P^{ref}_{t} = \\frac{P^{close}_{t-1} - D + P_{rights} k_{r}}{1 + k_{s} + k_{r}},  \\quad AF_{t} = AF_{t-1} \\cdot \\frac{P^{close}_{t-1}}{P^{ref}_{t}}',
      '\\tilde{P}_{t} = P_{t} \\cdot AF_{t} / AF_{T}  \\quad (\\text{前复权})'
    ],
    symbols: ['D: 每股现金红利', 'k_{s}: 送转比例；k_{r}: 配股比例', 'AF_{t}: 累积复权因子'],
    redline: '除权日复权收益跳变 = 0 条（建议门禁）',
    pitfalls: [
      '用前复权价格构造价格水平类因子（如低价股）：前复权会随未来分红改写历史价格，形成前视；收益用后复权，价格水平用不复权',
      '复权因子未按 PIT 版本存储：新一次分红会改写整段前复权序列，历史回测不可复现'
    ],
    related: ['ohlcv_geometry', 'point_in_time', 'scale_invariance', 'fact_fingerprint'],
    mounts: 'D 质量审计',
    source: 'builtin'
  },
  {
    id: 'ohlcv_geometry',
    zh: 'OHLCV 几何一致性',
    en: 'OHLCV Geometric Consistency',
    domain: 'Q1',
    prd: 'Q1.3',
    aliases: ['QA-01', 'OHLC check', 'bar sanity check', 'K 线一致性', 'OHLC 校验'],
    plain: '一根 K 线必须自洽：最高价不低于开收盘，最低价不高于开收盘，成交量与成交额非负且成交均价落在高低价之间。',
    formula: [
      'L_{t} \\le \\min(O_{t}, C_{t}) \\le \\max(O_{t}, C_{t}) \\le H_{t}',
      'V_{t} \\ge 0,  \\quad A_{t} \\ge 0,  \\quad L_{t} \\le A_{t} / V_{t} \\le H_{t}'
    ],
    symbols: ['A_{t}: 成交额；A_{t}/V_{t} 即 VWAP，注意手与股的单位换算'],
    redline: '几何违规 = 0 条（建议门禁）',
    pitfalls: [
      '成交量单位混用（手 vs 股）使 VWAP 落在 [L, H] 之外：要么被误判为脏数据，要么被静默放过',
      '停牌日用前收盘价填充 OHLC 却保留非零成交量：须统一停牌标记，避免回测在停牌日成交'
    ],
    related: ['corp_action_adj', 'price_limit_rules', 'coverage', 'freshness'],
    mounts: 'D 质量审计',
    source: 'builtin'
  },
  {
    id: 'historical_constituents',
    zh: '历史成分与权重',
    en: 'Historical Index Constituents',
    domain: 'Q1',
    prd: 'Q1.1',
    aliases: ['QA-05', 'universe', 'constituents', 'CSI300', 'CSI500', 'CSI1000', '沪深300', '中证500', '指数成分'],
    plain: '回测股票池与基准权重必须使用当日真实的指数成分与权重，不能拿今天的名单回溯历史。',
    formula: [
      'U_{t} = \\text{Constituents}(I, t),  \\quad \\sum_{i \\in U_{t}} w^{b}_{i,t} = 1',
      '| \\sum_{i} w^{b}_{i,t} - 1 | < 0.001'
    ],
    symbols: ['I: 基准指数（如 CSI500）', 'w^{b}_{i,t}: 当日基准权重'],
    redline: '成分权重和偏差 < 0.1%（建议门禁），调样按生效日切换',
    pitfalls: [
      '按调样公告日而非生效日切换成分：中证指数定期调样通常在 6 月与 12 月第二个星期五的下一交易日生效',
      '基准权重只取月末快照、不做日间价格漂移：跟踪误差与 Brinson 配置效应会被系统性扭曲'
    ],
    related: ['survivorship_bias', 'point_in_time', 'tracking_error', 'brinson_allocation'],
    mounts: 'D 质量审计 · T UNIVERSE',
    source: 'builtin'
  },
  {
    id: 'sw_industry_pit',
    zh: '申万行业 PIT 归属',
    en: 'SW Industry Point-in-Time Classification',
    domain: 'Q1',
    prd: 'Q1.1',
    aliases: ['QA-06', 'SW L1', 'Shenwan', 'SW2021', '申万一级', '行业分类'],
    plain: '申万行业分类会调整（2021 版一级行业由 28 个扩为 31 个），行业中性化与归因必须按当日归属而不是最新归属。',
    formula: [
      'g(i, t) = \\text{SW}_{L1}(i, t) \\in \\text{31 个一级行业}',
      '\\forall i: \\text{归属区间首尾相接，不重叠且无缺口}'
    ],
    redline: '归属区间重叠 / 缺失 = 0（建议门禁）',
    pitfalls: [
      '用最新行业归属做历史中性化：被重新分类的公司会把未来的行业信息带入历史截面',
      '新旧版体系切换时做硬映射：拆分出的新行业（如美容护理、环保）在切换前没有原生历史，需显式回溯映射'
    ],
    related: ['industry_neutral', 'point_in_time', 'industry_active_dev', 'brinson_allocation'],
    mounts: 'D 质量审计 · A 行业中性 Chip',
    source: 'builtin'
  },
  {
    id: 'price_limit_rules',
    zh: '涨跌停规则',
    en: 'Price Limit Rules',
    domain: 'Q1',
    prd: 'Q1.3',
    aliases: ['QA-07', 'limit up', 'limit down', 'price limit', '涨停', '跌停', '涨跌幅限制'],
    plain: 'A 股按板块设置日涨跌幅限制：主板 ±10%、创业板 / 科创板 ±20%、ST ±5%、北交所 ±30%；封涨停买不进、封跌停卖不出。',
    formula: [
      'P^{up}_{t} = \\text{round}(P^{close}_{t-1} (1 + L_{b}), 2),  \\quad P^{dn}_{t} = \\text{round}(P^{close}_{t-1} (1 - L_{b}), 2)',
      'L_{b}: \\text{主板 } 0.10, \\quad \\text{创业板 / 科创板 } 0.20, \\quad \\text{ST } 0.05, \\quad \\text{北交所 } 0.30'
    ],
    symbols: ['L_{b}: 板块 b 的涨跌幅限制，须按当日板块与 ST 状态取值'],
    redline: '规则错配 = 0（建议门禁）：板块、ST 状态与制度切换日均需 PIT',
    pitfalls: [
      '创业板 2020-08-24 注册制改革前为 ±10%：按现行规则回溯会把历史涨停误判为可成交',
      '注册制新股上市前 5 个交易日不设涨跌幅限制：套用常规规则会错杀或错放成交'
    ],
    related: ['limit_queue', 't_plus_1', 'ohlcv_geometry', 'fill_rate'],
    mounts: 'D 质量审计 · B 拒单原因',
    source: 'builtin'
  },
  {
    id: 'coverage',
    zh: '覆盖率',
    en: 'Coverage',
    domain: 'Q1',
    prd: 'Q1.3',
    aliases: ['factor coverage', 'min_coverage', 'non-NaN ratio', '有效覆盖', '因子覆盖率'],
    plain: '截面上拿到有效因子值的股票占可交易股票池的比例；覆盖太低时 IC 只在少数股票上成立，组合无法落地。',
    formula: [
      '\\text{Coverage}_{t} = \\frac{#( i \\in U^{elig}_{t} : f_{i,t} \\text{ finite} )}{# U^{elig}_{t}}',
      '\\bar{C} = \\frac{1}{T} \\sum_{t} \\text{Coverage}_{t} \\ge 0.80'
    ],
    symbols: ['U^{elig}_{t}: 剔除停牌、上市未满 N 日后的可交易股票池'],
    gate: GATE_SPECS.coverage,
    redline: 'Coverage >= 80%（合同硬门禁），70%–80% 观察',
    pitfalls: [
      '分母用全市场而非可交易池：停牌与次新股会让覆盖率虚低或虚高',
      '先用行业中位数填充缺失再统计覆盖率：数值上接近 100%，实际引入大量伪信号'
    ],
    related: ['rank_ic', 'freshness', 'survivorship_bias', 'mad_winsorize'],
    mounts: 'A 编辑器状态栏 · D KPI',
    source: 'builtin'
  },
  {
    id: 'freshness',
    zh: '数据新鲜度',
    en: 'Data Freshness',
    domain: 'Q1',
    prd: 'Q1.1',
    aliases: ['staleness', 'data lag', '数据时效', '更新延迟'],
    plain: '最新入湖数据距当前的时间差；超过 1 个交易日未更新，信号就是在用过期数据。',
    formula: [
      '\\text{Lag} = t_{now} - \\max_{r} t^{ingest}_{r}',
      '\\text{Lag} \\le 1 \\text{ 交易日}  \\quad (\\text{前端上限 } 1.5 \\times 86400 \\text{ s})'
    ],
    redline: '<= 1 个交易日（建议门禁，前端上限 1.5 × 86400 s）',
    pitfalls: [
      '只看行情表新鲜度：财务、成分、行业、停复牌等表可能已经停更数日',
      '节假日按自然日计算滞后会误报：应按交易日历计算'
    ],
    related: ['coverage', 'fact_fingerprint', 'pit_snapshot_lock', 'point_in_time'],
    mounts: 'D KPI',
    source: 'builtin'
  },
  {
    id: 'fact_fingerprint',
    zh: '事实指纹',
    en: 'Fact Fingerprint & Snapshot',
    domain: 'Q1',
    prd: 'Q1.2',
    aliases: ['fingerprint', 'data hash', 'snapshot hash', 'content hash', '数据指纹', '快照哈希'],
    plain: '对一次计算实际读取的数据切片（表、分区、版本、行集合）求内容哈希；任何人用同一指纹都能逐位复现结果。',
    formula: [
      'FP = \\text{SHA256}( \\text{table}, \\text{partition}, \\text{version}, \\text{RowHash} )',
      'FP_{rerun} = FP_{orig} \\Rightarrow \\text{结果逐位一致}'
    ],
    redline: '复算哈希一致（建议门禁）：主控复算指纹不一致即作废',
    pitfalls: [
      '只对代码求指纹而不对数据求指纹：数据湖回补或复权更新后，同一份代码产出不同结果',
      '哈希包含写入时间、行顺序等非确定性字段会造成假不一致：应规范化排序后再哈希'
    ],
    related: ['pit_snapshot_lock', 'point_in_time', 'cluster_dispatch', 'study_lineage'],
    mounts: 'D · S · R Artifact',
    source: 'builtin'
  }
];
