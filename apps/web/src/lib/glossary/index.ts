'use client';

/**
 * Glossary registry: lazily-loaded builtin terms (code-split chunk) merged with the
 * backend `/api/v1/glossary` catalogue. Backend terms never overwrite builtin prose; they
 * fill missing fields (redline / pitfalls / bot prompt) or add unknown terms (source 'api').
 */
import { useEffect, useSyncExternalStore } from 'react';
import { researchApi } from '../research-api';
import type { ApiGlossaryTerm } from '../types';
import type { GlossaryTerm, TermDomain } from './types';

export type ApiMergeState = 'idle' | 'loading' | 'merged' | 'unavailable';

interface Registry {
  terms: GlossaryTerm[];
  byId: Map<string, GlossaryTerm>;
  api: ApiMergeState;
  apiCount: number;
}

const EMPTY: Registry = { terms: [], byId: new Map(), api: 'idle', apiCount: 0 };
let reg: Registry = EMPTY;
let builtinPromise: Promise<void> | null = null;
let apiPromise: Promise<void> | null = null;
const listeners = new Set<() => void>();

function publish(terms: GlossaryTerm[], api: ApiMergeState, apiCount: number) {
  const byId = new Map<string, GlossaryTerm>();
  for (const t of terms) byId.set(t.id, t);
  reg = { terms, byId, api, apiCount };
  listeners.forEach((l) => l());
}

export function loadBuiltinTerms(): Promise<void> {
  if (!builtinPromise) {
    builtinPromise = import('./terms').then((m) => {
      if (!reg.terms.length) publish(m.BUILTIN_TERMS, reg.api, reg.apiCount);
    });
  }
  return builtinPromise;
}

const DOMAINS = new Set<TermDomain>(['Q1', 'Q2', 'Q3', 'Q4', 'Q5', 'Q6', 'Q7', 'MATH']);
const key = (s: string) => s.toLowerCase().replace(/[\s\-_·]+/g, '');

export function mergeApiTerms(base: GlossaryTerm[], api: ApiGlossaryTerm[]): GlossaryTerm[] {
  const out = base.map((t) => ({ ...t }));
  const index = new Map<string, number>();
  out.forEach((t, i) => [t.id, t.zh, t.en, ...t.aliases].forEach((s) => s && index.set(key(s), i)));
  for (const a of api) {
    if (!a?.term_id) continue;
    const hit = [a.term_id, a.term, a.en_name, ...(a.aliases ?? [])].map((s) => (s ? index.get(key(s)) : undefined)).find((i) => i !== undefined);
    if (hit !== undefined) {
      const t = out[hit];
      const extra = (a.common_pitfalls ?? []).filter((p) => p && !t.pitfalls.includes(p));
      out[hit] = {
        ...t,
        aliases: [...new Set([...t.aliases, ...(a.aliases ?? []), a.term_id !== t.id ? a.term_id : ''].filter(Boolean))],
        redline: t.redline ?? (a.institutional_threshold || undefined),
        pitfalls: [...t.pitfalls, ...extra].slice(0, Math.max(t.pitfalls.length, 4)),
        formula: t.formula.length ? t.formula : a.formula_lines?.length ? a.formula_lines : a.formula_latex ? [a.formula_latex] : [],
        botPrompt: t.botPrompt ?? (a.bot_prompt_template || undefined),
        source: 'merged'
      };
      continue;
    }
    const term: GlossaryTerm = {
      id: a.term_id,
      zh: a.term || a.term_id,
      en: a.en_name || a.term_id,
      domain: DOMAINS.has(a.domain as TermDomain) ? (a.domain as TermDomain) : 'MATH',
      aliases: a.aliases ?? [],
      plain: a.short_tip || '后端术语库未提供解释。',
      formula: a.formula_lines?.length ? a.formula_lines : a.formula_latex ? [a.formula_latex] : [],
      redline: a.institutional_threshold || undefined,
      pitfalls: a.common_pitfalls ?? [],
      related: a.related_terms ?? [],
      botPrompt: a.bot_prompt_template || undefined,
      source: 'api'
    };
    out.push(term);
    [term.id, term.zh, term.en, ...term.aliases].forEach((s) => s && index.set(key(s), out.length - 1));
  }
  const ids = new Set(out.map((t) => t.id));
  return out.map((t) => ({ ...t, related: t.related.filter((r) => ids.has(r) && r !== t.id) }));
}

/** One-shot backend merge; failure (offline / 404) just marks the registry as builtin-only. */
export function mergeBackendGlossary(): Promise<void> {
  if (!apiPromise) {
    publish(reg.terms, 'loading', 0);
    apiPromise = loadBuiltinTerms()
      .then(() => researchApi.glossary())
      .then((res) => {
        const list = Array.isArray(res?.terms) ? res.terms : [];
        publish(mergeApiTerms(reg.terms, list), 'merged', list.length);
      })
      .catch(() => {
        publish(reg.terms, 'unavailable', 0);
        apiPromise = null;
      });
  }
  return apiPromise;
}

const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};
const snap = () => reg;
const serverSnap = () => EMPTY;

/** Registry hook. `withApi` triggers the backend merge (drawer / card), plain triggers only builtin. */
export function useGlossary(withApi = false): Registry {
  const r = useSyncExternalStore(subscribe, snap, serverSnap);
  useEffect(() => {
    void loadBuiltinTerms();
    if (withApi && reg.api === 'idle') void mergeBackendGlossary();
  }, [withApi]);
  return r;
}

export const getTerm = (id: string) => reg.byId.get(id);
export const getTerms = () => reg.terms;
