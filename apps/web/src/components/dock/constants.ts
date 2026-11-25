import type { BotId } from '@/lib/workbench';

export const AGENT_HOME_REPO = process.env.NEXT_PUBLIC_AGENT_HOME_REPO || '../ppeng-agent-core';
export const AGENT_HOME_START_CMD = `cd ${AGENT_HOME_REPO} && pnpm dev:lab`;
export const REGISTER_BOT_CMD = 'make register-bot';

export interface BotMeta {
  id: BotId;
  name: string;
  short: string;
  role: string;
  glyph: 'hex' | 'dia' | 'sq';
  moe: boolean;
}

export const BOTS: BotMeta[] = [
  { id: 'quant-lead', name: 'Quant-Lead Bot', short: 'Quant-Lead', role: '主研总助 · 永久记忆 + Routine', glyph: 'hex', moe: false },
  { id: 'alpha-miner', name: 'Alpha-Miner', short: 'Alpha-Miner', role: '因子挖掘 · CPCV 检验专家', glyph: 'dia', moe: true },
  { id: 'risk-pm', name: 'Risk-PM', short: 'Risk-PM', role: 'Barra 约束 · 归因专家', glyph: 'sq', moe: true }
];

export const botMeta = (id: BotId): BotMeta => BOTS.find((b) => b.id === id) ?? BOTS[0];

export const QUICK_CMDS = [
  { key: 'ic', label: '/IC分层', icon: 'play', title: '跑 RankIC / ICIR / NW-t 与 10 分层单调性' },
  { key: 'cpcv', label: '/CPCV体检', icon: 'models', title: 'CPCV 过拟合体检（PBO / DSR / 负路径）' },
  { key: 'barra', label: '/Barra诊断', icon: 'shield', title: 'Barra CNE6 风格与行业暴露诊断' },
  { key: 'term', label: '/查术语', icon: 'book', title: '打开术语速查库（⌘K）；输入「/查术语 PBO」直接解读' },
  { key: 'study', label: '/派发研究', icon: 'research', title: '基于当前画布上下文起草研究并派发集群' }
] as const;

/** Quick commands that run through quant-api / Agent Home (term & study open local drawers). */
export const COMPUTE_CMDS = new Set(['ic', 'cpcv', 'barra']);

export const STABLE_SYSTEM_VERSION = 'v19';
export const CANVAS_CONTEXT_MAX_CHARS = 800;
