'use client';

import type { ProofCheckItem, ResearchStudyDetail } from '@/lib/types';
import { FormulaBlock } from '../../glossary/FormulaBlock';
import { TermTip } from '../../glossary/TermTip';
import { EmptyState, Panel, Tag, type Tone } from '../../ui';

const SIGN: Record<string, string> = { positive: '预期正向', negative: '预期负向', two_sided: '双侧' };

function assumptionText(a: Record<string, unknown>): string {
  return String(a.statement ?? a.text ?? a.description ?? a.name ?? a.id ?? JSON.stringify(a));
}

/** Link an assumption to the proof ledger when it names a check / term id. */
function assumptionTone(a: Record<string, unknown>, checks: ProofCheckItem[]): { tone: Tone; label: string } {
  const st = String(a.status ?? '').toLowerCase();
  if (st === 'pass' || st === 'verified') return { tone: 'pass', label: 'PASS' };
  if (st === 'fail' || st === 'falsified') return { tone: 'fail', label: 'FAIL' };
  const ref = String(a.check_id ?? a.term_id ?? a.verified_by ?? '');
  const c = ref ? checks.find((x) => x.check_id === ref || x.term_id === ref) : undefined;
  if (c) return { tone: c.status === 'pass' ? 'pass' : c.status === 'fail' ? 'fail' : c.status === 'warn' ? 'warn' : 'mute', label: c.status.toUpperCase() };
  return { tone: 'mute', label: 'PENDING' };
}

/** ② Economic hypothesis, numbered derivation, assumptions (linked to proofs), boundaries, failure regimes. */
export function MathCard({ detail, onRegime }: { detail: ResearchStudyDetail; onRegime?: (r: string) => void }) {
  const m = detail.math_derivation;
  const checks = detail.formal_proof?.checks ?? [];
  if (!m) {
    return (
      <Panel title="② 数学推演与前提假设" flush>
        <div className="pb">
          <div className="mathc">
            <div className="mh">{detail.hypothesis}</div>
          </div>
          <EmptyState height={70} title="MATH DERIVATION · 未提供" detail="MATH 阶段尚未完成，或派发时未附 math_derivation" />
        </div>
      </Panel>
    );
  }
  const lines = [m.formula_latex, ...m.derivation_steps].filter(Boolean).map((l, i) => `(${i + 1})\\quad ${l}`);
  return (
    <Panel title="② 数学推演与前提假设" sub={SIGN[m.expected_sign] ?? m.expected_sign}>
      <div className="mathc" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <div>
          <div className="tc-l">经济假设</div>
          <div className="mh">{m.economic_hypothesis || detail.hypothesis}</div>
        </div>
        {m.mechanism ? (
          <div>
            <div className="tc-l">传导机制</div>
            <div className="a2b">{m.mechanism}</div>
          </div>
        ) : null}
        {lines.length ? (
          <div>
            <div className="tc-l">公式推演</div>
            <FormulaBlock lines={lines} />
          </div>
        ) : null}
        {m.assumptions.length ? (
          <div>
            <div className="tc-l">前提假设</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              {m.assumptions.map((a, i) => {
                const t = assumptionTone(a, checks);
                return (
                  <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'baseline', fontSize: 11 }}>
                    <b className="mono c-3" style={{ fontSize: 10, flex: 'none' }}>
                      A{i + 1}
                    </b>
                    <span style={{ flex: 1, minWidth: 0 }}>{assumptionText(a)}</span>
                    <Tag tone={t.tone}>{t.label}</Tag>
                  </div>
                );
              })}
            </div>
          </div>
        ) : null}
        {m.boundary_conditions.length ? (
          <div>
            <div className="tc-l">边界条件</div>
            <ul className="tc-pit">
              {m.boundary_conditions.map((b, i) => (
                <li key={i}>{b}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {m.expected_failure_regimes.length ? (
          <div>
            <div className="tc-l">
              <TermTip id="regime_classification">理论失效 Regime</TermTip>
            </div>
            <div className="chips">
              {m.expected_failure_regimes.map((r) => (
                <button key={r} type="button" className="tag warn" onClick={() => onRegime?.(r)} style={{ cursor: onRegime ? 'pointer' : 'default' }}>
                  {r}
                </button>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </Panel>
  );
}
