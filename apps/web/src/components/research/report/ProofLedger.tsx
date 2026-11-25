'use client';

import { useState } from 'react';
import { shortHash } from '@/lib/format';
import { openStudyDrawer } from '@/lib/glossary/store';
import type { ProofCheckItem, ProofStatus, ResearchStudyDetail } from '@/lib/types';
import { useWorkbench } from '@/lib/workbench';
import { TermTip } from '../../glossary/TermTip';
import { Icon } from '../../icons';
import { EmptyState, Panel, Tag, type Tone } from '../../ui';

const TONE: Record<ProofStatus, Tone> = { pass: 'pass', warn: 'warn', fail: 'fail', insufficient: 'mute', not_applicable: 'mute' };
const VERDICT: Record<ProofStatus, 'PASS' | 'WARN' | 'FAIL' | 'N/A'> = { pass: 'PASS', warn: 'WARN', fail: 'FAIL', insufficient: 'N/A', not_applicable: 'N/A' };

const kvText = (v: unknown) => (typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v));

/** ③ Formal verification ledger: 8 proofs, expandable evidence, hover actions (ask Bot / re-dispatch). */
export function ProofLedger({ detail, highlight }: { detail: ResearchStudyDetail; highlight?: string | null }) {
  const { dock, view } = useWorkbench();
  const [open, setOpen] = useState<Set<string>>(new Set());
  const fp = detail.formal_proof;
  if (!fp) {
    return (
      <Panel title={<TermTip id="ast_causal_proof">③ 形式化验证证明链</TermTip>}>
        <EmptyState height={90} title="FORMAL PROOF · 未生成" detail="FORMAL_VERIFY 阶段完成后由后端 FormalVerificationEngine 写入" />
      </Panel>
    );
  }
  const n = (s: ProofStatus) => fp.checks.filter((c) => c.status === s).length;
  const toggle = (id: string) =>
    setOpen((o) => {
      const x = new Set(o);
      if (x.has(id)) x.delete(id);
      else x.add(id);
      return x;
    });
  const ask = (c: ProofCheckItem) =>
    dock.explainTerm({
      termId: c.term_id || 'ast_causal_proof',
      term: c.label,
      value: c.error_metric || c.statistic,
      display: `${c.statistic}${c.error_metric ? ` · ${c.error_metric}` : ''}`,
      threshold: c.threshold,
      verdict: VERDICT[c.status],
      gap: c.reason_code || undefined,
      view,
      context: { study_id: detail.study_id, check_id: c.check_id, suite: c.suite, n: c.n_observed }
    });
  const redo = (c: ProofCheckItem) =>
    openStudyDrawer({
      title: `${detail.title} · 修复 ${c.label}`.slice(0, 60),
      hypothesis: `${detail.hypothesis}\n\n复研目标：证明项 ${c.check_id}（${c.label}）${c.status.toUpperCase()}，统计量 ${c.statistic}，阈值 ${c.threshold}${c.reason_code ? `，原因 ${c.reason_code}` : ''}。`,
      kind: detail.kind,
      parent_study_id: detail.study_id,
      family_id: detail.family_id,
      lockParent: true,
      factor_id: detail.factor_id ?? null,
      expression: detail.expression ?? null,
      universe_id: detail.universe_id,
      target_cluster: detail.target_cluster,
      avoid: [`${c.label}：${c.reason_code || c.status}`],
      source: `${detail.study_id} · ${c.check_id}`
    });

  return (
    <Panel
      title={<TermTip id="ast_causal_proof">③ 形式化验证证明链</TermTip>}
      sub={`${fp.proofs_passed}/${fp.proofs_total} PASS · ${n('warn')} WARN · ${n('fail')} FAIL`}
      tools={
        <>
          <Tag tone={fp.passed ? 'pass' : 'fail'}>{fp.passed ? 'PROVEN' : 'BLOCKED'}</Tag>
          <span className="ps" title={fp.proof_sha256}>
            sha {shortHash(fp.proof_sha256, 4)}
          </span>
        </>
      }
      flush
    >
      {fp.blocking_failures.length ? (
        <div className="notice" style={{ margin: 8 }}>
          <Icon name="warn" size={13} style={{ color: 'var(--up)', flex: 'none', marginTop: 1 }} />
          <div>
            <b>阻断项</b> · {fp.blocking_failures.join(' / ')}（任一阻断项失败时研究不得标记为 VERIFIED）
          </div>
        </div>
      ) : null}
      <div role="list">
        {fp.checks.map((c) => {
          const isOpen = open.has(c.check_id);
          const entries = Object.entries(c.detail ?? {}).slice(0, 10);
          return (
            <div key={c.check_id} role="listitem" className={`prow ${isOpen ? 'open' : ''}`} id={`proof-${c.check_id}`} style={highlight && (highlight === c.check_id || highlight === c.term_id) ? { background: 'var(--sig-bg)' } : undefined}>
              <div
                className="prow-h"
                role="button"
                tabIndex={0}
                aria-expanded={isOpen}
                onClick={() => toggle(c.check_id)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    toggle(c.check_id);
                  }
                }}
              >
                <Icon name="chev" size={10} sw={2} />
                <span className="ps2">
                  <i className={`vd ${TONE[c.status]}`} style={{ marginRight: 4, verticalAlign: 1 }} />
                  {c.suite}
                </span>
                <span className="pl">
                  <TermTip id={c.term_id || 'ast_causal_proof'} as="span" display={`${c.statistic} · ${VERDICT[c.status]}`} context={{ threshold: c.threshold, status: c.status }}>
                    {c.label}
                  </TermTip>
                </span>
                <span className="pv" title={`${c.statistic} · 阈值 ${c.threshold}`}>
                  {c.statistic} <span className="c-3">/ {c.threshold}</span>
                </span>
                <Tag tone={TONE[c.status]}>{c.status === 'not_applicable' ? 'N/A' : c.status.toUpperCase()}</Tag>
                <span className="pa" onClick={(e) => e.stopPropagation()}>
                  <button type="button" className="btn sm" onClick={() => ask(c)} title="问 Bot：结合实测值解读该证明项">
                    <Icon name="hex" size={9} sw={2} />
                    问 Bot
                  </button>
                  <button type="button" className="btn sm" onClick={() => redo(c)} title="以本研究为父课题派发复研">
                    <Icon name="branch" size={9} sw={2} />
                    派发复研
                  </button>
                </span>
              </div>
              <div className="prow-d">
                <div className="kvg">
                  <span>误差 / 证伪量</span>
                  <b>{c.error_metric || '—'}</b>
                  <span>reason_code</span>
                  <b>{c.reason_code || '—'}</b>
                  <span>样本量 n</span>
                  <b>{c.n_observed}</b>
                  {c.witness_keys.length ? (
                    <>
                      <span>witness</span>
                      <b>{c.witness_keys.join(', ')}</b>
                    </>
                  ) : null}
                  {c.evidence_uri ? (
                    <>
                      <span>evidence</span>
                      <b>{c.evidence_uri}</b>
                    </>
                  ) : null}
                  {entries.map(([k, v]) => (
                    <span key={k} style={{ display: 'contents' }}>
                      <span>{k}</span>
                      <b>{kvText(v).slice(0, 200)}</b>
                    </span>
                  ))}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </Panel>
  );
}
