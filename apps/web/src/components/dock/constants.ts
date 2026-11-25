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
  { key: 'ic', label: '/跑IC与10分层', icon: 'play' },
  { key: 'cpcv', label: '/CPCV过拟合体检', icon: 'models' },
  { key: 'barra', label: '/Barra风险诊断', icon: 'shield' }
] as const;

export const STABLE_SYSTEM_VERSION = 'v19';
export const CANVAS_CONTEXT_MAX_CHARS = 800;
