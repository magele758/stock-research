/**
 * Deterministic, rule-based read-outs computed from *real* quant-api responses.
 * Used when Agent Home is offline (or the bot is not registered). Nothing here
 * invents numbers: every figure is taken from the response being summarized.
 */
import { BASIC_CATALOG, QUANT_CATALOG, buildSurfaceEnvelopes } from '@/lib/a2ui';
import { fx, isNum, ratioPct, sg } from '@/lib/format';
import { BARRA_HARD_LIMIT } from '@/lib/meta';
import { normalizeGateChecks } from '@/lib/quant-api';
import type { CpcvResult, FactorDecayItem, FactorEvalResult, OptConstraints, PortfolioOptResult } from '@/lib/types';
import type { GlossaryTerm, TermQuery } from '@/lib/glossary/types';

export type SurfaceBatch = ReturnType<typeof buildSurfaceEnvelopes>;

export interface LocalOutcome {
  botText: string;
  summary: Record<string, unknown>;
  surface?: SurfaceBatch;
}

const p = (v: number | null | undefined, d = 1) => {
  const r = ratioPct(v);
  return r === null ? '—' : `${fx(r, d)}%`;
};

function gateLine(list: Record<string, unknown>[] | undefined): { line: string; failed: string[] } {
  const g = normalizeGateChecks(list);
  if (!g.length) return { line: '门禁明细未返回', failed: [] };
  const passed = g.filter((x) => x.passed === true).length;
  const failed = g.filter((x) => x.passed === false).map((x) => `${x.name}${x.value ? ` ${x.value}` : ''}${x.threshold ? ` (阈值 ${x.threshold})` : ''}`);
  return { line: `门禁 ${passed}/${g.length} 通过`, failed };
}

// ------------------------------------------------------------------ Q2: IC + deciles

export function summarizeEval(r: FactorEvalResult): LocalOutcome {
  const dec = r.decile_ann_returns ?? [];
  const spread = dec.length >= 2 && isNum(dec[0]) && isNum(dec[dec.length - 1]) ? dec[dec.length - 1] - dec[0] : null;
  const gate = gateLine(r.gate_checks);
  const lines = [
    `**${r.factor_id}** @ ${r.universe_id} · snapshot \`${r.snapshot_id}\``,
    `RankIC 均值 **${fx(r.rank_ic_mean, 4)}** · IC σ ${fx(r.ic_std, 4)} · ICIR(年化) **${fx(r.icir_ann, 2)}** · NW-t ${fx(r.nw_t_stat, 2)} · IC 胜率 ${p(r.ic_win_rate)}`,
    `10 分层：单调性 Spearman **${fx(r.monotonicity_spearman, 2)}**${dec.length ? ` · D1 ${p(dec[0])} → D${dec.length} ${p(dec[dec.length - 1])}` : ''}${spread !== null ? ` · 头尾价差 ${p(spread)}` : ''}`,
    `多空年化 ${p(r.long_short_ann)} · Top 超额 ${p(r.top_excess_ann)} · 单边日换手 ${p(r.daily_turnover_oneway)} · 半衰期 ${fx(r.half_life_days, 1)} 天`,
    `库内最大相关 ${fx(r.max_corr_in_library, 2)}${r.max_corr_peer ? `（vs \`${r.max_corr_peer}\`）` : ''} · 覆盖率 ${p(r.coverage_pct)}`,
    r.ast_safe ? `AST 安全校验通过${r.ast_warnings?.length ? `（${r.ast_warnings.length} 条警告）` : ''}` : `AST 校验未通过：${(r.ast_warnings ?? []).join('；') || '未知原因'}`,
    `${gate.line} → **${r.admission_passed ? '准入：可入因子库' : '拒绝入库'}**${gate.failed.length ? `；未过：${gate.failed.join('；')}` : ''}`
  ];
  return {
    botText: lines.join('\n'),
    summary: {
      factor_id: r.factor_id,
      rank_ic_mean: r.rank_ic_mean,
      icir_ann: r.icir_ann,
      nw_t_stat: r.nw_t_stat,
      monotonicity_spearman: r.monotonicity_spearman,
      long_short_ann: r.long_short_ann,
      admission_passed: r.admission_passed
    }
  };
}

// ------------------------------------------------------------------ Q3: CPCV

export function summarizeCpcv(r: CpcvResult): LocalOutcome {
  const gate = gateLine(r.gate_details);
  const verdict = r.verdict === 'pass' ? '通过（可进入组合优化）' : r.verdict === 'reject' ? '拒绝（疑似过拟合）' : '不确定（需更多样本/路径）';
  const negShare = r.n_oos_paths ? r.negative_path_count / r.n_oos_paths : null;
  const lines = [
    `**${r.experiment_id}** · CPCV N=${r.n_splits}, k=${r.k_test} → ${r.n_combinations} 组合 / **${r.n_oos_paths} 条 OOS 路径** · purge ${r.purge_days}D · embargo ${r.embargo_days}D${r.purge_conflicts ? ` · purge 冲突 ${r.purge_conflicts}` : ''}`,
    `IS Sharpe ${fx(r.is_sharpe, 2)} → OOS 中位 **${fx(r.oos_median_sharpe, 2)}** · OOS/IS ${fx(r.oos_is_ratio, 2)}`,
    `PBO **${fx(r.pbo, 3)}** · DSR **${fx(r.dsr, 3)}** · 负 Sharpe 路径 ${r.negative_path_count}/${r.n_oos_paths}${negShare !== null ? `（${fx(negShare * 100, 0)}%）` : ''}`,
    `${gate.line} → 结论：**${verdict}**${gate.failed.length ? `；未过：${gate.failed.join('；')}` : ''}`
  ];
  return {
    botText: lines.join('\n'),
    summary: { experiment_id: r.experiment_id, pbo: r.pbo, dsr: r.dsr, oos_median_sharpe: r.oos_median_sharpe, verdict: r.verdict }
  };
}

// ------------------------------------------------------------------ Q4: Barra + constraint card

const isSize = (f: string) => /^size$|^ln_?size|^size_?(factor)?$/i.test(f.trim()) || (/size/i.test(f) && !/non|mid|nl/i.test(f));
const r2 = (v: number) => Math.round(v * 100) / 100;
const r3 = (v: number) => Math.round(v * 1000) / 1000;

export interface ConstraintSuggestion {
  turnoverLambda: number;
  maxIndustryDev: number;
  maxSizeZ: number;
}

export function suggestConstraints(r: PortfolioOptResult, c: OptConstraints): { s: ConstraintSuggestion; reasons: string[] } {
  const reasons: string[] = [];
  const size = r.style_exposures.find((e) => isSize(e.factor));
  let maxSizeZ = c.max_size_z;
  if (size && Math.abs(size.post_opt) > 0.6 * Math.min(c.max_size_z, BARRA_HARD_LIMIT)) {
    maxSizeZ = Math.max(0.1, r2(Math.min(c.max_size_z, Math.abs(size.post_opt)) * 0.6));
    reasons.push(`Size 优化后暴露 ${sg(size.post_opt, 2)}σ 贴近上限 ±${fx(c.max_size_z, 2)}σ → 收紧至 ±${fx(maxSizeZ, 2)}σ`);
  }
  let turnoverLambda = c.turnover_lambda;
  const toCap = c.max_turnover_oneway * 100;
  if (isNum(r.oneway_turnover_pct) && r.oneway_turnover_pct > 0.85 * toCap) {
    turnoverLambda = r2(Math.min(5, Math.max(c.turnover_lambda * 1.5, c.turnover_lambda + 0.2)));
    reasons.push(`单边换手 ${fx(r.oneway_turnover_pct, 1)}% 接近上限 ${fx(toCap, 0)}% → λ ${fx(c.turnover_lambda, 2)} → ${fx(turnoverLambda, 2)}`);
  }
  let maxIndustryDev = c.max_industry_dev;
  if (isNum(r.predicted_te_pct) && r.predicted_te_pct > c.target_te * 100) {
    maxIndustryDev = Math.max(0.005, r3(c.max_industry_dev * 0.8));
    reasons.push(`预测 TE ${fx(r.predicted_te_pct, 2)}% 超目标 ${fx(c.target_te * 100, 1)}% → 行业偏离上限 ${fx(c.max_industry_dev * 100, 1)}% → ${fx(maxIndustryDev * 100, 1)}%`);
  }
  return { s: { turnoverLambda, maxIndustryDev, maxSizeZ }, reasons };
}

export function constraintCard(surfaceId: string, title: string, body: string, cur: OptConstraints, s: ConstraintSuggestion, sizePost: number | null): SurfaceBatch {
  return buildSurfaceEnvelopes(surfaceId, BASIC_CATALOG, { ...s }, [
    { id: 'root', component: 'Card', child: 'col' },
    { id: 'col', component: 'Column', children: ['title', 'body', 'f_lambda', 'f_ind', 'f_size', 'diff', 'actions'] },
    { id: 'title', component: 'Text', text: title, usageHint: 'h4' },
    { id: 'body', component: 'Text', text: body },
    { id: 'f_lambda', component: 'TextField', label: '换手惩罚 λ (turnoverLambda)', value: { path: '/turnoverLambda' } },
    { id: 'f_ind', component: 'TextField', label: '行业偏离上限 (maxIndustryDev, 小数)', value: { path: '/maxIndustryDev' } },
    { id: 'f_size', component: 'TextField', label: 'Size 中性上限 σ (maxSizeZ)', value: { path: '/maxSizeZ' } },
    {
      id: 'diff',
      component: 'QuantDiffGrid',
      rows: [
        { label: '换手惩罚 λ', from: fx(cur.turnover_lambda, 2), to: { path: '/turnoverLambda' } },
        { label: '行业偏离上限', from: fx(cur.max_industry_dev, 3), to: { path: '/maxIndustryDev' } },
        { label: 'Size 上限 σ', from: fx(cur.max_size_z, 2), to: { path: '/maxSizeZ' } },
        { label: 'Size 暴露 (post)', from: sizePost === null ? '—' : sg(sizePost, 2), to: '重跑后回执' }
      ]
    },
    { id: 'actions', component: 'Row', children: ['btn_apply', 'btn_view'] },
    { id: 'btn_apply', component: 'Button', label: '应用约束并重跑', primary: true, action: { name: 'quant.rerun_optimization', sendDataModel: true } },
    { id: 'btn_view', component: 'Button', label: '仅打开组合页', action: { name: 'quant.open_view', context: { path: '/portfolio' }, sendDataModel: false } }
  ]);
}

export function summarizeBarra(r: PortfolioOptResult, c: OptConstraints, surfaceId: string): LocalOutcome & { suggestion: ConstraintSuggestion } {
  const breached = r.style_exposures.filter((e) => e.breached || Math.abs(e.post_opt) > BARRA_HARD_LIMIT);
  const top = [...r.style_exposures].sort((a, b) => Math.abs(b.post_opt) - Math.abs(a.post_opt)).slice(0, 3);
  const size = r.style_exposures.find((e) => isSize(e.factor));
  const ind = [...r.industry_deviations].sort((a, b) => Math.abs(b.active_weight_pct) - Math.abs(a.active_weight_pct))[0];
  const { s, reasons } = suggestConstraints(r, c);
  const lines = [
    `cvxpy · ${r.solver} · **${r.solve_status.toUpperCase()}** · ${fx(r.solve_ms, 0)} ms${r.relaxation_notes?.length ? ` · 松弛：${r.relaxation_notes.join('；')}` : ''}`,
    `预期净超额 **${fx(r.expected_net_excess_pct, 2)}%**（毛 ${fx(r.expected_gross_excess_pct, 2)}% − 成本 ${fx(r.expected_cost_pct, 2)}%）· 预测 TE ${fx(r.predicted_te_pct, 2)}% · IR ${fx(r.expected_ir, 2)} · 有效持股 ${fx(r.effective_n_stocks, 0)}`,
    `风格暴露 Top3（post）：${top.map((e) => `${e.factor} ${sg(e.post_opt, 2)}σ`).join(' · ') || '—'}${size ? ` · Size ${sg(size.pre_opt, 2)} → ${sg(size.post_opt, 2)}σ` : ''}`,
    breached.length ? `**越界 ${breached.length} 项**（±${BARRA_HARD_LIMIT}σ 硬线）：${breached.map((e) => `${e.factor} ${sg(e.post_opt, 2)}`).join('、')}` : `全部风格暴露在 ±${BARRA_HARD_LIMIT}σ 硬线内`,
    ind ? `最大行业偏离：${ind.industry} ${sg(ind.active_weight_pct, 2)}%（上限 ${fx(ind.limit_pct, 1)}%）` : '',
    reasons.length ? `规则建议：${reasons.join('；')}` : '约束未贴边，当前参数无需收紧；可在下方卡片手动调整后重跑。'
  ].filter(Boolean);
  const title = breached.length ? 'Barra 暴露越界 · 建议收紧约束并重跑 cvxpy' : reasons.length ? '约束贴边 · 建议微调并重跑 cvxpy' : '约束调参卡 · 手动调整后重跑 cvxpy';
  const body = `基于 ${r.opt_run_id} 实算结果（${r.universe_id} · ${r.snapshot_id}）。点击「应用约束并重跑」会写入 /portfolio 约束并真实触发一次优化。`;
  return {
    botText: lines.join('\n'),
    summary: {
      opt_run_id: r.opt_run_id,
      solve_status: r.solve_status,
      expected_net_excess_pct: r.expected_net_excess_pct,
      predicted_te_pct: r.predicted_te_pct,
      breached: breached.map((e) => e.factor)
    },
    surface: constraintCard(surfaceId, title, body, c, s, size ? size.post_opt : null),
    suggestion: s
  };
}

/** Validate the A2UI data model and map it onto OptConstraints. Accepts "3" meaning 3% for industry dev. */
export function constraintsFromModel(model: Record<string, unknown> | undefined, c: OptConstraints): { next: OptConstraints; changes: string[] } | { error: string } {
  if (!model) return { error: '卡片未回传 dataModel' };
  const num = (k: string) => {
    const v = model[k];
    const n = typeof v === 'number' ? v : typeof v === 'string' && v.trim() ? Number(v) : NaN;
    return Number.isFinite(n) ? n : null;
  };
  const lam = num('turnoverLambda');
  let ind = num('maxIndustryDev');
  const size = num('maxSizeZ');
  if (lam === null || ind === null || size === null) return { error: 'turnoverLambda / maxIndustryDev / maxSizeZ 必须为数字' };
  if (ind > 0.3 && ind <= 30) ind = ind / 100;
  if (lam < 0 || lam > 10) return { error: `turnoverLambda=${lam} 超出 [0, 10]` };
  if (ind <= 0 || ind > 0.3) return { error: `maxIndustryDev=${ind} 超出 (0, 0.3]` };
  if (size <= 0 || size > 3) return { error: `maxSizeZ=${size} 超出 (0, 3]` };
  const next = { ...c, turnover_lambda: lam, max_industry_dev: ind, max_size_z: size };
  const changes: string[] = [];
  if (lam !== c.turnover_lambda) changes.push(`λ ${fx(c.turnover_lambda, 2)}→${fx(lam, 2)}`);
  if (ind !== c.max_industry_dev) changes.push(`行业偏离 ${fx(c.max_industry_dev * 100, 1)}%→${fx(ind * 100, 1)}%`);
  if (size !== c.max_size_z) changes.push(`Size ±${fx(c.max_size_z, 2)}σ→±${fx(size, 2)}σ`);
  return { next, changes };
}

export function compareOpt(before: PortfolioOptResult | null, after: PortfolioOptResult): string {
  const sz = (r: PortfolioOptResult | null) => r?.style_exposures.find((e) => isSize(e.factor))?.post_opt ?? null;
  const row = (label: string, a: number | null | undefined, b: number | null | undefined, d = 2, unit = '') =>
    `${label} ${isNum(a) ? fx(a, d) + unit : '—'} → **${isNum(b) ? fx(b, d) + unit : '—'}**`;
  return [
    `重跑完成 · ${after.opt_run_id} · **${after.solve_status.toUpperCase()}** · ${fx(after.solve_ms, 0)} ms`,
    [
      row('净超额', before?.expected_net_excess_pct, after.expected_net_excess_pct, 2, '%'),
      row('TE', before?.predicted_te_pct, after.predicted_te_pct, 2, '%'),
      row('单边换手', before?.oneway_turnover_pct, after.oneway_turnover_pct, 1, '%'),
      row('Size 暴露', sz(before), sz(after), 2, 'σ')
    ].join(' · '),
    `越界风格：${after.style_exposures.filter((e) => e.breached).map((e) => e.factor).join('、') || '无'}`
  ].join('\n');
}

// ------------------------------------------------------------------ Q6: factor decay

export function summarizeDecay(item: FactorDecayItem, ev: FactorEvalResult | null): LocalOutcome {
  const pctl = item.hist_percentile;
  const crowdHi = item.crowding_index >= (item.crowding_index > 1.5 ? 2 : 0.8);
  const causes: string[] = [];
  if (item.rank_ic_60d <= 0) causes.push(`60D RankIC 已翻负（${fx(item.rank_ic_60d, 4)}）：信号方向失效`);
  if (pctl <= 20) causes.push(`60D IC 处于历史 ${fx(pctl, 0)} 分位：显著衰减`);
  if (crowdHi) causes.push(`拥挤度 ${fx(item.crowding_index, 2)} 偏高：同质资金拥挤，踩踏回撤风险上升`);
  if (!causes.length) causes.push(`60D RankIC ${fx(item.rank_ic_60d, 4)} · 分位 ${fx(pctl, 0)} · 拥挤度 ${fx(item.crowding_index, 2)}：未触发硬规则`);
  const action =
    item.status === 'KILL-SWITCH'
      ? '处置：从合成模型剔除（权重置 0），保留影子跟踪；重新上线需过 CPCV + 6/6 门禁。'
      : item.status === 'DEGRADING'
        ? '处置：合成权重减半，对所在实验重跑 CPCV 体检；若 PBO 上升则进入 Kill-Switch 观察。'
        : '处置：维持权重，继续每日监控。';
  const lines = [`**${item.factor_id}** ${item.name ? `（${item.name}）` : ''}· 状态 **${item.status}**`, ...causes.map((c) => `- ${c}`)];
  if (item.note) lines.push(`后端备注：${item.note}`);
  if (ev) lines.push(`全样本复核（${ev.universe_id}）：RankIC ${fx(ev.rank_ic_mean, 4)} · ICIR ${fx(ev.icir_ann, 2)} · 半衰期 ${fx(ev.half_life_days, 1)} 天 · 库内最大相关 ${fx(ev.max_corr_in_library, 2)}`);
  lines.push(action);
  return { botText: lines.join('\n'), summary: { ...item, full_sample_rank_ic: ev?.rank_ic_mean ?? null } };
}

export function routeCard(surfaceId: string, title: string, body: string, buttons: { label: string; action: string; context: Record<string, unknown>; primary?: boolean }[]): SurfaceBatch {
  return buildSurfaceEnvelopes(surfaceId, BASIC_CATALOG, {}, [
    { id: 'root', component: 'Card', child: 'col' },
    { id: 'col', component: 'Column', children: ['title', 'body', 'actions'] },
    { id: 'title', component: 'Text', text: title, usageHint: 'h4' },
    { id: 'body', component: 'Text', text: body },
    { id: 'actions', component: 'Row', children: buttons.map((_, i) => `b${i}`) },
    ...buttons.map((b, i) => ({ id: `b${i}`, component: 'Button', label: b.label, primary: b.primary, action: { name: b.action, context: b.context, sendDataModel: false } }))
  ]);
}

// ------------------------------------------------------------------ V3 · glossary term card (offline explain)

export function termBotText(term: GlossaryTerm, q: TermQuery | null): string {
  const lines = [`**${term.zh}** (${term.en}) · ${term.domain}`, term.plain];
  const red = q?.threshold ?? term.redline;
  if (red) lines.push(`机构红线：${red}`);
  if (q && (q.display || (q.value !== undefined && q.value !== null))) {
    lines.push(`当前 ${q.view.toUpperCase()} 实测：\`${q.display ?? String(q.value)}\` → **${q.verdict ?? 'N/A'}**${q.gap ? `（${q.gap}）` : ''}`);
  }
  if (term.pitfalls.length) lines.push(`常见陷阱：${term.pitfalls.slice(0, 2).join('；')}`);
  lines.push('_LOCAL 术语库确定性解读（未经 LLM）；启动 Agent Home 后可由 Quant-Lead 结合全部画布上下文深入分析。_');
  return lines.join('\n');
}

export function termCard(surfaceId: string, term: GlossaryTerm): SurfaceBatch {
  const related = term.related.slice(0, 3);
  return buildSurfaceEnvelopes(surfaceId, BASIC_CATALOG, {}, [
    { id: 'root', component: 'Card', child: 'col' },
    { id: 'col', component: 'Column', children: ['title', 'body', 'actions'] },
    { id: 'title', component: 'Text', text: `术语 · ${term.zh}`, usageHint: 'h4' },
    { id: 'body', component: 'Text', text: term.formula.length ? `公式见术语库卡片（${term.formula.length} 行）。相关：${related.join(' / ') || '—'}` : `相关：${related.join(' / ') || '—'}` },
    { id: 'actions', component: 'Row', children: ['b_open', ...related.map((_, i) => `b_r${i}`)] },
    { id: 'b_open', component: 'Button', label: '在术语库打开', primary: true, action: { name: 'quant.explain_term', context: { term_id: term.id, open: 'glossary' }, sendDataModel: false } },
    ...related.map((r, i) => ({ id: `b_r${i}`, component: 'Button', label: r, action: { name: 'quant.explain_term', context: { term_id: r }, sendDataModel: false } }))
  ]);
}

// ------------------------------------------------------------------ V3 · study card (A2UI QuantStudyCard)

export interface StudyCardInput {
  title: string;
  study_id?: string | null;
  parent_study_id?: string | null;
  status?: string;
  verdict?: string;
  kind?: string;
  hypothesis?: string;
  target_cluster?: string;
  metrics?: { label: string; value: string }[];
  proofs?: string;
  warnings?: string[];
}

export function studyCard(surfaceId: string, s: StudyCardInput, draft?: Record<string, unknown>): SurfaceBatch {
  const buttons: { id: string; label: string; action: string; context: Record<string, unknown>; primary?: boolean }[] = [];
  if (s.study_id) buttons.push({ id: 'b_open', label: '打开研究报告', action: 'quant.open_study', context: { study_id: s.study_id }, primary: true });
  buttons.push({
    id: 'b_spawn',
    label: s.study_id ? '创建迭代子研究' : '在抽屉中编辑并派发',
    action: 'quant.spawn_child_study',
    context: { ...(draft ?? {}), ...(s.study_id ? { parent_study_id: s.study_id } : {}) },
    primary: !s.study_id
  });
  return buildSurfaceEnvelopes(surfaceId, QUANT_CATALOG, {}, [
    { id: 'root', component: 'Card', child: 'col' },
    { id: 'col', component: 'Column', children: ['study', 'actions'] },
    {
      id: 'study',
      component: 'QuantStudyCard',
      title: s.title,
      study_id: s.study_id ?? null,
      parent_study_id: s.parent_study_id ?? null,
      status: s.status ?? 'DRAFT',
      verdict: s.verdict ?? 'PENDING',
      kind: s.kind ?? 'factor',
      hypothesis: s.hypothesis ?? '',
      target_cluster: s.target_cluster ?? 'engine_sandbox',
      metrics: s.metrics ?? [],
      proofs: s.proofs ?? null,
      warnings: s.warnings ?? []
    },
    { id: 'actions', component: 'Row', children: buttons.map((b) => b.id) },
    ...buttons.map((b) => ({ id: b.id, component: 'Button', label: b.label, primary: b.primary, action: { name: b.action, context: b.context, sendDataModel: false } }))
  ]);
}
