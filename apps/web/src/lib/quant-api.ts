/**
 * QuantLab Pro · quant-api REST client (`services/quant-api`, FastAPI :8100, prefix /api/v1).
 *
 * Every function performs a real HTTP request. There is no mock fallback: when the
 * backend is down the caller receives a `QuantApiError` with `kind: 'offline'` and
 * the UI must surface `API OFFLINE` instead of rendering numbers.
 */
import type {
  ApiHealth,
  BacktestRequest,
  BacktestResult,
  BarraExposureRequest,
  BarraExposureResult,
  CpcvRequest,
  CpcvResult,
  DataHealthReport,
  DataSyncRequest,
  DatasetSnapshot,
  ExperimentRecord,
  FactorCatalogItem,
  FactorEvalRequest,
  FactorEvalResult,
  McpCatalog,
  McpToolStatus,
  PortfolioOptRequest,
  PortfolioOptResult,
  ShadowDriftResult
} from './types';

const rawApiUrl = process.env.NEXT_PUBLIC_QUANT_API_URL?.trim();
export const QUANT_API_URL =
  rawApiUrl === 'same-origin'
    ? ''
    : (rawApiUrl || 'http://127.0.0.1:8100').replace(/\/$/, '');
export const API_PREFIX = '/api/v1';

export const START_COMMANDS = {
  api: 'make dev-api',
  seed: 'make seed',
  setup: 'make setup',
  mcp: 'make dev-mcp'
} as const;

export type QuantApiErrorKind = 'offline' | 'timeout' | 'http' | 'parse' | 'aborted';

export class QuantApiError extends Error {
  readonly kind: QuantApiErrorKind;
  readonly status?: number;
  readonly detail?: unknown;
  readonly path: string;

  constructor(kind: QuantApiErrorKind, path: string, message: string, status?: number, detail?: unknown) {
    super(message);
    this.name = 'QuantApiError';
    this.kind = kind;
    this.path = path;
    this.status = status;
    this.detail = detail;
  }

  /** 503/404/409 on data endpoints usually means the lake has no snapshot yet. */
  get needsSeed(): boolean {
    if (this.kind !== 'http') return false;
    const text = `${this.message} ${JSON.stringify(this.detail ?? '')}`.toLowerCase();
    return this.status === 503 || /snapshot|seed|no data|empty lake|not seeded|not found/.test(text);
  }
}

export function isAbort(err: unknown): boolean {
  return err instanceof QuantApiError ? err.kind === 'aborted' : err instanceof DOMException && err.name === 'AbortError';
}

interface RequestOpts {
  method?: 'GET' | 'POST';
  body?: unknown;
  query?: Record<string, string | number | boolean | null | undefined>;
  timeoutMs?: number;
  signal?: AbortSignal;
}

function detailMessage(detail: unknown): string {
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => {
        if (d && typeof d === 'object') {
          const o = d as { loc?: unknown[]; msg?: string };
          return `${Array.isArray(o.loc) ? o.loc.join('.') : ''} ${o.msg ?? ''}`.trim();
        }
        return String(d);
      })
      .join('; ');
  }
  if (detail && typeof detail === 'object') {
    const o = detail as { message?: string; error?: string; detail?: unknown };
    return o.message ?? o.error ?? (o.detail ? detailMessage(o.detail) : JSON.stringify(detail));
  }
  return '';
}

/** Absolute URL of an `/api/v1` path (same resolution as `quantRequest`; used by EventSource / raw text fetches). */
export function apiUrl(path: string, query?: RequestOpts['query']): string {
  const base = QUANT_API_URL || (typeof window !== 'undefined' ? window.location.origin : 'http://127.0.0.1:8100');
  const url = new URL(`${base}${API_PREFIX}${path}`);
  for (const [k, v] of Object.entries(query ?? {})) {
    if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v));
  }
  return url.toString();
}

export async function quantRequest<T>(path: string, opts: RequestOpts = {}): Promise<T> {
  const url = apiUrl(path, opts.query);
  const ctrl = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    ctrl.abort();
  }, opts.timeoutMs ?? 15_000);
  const onAbort = () => ctrl.abort();
  opts.signal?.addEventListener('abort', onAbort, { once: true });
  if (opts.signal?.aborted) ctrl.abort();

  let res: Response;
  try {
    res = await fetch(url, {
      method: opts.method ?? (opts.body === undefined ? 'GET' : 'POST'),
      headers: opts.body === undefined ? { accept: 'application/json' } : { accept: 'application/json', 'content-type': 'application/json' },
      body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
      signal: ctrl.signal,
      cache: 'no-store'
    });
  } catch (err) {
    if (timedOut) throw new QuantApiError('timeout', path, `quant-api 请求超时 (${Math.round((opts.timeoutMs ?? 15_000) / 1000)}s)`);
    if (opts.signal?.aborted) throw new QuantApiError('aborted', path, 'aborted');
    throw new QuantApiError('offline', path, `quant-api 不可达：${QUANT_API_URL} (${err instanceof Error ? err.message : String(err)})`);
  } finally {
    clearTimeout(timer);
    opts.signal?.removeEventListener('abort', onAbort);
  }

  const text = await res.text();
  let payload: unknown = undefined;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      if (res.ok) throw new QuantApiError('parse', path, `quant-api 返回非 JSON 响应 (${text.slice(0, 120)})`, res.status);
      payload = text;
    }
  }
  if (!res.ok) {
    const detail = payload && typeof payload === 'object' && 'detail' in (payload as object) ? (payload as { detail: unknown }).detail : payload;
    const msg = detailMessage(detail) || res.statusText || 'request failed';
    throw new QuantApiError('http', path, `HTTP ${res.status} · ${msg}`, res.status, detail);
  }
  return payload as T;
}

// ------------------------------------------------------------------ normalizers

function pickArray(raw: unknown, keys: string[]): unknown[] {
  if (Array.isArray(raw)) return raw;
  if (raw && typeof raw === 'object') {
    for (const k of keys) {
      const v = (raw as Record<string, unknown>)[k];
      if (Array.isArray(v)) return v;
    }
  }
  return [];
}

const str = (v: unknown): string | undefined => (typeof v === 'string' && v ? v : typeof v === 'number' ? String(v) : undefined);
const num = (v: unknown): number | undefined => (typeof v === 'number' && Number.isFinite(v) ? v : undefined);

export function normalizeFactorCatalog(raw: unknown): FactorCatalogItem[] {
  return pickArray(raw, ['factors', 'items', 'catalog', 'data'])
    .map((it): FactorCatalogItem | null => {
      if (typeof it === 'string') return { factor_id: it, name: it };
      if (!it || typeof it !== 'object') return null;
      const o = it as Record<string, unknown>;
      const id = str(o.factor_id) ?? str(o.id) ?? str(o.name);
      if (!id) return null;
      return {
        factor_id: id,
        name: str(o.display_name) ?? str(o.title) ?? str(o.label) ?? (str(o.name) !== id ? str(o.name) : undefined) ?? id,
        expression: str(o.expression) ?? str(o.code) ?? str(o.expr),
        lang: str(o.lang) ?? str(o.language) ?? str(o.dsl),
        category: str(o.category) ?? str(o.family),
        source: str(o.source) ?? str(o.origin),
        description: str(o.description) ?? str(o.hypothesis),
        builtin: typeof o.builtin === 'boolean' ? o.builtin : undefined
      };
    })
    .filter((x): x is FactorCatalogItem => x !== null);
}

export function normalizeMcpCatalog(raw: unknown): McpCatalog {
  const tools = pickArray(raw, ['tools', 'items', 'catalog'])
    .map((it): McpToolStatus | null => {
      if (typeof it === 'string') return { name: it };
      if (!it || typeof it !== 'object') return null;
      const o = it as Record<string, unknown>;
      const stats = (o.stats && typeof o.stats === 'object' ? o.stats : {}) as Record<string, unknown>;
      const name = str(o.name) ?? str(o.tool) ?? str(o.id);
      if (!name) return null;
      return {
        name,
        layer: str(o.layer) ?? str(o.domain) ?? str(o.group),
        side_effect: str(o.side_effect) ?? str(o.sideEffect) ?? str(o.side_effect_level) ?? str(o.access) ?? str(o.mode),
        approval_mode: str(o.approval_mode) ?? str(o.approvalMode) ?? str(o.approval),
        status: str(o.status) ?? str(o.state) ?? (typeof o.enabled === 'boolean' ? (o.enabled ? 'enabled' : 'disabled') : undefined),
        calls_24h: num(o.calls_24h) ?? num(o.calls) ?? num(o.call_count) ?? num(stats.calls_24h) ?? num(stats.calls),
        p50_ms: num(o.p50_ms) ?? num(o.latency_p50_ms) ?? num(stats.p50_ms),
        p95_ms: num(o.p95_ms) ?? num(o.latency_p95_ms) ?? num(stats.p95_ms),
        description: str(o.description) ?? str(o.summary)
      };
    })
    .filter((x): x is McpToolStatus => x !== null);
  const o = (raw && typeof raw === 'object' && !Array.isArray(raw) ? raw : {}) as Record<string, unknown>;
  const srv = (o.server && typeof o.server === 'object' ? o.server : {}) as Record<string, unknown>;
  return {
    server: str(o.server) ?? str(srv.name) ?? str(o.name),
    version: str(o.version) ?? str(srv.version),
    transport: str(o.transport) ?? str(srv.transport) ?? str(o.endpoint) ?? str(srv.url),
    tools,
    raw
  };
}

export interface GateCheck {
  name: string;
  value?: string;
  threshold?: string;
  passed: boolean | null;
}

const fmtVal = (v: unknown): string | undefined =>
  typeof v === 'number' ? (Number.isInteger(v) ? String(v) : Math.abs(v) >= 100 ? v.toFixed(1) : v.toFixed(Math.abs(v) < 0.1 ? 4 : 2)) : str(v);

/** gate_checks / gate_details are `list[dict[str, Any]]`; tolerate common key spellings. */
export function normalizeGateChecks(list: Record<string, unknown>[] | undefined): GateCheck[] {
  return (list ?? []).map((o, i) => {
    const status = str(o.status)?.toLowerCase();
    const passedRaw = o.passed ?? o.pass ?? o.ok;
    const passed = typeof passedRaw === 'boolean' ? passedRaw : status ? ['pass', 'ok', 'passed', 'true'].includes(status) : null;
    const op = str(o.op) ?? str(o.operator) ?? '';
    const thr = fmtVal(o.threshold ?? o.limit ?? o.rule ?? o.target);
    return {
      name: str(o.name) ?? str(o.check) ?? str(o.label) ?? str(o.metric) ?? str(o.id) ?? `check_${i + 1}`,
      value: fmtVal(o.value ?? o.actual ?? o.observed),
      threshold: thr ? `${op ? op + ' ' : ''}${thr}` : undefined,
      passed
    };
  });
}

// ------------------------------------------------------------------ endpoints

export const quantApi = {
  health: (signal?: AbortSignal) => quantRequest<ApiHealth>('/health', { signal, timeoutMs: 4_000 }),

  dataHealth: (snapshotId?: string | null, signal?: AbortSignal) =>
    quantRequest<DataHealthReport>('/data/health', { signal, query: { snapshot_id: snapshotId }, timeoutMs: 20_000 }),

  snapshots: (signal?: AbortSignal) => quantRequest<DatasetSnapshot[]>('/data/snapshots', { signal, timeoutMs: 8_000 }),

  dataSync: (req: DataSyncRequest, signal?: AbortSignal) =>
    quantRequest<unknown>('/data/sync', { method: 'POST', body: req, signal, timeoutMs: 120_000 }),

  factorCatalog: async (signal?: AbortSignal) =>
    normalizeFactorCatalog(await quantRequest<unknown>('/factors/catalog', { signal, timeoutMs: 8_000 })),

  evaluateFactor: (req: FactorEvalRequest, signal?: AbortSignal) =>
    quantRequest<FactorEvalResult>('/factors/evaluate', { method: 'POST', body: req, signal, timeoutMs: 90_000 }),

  experiments: (limit = 20, signal?: AbortSignal) =>
    quantRequest<ExperimentRecord[]>('/experiments', { signal, query: { limit }, timeoutMs: 8_000 }),

  cpcv: (req: CpcvRequest, signal?: AbortSignal) =>
    quantRequest<CpcvResult>('/validation/cpcv', { method: 'POST', body: req, signal, timeoutMs: 180_000 }),

  barraExposure: (req: BarraExposureRequest, signal?: AbortSignal) =>
    quantRequest<BarraExposureResult>('/risk/barra-exposure', { method: 'POST', body: req, signal, timeoutMs: 30_000 }),

  optimizePortfolio: (req: PortfolioOptRequest, signal?: AbortSignal) =>
    quantRequest<PortfolioOptResult>('/portfolio/optimize', { method: 'POST', body: req, signal, timeoutMs: 45_000 }),

  runBacktest: (req: BacktestRequest, signal?: AbortSignal) =>
    quantRequest<BacktestResult>('/backtest/run', { method: 'POST', body: req, signal, timeoutMs: 240_000 }),

  shadowDrift: (q: { universe_id: string; shadow_portfolio_id?: string; window_days?: number; snapshot_id?: string | null }, signal?: AbortSignal) =>
    quantRequest<ShadowDriftResult>('/shadow/drift', { signal, query: q, timeoutMs: 30_000 }),

  mcpCatalog: async (signal?: AbortSignal) => normalizeMcpCatalog(await quantRequest<unknown>('/mcp/catalog', { signal, timeoutMs: 8_000 }))
};

export type QuantApi = typeof quantApi;
