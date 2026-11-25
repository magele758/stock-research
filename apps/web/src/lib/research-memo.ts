/**
 * Client-side IC Memo (Markdown) built strictly from a `ResearchStudyDetail` the backend returned.
 * Only used when `GET /research/studies/{id}/memo` is unavailable; every figure comes from the detail.
 */
import type { ResearchStudyDetail } from './types';

const cell = (v: unknown) => String(v ?? '—').replace(/\|/g, '\\|').replace(/\n/g, ' ');

export function buildMemo(d: ResearchStudyDetail): string {
  const L: string[] = [];
  L.push(`# IC Memo · ${d.title}`, '');
  L.push(`> 由 QuantLab Pro 前端根据研究报告对象生成（后端 memo 接口不可用）。所有数值取自 \`/research/studies/${d.study_id}\`。`, '');
  L.push('| 字段 | 值 |', '| :--- | :--- |');
  const meta: [string, unknown][] = [
    ['study_id', d.study_id],
    ['parent_study_id', d.parent_study_id],
    ['family_id', d.family_id],
    ['kind', d.kind],
    ['status / verdict', `${d.status} / ${d.verdict}`],
    ['universe / snapshot', `${d.universe_id} / ${d.snapshot_id}`],
    ['cluster', `${d.dispatch_mode} · ${d.target_cluster}`],
    ['fact_fingerprint', d.fact_fingerprint],
    ['created / completed', `${d.created_at} / ${d.completed_at ?? '—'}`]
  ];
  meta.forEach(([k, v]) => L.push(`| ${k} | ${cell(v)} |`));
  L.push('', '## 1. 假设', '', d.hypothesis || '—', '');
  if (d.math_derivation) {
    const m = d.math_derivation;
    L.push('## 2. 数学推演', '', m.mechanism || '', '', '```tex', [m.formula_latex, ...m.derivation_steps].filter(Boolean).join('\n'), '```', '');
    if (m.boundary_conditions.length) L.push('边界条件：', ...m.boundary_conditions.map((b) => `- ${b}`), '');
    if (m.expected_failure_regimes.length) L.push(`理论失效 Regime：${m.expected_failure_regimes.join(' / ')}`, '');
  }
  const fp = d.formal_proof;
  if (fp) {
    L.push(`## 3. 形式化验证 · ${fp.proofs_passed}/${fp.proofs_total} · ${fp.passed ? 'PROVEN' : 'BLOCKED'}`, '');
    L.push('| 套件 | 证明项 | 统计量 | 阈值 | 误差 | 状态 |', '| :--- | :--- | :--- | :--- | :--- | :--- |');
    fp.checks.forEach((c) => L.push(`| ${c.suite} | ${cell(c.label)} | ${cell(c.statistic)} | ${cell(c.threshold)} | ${cell(c.error_metric)} | ${c.status.toUpperCase()} |`));
    L.push('', `proof_sha256: \`${fp.proof_sha256}\``, '');
  }
  const ms = Object.entries(d.metrics_summary ?? {}).filter(([, v]) => typeof v === 'number' || typeof v === 'string' || typeof v === 'boolean');
  if (ms.length) {
    L.push('## 4. 关键指标', '', '| 指标 | 值 |', '| :--- | :--- |');
    ms.forEach(([k, v]) => L.push(`| ${k} | ${cell(v)} |`));
    L.push('');
  }
  const da = d.dimension_analysis;
  if (da?.slices.length) {
    L.push('## 5. 跨维度稳定性', '');
    if (da.warning_summary) L.push(`> ${da.warning_summary}`, '');
    L.push('| 维度 | 桶 | 策略超额% | 基线% | Δ% | 贡献% |', '| :--- | :--- | ---: | ---: | ---: | ---: |');
    da.slices.forEach((s) => L.push(`| ${s.dim} | ${cell(s.bucket)} | ${s.strategy_excess_pct} | ${s.baseline_return_pct} | ${s.alpha_vs_baseline_pct} | ${s.contribution_pct} |`));
    L.push('');
  }
  if (d.diagnosis.length) {
    L.push('## 6. 诊断与改进', '');
    d.diagnosis.forEach((x) => {
      L.push(`### [${x.severity.toUpperCase()}] ${x.claim}`, '', `根因 \`${x.root_cause}\` · 规则 \`${x.rule_id}\``, '');
      x.guidance.forEach((g) => L.push(`- ${g}`));
      L.push('');
    });
  }
  return L.join('\n');
}
