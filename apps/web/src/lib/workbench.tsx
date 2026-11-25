'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { usePathname } from 'next/navigation';
import { DEFAULT_CONSTRAINTS, DEFAULT_EXPERIMENT_ID, DEFAULT_PREPROCESS, viewFromPath, type ViewKey } from './meta';
import { quantApi, QuantApiError } from './quant-api';
import type { ApiHealth, BacktestEngineMode, FactorDecayItem, OptConstraints, PreprocessConfig, UniverseId } from './types';

export type Theme = 'dark' | 'light';
export type ConnState = 'checking' | 'online' | 'offline';

export interface QuantApiStatus {
  state: ConnState;
  health?: ApiHealth;
  error?: string;
  checkedAt?: number;
  rttMs?: number;
}

export interface AgentHomeStatus {
  state: ConnState;
  online: boolean;
  url?: string;
  port?: number;
  source?: string;
  version?: string;
  adapter?: string;
  triedUrl?: string;
  tried?: string[];
  rttMs?: number;
  authConfigured?: boolean;
  checkedAt?: number;
}

export type BotId = 'quant-lead' | 'alpha-miner' | 'risk-pm';
export type QuickCmd = 'ic' | 'cpcv' | 'barra';

export interface DockController {
  send: (text: string, opts?: { bot?: BotId }) => void;
  runCommand: (cmd: QuickCmd, opts?: { bot?: BotId }) => void;
  diagnoseFactor: (item: FactorDecayItem, context: { universe: string; shadowId: string; snapshotId: string }) => void;
}

export type ViewMetrics = Record<string, string | number | boolean | null>;

interface WorkbenchValue {
  view: ViewKey;
  universe: UniverseId;
  setUniverse: (u: UniverseId) => void;
  snapshotLock: { locked: boolean; snapshotId: string | null };
  setSnapshotLock: (s: { locked: boolean; snapshotId: string | null }) => void;
  /** Snapshot to send with requests: pinned id when locked, otherwise null (= latest). */
  snapshotId: string | null;
  theme: Theme;
  setTheme: (t: Theme) => void;
  prd: boolean;
  setPrd: (v: boolean) => void;
  botOpen: boolean;
  setBotOpen: (v: boolean) => void;

  factorId: string;
  setFactorId: (id: string) => void;
  preprocess: PreprocessConfig;
  setPreprocess: (p: PreprocessConfig) => void;
  experimentId: string;
  setExperimentId: (id: string) => void;
  engine: BacktestEngineMode;
  setEngine: (e: BacktestEngineMode) => void;
  constraints: OptConstraints;
  setConstraints: (c: OptConstraints | ((prev: OptConstraints) => OptConstraints)) => void;
  /** Bumped when an external actor (Bot Dock / A2UI) patches constraints, so /portfolio can flash. */
  constraintsRev: number;

  api: QuantApiStatus;
  apiOnline: boolean;
  recheckApi: () => void;
  agent: AgentHomeStatus;
  recheckAgent: () => void;

  metrics: Partial<Record<ViewKey, ViewMetrics>>;
  publishMetrics: (view: ViewKey, m: ViewMetrics | null) => void;

  registerDock: (c: DockController | null) => void;
  dock: DockController;
}

const Ctx = createContext<WorkbenchValue | null>(null);

const LS_KEY = 'quantlab.v2.prefs';

interface Prefs {
  theme?: Theme;
  prd?: boolean;
  botOpen?: boolean;
  universe?: UniverseId;
}

function readPrefs(): Prefs {
  try {
    return JSON.parse(localStorage.getItem(LS_KEY) || '{}') as Prefs;
  } catch {
    return {};
  }
}

export function WorkbenchProvider({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const view = viewFromPath(pathname);

  const [universe, setUniverse] = useState<UniverseId>('CSI500');
  const [snapshotLock, setSnapshotLock] = useState<{ locked: boolean; snapshotId: string | null }>({ locked: false, snapshotId: null });
  const [theme, setTheme] = useState<Theme>('dark');
  const [prd, setPrd] = useState(false);
  const [botOpen, setBotOpen] = useState(true);
  const [prefsLoaded, setPrefsLoaded] = useState(false);

  const [factorId, setFactorId] = useState('alpha_vp_corr_20d');
  const [preprocess, setPreprocess] = useState<PreprocessConfig>(DEFAULT_PREPROCESS);
  const [experimentId, setExperimentId] = useState(DEFAULT_EXPERIMENT_ID);
  const [engine, setEngine] = useState<BacktestEngineMode>('event');
  const [constraints, setConstraintsState] = useState<OptConstraints>(DEFAULT_CONSTRAINTS);
  const [constraintsRev, setConstraintsRev] = useState(0);

  const [api, setApi] = useState<QuantApiStatus>({ state: 'checking' });
  const [agent, setAgent] = useState<AgentHomeStatus>({ state: 'checking', online: false });
  const [apiNonce, setApiNonce] = useState(0);
  const [agentNonce, setAgentNonce] = useState(0);
  const [metrics, setMetrics] = useState<Partial<Record<ViewKey, ViewMetrics>>>({});

  useEffect(() => {
    const p = readPrefs();
    if (p.theme === 'dark' || p.theme === 'light') setTheme(p.theme);
    if (typeof p.prd === 'boolean') setPrd(p.prd);
    if (typeof p.botOpen === 'boolean') setBotOpen(p.botOpen);
    if (p.universe) setUniverse(p.universe);
    setPrefsLoaded(true);
  }, []);

  useEffect(() => {
    if (!prefsLoaded) return;
    try {
      localStorage.setItem(LS_KEY, JSON.stringify({ theme, prd, botOpen, universe } satisfies Prefs));
    } catch {
      /* private mode */
    }
  }, [theme, prd, botOpen, universe, prefsLoaded]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  useEffect(() => {
    document.body.classList.toggle('prd-on', prd);
  }, [prd]);

  // quant-api heartbeat
  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      const t0 = performance.now();
      try {
        const health = await quantApi.health();
        if (stop) return;
        setApi({ state: 'online', health, checkedAt: Date.now(), rttMs: performance.now() - t0 });
        timer = setTimeout(tick, 15_000);
      } catch (err) {
        if (stop) return;
        const msg = err instanceof QuantApiError ? err.message : String(err);
        setApi({ state: 'offline', error: msg, checkedAt: Date.now() });
        timer = setTimeout(tick, 6_000);
      }
    };
    tick();
    return () => {
      stop = true;
      if (timer) clearTimeout(timer);
    };
  }, [apiNonce]);

  // Agent Home daemon heartbeat (server-side probe, never exposes the token)
  useEffect(() => {
    let stop = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const res = await fetch('/api/agent-home-status', { cache: 'no-store' });
        const j = (await res.json()) as Omit<AgentHomeStatus, 'state'>;
        if (stop) return;
        setAgent({ ...j, state: j.online ? 'online' : 'offline', checkedAt: Date.now() });
        timer = setTimeout(tick, j.online ? 12_000 : 8_000);
      } catch {
        if (stop) return;
        setAgent({ state: 'offline', online: false, checkedAt: Date.now() });
        timer = setTimeout(tick, 8_000);
      }
    };
    tick();
    return () => {
      stop = true;
      if (timer) clearTimeout(timer);
    };
  }, [agentNonce]);

  const setConstraints = useCallback((c: OptConstraints | ((prev: OptConstraints) => OptConstraints)) => {
    setConstraintsState(c);
  }, []);

  const publishMetrics = useCallback((v: ViewKey, m: ViewMetrics | null) => {
    setMetrics((prev) => {
      if (m === null) {
        if (!(v in prev)) return prev;
        const next = { ...prev };
        delete next[v];
        return next;
      }
      if (JSON.stringify(prev[v]) === JSON.stringify(m)) return prev;
      return { ...prev, [v]: m };
    });
  }, []);

  const dockRef = useRef<DockController | null>(null);
  const pending = useRef<((c: DockController) => void)[]>([]);
  const registerDock = useCallback((c: DockController | null) => {
    dockRef.current = c;
    if (c) {
      const q = pending.current.splice(0);
      q.forEach((fn) => fn(c));
    }
  }, []);

  const withDock = useCallback(
    (fn: (c: DockController) => void) => {
      setBotOpen(true);
      if (dockRef.current) fn(dockRef.current);
      else pending.current.push(fn);
    },
    []
  );

  const dock = useMemo<DockController>(
    () => ({
      send: (text, opts) => withDock((c) => c.send(text, opts)),
      runCommand: (cmd, opts) => withDock((c) => c.runCommand(cmd, opts)),
      diagnoseFactor: (item, context) => withDock((c) => c.diagnoseFactor(item, context))
    }),
    [withDock]
  );

  const patchConstraintsExternal = useCallback((c: OptConstraints | ((prev: OptConstraints) => OptConstraints)) => {
    setConstraintsState(c);
    setConstraintsRev((r) => r + 1);
  }, []);

  const value: WorkbenchValue = {
    view,
    universe,
    setUniverse,
    snapshotLock,
    setSnapshotLock,
    snapshotId: snapshotLock.locked ? snapshotLock.snapshotId : null,
    theme,
    setTheme,
    prd,
    setPrd,
    botOpen,
    setBotOpen,
    factorId,
    setFactorId,
    preprocess,
    setPreprocess,
    experimentId,
    setExperimentId,
    engine,
    setEngine,
    constraints,
    setConstraints,
    constraintsRev,
    api,
    apiOnline: api.state === 'online',
    recheckApi: () => setApiNonce((n) => n + 1),
    agent,
    recheckAgent: () => setAgentNonce((n) => n + 1),
    metrics,
    publishMetrics,
    registerDock,
    dock
  };

  return (
    <Ctx.Provider value={value}>
      <ExternalPatchCtx.Provider value={patchConstraintsExternal}>{children}</ExternalPatchCtx.Provider>
    </Ctx.Provider>
  );
}

const ExternalPatchCtx = createContext<((c: OptConstraints | ((prev: OptConstraints) => OptConstraints)) => void) | null>(null);

export function useWorkbench(): WorkbenchValue {
  const v = useContext(Ctx);
  if (!v) throw new Error('useWorkbench must be used inside <WorkbenchProvider>');
  return v;
}

/** Constraint patch issued by the Bot Dock / A2UI cards (bumps `constraintsRev`). */
export function useExternalConstraintPatch() {
  const v = useContext(ExternalPatchCtx);
  if (!v) throw new Error('useExternalConstraintPatch must be used inside <WorkbenchProvider>');
  return v;
}

/** Publish the current view's headline numbers into the Bot Dock canvas context. */
export function usePublishMetrics(view: ViewKey, m: ViewMetrics | null) {
  const { publishMetrics } = useWorkbench();
  const key = JSON.stringify(m);
  useEffect(() => {
    publishMetrics(view, m);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, key, publishMetrics]);
}
