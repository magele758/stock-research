'use client';

import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { useRouter } from 'next/navigation';
import { applyA2ui, type SurfaceState } from '@/lib/a2ui';
import { agentHome, AgentHomeError, stripCanvasContext } from '@/lib/agent-home-client';
import { buildCanvasContext, canvasChips, serializeCanvas, withCanvasPrefix } from '@/lib/canvas-context';
import { copyText, fmtMs, nowHms, shortHash } from '@/lib/format';
import { BUILTIN_FACTORS, VIEWS } from '@/lib/meta';
import { quantApi, QuantApiError } from '@/lib/quant-api';
import type { FactorDecayItem, PortfolioOptResult } from '@/lib/types';
import { useExternalConstraintPatch, useWorkbench, type BotId, type QuickCmd } from '@/lib/workbench';
import { A2uiSurface, type A2uiActionEvent } from './dock/A2uiSurface';
import { AGENT_HOME_START_CMD, BOTS, QUICK_CMDS, REGISTER_BOT_CMD, STABLE_SYSTEM_VERSION, botMeta } from './dock/constants';
import { InlineMd } from './dock/InlineMd';
import {
  compareOpt,
  constraintsFromModel,
  routeCard,
  summarizeBarra,
  summarizeCpcv,
  summarizeDecay,
  summarizeEval,
  type SurfaceBatch
} from './dock/local-diagnostics';
import { Icon } from './icons';

type Origin = 'daemon' | 'local';

type Msg =
  | { id: string; kind: 'sys'; text: string; tone?: 'err' | 'ok'; cmd?: string; time: string }
  | { id: string; kind: 'user'; text: string; steer?: boolean; ctxChars?: number; time: string }
  | { id: string; kind: 'bot'; text: string; streaming?: boolean; origin: Origin; time: string }
  | { id: string; kind: 'tool'; name: string; args: string; result?: string; status: 'run' | 'ok' | 'err'; ms?: number; origin: Origin }
  | { id: string; kind: 'a2ui'; surface: SurfaceState; origin: Origin; done?: string };

interface Thread {
  msgs: Msg[];
  sessionId?: string;
  running: boolean;
  think?: string;
  unread: number;
  historyLoaded: boolean;
}

const newThread = (): Thread => ({ msgs: [], running: false, unread: 0, historyLoaded: false });
const SESS_KEY = 'quantlab.v2.sessions';
let seq = 0;
const uid = (p = 'm') => `${p}-${Date.now().toString(36)}-${(seq++).toString(36)}`;
const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));
const json = (v: unknown) => JSON.stringify(v, null, 1) ?? String(v);

const QUICK_PROMPTS: Record<QuickCmd, string> = {
  ic: '请调用 quant MCP 的因子评估工具，对 quant_canvas_context 中的当前因子与预处理管道计算 RankIC/ICIR/NW-t 与 10 分层单调性，并逐项解读 6/6 入库门禁。',
  cpcv: '请对 quant_canvas_context 中的当前实验执行 CPCV 过拟合体检（15 条 OOS 路径、PBO、DSR、负路径占比），给出是否允许进入组合优化的结论。',
  barra:
    '请调用 Barra 暴露 / 组合优化工具诊断当前约束下的 CNE6 风格与申万行业暴露；若越过 ±0.5σ 硬线，用 A2UI 约束调参卡（turnoverLambda / maxIndustryDev / maxSizeZ，action quant.rerun_optimization）给出建议。'
};

function matchCommand(text: string): QuickCmd | null {
  const t = text.toLowerCase();
  if (/barra|风险|暴露|size|约束|组合/.test(t)) return 'barra';
  if (/cpcv|过拟合|pbo|dsr/.test(t)) return 'cpcv';
  if (/\bic\b|ic值|分层|因子|rankic/.test(t)) return 'ic';
  return null;
}

const surfaceOf = (batch: SurfaceBatch): SurfaceState => applyA2ui({}, batch)[batch.surfaceId];

export function AgentHomeDock() {
  const wb = useWorkbench();
  const patchConstraints = useExternalConstraintPatch();
  const router = useRouter();

  const [bot, setBot] = useState<BotId>('quant-lead');
  const [threads, setThreads] = useState<Record<BotId, Thread>>(() => ({ 'quant-lead': newThread(), 'alpha-miner': newThread(), 'risk-pm': newThread() }));
  const [input, setInput] = useState('');
  const [showCtx, setShowCtx] = useState(false);
  const [openTools, setOpenTools] = useState<Set<string>>(new Set());
  const [copied, setCopied] = useState(false);

  const threadEl = useRef<HTMLDivElement>(null);
  const aborts = useRef<Partial<Record<BotId, AbortController>>>({});
  const localBusy = useRef<Partial<Record<BotId, boolean>>>({});
  const sessions = useRef<Partial<Record<BotId, string>>>({});
  const lastOpt = useRef<PortfolioOptResult | null>(null);
  const botRef = useRef(bot);
  const wbRef = useRef(wb);
  useEffect(() => {
    botRef.current = bot;
    wbRef.current = wb;
  });

  useEffect(() => {
    try {
      sessions.current = JSON.parse(localStorage.getItem(SESS_KEY) || '{}') as Partial<Record<BotId, string>>;
    } catch {
      sessions.current = {};
    }
  }, []);

  // ---------------------------------------------------------------- canvas context (user-prefix injection)
  const latestSnapshot = typeof wb.api.health?.snapshot_id === 'string' ? wb.api.health.snapshot_id : null;
  const viewMetrics = wb.metrics[wb.view];
  const canvas = useMemo(
    () =>
      buildCanvasContext({
        view: wb.view,
        universe: wb.universe,
        snapshotId: wb.snapshotId,
        latestSnapshot,
        factorId: wb.factorId,
        preprocess: wb.preprocess,
        experimentId: wb.experimentId,
        engine: wb.engine,
        constraints: wb.constraints,
        metrics: viewMetrics
      }),
    [wb.view, wb.universe, wb.snapshotId, latestSnapshot, wb.factorId, wb.preprocess, wb.experimentId, wb.engine, wb.constraints, viewMetrics]
  );
  const canvasRef = useRef(canvas);
  useEffect(() => {
    canvasRef.current = canvas;
  }, [canvas]);
  const serialized = useMemo(() => serializeCanvas(canvas), [canvas]);
  const chips = useMemo(() => canvasChips(canvas), [canvas]);
  const prevChips = useRef<string[]>([]);
  const [freshChips, setFreshChips] = useState<Set<string>>(new Set());
  useEffect(() => {
    const fresh = chips.filter((c) => prevChips.current.length && !prevChips.current.includes(c));
    prevChips.current = chips;
    setFreshChips(new Set(fresh));
  }, [chips]);

  // ---------------------------------------------------------------- thread state helpers
  const patchThread = useCallback((b: BotId, fn: (t: Thread) => Thread) => setThreads((prev) => ({ ...prev, [b]: fn(prev[b]) })), []);
  const push = useCallback(
    (b: BotId, m: Msg) => patchThread(b, (t) => ({ ...t, msgs: [...t.msgs, m], unread: b === botRef.current ? 0 : t.unread + 1 })),
    [patchThread]
  );
  const update = useCallback((b: BotId, id: string, fn: (m: Msg) => Msg) => patchThread(b, (t) => ({ ...t, msgs: t.msgs.map((m) => (m.id === id ? fn(m) : m)) })), [patchThread]);
  const removeIds = useCallback((b: BotId, ids: string[]) => patchThread(b, (t) => ({ ...t, msgs: t.msgs.filter((m) => !ids.includes(m.id)) })), [patchThread]);
  const sys = useCallback((b: BotId, text: string, tone?: 'err' | 'ok', cmd?: string) => push(b, { id: uid('s'), kind: 'sys', text, tone, cmd, time: nowHms() }), [push]);
  const setThink = useCallback((b: BotId, think?: string) => patchThread(b, (t) => ({ ...t, think })), [patchThread]);
  const setRunning = useCallback((b: BotId, running: boolean, think?: string) => patchThread(b, (t) => ({ ...t, running, think: running ? think : undefined })), [patchThread]);
  const pushBot = useCallback((b: BotId, text: string, origin: Origin) => push(b, { id: uid('b'), kind: 'bot', text, origin, time: nowHms() }), [push]);
  const pushSurface = useCallback((b: BotId, batch: SurfaceBatch) => push(b, { id: uid('a'), kind: 'a2ui', surface: surfaceOf(batch), origin: 'local' }), [push]);

  // ---------------------------------------------------------------- local (quant-api) tool trace
  const localTool = useCallback(
    async <T,>(b: BotId, name: string, args: unknown, run: () => Promise<T>): Promise<{ r: T; id: string } | null> => {
      const id = uid('t');
      const t0 = performance.now();
      push(b, { id, kind: 'tool', name, args: json(args), status: 'run', origin: 'local' });
      try {
        const r = await run();
        update(b, id, (m) => (m.kind === 'tool' ? { ...m, status: 'ok', ms: performance.now() - t0 } : m));
        return { r, id };
      } catch (e) {
        update(b, id, (m) => (m.kind === 'tool' ? { ...m, status: 'err', ms: performance.now() - t0, result: errMsg(e) } : m));
        if (e instanceof QuantApiError && e.needsSeed) sys(b, `${e.message} · 数据快照缺失，先灌入种子数据：`, 'err', 'make seed');
        else if (e instanceof QuantApiError && e.kind === 'offline') sys(b, `quant-api 不可达（${e.message}）。启动：`, 'err', 'make dev-api');
        else sys(b, `调用失败：${errMsg(e)}`, 'err');
        return null;
      }
    },
    [push, update, sys]
  );
  const setToolResult = useCallback((b: BotId, id: string, v: unknown) => update(b, id, (m) => (m.kind === 'tool' ? { ...m, result: json(v) } : m)), [update]);

  const reoptimize = useCallback(
    async (b: BotId, constraints = wbRef.current.constraints) => {
      const w = wbRef.current;
      const req = { experiment_id: w.experimentId, universe_id: w.universe, snapshot_id: w.snapshotId, constraints };
      const res = await localTool(b, 'quant-api · POST /api/v1/portfolio/optimize', req, () => quantApi.optimizePortfolio(req));
      if (!res) return;
      setToolResult(b, res.id, { opt_run_id: res.r.opt_run_id, solve_status: res.r.solve_status, solve_ms: res.r.solve_ms });
      pushBot(b, compareOpt(lastOpt.current, res.r), 'local');
      lastOpt.current = res.r;
    },
    [localTool, setToolResult, pushBot]
  );

  const runLocal = useCallback(
    async (b: BotId, cmd: QuickCmd) => {
      const w = wbRef.current;
      if (!w.apiOnline) {
        sys(b, 'quant-api 离线：本地确定性诊断不可用（不会伪造结果）。先启动后端：', 'err', 'make dev-api');
        return;
      }
      if (localBusy.current[b]) return;
      localBusy.current[b] = true;
      setRunning(b, true, `本地计算 · ${QUICK_CMDS.find((q) => q.key === cmd)?.label}`);
      try {
        if (cmd === 'ic') {
          const req = { factor_id: w.factorId, universe_id: w.universe, snapshot_id: w.snapshotId, preprocess: w.preprocess };
          const res = await localTool(b, 'quant-api · POST /api/v1/factors/evaluate', req, () => quantApi.evaluateFactor(req));
          if (!res) return;
          const out = summarizeEval(res.r);
          setToolResult(b, res.id, out.summary);
          pushBot(b, out.botText, 'local');
          if (w.view !== 'alpha')
            pushSurface(b, routeCard(uid('surf-ic'), '查看完整 IC / 10 分层图表', `在 Alpha 因子工坊查看 ${res.r.factor_id} 的分层净值、滚动 IC 与相关性矩阵。`, [{ label: '打开 /alpha', action: 'quant.open_view', context: { path: '/alpha' }, primary: true }]));
        } else if (cmd === 'cpcv') {
          const req = { experiment_id: w.experimentId, snapshot_id: w.snapshotId };
          const res = await localTool(b, 'quant-api · POST /api/v1/validation/cpcv', req, () => quantApi.cpcv(req));
          if (!res) return;
          const out = summarizeCpcv(res.r);
          setToolResult(b, res.id, out.summary);
          pushBot(b, out.botText, 'local');
          if (w.view !== 'models')
            pushSurface(b, routeCard(uid('surf-cpcv'), '查看 CPCV 路径分布', `在模型页查看 ${res.r.experiment_id} 的 OOS Sharpe 直方图与 Walk-Forward 时间线。`, [{ label: '打开 /models', action: 'quant.open_view', context: { path: '/models' }, primary: true }]));
        } else {
          const req = { experiment_id: w.experimentId, universe_id: w.universe, snapshot_id: w.snapshotId, constraints: w.constraints };
          const res = await localTool(b, 'quant-api · POST /api/v1/portfolio/optimize', req, () => quantApi.optimizePortfolio(req));
          if (!res) return;
          lastOpt.current = res.r;
          const out = summarizeBarra(res.r, w.constraints, uid('surf-barra'));
          setToolResult(b, res.id, out.summary);
          pushBot(b, out.botText, 'local');
          if (out.surface) pushSurface(b, out.surface);
        }
      } finally {
        localBusy.current[b] = false;
        setRunning(b, false);
      }
    },
    [sys, setRunning, localTool, setToolResult, pushBot, pushSurface]
  );

  // ---------------------------------------------------------------- daemon session + SSE
  const persistSessions = () => {
    try {
      localStorage.setItem(SESS_KEY, JSON.stringify(sessions.current));
    } catch {
      /* ignore */
    }
  };

  const ensureSession = useCallback(
    async (b: BotId): Promise<string> => {
      const r = await agentHome.openBot(b);
      const sid = r.session?.id;
      if (!sid) throw new Error('open 响应缺少 session.id');
      const prev = sessions.current[b];
      sessions.current[b] = sid;
      persistSessions();
      patchThread(b, (t) => ({ ...t, sessionId: sid }));
      if (r.createdSession || (prev && prev !== sid)) sys(b, `已为 ${botMeta(b).short} 打开 session ${sid}`, 'ok');
      return sid;
    },
    [patchThread, sys]
  );

  const backfillToolResults = useCallback(
    async (b: BotId, sid: string, toolMsgIds: Map<string, string>) => {
      if (!toolMsgIds.size) return;
      try {
        const detail = await agentHome.session(sid, 24);
        const messages = Array.isArray(detail.messages) ? (detail.messages as { parts?: Record<string, unknown>[] }[]) : [];
        for (const m of messages)
          for (const part of m.parts ?? []) {
            if (part.type !== 'tool_result') continue;
            const id = toolMsgIds.get(String(part.toolCallId));
            if (!id) continue;
            const content = typeof part.content === 'string' ? part.content : json(part.content);
            update(b, id, (x) => (x.kind === 'tool' ? { ...x, status: part.ok === false ? 'err' : 'ok', result: content.slice(0, 4000) } : x));
          }
      } catch {
        /* tool results are an enhancement; the stream already completed */
      }
    },
    [update]
  );

  const streamTurn = useCallback(
    async (b: BotId, sid: string, body: Record<string, unknown>) => {
      const ac = new AbortController();
      aborts.current[b] = ac;
      setRunning(b, true, '连接 Agent Home SSE…');
      let botId: string | null = null;
      let seg: string[] = [];
      let gotText = false;
      const tools = new Map<string, string>();
      const surfaceMsg = new Map<string, string>();
      let surfaces: Record<string, SurfaceState> = {};

      const closeBot = () => {
        if (!botId) return;
        const id = botId;
        update(b, id, (m) => (m.kind === 'bot' ? { ...m, streaming: false } : m));
        botId = null;
      };
      const settleTools = () => tools.forEach((id) => update(b, id, (m) => (m.kind === 'tool' && m.status === 'run' ? { ...m, status: 'ok' } : m)));

      try {
        await agentHome.stream(
          sid,
          body,
          (ev, payload) => {
            const p = (payload && typeof payload === 'object' ? payload : {}) as Record<string, unknown>;
            if (ev === 'model') {
              switch (p.type) {
                case 'text_delta': {
                  const text = String(p.text ?? '');
                  if (!text) break;
                  if (!botId) {
                    settleTools();
                    botId = uid('b');
                    seg.push(botId);
                    push(b, { id: botId, kind: 'bot', text, streaming: true, origin: 'daemon', time: nowHms() });
                  } else {
                    const id = botId;
                    update(b, id, (m) => (m.kind === 'bot' ? { ...m, text: m.text + text } : m));
                  }
                  gotText = true;
                  setThink(b, undefined);
                  break;
                }
                case 'reasoning_delta':
                  setThink(b, `推理中 · ${String(p.text ?? '').slice(-48).replace(/\s+/g, ' ')}`);
                  break;
                case 'tool_call_start': {
                  closeBot();
                  const id = uid('t');
                  tools.set(String(p.toolCallId), id);
                  seg.push(id);
                  push(b, { id, kind: 'tool', name: String(p.name ?? 'tool'), args: '', status: 'run', origin: 'daemon' });
                  setThink(b, `调用 ${String(p.name ?? 'tool')}`);
                  break;
                }
                case 'tool_call_delta': {
                  const id = tools.get(String(p.toolCallId));
                  const frag = String(p.argumentsFragment ?? '');
                  if (id && frag) update(b, id, (m) => (m.kind === 'tool' ? { ...m, args: m.args + frag } : m));
                  break;
                }
                case 'a2ui_message': {
                  const sidf = String(p.surfaceId ?? '');
                  surfaces = applyA2ui(surfaces, p.envelope, sidf || undefined);
                  const key = sidf || Object.keys(surfaces).pop() || '';
                  const surface = surfaces[key];
                  if (!surface) break;
                  const existing = surfaceMsg.get(key);
                  if (existing) update(b, existing, (m) => (m.kind === 'a2ui' ? { ...m, surface } : m));
                  else {
                    closeBot();
                    const id = uid('a');
                    surfaceMsg.set(key, id);
                    seg.push(id);
                    push(b, { id, kind: 'a2ui', surface, origin: 'daemon' });
                  }
                  break;
                }
                case 'stream_reset':
                  removeIds(b, seg);
                  seg = [];
                  botId = null;
                  tools.clear();
                  surfaceMsg.clear();
                  sys(b, `模型流重置（${String(p.reason ?? 'retry')}），已丢弃本段未保存输出`);
                  break;
                case 'done':
                  closeBot();
                  seg = [];
                  setThink(b, p.stopReason === 'tool_use' ? '执行工具中…' : undefined);
                  break;
                default:
                  break;
              }
            } else if (ev === 'session') {
              const s = (p.session ?? p) as Record<string, unknown>;
              if (typeof s.status === 'string' && s.status !== 'running') setThink(b, `session ${s.status}`);
            } else if (ev === 'result') {
              settleTools();
              const la = p.latestAssistant;
              if (!gotText && typeof la === 'string' && la.trim()) pushBot(b, la, 'daemon');
            } else if (ev === 'error') {
              sys(b, `Agent Home 错误：${String(p.message ?? p.error ?? 'unknown')}`, 'err');
            }
          },
          ac.signal
        );
      } catch (e) {
        if (ac.signal.aborted) sys(b, '已取消本次运行');
        else sys(b, `SSE 中断：${errMsg(e)}`, 'err');
      } finally {
        closeBot();
        settleTools();
        if (aborts.current[b] === ac) delete aborts.current[b];
        setRunning(b, false);
        void backfillToolResults(b, sid, tools);
      }
    },
    [setRunning, update, push, setThink, removeIds, sys, pushBot, backfillToolResults]
  );

  const sendOnline = useCallback(
    async (b: BotId, text: string, opts?: { display?: string; fallback?: QuickCmd }) => {
      const runningSid = aborts.current[b] ? sessions.current[b] : undefined;
      if (runningSid) {
        push(b, { id: uid('u'), kind: 'user', text: opts?.display ?? text, steer: true, time: nowHms() });
        try {
          await agentHome.steer(runningSid, text);
          sys(b, 'Steer 已写入 session inbox，将在下一拍注入（不打断在途请求）', 'ok');
        } catch (e) {
          sys(b, `Steer 失败：${errMsg(e)}`, 'err');
        }
        return;
      }
      let sid: string;
      try {
        sid = await ensureSession(b);
      } catch (e) {
        if (e instanceof AgentHomeError && e.status === 404) sys(b, `Agent Home 在线，但 bot「${b}」未注册。在 stock-research 根目录执行：`, 'err', REGISTER_BOT_CMD);
        else if (e instanceof AgentHomeError && e.status === 401) sys(b, 'Agent Home 拒绝鉴权（401）：检查 apps/web 进程的 RAW_AGENT_AUTH_TOKEN 是否与 daemon 一致', 'err');
        else sys(b, `打开 bot session 失败：${errMsg(e)}`, 'err');
        if (opts?.fallback) {
          sys(b, '回退为本地确定性诊断（quant-api 实算，不经 LLM）');
          await runLocal(b, opts.fallback);
        }
        return;
      }
      const ctx = canvasRef.current;
      push(b, { id: uid('u'), kind: 'user', text: opts?.display ?? text, ctxChars: serializeCanvas(ctx).length, time: nowHms() });
      await streamTurn(b, sid, { message: withCanvasPrefix(ctx, text) });
    },
    [push, sys, ensureSession, runLocal, streamTurn]
  );

  const dispatchText = useCallback(
    async (b: BotId, text: string) => {
      const t = text.trim();
      if (!t) return;
      if (wbRef.current.agent.online) return sendOnline(b, t);
      push(b, { id: uid('u'), kind: 'user', text: t, time: nowHms() });
      const cmd = matchCommand(t);
      if (cmd) {
        sys(b, `DAEMON OFFLINE · 自由对话需要 Agent Home。已匹配本地确定性指令 ${QUICK_CMDS.find((q) => q.key === cmd)?.label}`);
        await runLocal(b, cmd);
      } else sys(b, 'DAEMON OFFLINE · 自由对话需要 Agent Home（快捷指令仍可直连 quant-api）。启动 Agent Home：', 'err', AGENT_HOME_START_CMD);
    },
    [sendOnline, push, sys, runLocal]
  );

  const dispatchCommand = useCallback(
    async (b: BotId, cmd: QuickCmd) => {
      const label = QUICK_CMDS.find((q) => q.key === cmd)?.label ?? cmd;
      if (wbRef.current.agent.online) return sendOnline(b, `${label}\n${QUICK_PROMPTS[cmd]}`, { display: label, fallback: cmd });
      push(b, { id: uid('u'), kind: 'user', text: label, time: nowHms() });
      await runLocal(b, cmd);
    },
    [sendOnline, push, runLocal]
  );

  const diagnose = useCallback(
    async (b: BotId, item: FactorDecayItem, ctx: { universe: string; shadowId: string; snapshotId: string }) => {
      const prompt = `诊断因子 ${item.factor_id} 失效原因：状态 ${item.status} · 60D RankIC ${item.rank_ic_60d} · 历史分位 ${item.hist_percentile} · 拥挤度 ${item.crowding_index}（${ctx.shadowId} · ${ctx.universe} · snapshot ${ctx.snapshotId}）。请拆解原因并给出处置建议（降权 / 剔除 / CPCV 复检）。`;
      const display = `/诊断因子 ${item.factor_id} · ${item.status}`;
      if (wbRef.current.agent.online) return sendOnline(b, prompt, { display });
      push(b, { id: uid('u'), kind: 'user', text: display, time: nowHms() });
      const traceId = uid('t');
      push(b, {
        id: traceId,
        kind: 'tool',
        name: `quant-api · GET /api/v1/shadow/drift → factor_decay[${item.factor_id}]`,
        args: json(ctx),
        result: json(item),
        status: 'ok',
        origin: 'local'
      });
      const builtin = BUILTIN_FACTORS.some((f) => f.factor_id === item.factor_id);
      let ev = null;
      if (builtin && wbRef.current.apiOnline) {
        setRunning(b, true, '复核全样本 IC');
        const req = { factor_id: item.factor_id, universe_id: ctx.universe, snapshot_id: ctx.snapshotId || null, preprocess: wbRef.current.preprocess };
        const res = await localTool(b, 'quant-api · POST /api/v1/factors/evaluate', req, () => quantApi.evaluateFactor(req));
        setRunning(b, false);
        if (res) {
          ev = res.r;
          setToolResult(b, res.id, summarizeEval(res.r).summary);
        }
      }
      pushBot(b, summarizeDecay(item, ev).botText, 'local');
      if (builtin)
        pushSurface(b, routeCard(uid('surf-decay'), '在因子工坊复检', `切换到 ${item.factor_id} 并重新计算 IC / 分层 / 门禁。`, [{ label: '打开 /alpha 复检', action: 'quant.open_factor', context: { factor_id: item.factor_id }, primary: true }]));
    },
    [sendOnline, push, setRunning, localTool, setToolResult, pushBot, pushSurface]
  );

  // ---------------------------------------------------------------- A2UI actions
  const applyQuantAction = useCallback(
    async (b: BotId, e: A2uiActionEvent): Promise<string | null> => {
      const w = wbRef.current;
      if (/rerun_optimization|apply_constraints?|tune_constraints/i.test(e.name)) {
        const parsed = constraintsFromModel(e.dataModel ?? e.context, w.constraints);
        if ('error' in parsed) {
          sys(b, `约束无效：${parsed.error}`, 'err');
          return null;
        }
        patchConstraints(parsed.next);
        router.push('/portfolio');
        sys(b, `已写入 /portfolio 约束（${parsed.changes.join('，') || '数值未变'}），cvxpy 重算中`, 'ok');
        if (w.apiOnline) await reoptimize(b, parsed.next);
        else sys(b, 'quant-api 离线：约束已写入，但无法重算（启动后端后组合页会自动求解）', 'err', 'make dev-api');
        return `已应用 · ${parsed.changes.join('，') || '约束未变'} · 已触发 cvxpy 重算`;
      }
      if (e.name === 'quant.open_view') {
        const path = String(e.context.path ?? '');
        if (VIEWS.some((v) => v.path === path)) {
          router.push(path);
          return `已打开 ${path}`;
        }
        return null;
      }
      if (e.name === 'quant.open_factor') {
        const id = String(e.context.factor_id ?? '');
        if (id) {
          w.setFactorId(id);
          router.push('/alpha');
          return `已切换因子 ${id}`;
        }
        return null;
      }
      return null;
    },
    [sys, patchConstraints, router, reoptimize]
  );

  const onA2uiAction = useCallback(
    async (b: BotId, msgId: string, origin: Origin, e: A2uiActionEvent) => {
      update(b, msgId, (m) => (m.kind === 'a2ui' ? { ...m, done: `提交 ${e.name}…` } : m));
      const local = await applyQuantAction(b, e);
      if (origin === 'local') {
        update(b, msgId, (m) => (m.kind === 'a2ui' ? { ...m, done: local ?? `未识别的本地 action：${e.name}` } : m));
        return;
      }
      const sid = sessions.current[b];
      if (!sid || !wbRef.current.agent.online) {
        update(b, msgId, (m) => (m.kind === 'a2ui' ? { ...m, done: local ?? 'DAEMON OFFLINE · action 未回传 bot' } : m));
        return;
      }
      try {
        await agentHome.a2uiAction(sid, { surfaceId: e.surfaceId, name: e.name, context: e.context, dataModel: e.dataModel, autoRun: false });
        update(b, msgId, (m) => (m.kind === 'a2ui' ? { ...m, done: `${local ? local + ' · ' : ''}已回传 bot（${e.name}）` } : m));
        await streamTurn(b, sid, {});
      } catch (err) {
        update(b, msgId, (m) => (m.kind === 'a2ui' ? { ...m, done: undefined } : m));
        sys(b, `A2UI action 回传失败：${errMsg(err)}`, 'err');
      }
    },
    [update, applyQuantAction, sys, streamTurn]
  );

  // ---------------------------------------------------------------- history (daemon online)
  const loadHistory = useCallback(
    async (b: BotId) => {
      patchThread(b, (t) => ({ ...t, historyLoaded: true }));
      let sid: string;
      try {
        sid = await ensureSession(b);
      } catch {
        return;
      }
      try {
        const detail = await agentHome.session(sid, 30);
        const raw = Array.isArray(detail.messages) ? (detail.messages as { role?: string; parts?: Record<string, unknown>[] }[]) : [];
        const out: Msg[] = [];
        const toolIdx = new Map<string, number>();
        let surfaces: Record<string, SurfaceState> = {};
        for (const m of raw) {
          const parts = m.parts ?? [];
          if (m.role === 'user') {
            const text = parts.filter((p) => p.type === 'text').map((p) => String(p.text ?? '')).join('');
            if (!text.trim()) continue;
            const s = stripCanvasContext(text);
            out.push({ id: uid('h'), kind: 'user', text: s.text, ctxChars: s.hadContext ? text.length - s.text.length : undefined, time: '' });
            continue;
          }
          for (const p of parts) {
            if (p.type === 'text' && String(p.text ?? '').trim()) {
              const last = out[out.length - 1];
              if (last && last.kind === 'bot') out[out.length - 1] = { ...last, text: `${last.text}${String(p.text)}` };
              else out.push({ id: uid('h'), kind: 'bot', text: String(p.text), origin: 'daemon', time: '' });
            } else if (p.type === 'tool_call') {
              toolIdx.set(String(p.toolCallId), out.length);
              out.push({ id: uid('h'), kind: 'tool', name: String(p.name ?? 'tool'), args: json(p.input), status: 'ok', origin: 'daemon' });
            } else if (p.type === 'tool_result') {
              const i = toolIdx.get(String(p.toolCallId));
              const t = i === undefined ? undefined : out[i];
              if (i !== undefined && t && t.kind === 'tool')
                out[i] = { ...t, status: p.ok === false ? 'err' : 'ok', result: (typeof p.content === 'string' ? p.content : json(p.content)).slice(0, 4000) };
            } else if (p.type === 'surface_update') {
              const sidf = String(p.surfaceId ?? '');
              surfaces = applyA2ui(surfaces, p, sidf);
              if (surfaces[sidf]) out.push({ id: uid('h'), kind: 'a2ui', surface: surfaces[sidf], origin: 'daemon', done: '历史 surface' });
            }
          }
        }
        if (out.length) {
          patchThread(b, (t) => ({ ...t, msgs: [...out, { id: uid('s'), kind: 'sys', text: `以上为 session ${sid} 最近 ${raw.length} 条历史`, time: nowHms() }, ...t.msgs] }));
        }
      } catch (e) {
        sys(b, `历史加载失败：${errMsg(e)}`, 'err');
      }
    },
    [patchThread, ensureSession, sys]
  );

  useEffect(() => {
    const t = threads[bot];
    if (wb.agent.online && !t.historyLoaded && !aborts.current[bot]) void loadHistory(bot);
  }, [wb.agent.online, bot, threads, loadHistory]);

  // ---------------------------------------------------------------- DockController (pages → dock)
  const actions = useRef({ dispatchText, dispatchCommand, diagnose });
  useEffect(() => {
    actions.current = { dispatchText, dispatchCommand, diagnose };
  });
  const { registerDock } = wb;
  useEffect(() => {
    registerDock({
      send: (text, opts) => {
        const b = opts?.bot ?? botRef.current;
        setBot(b);
        void actions.current.dispatchText(b, text);
      },
      runCommand: (cmd, opts) => {
        const b = opts?.bot ?? botRef.current;
        setBot(b);
        void actions.current.dispatchCommand(b, cmd);
      },
      diagnoseFactor: (item, ctx) => {
        setBot('alpha-miner');
        void actions.current.diagnose('alpha-miner', item, ctx);
      }
    });
    return () => registerDock(null);
  }, [registerDock]);

  // ---------------------------------------------------------------- UI
  const thread = threads[bot];
  useEffect(() => {
    patchThread(bot, (t) => (t.unread ? { ...t, unread: 0 } : t));
  }, [bot, patchThread]);
  useEffect(() => {
    const el = threadEl.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [thread.msgs, thread.think]);

  const online = wb.agent.online;
  const daemonRunning = thread.running && Boolean(aborts.current[bot]);
  const localRunning = thread.running && !daemonRunning;
  const meta = botMeta(bot);

  const submit = () => {
    const t = input.trim();
    if (!t || localRunning) return;
    setInput('');
    void dispatchText(bot, t);
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };
  const cancel = async () => {
    aborts.current[bot]?.abort();
    const sid = sessions.current[bot];
    if (sid) await agentHome.cancel(sid).catch(() => undefined);
  };
  const copyStart = async () => {
    if (await copyText(AGENT_HOME_START_CMD)) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    }
  };

  const statusEl =
    wb.agent.state === 'checking' ? (
      <span className="sv">
        <span className="pulse idle" />
        CHECKING…
      </span>
    ) : online ? (
      <span className="sv ok" title={`${wb.agent.url} · ${wb.agent.source}${wb.agent.authConfigured ? ' · bearer injected server-side' : ' · no RAW_AGENT_AUTH_TOKEN'}`}>
        <span className="pulse" />
        ONLINE :{wb.agent.port ?? '?'}
      </span>
    ) : (
      <span className="sv bad" onClick={copyStart} title={`tried: ${(wb.agent.tried ?? []).join(' , ') || wb.agent.triedUrl || '—'}\n${AGENT_HOME_START_CMD}`}>
        <span className="pulse off" />
        {copied ? '已复制启动命令' : 'OFFLINE · 点击复制启动命令'}
      </span>
    );

  return (
    <aside className="bot" aria-label="Agent Home Bot Dock">
      <div className="bhd">
        <div className="btop">
          <Icon name="hex" size={12} />
          AGENT HOME · QUANT BOT DOCK
          {statusEl}
        </div>
        <div className="bsub">
          {online
            ? `ppeng-agent-core${wb.agent.version ? ' ' + wb.agent.version : ''}${wb.agent.adapter ? ' · ' + wb.agent.adapter : ''} · session ${shortHash(thread.sessionId ?? sessions.current[bot] ?? null, 6)}${wb.agent.rttMs !== undefined ? ' · ' + fmtMs(wb.agent.rttMs) : ''}`
            : `LOCAL MODE · 快捷指令直连 quant-api ${wb.apiOnline ? 'ONLINE' : 'OFFLINE'}（确定性计算，不经 LLM）`}
        </div>
        <div className="btabs" role="tablist">
          {BOTS.map((b) => (
            <button key={b.id} role="tab" aria-selected={bot === b.id} className={`btab${bot === b.id ? ' on' : ''}`} onClick={() => setBot(b.id)} title={b.name}>
              <b>
                <Icon name={b.glyph} size={11} />
                {b.short}
                {b.moe && <sup>MoE</sup>}
              </b>
              <small>{b.role}</small>
              {threads[b.id].unread > 0 && bot !== b.id && <span className="unr">{threads[b.id].unread}</span>}
            </button>
          ))}
        </div>
      </div>

      <div className="ctxbar">
        <div className="ctxl">
          <span>CANVAS CONTEXT · {serialized.length} CH · ~{Math.ceil(serialized.length / 3.2)} TOK</span>
          <button className="mt" onClick={() => setShowCtx((v) => !v)} title="预览实际注入到最新 user 消息头部的内容">
            {showCtx ? '收起' : '预览注入'}
          </button>
        </div>
        <div className="cchips">
          {chips.map((c) => (
            <span key={c} className={`cc${freshChips.has(c) ? ' fl' : ''}`} title={c}>
              {c}
            </span>
          ))}
        </div>
        {showCtx && <pre className="a2x" style={{ marginBottom: 4 }}>{serialized}</pre>}
        <div className="kvs">
          <Icon name="shield" size={10} />
          KV-Cache Safe · User-Prefix · system {STABLE_SYSTEM_VERSION}
          <span>· system 只读</span>
        </div>
      </div>

      <div className="thread" ref={threadEl}>
        {thread.msgs.length === 0 && (
          <div className={`m-sys${online ? ' ok' : ''}`}>
            <Icon name={online ? 'plug' : 'warn'} size={11} />
            <span>
              {meta.name} · {meta.role}。
              {online
                ? ' 已连接 Agent Home：消息经同源代理 /api/agent-home 流式发送，画布上下文以 <quant_canvas_context> 注入最新 user 消息头部；运行中再次发送 = Steer。'
                : wb.apiOnline
                  ? ' Agent Home 离线：快捷指令将直接调用本地 quant-api 实算，并以 A2UI v0.9 卡片返回可执行建议；自由对话需启动 daemon。'
                  : ' Agent Home 与 quant-api 均离线：没有可用的数据来源，Dock 不会生成任何模拟结果。'}
            </span>
          </div>
        )}
        {thread.msgs.map((m) => {
          switch (m.kind) {
            case 'sys':
              return (
                <div key={m.id} className={`m-sys${m.tone ? ' ' + m.tone : ''}`}>
                  <Icon name={m.tone === 'err' ? 'warn' : m.tone === 'ok' ? 'check' : 'term'} size={11} />
                  <span style={{ minWidth: 0 }}>
                    {m.text}
                    {m.cmd && (
                      <>
                        {' '}
                        <code style={{ cursor: 'pointer' }} title="点击复制" onClick={() => void copyText(m.cmd ?? '')}>
                          {m.cmd}
                        </code>
                      </>
                    )}
                  </span>
                  <span className="mt">{m.time}</span>
                </div>
              );
            case 'user':
              return (
                <div key={m.id} className={`m-user${m.steer ? ' steer' : ''}`}>
                  <div className="ubb">{m.text}</div>
                  <div className="um mt">
                    {m.steer ? 'STEER · ' : ''}
                    {m.ctxChars ? `+ctx ${m.ctxChars}ch · ` : ''}
                    {m.time}
                  </div>
                </div>
              );
            case 'bot':
              return (
                <div key={m.id} className="m-bot">
                  <div className="bhh">
                    <Icon name={meta.glyph} size={11} />
                    {meta.short}
                    {m.origin === 'local' && <span className="trk">LOCAL · DETERMINISTIC</span>}
                    <span className="mt">{m.time}</span>
                  </div>
                  <div className="bb">
                    <InlineMd text={m.text} />
                    {m.streaming && <span className="caret" />}
                  </div>
                </div>
              );
            case 'tool': {
              const open = openTools.has(m.id);
              return (
                <div key={m.id} className={`m-tr${open ? ' open' : ''}`}>
                  <button
                    className="trh"
                    onClick={() =>
                      setOpenTools((s) => {
                        const n = new Set(s);
                        if (n.has(m.id)) n.delete(m.id);
                        else n.add(m.id);
                        return n;
                      })
                    }
                  >
                    <Icon name="chev" size={10} />
                    <span className={`trk ${m.status === 'run' ? 'run' : m.status === 'err' ? 'bad' : 'ok'}`}>{m.origin === 'daemon' ? 'TOOL' : 'API'}</span>
                    <span className="tn">{m.name}</span>
                    <span className="trm">{m.status === 'run' ? 'RUNNING' : m.ms !== undefined ? fmtMs(m.ms) : m.status === 'err' ? 'ERROR' : 'DONE'}</span>
                  </button>
                  <div className="trb">
                    <div className="trs">ARGS</div>
                    <pre>{m.args || '—'}</pre>
                    {m.result && (
                      <>
                        <div className="trs">{m.status === 'err' ? 'ERROR' : 'RESULT'}</div>
                        <pre>{m.result}</pre>
                      </>
                    )}
                  </div>
                </div>
              );
            }
            case 'a2ui':
              return <A2uiSurface key={m.id} surface={m.surface} origin={m.origin} done={m.done} busy={thread.running} onAction={(e) => void onA2uiAction(bot, m.id, m.origin, e)} />;
            default:
              return null;
          }
        })}
        {thread.running && (
          <div className="m-think">
            <i />
            <i />
            <i />
            <span>{thread.think ?? '处理中…'}</span>
          </div>
        )}
      </div>

      <div className="bcmds">
        {QUICK_CMDS.map((q) => (
          <button
            key={q.key}
            className="bcmd"
            disabled={localRunning || daemonRunning || (!online && !wb.apiOnline)}
            onClick={() => void dispatchCommand(bot, q.key)}
            title={online ? '经 Agent Home bot 执行（MCP 工具）' : wb.apiOnline ? '本地 quant-api 实算' : 'quant-api 离线'}
          >
            <Icon name={q.icon} size={11} />
            {q.label}
          </button>
        ))}
        <button className="bcmd" disabled={!daemonRunning} onClick={() => void cancel()} title="取消当前 Agent Home 运行">
          <Icon name="stop" size={11} />
          取消运行
        </button>
      </div>
      <div className="binput">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKey}
          placeholder={daemonRunning ? 'Steer：补充指令将注入正在运行的任务…' : online ? `向 ${meta.short} 提问（自动附带画布上下文）` : '离线：输入将匹配本地确定性指令（IC / CPCV / Barra）'}
        />
        <button className={`sendb${daemonRunning ? ' steer' : ''}`} onClick={submit} disabled={localRunning} title={daemonRunning ? 'Steer 正在运行的任务' : '发送 (Enter)'}>
          <Icon name={daemonRunning ? 'bolt' : 'send'} size={14} />
        </button>
      </div>
      <div className="bfoot">
        <span>{online ? 'mode: DAEMON · SSE · approvals=ask' : 'mode: LOCAL · quant-api'}</span>
        <span>{daemonRunning ? '运行中 · 发送 = Steer' : 'Enter 发送 · Shift+Enter 换行'}</span>
      </div>
    </aside>
  );
}
