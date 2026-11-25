'use client';

import { useRouter } from 'next/navigation';
import { DEFAULT_CONSTRAINTS } from '@/lib/meta';
import { openStudyDrawer } from '@/lib/glossary/store';
import type { DiagnosisItem, OptConstraints, ResearchStudyDetail } from '@/lib/types';
import { useExternalConstraintPatch, useWorkbench } from '@/lib/workbench';
import { Icon } from '../../icons';
import { EmptyState, Panel, Tag } from '../../ui';

const SEV = { critical: 'fail', major: 'warn', minor: 'info' } as const;

/** Only whitelisted, finite numeric OptConstraints keys are accepted from a diagnosis patch. */
function constraintPatch(p: DiagnosisItem['constraints_patch']): Partial<OptConstraints> | null {
  if (!p || typeof p !== 'object') return null;
  const out: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(p)) {
    if (!(k in DEFAULT_CONSTRAINTS)) continue;
    const def = (DEFAULT_CONSTRAINTS as unknown as Record<string, unknown>)[k];
    if (typeof def === 'number' && typeof v === 'number' && Number.isFinite(v)) out[k] = v;
    else if (typeof def === 'string' && typeof v === 'string') out[k] = v;
  }
  return Object.keys(out).length ? (out as Partial<OptConstraints>) : null;
}

const evText = (e: Record<string, unknown>) => {
  const kind = String(e.kind ?? e.type ?? 'evidence');
  const ref = e.ref ?? e.key ?? e.metric ?? '';
  const v = e.value ?? e.observed ?? '';
  return `${kind}:${String(ref)}${v !== '' ? ` ${typeof v === 'number' ? +v.toFixed(4) : String(v)}` : ''}`;
};

/** ⑤ Root-cause diagnosis with three actions: V2 child study, write constraints to /portfolio, ask Quant-Lead. */
export function DiagnosisCards({ detail, onEvidence }: { detail: ResearchStudyDetail; onEvidence?: (kind: string, ref: string) => void }) {
  const { dock } = useWorkbench();
  const patch = useExternalConstraintPatch();
  const router = useRouter();
  const items = detail.diagnosis ?? [];
  if (!items.length) {
    const terminal = ['COMPLETED', 'FALSIFIED', 'FAILED'].includes(String(detail.status));
    return (
      <Panel title="⑤ 根因诊断 & 改进指导">
        <EmptyState height={80} title={terminal ? 'NO FINDINGS' : 'DIAGNOSIS · 等待中'} detail={terminal ? '确定性诊断规则未触发任何问题' : 'DIAGNOSIS 阶段完成后生成'} />
      </Panel>
    );
  }
  const child = (d: DiagnosisItem) => {
    const cp = (d.child_study_patch ?? {}) as Record<string, unknown>;
    const s = (k: string) => (typeof cp[k] === 'string' ? (cp[k] as string) : undefined);
    openStudyDrawer({
      title: s('title') ?? `${detail.title} V2`.slice(0, 60),
      hypothesis: s('hypothesis') ?? `${detail.hypothesis}\n\n改进（${d.rule_id}）：${d.guidance.join('；')}`,
      kind: (s('kind') as typeof detail.kind) ?? detail.kind,
      parent_study_id: detail.study_id,
      family_id: detail.family_id,
      lockParent: true,
      factor_id: s('factor_id') ?? detail.factor_id ?? null,
      expression: s('expression') ?? detail.expression ?? null,
      universe_id: detail.universe_id,
      target_cluster: s('target_cluster') ?? detail.target_cluster,
      dimensions: Array.isArray(cp.dimensions) ? (cp.dimensions as unknown[]).map(String) : undefined,
      constraints: constraintPatch(d.constraints_patch) ? ({ ...DEFAULT_CONSTRAINTS, ...constraintPatch(d.constraints_patch) } as OptConstraints) : null,
      avoid: [d.claim],
      source: `${detail.study_id} · ${d.id}`
    });
  };
  return (
    <Panel title="⑤ 根因诊断 & 改进指导" sub={`${items.length} 条 · ${items.filter((d) => d.severity === 'critical').length} CRITICAL`}>
      {items.map((d) => {
        const cp = constraintPatch(d.constraints_patch);
        return (
          <div key={d.id} className={`diag ${d.severity}`}>
            <div className="dh">
              <Tag tone={SEV[d.severity] ?? 'warn'}>{d.severity.toUpperCase()}</Tag>
              <span title={d.claim}>{d.claim}</span>
            </div>
            <p>
              根因 <code className="mono">{d.root_cause}</code> · 规则 <code className="mono">{d.rule_id}</code>
            </p>
            {d.evidence.length ? (
              <div className="chips" style={{ marginTop: 4 }}>
                {d.evidence.slice(0, 6).map((e, i) => (
                  <button key={i} type="button" className="cc" style={{ cursor: onEvidence ? 'pointer' : 'default' }} onClick={() => onEvidence?.(String(e.kind ?? ''), String(e.ref ?? e.key ?? ''))}>
                    {evText(e)}
                  </button>
                ))}
              </div>
            ) : null}
            {d.guidance.length ? (
              <ul>
                {d.guidance.slice(0, 3).map((g, i) => (
                  <li key={i}>{g}</li>
                ))}
              </ul>
            ) : null}
            {cp ? (
              <div className="dev">
                {Object.entries(cp).map(([k, v]) => (
                  <span key={k}>
                    {k} → <b>{String(v)}</b>
                  </span>
                ))}
              </div>
            ) : null}
            <div className="da">
              <button type="button" className="btn sm pri" onClick={() => child(d)}>
                <Icon name="branch" size={10} sw={2} />
                创建 V2 迭代子研究并派发集群
              </button>
              {cp ? (
                <button
                  type="button"
                  className="btn sm"
                  onClick={() => {
                    patch((prev) => ({ ...prev, ...cp }));
                    router.push('/portfolio');
                  }}
                >
                  <Icon name="portfolio" size={10} sw={2} />
                  把建议约束写入 /portfolio
                </button>
              ) : null}
              <button
                type="button"
                className="btn sm"
                onClick={() =>
                  dock.send(`请深入追问研究 ${detail.study_id} 的诊断 ${d.id}（${d.rule_id} · ${d.severity}）：${d.claim}。根因 ${d.root_cause}。证据：${d.evidence.map(evText).join('；') || '—'}。请给出可证伪的下一步实验设计。`, { bot: 'quant-lead' })
                }
              >
                <Icon name="hex" size={10} sw={2} />
                让 Quant-Lead 深入追问
              </button>
            </div>
          </div>
        );
      })}
    </Panel>
  );
}
