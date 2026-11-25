'use client';

import { fmtTs, fx, shortHash } from '@/lib/format';
import { openStudyDrawer } from '@/lib/glossary/store';
import type { FalsifiedTombstoneRecord } from '@/lib/types';
import { TermTip } from '../glossary/TermTip';
import { Icon } from '../icons';

const metricText = (m: Record<string, unknown>) =>
  Object.entries(m)
    .filter(([, v]) => typeof v === 'number' || typeof v === 'string' || typeof v === 'boolean')
    .slice(0, 3)
    .map(([k, v]) => `${k} ${typeof v === 'number' ? fx(v, Math.abs(v) < 1 ? 3 : 2) : String(v)}`)
    .join(' · ');

/** Falsified tombstones: failure statistics + lesson; "基于教训重开" pre-fills the drawer with avoid-conditions. */
export function TombstoneList({ items, onOpen }: { items: FalsifiedTombstoneRecord[]; onOpen: (studyId: string) => void }) {
  if (!items.length) {
    return (
      <div className="empty" style={{ height: 100, margin: 8 }}>
        <b>NO TOMBSTONES</b>
        <span>尚无已证伪课题</span>
      </div>
    );
  }
  return (
    <div>
      {items.map((t) => (
        <div key={t.tombstone_id} className="tomb" style={{ borderLeft: '2px solid var(--up)' }}>
          <div className="th2">
            <Icon name="tomb" size={11} style={{ color: 'var(--up)', flex: 'none' }} />
            <span title={t.expression}>{t.factor_id || shortHash(t.expr_hash, 5)}</span>
            <span className="tag fail" style={{ flex: 'none' }}>
              <TermTip id="falsified_tombstone" as="span" context={{ failure_code: t.failure_code, ast: shortHash(t.ast_fingerprint, 4) }}>
                {t.failure_code}
              </TermTip>
            </span>
          </div>
          <div className="mono c-3" style={{ fontSize: 9.5, marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={metricText(t.failure_metrics)}>
            {metricText(t.failure_metrics) || '—'} · {fmtTs(t.created_at).slice(0, 10)}
          </div>
          <p title={t.lesson_summary}>{t.lesson_summary || t.memory_line}</p>
          <div style={{ display: 'flex', gap: 4 }}>
            {t.study_id ? (
              <button type="button" className="btn sm" onClick={() => onOpen(t.study_id as string)}>
                查看报告
              </button>
            ) : null}
            <button
              type="button"
              className="btn sm"
              onClick={() =>
                openStudyDrawer({
                  title: `${t.factor_id || '因子'} · 基于教训重开`,
                  hypothesis: `在已证伪课题（${t.failure_code}）的基础上修正：`,
                  expression: t.expression || null,
                  factor_id: t.factor_id || null,
                  universe_id: t.universe_id,
                  avoid: [t.lesson_summary || t.memory_line].filter(Boolean),
                  source: `墓碑 ${t.tombstone_id}`
                })
              }
            >
              <Icon name="branch" size={10} sw={2} />
              基于教训重开
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
