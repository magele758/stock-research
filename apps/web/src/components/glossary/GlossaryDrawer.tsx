'use client';

import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { useGlossary } from '@/lib/glossary';
import { searchTerms } from '@/lib/glossary/search';
import { drawerStore, usePageValues } from '@/lib/glossary/store';
import { DOMAINS, type PageValue, type TermDomain } from '@/lib/glossary/types';
import { verdictTone } from '@/lib/thresholds';
import { useWorkbench } from '@/lib/workbench';
import { Icon } from '../icons';
import { Drawer } from './Drawer';
import { TermCardBody } from './TermCard';

type Filter = 'ALL' | 'PAGE' | TermDomain;

/** ⌘K glossary: fuzzy zh / en / acronym search, domain + "本页术语" filters, live verdict dots. */
export function GlossaryDrawer({ focus, query: q0 }: { focus?: string; query?: string }) {
  const { terms, byId, api, apiCount } = useGlossary(true);
  const { view } = useWorkbench();
  const pageVals = usePageValues();
  const [query, setQuery] = useState(q0 ?? '');
  const [filter, setFilter] = useState<Filter>(focus || q0 ? 'ALL' : pageVals.length ? 'PAGE' : 'ALL');
  const [sel, setSel] = useState<string | undefined>(focus);
  const input = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    input.current?.focus();
  }, []);

  const byTerm = useMemo(() => {
    const m = new Map<string, PageValue>();
    const rank = { FAIL: 3, WARN: 2, PASS: 1, 'N/A': 0 } as const;
    for (const p of pageVals) {
      const cur = m.get(p.termId);
      if (!cur || rank[p.verdict] > rank[cur.verdict]) m.set(p.termId, p);
    }
    return m;
  }, [pageVals]);

  const onPage = useMemo(() => new Set(byTerm.keys()), [byTerm]);

  const list = useMemo(() => {
    const scoped = filter === 'ALL' ? terms : filter === 'PAGE' ? terms.filter((t) => onPage.has(t.id)) : terms.filter((t) => t.domain === filter);
    return searchTerms(scoped, query, onPage);
  }, [terms, filter, query, onPage]);

  const current = (sel && byId.get(sel)) || list[0];

  useEffect(() => {
    listRef.current?.querySelector('.gi.on')?.scrollIntoView({ block: 'nearest' });
  }, [current?.id]);

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    e.preventDefault();
    if (!list.length) return;
    const i = Math.max(0, list.findIndex((t) => t.id === current?.id));
    const n = e.key === 'ArrowDown' ? Math.min(list.length - 1, i + 1) : Math.max(0, i - 1);
    setSel(list[n].id);
  };

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const t of terms) c[t.domain] = (c[t.domain] ?? 0) + 1;
    return c;
  }, [terms]);

  const apiText = api === 'merged' ? `后端术语库已合并 ${apiCount} 条` : api === 'loading' ? '合并后端术语库中…' : api === 'unavailable' ? '后端 /glossary 不可用 · 仅内置条目' : '';

  return (
    <Drawer
      label="术语速查库"
      title="术语速查库"
      sub={`${terms.length} 条 · ${view.toUpperCase()} 本页 ${onPage.size}`}
      onClose={drawerStore.close}
      tools={<kbd>⌘K</kbd>}
      footer={
        <span className="ps" style={{ display: 'flex', gap: 10, width: '100%' }}>
          <span>↑↓ 选择 · Esc 关闭</span>
          <span style={{ marginLeft: 'auto', color: api === 'unavailable' ? 'var(--warn)' : undefined }}>{apiText}</span>
        </span>
      }
    >
      <div className="gq">
        <Icon name="search" size={13} />
        <input
          ref={input}
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setSel(undefined);
          }}
          onKeyDown={onKey}
          placeholder="搜索 中文 / English / 缩写，如 PBO、秩相关、nw"
          aria-label="搜索术语"
        />
        {query ? (
          <button type="button" className="c-3" aria-label="清空" onClick={() => setQuery('')}>
            <Icon name="x" size={11} sw={2} />
          </button>
        ) : null}
      </div>
      <div className="gf" role="tablist" aria-label="领域筛选">
        <button type="button" className={filter === 'ALL' ? 'on' : ''} onClick={() => setFilter('ALL')}>
          全部 {terms.length}
        </button>
        <button type="button" className={filter === 'PAGE' ? 'on' : ''} onClick={() => setFilter('PAGE')} disabled={!onPage.size}>
          本页术语 {onPage.size}
        </button>
        {DOMAINS.map((d) => (
          <button key={d.key} type="button" className={filter === d.key ? 'on' : ''} onClick={() => setFilter(d.key)}>
            {d.key} {d.label} {counts[d.key] ?? 0}
          </button>
        ))}
      </div>
      <div className="gbody">
        <div className="glist" ref={listRef} role="listbox" aria-label="术语列表">
          {list.length ? (
            list.map((t) => {
              const pv = byTerm.get(t.id);
              return (
                <button key={t.id} type="button" role="option" aria-selected={current?.id === t.id} className={`gi ${current?.id === t.id ? 'on' : ''}`} onClick={() => setSel(t.id)}>
                  <i className={`vd ${pv ? verdictTone(pv.verdict) : ''}`} title={pv ? `本页 ${pv.display ?? ''} · ${pv.verdict}` : undefined} />
                  <b>{t.zh}</b>
                  <em>{t.domain}</em>
                  <small>{t.en}</small>
                </button>
              );
            })
          ) : (
            <div className="empty" style={{ height: 120, margin: 10 }}>
              <b>{terms.length ? 'NO MATCH' : 'LOADING'}</b>
              <span>{terms.length ? '换个关键词或切换领域' : '加载术语库…'}</span>
            </div>
          )}
        </div>
        <div className="gdet">
          {current ? (
            <TermCardBody key={current.id} term={current} tv={byTerm.get(current.id)} onRelated={(id) => setSel(id)} />
          ) : null}
        </div>
      </div>
    </Drawer>
  );
}
