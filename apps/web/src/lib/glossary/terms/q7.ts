import type { GlossaryTerm } from '../types';

export const Q7_TERMS: GlossaryTerm[] = [
  {
    id: 'study_lineage',
    zh: '研究血缘',
    en: 'Study Lineage',
    domain: 'Q7',
    aliases: ['lineage', 'parent_study_id', 'family_id', 'study family', '研究谱系', '血缘树'],
    plain: '每个研究以 parent_study_id 指向其派生来源、以 family_id 归入同一研究家族，形成可追溯的知识树；家族内累计试验数是多重检验惩罚的依据。',
    formula: [
      '\\text{family}(s) = \\text{root}( s \\to \\text{parent}(s) \\to ... )',
      'M_{family} = \\sum_{s \\in \\text{family}} M_{s}'
    ],
    symbols: ['M_{s}: 研究 s 申报的试验次数'],
    redline: '无数值门禁 · 每个研究必须有 family_id，派生研究必须声明 parent_study_id',
    pitfalls: [
      '换个名字另起研究以绕过家族累计 M：须按表达式指纹自动挂靠到已有家族',
      '血缘只记录代码版本、不记录数据快照：复现时数据早已变化'
    ],
    related: ['trial_count_m', 'falsified_tombstone', 'pit_snapshot_lock', 'ic_memo'],
    mounts: 'R 知识树',
    source: 'builtin'
  },
  {
    id: 'falsified_tombstone',
    zh: '已证伪墓碑库',
    en: 'Falsified Tombstone Registry',
    domain: 'Q7',
    aliases: ['tombstone', 'falsified', 'dedup', 'expr_hash', '墓碑库', '证伪库', '查重'],
    plain: '被证伪的因子与策略不删除，而是连同失败证据进入墓碑库；新研究提交前做三层查重，防止换皮重复试验与 p-hacking。',
    formula: [
      '\\text{L1}: \\text{ExprHash}(e_{new}) = \\text{ExprHash}(e_{dead})',
      '\\text{L2}: \\text{ASTFingerprint}(e_{new}) = \\text{ASTFingerprint}(e_{dead})  \\quad (\\text{变量重命名、常数归一化后})',
      '\\text{L3}: J = \\frac{# \\text{共同关键词}}{# \\text{关键词并集}} \\ge \\theta_{J}'
    ],
    symbols: ['e_{dead}: 墓碑库中已证伪的表达式', '\\theta_{J}: 关键词 Jaccard 阈值（系统配置）'],
    redline: '任一层命中即阻断或强制引用墓碑证据（无合同数值门禁）',
    pitfalls: [
      '只做 L1 字符串哈希：交换操作数顺序或把窗口从 20 改为 21 即可绕过',
      '墓碑不记录失败时的数据快照与 regime：在新 regime 下复活已证伪因子可以允许，但须作为原家族成员计入 M'
    ],
    related: ['study_lineage', 'trial_count_m', 'max_corr_library', 'bh_fdr'],
    mounts: 'R 左栏墓碑库',
    source: 'builtin'
  },
  {
    id: 'cluster_dispatch',
    zh: '集群派发',
    en: 'Cluster Dispatch',
    domain: 'Q7',
    aliases: ['dispatch', 'engine_sandbox', 'cluster_dev_worker', 'worker', 'zero trust', '集群调度', '零信任复算'],
    plain: '研究任务派发到 engine_sandbox 或 cluster_dev_worker 节点执行，节点只读挂载数据湖；结果回传后由主控按同一快照独立复算，零信任校验通过才入账。',
    formula: [
      '\\text{Accept} = ( FP^{worker} = FP^{master} ) \\text{ 且 } \\max_{m} | m^{worker} - m^{master} | \\le \\epsilon_{m}',
      '\\text{WritePerm}(\\text{worker}, \\text{Lake}) = 0'
    ],
    symbols: ['m: 关键指标（IC、Sharpe、PBO 等）', '\\epsilon_{m}: 指标级复算容差'],
    redline: '主控复算不一致即拒收（零信任）；worker 对数据湖写权限 = 0',
    pitfalls: [
      '只校验 worker 回传的指标而不复算：篡改或环境漂移（依赖版本、BLAS 线程数）导致的偏差无法发现',
      '并行浮点归约顺序不同会产生 1e-12 量级差异：容差应按指标设定，而不是要求逐位相等'
    ],
    related: ['fact_fingerprint', 'pit_snapshot_lock', 'study_lineage', 'mcp_side_effect'],
    mounts: 'R 集群健康条 · K',
    source: 'builtin'
  },
  {
    id: 'ic_memo',
    zh: '投资委员会备忘录',
    en: 'Investment Committee Memo',
    domain: 'Q7',
    aliases: ['IC Memo', 'investment memo', 'committee memo', '投委会备忘录', '研究报告'],
    plain: '研究结论提交投资委员会（此处 IC 指 Investment Committee）的标准化文档：论点、证据链（门禁与形式化证明）、风险、容量与反证，所有数字须可由快照复现。',
    formula: [
      '\\text{Recommend} \\Rightarrow \\forall g \\in \\text{HardGates}: g = \\text{PASS}',
      '\\forall x \\in \\text{Memo}: x = \\text{Artifact}(\\text{SnapshotId}, FP)'
    ],
    redline: '无数值门禁 · 任一硬门禁 FAIL 不得给出「建议入库」结论',
    pitfalls: [
      '只呈现通过的指标，省略失败试验与墓碑记录：违反多重检验披露要求',
      '备忘录中的数字手工抄录：须直接引用快照与 artifact 指纹，避免与系统数值不一致'
    ],
    related: ['study_lineage', 'pit_snapshot_lock', 'dsr', 'capacity_frontier'],
    mounts: 'R 研究报告 · K',
    source: 'builtin'
  },
  {
    id: 'mcp_side_effect',
    zh: 'MCP 工具副作用',
    en: 'MCP Tool Side Effects',
    domain: 'Q7',
    aliases: ['MCP', 'side effect', 'READ', 'ASK', 'BG_RUN', 'tool approval', '工具审批'],
    plain: '每个 MCP 工具由服务端声明副作用等级：READ 只读可直接调用；ASK 须用户确认；BG_RUN 为后台长任务，须审批并可追踪。交易类工具一律不存在。',
    formula: [
      '\\text{Execute}(tool, args) \\Rightarrow \\text{level} = \\text{READ} \\text{ 或 } \\text{Approved}(tool, args)',
      '#( tool : \\text{名称匹配交易类关键词} ) = 0'
    ],
    redline: '未经审批的 ASK / BG_RUN 调用 = 0；交易类工具名匹配数 = 0',
    pitfalls: [
      '审批只绑定工具名不绑定参数：一次批准被复用到其他研究或更大资源配额',
      '把会写入数据湖或注册表的工具误标为 READ：副作用等级必须由服务端声明，不能由客户端推断'
    ],
    related: ['authority_gate', 'cluster_dispatch', 'kv_cache_safe', 'ast_safe'],
    mounts: 'K 工具审批 · R',
    source: 'builtin'
  },
  {
    id: 'pit_snapshot_lock',
    zh: 'PIT 快照锁',
    en: 'PIT Snapshot Lock',
    domain: 'Q7',
    aliases: ['snapshot_id', 'snapshot lock', 'PIT lock', 'as-of lock', '快照锁'],
    plain: '研究与回测固定一个 snapshot_id，所有读取都解析到该快照下的数据版本；数据湖后续回补或修订不会改变已锁定研究的结果。',
    formula: [
      '\\text{Read}(q) = \\text{Lake}( q \\mid \\text{SnapshotId}, \\text{AsOf} )',
      '\\text{Rerun}(\\text{SnapshotId}) \\Rightarrow FP_{rerun} = FP_{orig}'
    ],
    redline: '同一 snapshot_id 复算指纹一致（无数值门禁）',
    pitfalls: [
      '只锁定行情表不锁定财务、行业与成分表：多表快照必须原子一致',
      '快照锁不等于 PIT：锁定的是数据版本，单个快照内部仍须按公告日对齐'
    ],
    related: ['point_in_time', 'fact_fingerprint', 'study_lineage', 'cluster_dispatch'],
    mounts: 'T PIT 锁 · R · D',
    source: 'builtin'
  },
  {
    id: 'kv_cache_safe',
    zh: 'KV-Cache 安全',
    en: 'KV-Cache Safe Context Injection',
    domain: 'Q7',
    aliases: ['KV cache', 'KV-Cache Safe', 'prompt cache', 'prefix cache', 'canvas context', '前缀缓存'],
    plain: '画布上下文（当前页数值、术语焦点）只注入到最新一条 user 消息中，system 提示词前缀保持字节不变，从而让大模型的前缀 KV 缓存持续命中。',
    formula: [
      '\\text{bytes}(\\text{system}_{n}) = \\text{bytes}(\\text{system}_{n-1})',
      '\\text{Context}_{n} \\to \\text{user}_{n} \\text{ only},  \\quad | \\text{Context}_{n} | \\le 800 \\text{ chars}'
    ],
    redline: 'system 前缀逐字节不变；画布上下文 <= 800 字符且只出现在最新 user 消息',
    pitfalls: [
      '在 system 中插入时间戳或当前页面状态：每轮缓存全部失效，延迟与成本陡增',
      '回写历史 user 消息中的旧画布上下文：同样破坏前缀一致性，只能追加'
    ],
    related: ['mcp_side_effect', 'ic_memo', 'pit_snapshot_lock'],
    mounts: 'K 状态 Tag',
    source: 'builtin'
  }
];
