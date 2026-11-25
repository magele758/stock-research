import { gateText, judge, type GateSpec, type Judgement } from '../thresholds';
import type { GlossaryTerm, TermValue } from './types';

export interface Evaluation extends Judgement {
  gate?: GateSpec;
  thresholdText: string;
}

/** Gate priority: trigger override (already merged with backend gate_checks) > contract gate on the term. */
export function evaluate(term: GlossaryTerm | undefined, v: TermValue | undefined): Evaluation {
  const gate = v?.gate ?? term?.gate;
  const j = judge(gate, v?.value);
  return { ...j, gate, thresholdText: gate ? gateText(gate) : (term?.redline ?? '—') };
}

/** Position (0–1) of `x` on the gate axis, for the card's position bar. */
export function axisPos(gate: GateSpec, x: number): number {
  const [lo, hi] = gate.range ?? [Math.min(0, gate.value * 0.5), gate.value * 2 || 1];
  const v = gate.op === 'abs<=' ? Math.abs(x) : x;
  if (!(hi > lo)) return 0.5;
  return Math.max(0, Math.min(1, (v - lo) / (hi - lo)));
}
