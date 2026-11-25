'use client';

import type { InvariantCheckItem } from '@/lib/types';
import { TermTip } from '../glossary/TermTip';
import { EmptyState } from '../ui';

/** Map backend invariant keys to glossary terms (substring match, first hit wins). */
const KEY_TERM: [RegExp, string][] = [
  [/brinson|closure|attribution_sum/i, 'closure_error'],
  [/barra/i, 'barra_attribution'],
  [/psd|eigen/i, 'psd'],
  [/weight|budget|sum_to_one/i, 'opt_objective'],
  [/turnover/i, 'turnover_lambda'],
  [/style|exposure/i, 'style_exposure'],
  [/industry/i, 'industry_active_dev'],
  [/prefix|lookahead|look_ahead/i, 'prefix_invariance'],
  [/t\+?1|t_plus/i, 't_plus_1'],
  [/pov|participation/i, 'pov'],
  [/limit/i, 'price_limit_rules'],
  [/conserv|cash|nav/i, 'conservation_law'],
  [/scale/i, 'scale_invariance']
];

export function invariantTerm(key: string, label = ''): string {
  return KEY_TERM.find(([re]) => re.test(key) || re.test(label))?.[1] ?? 'conservation_law';
}

/** Horizontal invariant badges (backend `invariants[]`); empty → honest placeholder. */
export function InvariantStrip({ items, emptyDetail, height = 54 }: { items: InvariantCheckItem[]; emptyDetail?: string; height?: number }) {
  if (!items.length) return <EmptyState height={height} title="INVARIANTS · 后端未返回" detail={emptyDetail ?? '需 V3 后端在结果中附带 invariants[]'} />;
  const bad = items.filter((i) => !i.passed).length;
  return (
    <div className="inv" role="list" aria-label={`不变量 ${items.length - bad}/${items.length} 通过`}>
      {items.map((it) => (
        <div key={it.key} role="listitem" className={`invb ${it.passed ? '' : 'fail'}`} title={it.detail || undefined}>
          <span className={`st ${it.passed ? 'c-pass' : 'c-up'}`}>
            <i style={{ background: it.passed ? 'var(--pass)' : 'var(--up)' }} />
            {it.passed ? 'OK' : 'BREAK'}
          </span>
          <TermTip id={invariantTerm(it.key, it.label)} display={`${it.value} (tol ${it.tolerance})`} context={{ invariant: it.key, tolerance: it.tolerance, passed: it.passed }}>
            {it.label}
          </TermTip>
          <b>{it.value}</b>
          <small>tol {it.tolerance}</small>
        </div>
      ))}
    </div>
  );
}
