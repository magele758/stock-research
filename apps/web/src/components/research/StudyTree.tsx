'use client';

import { useMemo, useState, type KeyboardEvent } from 'react';
import { isStudyRunning } from '@/lib/research-api';
import type { ResearchStudySummary } from '@/lib/types';
import { Icon } from '../icons';
import { metricLine, studyLabel, studyTone } from './shared';

type Filter = 'all' | 'run' | 'ok' | 'bad';
interface Row {
  s: ResearchStudySummary;
  depth: number;
  family: string;
  hasKids: boolean;
}

const MAX_DEPTH = 4;

function flatten(studies: ResearchStudySummary[], collapsed: Set<string>): Row[] {
  const byId = new Map(studies.map((s) => [s.study_id, s]));
  const kids = new Map<string, ResearchStudySummary[]>();
  const roots: ResearchStudySummary[] = [];
  for (const s of studies) {
    const p = s.parent_study_id && byId.has(s.parent_study_id) ? s.parent_study_id : null;
    if (p) kids.set(p, [...(kids.get(p) ?? []), s]);
    else roots.push(s);
  }
  const by = (a: ResearchStudySummary, b: ResearchStudySummary) => (a.created_at < b.created_at ? 1 : -1);
  const out: Row[] = [];
  const walk = (s: ResearchStudySummary, depth: number) => {
    const ch = (kids.get(s.study_id) ?? []).sort((a, b) => (a.created_at < b.created_at ? -1 : 1));
    out.push({ s, depth: Math.min(depth, MAX_DEPTH), family: s.family_id, hasKids: ch.length > 0 });
    if (collapsed.has(s.study_id)) return;
    ch.forEach((c) => walk(c, depth + 1));
  };
  roots.sort(by).forEach((r) => walk(r, 0));
  return out;
}

export function StudyTree({ studies, selected, onSelect }: { studies: ResearchStudySummary[]; selected: string | null; onSelect: (id: string) => void }) {
  const [filter, setFilter] = useState<Filter>('all');
  const [q, setQ] = useState('');
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const visible = useMemo(() => {
    const ql = q.trim().toLowerCase();
    const match = (s: ResearchStudySummary) => {
      const tone = studyTone(s.status, s.verdict);
      if (filter === 'run' && !isStudyRunning(s.status)) return false;
      if (filter === 'ok' && tone !== 'pass') return false;
      if (filter === 'bad' && tone !== 'fail') return false;
      return !ql || s.study_id.toLowerCase().includes(ql) || s.title.toLowerCase().includes(ql) || s.topic_tags.some((t) => t.toLowerCase().includes(ql));
    };
    const keep = studies.filter(match);
    return filter === 'all' && !ql ? flatten(studies, collapsed) : keep.map((s) => ({ s, depth: 0, family: s.family_id, hasKids: false }));
  }, [studies, filter, q, collapsed]);

  const counts = useMemo(
    () => ({
      run: studies.filter((s) => isStudyRunning(s.status)).length,
      ok: studies.filter((s) => studyTone(s.status, s.verdict) === 'pass').length,
      bad: studies.filter((s) => studyTone(s.status, s.verdict) === 'fail').length
    }),
    [studies]
  );

  const toggle = (id: string, open?: boolean) =>
    setCollapsed((c) => {
      const n = new Set(c);
      const isOpen = !n.has(id);
      if (open === undefined ? isOpen : !open) n.add(id);
      else n.delete(id);
      return n;
    });

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const i = visible.findIndex((r) => r.s.study_id === selected);
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const n = visible[Math.max(0, Math.min(visible.length - 1, i + (e.key === 'ArrowDown' ? 1 : -1)))];
      if (n) onSelect(n.s.study_id);
    } else if ((e.key === 'ArrowLeft' || e.key === 'ArrowRight') && i >= 0 && visible[i].hasKids) {
      e.preventDefault();
      toggle(visible[i].s.study_id, e.key === 'ArrowRight');
    }
  };

  let lastFamily = '';
  return (
    <div>
      <div style={{ padding: '6px 8px', display: 'flex', flexDirection: 'column', gap: 5, borderBottom: '1px solid var(--line)' }}>
        <div className="gf" style={{ padding: 0, border: 0 }}>
          {(
            [
              ['all', `全部 ${studies.length}`],
              ['run', `进行中 ${counts.run}`],
              ['ok', `已验证 ${counts.ok}`],
              ['bad', `已证伪 ${counts.bad}`]
            ] as [Filter, string][]
          ).map(([k, l]) => (
            <button key={k} type="button" className={filter === k ? 'on' : ''} onClick={() => setFilter(k)}>
              {l}
            </button>
          ))}
        </div>
        <input className="inp" style={{ height: 24, fontSize: 11 }} placeholder="搜索 study_id / 标题 / 标签" value={q} onChange={(e) => setQ(e.target.value)} aria-label="搜索研究" />
      </div>
      <div role="tree" aria-label="研究课题树" tabIndex={0} onKeyDown={onKey} style={{ outline: 'none' }}>
        {visible.length === 0 ? (
          <div className="empty" style={{ height: 90, margin: 8 }}>
            <b>NO STUDIES</b>
            <span>{studies.length ? '无匹配课题' : '点击「新建研究」派发第一个课题'}</span>
          </div>
        ) : (
          visible.map((r) => {
            const head = filter === 'all' && !q && r.depth === 0 && r.family !== lastFamily;
            if (r.depth === 0) lastFamily = r.family;
            const on = r.s.study_id === selected;
            const tone = studyTone(r.s.status, r.s.verdict);
            const ml = metricLine(r.s);
            return (
              <div key={r.s.study_id}>
                {head ? <div className="nsec" style={{ padding: '7px 9px 3px' }}>{r.family}</div> : null}
                <button
                  type="button"
                  role="treeitem"
                  aria-selected={on}
                  aria-expanded={r.hasKids ? !collapsed.has(r.s.study_id) : undefined}
                  className={`trow ${on ? 'on' : ''}`}
                  style={{ paddingLeft: 8 + r.depth * 12 }}
                  onClick={() => onSelect(r.s.study_id)}
                  onDoubleClick={() => r.hasKids && toggle(r.s.study_id)}
                >
                  {r.depth > 0 ? <span className="lnk" style={{ left: r.depth * 12 - 2 }} /> : null}
                  <i className={`vd ${tone === 'info' ? 'warn' : tone === 'mute' ? '' : tone}`} />
                  <b>
                    <span className="mono" style={{ fontSize: 10.5, color: 'var(--tx2)', marginRight: 6 }}>
                      {r.s.study_id}
                    </span>
                    {r.s.title}
                  </b>
                  <span className={`tag ${tone}`} style={{ fontSize: 8.5 }}>
                    {isStudyRunning(r.s.status) ? <i className="spin" style={{ width: 7, height: 7 }} /> : null}
                    {studyLabel(r.s.status, r.s.verdict)}
                  </span>
                  <small>
                    {r.hasKids ? (
                      <span
                        role="button"
                        tabIndex={-1}
                        onClick={(e) => {
                          e.stopPropagation();
                          toggle(r.s.study_id);
                        }}
                        style={{ display: 'inline-flex', verticalAlign: -2, marginRight: 3 }}
                      >
                        <Icon name={collapsed.has(r.s.study_id) ? 'chev' : 'down'} size={9} sw={2} />
                      </span>
                    ) : null}
                    {ml || `${r.s.kind} · ${r.s.target_cluster}`}
                  </small>
                </button>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
