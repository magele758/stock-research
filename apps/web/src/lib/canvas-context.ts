import { PIPELINE, VIEWS, type ViewKey } from './meta';
import type { OptConstraints, PreprocessConfig } from './types';
import type { ViewMetrics } from './workbench';
import { CANVAS_CLOSE, CANVAS_OPEN } from './agent-home-client';

const MAX_CHARS = 800;

export interface CanvasInput {
  view: ViewKey;
  universe: string;
  snapshotId: string | null;
  latestSnapshot: string | null;
  factorId: string;
  preprocess: PreprocessConfig;
  experimentId: string;
  engine: string;
  constraints: OptConstraints;
  metrics?: ViewMetrics;
}

const r4 = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? +v.toFixed(4) : v);

/**
 * Whitelisted, numeric-only canvas snapshot (≤ 800 chars) that is prepended to the
 * newest `user` message. Never put this into system/instructions: the stable prefix
 * (STABLE_SYSTEM_VERSION v19) must stay byte-identical for KV-cache hits.
 */
export function buildCanvasContext(c: CanvasInput): Record<string, unknown> {
  const ctx: Record<string, unknown> = {
    active_view: VIEWS.find((v) => v.key === c.view)?.path ?? `/${c.view}`,
    universe: c.universe,
    snapshot_id: c.snapshotId ?? c.latestSnapshot ?? null,
    pit_lock: c.snapshotId ? 'locked' : 'latest'
  };
  if (c.view === 'alpha' || c.view === 'shadow') {
    ctx.factor_id = c.factorId;
    ctx.preprocess = PIPELINE.filter((p) => c.preprocess[p.key]).map((p) => p.short).join('+') || 'none';
  }
  if (c.view === 'models' || c.view === 'portfolio' || c.view === 'backtest') ctx.experiment_id = c.experimentId;
  if (c.view === 'backtest') ctx.engine = c.engine;
  if (c.view === 'portfolio' || c.view === 'backtest') {
    ctx.constraints = {
      objective: c.constraints.objective,
      w_max: c.constraints.max_stock_weight,
      ind_dev: c.constraints.max_industry_dev,
      lambda: c.constraints.turnover_lambda,
      te: c.constraints.target_te,
      size_z: c.constraints.max_size_z
    };
  }
  if (c.metrics && Object.keys(c.metrics).length) {
    ctx.tearsheet_metrics = Object.fromEntries(Object.entries(c.metrics).map(([k, v]) => [k, r4(v)]));
  }
  // Enforce the 800-char budget by trimming metric keys from the tail.
  let s = JSON.stringify(ctx);
  while (s.length > MAX_CHARS && ctx.tearsheet_metrics && Object.keys(ctx.tearsheet_metrics as object).length) {
    const m = { ...(ctx.tearsheet_metrics as Record<string, unknown>) };
    delete m[Object.keys(m).pop() as string];
    ctx.tearsheet_metrics = m;
    s = JSON.stringify(ctx);
  }
  return ctx;
}

export function serializeCanvas(ctx: Record<string, unknown>): string {
  return `${CANVAS_OPEN}\n${JSON.stringify(ctx, null, 1)}\n${CANVAS_CLOSE}`;
}

export function withCanvasPrefix(ctx: Record<string, unknown>, userText: string): string {
  return `${serializeCanvas(ctx)}\n\n${userText}`;
}

export function canvasChips(ctx: Record<string, unknown>): string[] {
  const chips: string[] = [`view:${ctx.active_view}`, `universe:${ctx.universe}`];
  if (ctx.factor_id) chips.push(`factor:${ctx.factor_id}`);
  if (ctx.preprocess) chips.push(`pipeline:${ctx.preprocess}`);
  if (ctx.experiment_id) chips.push(`exp:${ctx.experiment_id}`);
  if (ctx.engine) chips.push(`engine:${ctx.engine}`);
  const cons = ctx.constraints as Record<string, number> | undefined;
  if (cons) chips.push(`cons:w≤${(cons.w_max * 100).toFixed(1)}%,TE≤${(cons.te * 100).toFixed(1)}%,size±${cons.size_z.toFixed(2)}σ,λ${cons.lambda.toFixed(2)}`);
  chips.push(`pit:${ctx.snapshot_id ?? 'n/a'}${ctx.pit_lock === 'locked' ? '·lock' : ''}`);
  const m = ctx.tearsheet_metrics as Record<string, unknown> | undefined;
  if (m) {
    Object.entries(m)
      .slice(0, 3)
      .forEach(([k, v]) => chips.push(`${k}:${typeof v === 'number' ? +v.toFixed(3) : String(v)}`));
  }
  return chips;
}
