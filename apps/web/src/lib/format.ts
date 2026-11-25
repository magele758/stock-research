export const clamp = (v: number, a: number, b: number) => Math.max(a, Math.min(b, v));

export const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/** Fixed decimals; collapses -0.00 to 0.00. Non-finite → em dash. */
export function fx(v: number | null | undefined, d = 2): string {
  if (!isNum(v)) return '—';
  return (Math.abs(v) < 0.5 * Math.pow(10, -d) ? 0 : v).toFixed(d);
}

/** Signed fixed decimals (+1.23 / -0.40). */
export function sg(v: number | null | undefined, d = 2): string {
  if (!isNum(v)) return '—';
  const s = fx(v, d);
  return (+s > 0 ? '+' : '') + s;
}

/**
 * Ratio fields whose unit is not pinned by the schema (e.g. `long_short_ann`,
 * `daily_turnover_oneway`, `cpcv`-style rates). Values with |v| ≤ 1.5 are treated
 * as fractions and scaled to percent; larger values are assumed to already be percent.
 */
export function ratioPct(v: number | null | undefined): number | null {
  if (!isNum(v)) return null;
  return Math.abs(v) <= 1.5 ? v * 100 : v;
}

export const pct = (v: number | null | undefined, d = 1) => (isNum(v) ? `${fx(v, d)}%` : '—');
export const spct = (v: number | null | undefined, d = 1) => (isNum(v) ? `${sg(v, d)}%` : '—');

export function thou(v: number | null | undefined): string {
  if (!isNum(v)) return '—';
  return Math.round(v).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

export function fmtMs(ms: number | null | undefined): string {
  if (!isNum(ms)) return '—';
  return ms < 1000 ? `${Math.round(ms)}ms` : ms < 60_000 ? `${(ms / 1000).toFixed(1)}s` : `${(ms / 60_000).toFixed(1)}m`;
}

export function fmtMb(mb: number | null | undefined): string {
  if (!isNum(mb)) return '—';
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : mb >= 10 ? `${mb.toFixed(0)} MB` : `${mb.toFixed(1)} MB`;
}

export function fmtAge(sec: number | null | undefined): string {
  if (!isNum(sec)) return '—';
  if (sec < 90) return `${Math.round(sec)}s`;
  if (sec < 5400) return `${Math.round(sec / 60)}m`;
  if (sec < 172_800) return `${(sec / 3600).toFixed(1)}h`;
  return `${(sec / 86_400).toFixed(1)}d`;
}

/** ISO datetime → `YYYY-MM-DD HH:mm` (local). Plain dates pass through. */
export function fmtTs(iso: string | null | undefined): string {
  if (!iso) return '—';
  if (/^\d{4}-\d{2}-\d{2}$/.test(iso)) return iso;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function nowHms(): string {
  const d = new Date();
  return [d.getHours(), d.getMinutes(), d.getSeconds()].map((x) => String(x).padStart(2, '0')).join(':');
}

export const shortHash = (h: string | null | undefined, n = 4) => (h ? (h.length > n * 2 + 1 ? `${h.slice(0, n)}…${h.slice(-n)}` : h) : '—');

export const esc = (s: unknown) =>
  String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

export const mean = (a: number[]) => (a.length ? a.reduce((s, x) => s + x, 0) / a.length : NaN);

export function median(a: number[]): number {
  if (!a.length) return NaN;
  const s = a.slice().sort((x, y) => x - y);
  const k = s.length >> 1;
  return s.length % 2 ? s[k] : (s[k - 1] + s[k]) / 2;
}

export const sum = (a: number[]) => a.reduce((s, x) => s + x, 0);

export function mixHex(a: string, b: string, t: number): string {
  const p = (h: string) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const A = p(a);
  const B = p(b);
  return '#' + A.map((x, i) => Math.round(x + (B[i] - x) * t).toString(16).padStart(2, '0')).join('');
}

/** Ten-step decile palette Q1 (teal) → Q10 (red), A-share up/down convention. */
export const DECILE_COLORS = Array.from({ length: 10 }, (_, j) =>
  j < 5 ? mixHex('#26a69a', '#7c8794', j / 4.5) : mixHex('#7c8794', '#ef5350', (j - 4.5) / 4.5)
);

export function downloadBlob(filename: string, content: string, mime: string) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
