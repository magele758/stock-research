'use client';

import { fx } from '@/lib/format';
import { isStudyRunning } from '@/lib/research-api';
import type { ResearchStudySummary } from '@/lib/types';
import { Tag, type Tone } from '../ui';

export function studyTone(status?: string, verdict?: string): Tone {
  const v = String(verdict ?? '').toUpperCase();
  const s = String(status ?? '').toUpperCase();
  if (v === 'VERIFIED') return 'pass';
  if (v === 'FALSIFIED' || s === 'FALSIFIED' || s === 'FAILED' || v === 'ERROR') return 'fail';
  if (v === 'INCONCLUSIVE') return 'warn';
  if (isStudyRunning(s)) return 'info';
  if (s === 'COMPLETED') return 'pass';
  return 'mute';
}

export function studyLabel(status?: string, verdict?: string): string {
  const v = String(verdict ?? '').toUpperCase();
  if (v && v !== 'PENDING') return v;
  return String(status ?? 'DRAFT').toUpperCase();
}

export function StudyBadge({ s }: { s: Pick<ResearchStudySummary, 'status' | 'verdict'> }) {
  const running = isStudyRunning(s.status);
  return (
    <Tag tone={studyTone(s.status, s.verdict)}>
      {running ? <i className="spin" style={{ width: 8, height: 8 }} /> : null}
      {studyLabel(s.status, s.verdict)}
    </Tag>
  );
}

export const metricLine = (s: Pick<ResearchStudySummary, 'oos_sharpe' | 'dsr' | 'pbo' | 'proofs_passed' | 'proofs_total'>) =>
  [
    s.oos_sharpe !== null && s.oos_sharpe !== undefined ? `SR ${fx(s.oos_sharpe, 2)}` : null,
    s.dsr !== null && s.dsr !== undefined ? `DSR ${fx(s.dsr, 2)}` : null,
    s.pbo !== null && s.pbo !== undefined ? `PBO ${fx(s.pbo, 2)}` : null,
    s.proofs_total ? `证明 ${s.proofs_passed}/${s.proofs_total}` : null
  ]
    .filter(Boolean)
    .join(' · ');

/** Token-set Jaccard over CJK bigrams + latin words (tombstone duplicate warning). */
export function tokens(s: string): Set<string> {
  const out = new Set<string>();
  const low = s.toLowerCase();
  for (const w of low.match(/[a-z_][a-z0-9_]{1,}/g) ?? []) out.add(w);
  const cjk = low.replace(/[^\u4e00-\u9fa5]/g, '');
  for (let i = 0; i < cjk.length - 1; i++) out.add(cjk.slice(i, i + 2));
  return out;
}

export function jaccard(a: Set<string>, b: Set<string>): number {
  if (!a.size || !b.size) return 0;
  let n = 0;
  for (const x of a) if (b.has(x)) n++;
  return n / (a.size + b.size - n);
}
