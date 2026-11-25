import type { GlossaryTerm } from '../types';
import { Q1_TERMS } from './q1';
import { Q2_TERMS } from './q2';
import { Q3_TERMS } from './q3';
import { Q4_TERMS } from './q4';
import { Q5_TERMS } from './q5';
import { Q6_TERMS } from './q6';
import { Q7_TERMS } from './q7';
import { MATH_TERMS } from './math';

export const BUILTIN_TERMS: GlossaryTerm[] = [
  ...Q1_TERMS,
  ...Q2_TERMS,
  ...Q3_TERMS,
  ...Q4_TERMS,
  ...Q5_TERMS,
  ...Q6_TERMS,
  ...Q7_TERMS,
  ...MATH_TERMS
];
