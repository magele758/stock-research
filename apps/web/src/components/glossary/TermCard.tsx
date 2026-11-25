'use client';

import type { ReactNode } from 'react';
import { fmtGateValue, gateText, verdictTone, type GateSpec } from '@/lib/thresholds';
import { evaluate } from '@/lib/glossary/evaluate';
import { openGlossary } from '@/lib/glossary/store';
import { DOMAINS, type GlossaryTerm, type TermQuery, type TermValue } from '@/lib/glossary/types';
import { useWorkbench } from '@/lib/workbench';
import { Icon } from '../icons';
import { Tag } from '../ui';
import { FormulaBlock } from './FormulaBlock';

const domainLabel = (d: string) => DOMAINS.find((x) => x.key === d)?.label ?? d;

/** PASS / WARN / FAIL zones over the gate axis + current-value marker. */
function PositionBar({ gate, value }: { gate: GateSpec; value: number }) {
  if (gate.op === 'eq') return null;
  const abs = gate.op === 'abs<=';
  const [lo0, hi0] = gate.range ?? (abs ? [0, gate.value * 2] : [Math.min(0, gate.value * 0.5), gate.value * 2]);
  const x = abs ? Math.abs(value) : value;
  const lo = Math.min(lo0, x);
  const hi = Math.max(hi0, x);
  if (!(hi > lo)) return null;
  const p = (v: number) => Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100));
  const g = p(gate.value);
  const lowerBetter = gate.op === '<=' || gate.op === '<' || abs;
  const zones: { cls: string; a: number; b: number }[] = lowerBetter
    ? [
        { cls: 'pass', a: 0, b: g },
        { cls: 'fail', a: g, b: 100 }
      ]
    : [
        { cls: 'fail', a: 0, b: g },
        { cls: 'pass', a: g, b: 100 }
      ];
  if (gate.warn) zones.push({ cls: 'warn', a: p(Math.min(...gate.warn)), b: p(Math.max(...gate.warn)) });
  return (
    <div className="tc-bar" aria-hidden>
      {zones.map((z, i) => (
        <span key={i} className={`z ${z.cls}`} style={{ left: `${z.a}%`, width: `${Math.max(0, z.b - z.a)}%` }} />
      ))}
      <span className="gl" style={{ left: `${g}%` }} />
      <span className="mk" style={{ left: `${p(x)}%` }} />
      <span className="tk" style={{ left: '0%', transform: 'none' }}>
        {fmtGateValue(lo, gate.unit)}
      </span>
      <span className="tk" style={{ left: `${g}%` }}>
        {fmtGateValue(gate.value, gate.unit)}
      </span>
      <span className="tk" style={{ left: '100%', transform: 'translateX(-100%)' }}>
        {fmtGateValue(hi, gate.unit)}
      </span>
    </div>
  );
}

export function buildTermQuery(term: GlossaryTerm, tv: TermValue | undefined, view: string): TermQuery {
  const ev = evaluate(term, tv);
  return {
    termId: term.id,
    term: `${term.zh} (${term.en})`,
    value: tv?.value ?? null,
    display: tv?.display,
    threshold: ev.gate ? ev.gate.label || gateText(ev.gate) : term.redline,
    verdict: ev.verdict,
    gap: ev.verdict === 'N/A' ? undefined : ev.text,
    view,
    context: tv?.context
  };
}

export function TermCardBody({
  term,
  tv,
  head,
  onRelated,
  actions = true
}: {
  term: GlossaryTerm;
  tv?: TermValue;
  head?: ReactNode;
  onRelated?: (id: string) => void;
  actions?: boolean;
}) {
  const { dock, view } = useWorkbench();
  const ev = evaluate(term, tv);
  const hasVal = tv && ((tv.value !== undefined && tv.value !== null && tv.value !== '') || tv.display);
  const num = typeof tv?.value === 'number' ? tv.value : typeof tv?.value === 'string' ? Number(tv.value) : NaN;
  const ctx = Object.entries(tv?.context ?? {}).filter(([, v]) => v !== undefined && v !== null && v !== '');
  return (
    <>
      <div className="tc-h">
        <div className="tc-n">
          <b>{term.zh}</b>
          <span>
            {term.en}
            {term.aliases.length ? ` · ${term.aliases.slice(0, 2).join(' / ')}` : ''}
          </span>
        </div>
        <Tag tone="info">
          {term.domain} · {domainLabel(term.domain)}
        </Tag>
        {term.prd ? <span className="prd">{term.prd}</span> : null}
        {head}
      </div>
      {ev.gate || term.redline ? (
        <div className="tc-red">
          <em>红线</em>
          <span>{ev.gate ? `${ev.gate.label}${ev.gate.source === 'backend' ? '（后端 gate_checks）' : ''}` : term.redline}</span>
        </div>
      ) : null}
      <div className="tc-sec">
        <div className="tc-plain">{term.plain}</div>
      </div>
      {hasVal ? (
        <div className="tc-sec">
          <div className="tc-l">当前值 · {view.toUpperCase()}</div>
          <div className="tc-live">
            <span className="lv">{tv?.display ?? String(tv?.value)}</span>
            <Tag tone={verdictTone(ev.verdict)}>{ev.verdict}</Tag>
            <span className="gap">{ev.text}</span>
          </div>
          {ev.gate && Number.isFinite(num) ? <PositionBar gate={ev.gate} value={num} /> : null}
          {ctx.length ? (
            <div className="tc-ctx">
              {ctx.slice(0, 6).map(([k, v]) => (
                <span key={k} style={{ marginRight: 10 }}>
                  {k} <b>{String(v)}</b>
                </span>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
      {term.formula.length ? (
        <div className="tc-sec">
          <div className="tc-l">公式</div>
          <FormulaBlock lines={term.formula} symbols={term.symbols} />
        </div>
      ) : null}
      {term.pitfalls.length ? (
        <div className="tc-sec">
          <div className="tc-l">常见陷阱</div>
          <ul className="tc-pit">
            {term.pitfalls.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {term.related.length && onRelated ? (
        <div className="tc-sec">
          <div className="tc-l">相关术语</div>
          <div className="tc-rel">
            {term.related.map((r) => (
              <button key={r} type="button" onClick={() => onRelated(r)}>
                {r}
              </button>
            ))}
          </div>
        </div>
      ) : null}
      {actions ? (
        <div className="tc-act">
          <button type="button" className="btn sm pri" onClick={() => dock.explainTerm(buildTermQuery(term, tv, view))}>
            <Icon name="send" size={10} sw={2} />
            让 Quant-Lead 结合当前数据解读
          </button>
          <ActionGlossary id={term.id} />
        </div>
      ) : null}
    </>
  );
}

function ActionGlossary({ id }: { id: string }) {
  return (
    <button
      type="button"
      className="btn sm"
      style={{ flex: 'none' }}
      onClick={() => openGlossary(id)}
    >
      <Icon name="book" size={10} sw={2} />
      术语速查库 <kbd>⌘K</kbd>
    </button>
  );
}
