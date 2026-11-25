import type { GlossaryTerm } from './types';

const norm = (s: string) => s.toLowerCase().replace(/[\s\-_·/()（）]+/g, '');

/** Initials of the latin words: "Newey–West HAC" → "nwh", "Deflated Sharpe Ratio" → "dsr". */
function initials(s: string): string {
  return (s.match(/[A-Za-z][a-z]*|[A-Z]+(?![a-z])/g) ?? []).map((w) => w[0].toLowerCase()).join('');
}

/**
 * Case-insensitive substring + acronym scoring over id / zh / en / aliases.
 * Terms mounted on the current page get a boost so "本页" terms surface first.
 */
export function searchTerms(terms: GlossaryTerm[], query: string, onPage: Set<string> = new Set()): GlossaryTerm[] {
  const q = norm(query.trim());
  if (!q) return terms.slice().sort((a, b) => Number(onPage.has(b.id)) - Number(onPage.has(a.id)));
  const scored: [GlossaryTerm, number][] = [];
  for (const t of terms) {
    let s = 0;
    const fields = [t.id, t.zh, t.en, ...t.aliases];
    for (const f of fields) {
      const n = norm(f);
      if (!n) continue;
      if (n === q) s = Math.max(s, 100);
      else if (n.startsWith(q)) s = Math.max(s, 70);
      else if (n.includes(q)) s = Math.max(s, 45);
    }
    if (s < 60) {
      const ac = [t.en, ...t.aliases].map(initials);
      if (ac.some((a) => a === q)) s = Math.max(s, 65);
      else if (ac.some((a) => a.startsWith(q) && q.length >= 2)) s = Math.max(s, 40);
    }
    if (s < 30 && q.length >= 2 && norm(t.plain).includes(q)) s = 20;
    if (s > 0) scored.push([t, s + (onPage.has(t.id) ? 12 : 0)]);
  }
  return scored.sort((a, b) => b[1] - a[1]).map(([t]) => t);
}

/** Best single match for a free-text `/查术语 xxx` query. */
export function bestTerm(terms: GlossaryTerm[], query: string): GlossaryTerm | undefined {
  return searchTerms(terms, query)[0];
}
