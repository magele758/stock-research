/**
 * V3 research ledger / cluster / glossary client (V3-INSTITUTIONAL-CONTRACT §2.3).
 * Same honesty rule as `quant-api.ts`: no fallback data. A 404 means the V3 backend
 * route is not deployed yet and the UI must say so (`isV3Missing`).
 */
import { apiUrl, QuantApiError, quantRequest } from './quant-api';
import type {
  CapacityFrontierResult,
  ClusterNodeStatus,
  DimensionBaselineRecord,
  FalsifiedTombstoneRecord,
  FormalProofResult,
  GlossaryListResponse,
  OptConstraints,
  RegimeAnalysisResult,
  ResearchDispatchRequest,
  ResearchDispatchResponse,
  ResearchStudyDetail,
  ResearchStudySummary,
  StudyKind
} from './types';

/** Pre-filled "new study" form (from diagnosis cards, /alpha, /shadow, Bot, tombstones …). */
export interface StudyDraft {
  title?: string;
  hypothesis?: string;
  kind?: StudyKind;
  parent_study_id?: string | null;
  family_id?: string | null;
  lockParent?: boolean;
  target_cluster?: string;
  universe_id?: string;
  factor_id?: string | null;
  expression?: string | null;
  dimensions?: string[];
  trial_count_m?: number;
  bootstrap_b?: number;
  topic_tags?: string[];
  constraints?: OptConstraints | null;
  /** Lessons from a tombstone / diagnosis that the new study must avoid. */
  avoid?: string[];
  /** Origin view or card, for the drawer header. */
  source?: string;
}

export const STUDY_DIMENSIONS: { key: string; label: string }[] = [
  { key: 'market_regime', label: 'Regime 牛熊震荡' },
  { key: 'volatility_regime', label: '波动率分位' },
  { key: 'market_cap_tier', label: '市值分层' },
  { key: 'sw_industry', label: '申万行业' },
  { key: 'barra_style', label: 'Barra 风格' }
];

export const DIM_LABEL: Record<string, string> = {
  market_regime: 'Regime',
  volatility_regime: '波动率',
  market_cap_tier: '市值',
  sw_industry: '行业',
  barra_style: '风格'
};

export const isV3Missing = (e: unknown) => e instanceof QuantApiError && e.kind === 'http' && (e.status === 404 || e.status === 405 || e.status === 501);

function pick<T>(raw: unknown, keys: string[]): T[] {
  if (Array.isArray(raw)) return raw as T[];
  if (raw && typeof raw === 'object') {
    for (const k of keys) {
      const v = (raw as Record<string, unknown>)[k];
      if (Array.isArray(v)) return v as T[];
    }
  }
  return [];
}

async function memoText(id: string, signal?: AbortSignal): Promise<string> {
  const path = `/research/studies/${encodeURIComponent(id)}/memo`;
  let res: Response;
  try {
    res = await fetch(apiUrl(path), { headers: { accept: 'text/markdown, application/json' }, cache: 'no-store', signal });
  } catch (err) {
    if (signal?.aborted) throw new QuantApiError('aborted', path, 'aborted');
    throw new QuantApiError('offline', path, `quant-api 不可达 (${err instanceof Error ? err.message : String(err)})`);
  }
  const text = await res.text();
  if (!res.ok) throw new QuantApiError('http', path, `HTTP ${res.status} · ${text.slice(0, 160) || res.statusText}`, res.status);
  if ((res.headers.get('content-type') ?? '').includes('json')) {
    try {
      const j = JSON.parse(text) as Record<string, unknown>;
      const md = j.markdown ?? j.memo ?? j.content ?? j.memo_markdown;
      if (typeof md === 'string') return md;
    } catch {
      /* fall through: treat as markdown */
    }
  }
  return text;
}

export const researchApi = {
  studies: async (q: { status?: string; family_id?: string; limit?: number } = {}, signal?: AbortSignal) =>
    pick<ResearchStudySummary>(await quantRequest<unknown>('/research/studies', { signal, query: q, timeoutMs: 10_000 }), ['studies', 'items', 'data']),

  study: (id: string, signal?: AbortSignal) => quantRequest<ResearchStudyDetail>(`/research/studies/${encodeURIComponent(id)}`, { signal, timeoutMs: 15_000 }),

  dispatch: (req: ResearchDispatchRequest, signal?: AbortSignal) =>
    quantRequest<ResearchDispatchResponse>('/research/dispatch', { method: 'POST', body: req, signal, timeoutMs: 60_000 }),

  eventsUrl: (id: string) => apiUrl(`/research/studies/${encodeURIComponent(id)}/events`),

  memo: memoText,

  baselines: async (snapshotId?: string | null, universeId?: string, signal?: AbortSignal) =>
    pick<DimensionBaselineRecord>(
      await quantRequest<unknown>('/research/baselines', { signal, query: { snapshot_id: snapshotId, universe_id: universeId }, timeoutMs: 15_000 }),
      ['baselines', 'items', 'data']
    ),

  tombstones: async (signal?: AbortSignal) =>
    pick<FalsifiedTombstoneRecord>(await quantRequest<unknown>('/research/tombstones', { signal, query: { limit: 200 }, timeoutMs: 10_000 }), ['items', 'tombstones', 'data']),

  clusters: async (signal?: AbortSignal) =>
    pick<ClusterNodeStatus>(await quantRequest<unknown>('/research/clusters', { signal, timeoutMs: 6_000 }), ['clusters', 'nodes', 'items', 'data']),

  glossary: (signal?: AbortSignal) => quantRequest<GlossaryListResponse>('/glossary', { signal, timeoutMs: 8_000 }),

  formalVerify: (req: Record<string, unknown>, signal?: AbortSignal) =>
    quantRequest<FormalProofResult>('/validation/formal-verify', { method: 'POST', body: req, signal, timeoutMs: 180_000 }),

  regimeAnalysis: (req: Record<string, unknown>, signal?: AbortSignal) =>
    quantRequest<RegimeAnalysisResult>('/factors/regime-analysis', { method: 'POST', body: req, signal, timeoutMs: 90_000 }),

  capacity: (req: Record<string, unknown>, signal?: AbortSignal) =>
    quantRequest<CapacityFrontierResult>('/portfolio/capacity', { method: 'POST', body: req, signal, timeoutMs: 60_000 })
};

/** Draft → wire request (`ResearchDispatchRequest`, extra="forbid": only contract keys). */
export function draftToRequest(d: StudyDraft & { title: string; hypothesis: string }, snapshotId: string | null, preprocess?: ResearchDispatchRequest['preprocess']): ResearchDispatchRequest {
  const target = d.target_cluster ?? 'engine_sandbox';
  const req: ResearchDispatchRequest = {
    title: d.title,
    hypothesis: d.avoid?.length ? `${d.hypothesis}\n\n规避条件：\n${d.avoid.map((a) => `- ${a}`).join('\n')}` : d.hypothesis,
    kind: d.kind ?? 'factor',
    parent_study_id: d.parent_study_id ?? null,
    family_id: d.family_id ?? null,
    topic_tags: d.topic_tags ?? [],
    dispatch_mode: target === 'engine_sandbox' ? 'engine_sandbox' : 'cluster_dev_worker',
    target_cluster: target,
    universe_id: d.universe_id ?? 'CSI500',
    snapshot_id: snapshotId,
    factor_id: d.factor_id ?? null,
    expression: d.expression?.trim() ? d.expression : null,
    dimensions: d.dimensions,
    trial_count_m: d.trial_count_m,
    bootstrap_b: d.bootstrap_b,
    created_by: 'web-terminal'
  };
  if (preprocess) req.preprocess = preprocess;
  if (d.constraints) req.constraints = d.constraints;
  return req;
}

const TERMINAL = new Set(['COMPLETED', 'FALSIFIED', 'FAILED']);
export const isStudyTerminal = (status: string | undefined) => TERMINAL.has(String(status ?? '').toUpperCase());
export const isStudyRunning = (status: string | undefined) => {
  const s = String(status ?? '').toUpperCase();
  return !!s && !TERMINAL.has(s) && s !== 'DRAFT';
};
