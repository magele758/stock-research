'use client';

import { Fragment } from 'react';
import { esc, fx, isNum } from '@/lib/format';
import { ChartBox } from './core';

export interface WfFold {
  label: string;
  trainStart?: string;
  trainEnd?: string;
  valStart?: string;
  valEnd?: string;
  testStart?: string;
  testEnd?: string;
  ir: number | null;
  isSharpe?: number;
}

const pickStr = (o: Record<string, unknown>, keys: string[]) => {
  for (const k of keys) {
    const v = o[k];
    if (typeof v === 'string' && /^\d{4}-\d{2}-\d{2}/.test(v)) return v.slice(0, 10);
  }
  return undefined;
};
const pickNum = (o: Record<string, unknown>, keys: string[]) => {
  for (const k of keys) {
    const v = o[k];
    if (isNum(v)) return v;
  }
  return undefined;
};

/** `walk_forward_folds` is `list[dict]`; tolerate common key spellings. */
export function normalizeFolds(raw: Record<string, unknown>[] | undefined): WfFold[] {
  return (raw ?? []).map((o, i) => {
    const id = o.fold ?? o.name ?? o.id;
    return {
      label: typeof id === 'string' ? id : isNum(id) ? `F${id}` : `F${i + 1}`,
      trainStart: pickStr(o, ['train_start', 'is_start']),
      trainEnd: pickStr(o, ['train_end', 'is_end']),
      valStart: pickStr(o, ['validation_start', 'val_start']),
      valEnd: pickStr(o, ['validation_end', 'val_end']),
      testStart: pickStr(o, ['test_start', 'oos_start', 'start']),
      testEnd: pickStr(o, ['test_end', 'oos_end', 'end']),
      ir: pickNum(o, ['oos_ir', 'oos_sharpe', 'ir', 'sharpe']) ?? null,
      isSharpe: pickNum(o, ['is_sharpe', 'train_sharpe'])
    };
  });
}

const ms = (d: string) => Date.parse(`${d}T00:00:00Z`);

/** Walk-forward fold timeline (train / validation / purge+embargo / OOS) plus OOS IR bar column. */
export function WalkForward({ height, folds }: { height: number; folds: WfFold[] }) {
  return <ChartBox height={height}>{(W, H) => <WfInner W={W} H={H} folds={folds} />}</ChartBox>;
}

function WfInner({ W, H, folds }: { W: number; H: number; folds: WfFold[] }) {
  const n = folds.length;
  if (!n) return null;
  const dated = folds.some((f) => f.testStart && f.testEnd);
  const P = { l: 34, r: 10, t: 18, b: 6 };
  const colW = dated ? 170 : W - P.l - P.r;
  const tw = W - P.l - P.r - colW - 18;
  const rh = (H - P.t - P.b) / n;

  const ds = folds.flatMap((f) => [f.trainStart, f.valStart, f.testStart, f.testEnd].filter((d): d is string => !!d)).map(ms);
  const y0 = new Date(Math.min(...ds)).getUTCFullYear();
  const y1 = new Date(Math.max(...ds)).getUTCFullYear() + 1;
  const t0 = Date.UTC(y0, 0, 1);
  const t1 = Date.UTC(y1, 0, 1);
  const X = (d: string) => P.l + ((ms(d) - t0) / (t1 - t0)) * tw;
  const yStep = Math.max(1, Math.ceil((y1 - y0) / Math.max(2, Math.floor(tw / 56))));

  const irs = folds.map((f) => f.ir).filter(isNum);
  const lo = Math.min(-1, ...irs);
  const hi = Math.max(3, ...irs);
  const x0 = W - P.r - colW;
  const XI = (v: number) => x0 + 26 + ((v - lo) / (hi - lo)) * (colW - 60);

  return (
    <>
      {dated
        ? Array.from({ length: y1 - y0 + 1 }, (_, k) => y0 + k)
            .filter((y) => (y - y0) % yStep === 0)
            .map((y) => {
              const x = X(`${y}-01-01`);
              return (
                <Fragment key={`y${y}`}>
                  <line x1={x} x2={x} y1={P.t - 4} y2={H - P.b} className="sv-grid" />
                  <text x={x} y={P.t - 7} className="sv-ax" textAnchor="middle">
                    {y}
                  </text>
                </Fragment>
              );
            })
        : null}
      <text x={x0 + 26} y={P.t - 7} className="sv-ax">
        OOS IR
      </text>
      <line x1={XI(0)} x2={XI(0)} y1={P.t - 2} y2={H - P.b} className="sv-base" />
      <line x1={XI(1)} x2={XI(1)} y1={P.t - 2} y2={H - P.b} style={{ stroke: 'var(--tx3)', strokeDasharray: '2 3' }} />
      <text x={XI(1) + 3} y={P.t - 7} className="sv-ax">
        1.0
      </text>
      {folds.map((f, k) => {
        const y = P.t + k * rh + rh * 0.22;
        const h = rh * 0.56;
        const ty = y + h / 2 + 3.5;
        const seg = (a: string | undefined, b: string | undefined, fill: string, tip: string, min = 1) =>
          a && b ? <rect x={X(a)} y={y} width={Math.max(min, X(b) - X(a))} height={h} style={{ fill }} data-tip={esc(tip)} /> : null;
        const gapStart = f.valEnd ?? f.trainEnd;
        const v = f.ir;
        const xa = isNum(v) ? XI(Math.min(0, v)) : 0;
        const xb = isNum(v) ? XI(Math.max(0, v)) : 0;
        return (
          <Fragment key={`f${k}`}>
            <text x={4} y={ty} className="sv-ax">
              {f.label}
            </text>
            {dated ? (
              <>
                {seg(f.trainStart, f.trainEnd, 'var(--line2)', `${f.label} 训练 ${f.trainStart} → ${f.trainEnd}${isNum(f.isSharpe) ? ` · IS SR ${fx(f.isSharpe)}` : ''}`)}
                {f.valStart ? seg(f.trainEnd, f.valStart, 'var(--warn)', `${f.label} Purge + Embargo ${f.trainEnd} → ${f.valStart}`, 2) : null}
                {seg(f.valStart, f.valEnd, 'var(--tx3)', `${f.label} 验证 ${f.valStart} → ${f.valEnd}`)}
                {seg(gapStart, f.testStart, 'var(--warn)', `${f.label} Purge + Embargo ${gapStart} → ${f.testStart}`, 2)}
                {seg(f.testStart, f.testEnd, 'var(--sig)', `${f.label} OOS 测试 ${f.testStart} → ${f.testEnd} · IR ${fx(v)}`)}
              </>
            ) : null}
            {isNum(v) ? (
              <>
                <rect
                  x={xa}
                  y={y}
                  width={Math.max(1, xb - xa)}
                  height={h}
                  style={{ fill: v < 0 ? 'var(--dn)' : v < 1 ? 'var(--warn)' : 'var(--sig)' }}
                  data-tip={esc(`${f.label} OOS IR ${fx(v)}`)}
                />
                <text x={xb + 4} y={ty} className="sv-axl">
                  {fx(v)}
                </text>
              </>
            ) : (
              <text x={XI(0) + 4} y={ty} className="sv-ax">
                —
              </text>
            )}
          </Fragment>
        );
      })}
    </>
  );
}
