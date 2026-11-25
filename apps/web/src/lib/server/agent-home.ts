import { readFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { join, resolve } from 'node:path';

/**
 * Agent Home (ppeng-agent-core) daemon discovery — server-side only.
 * Resolution order (IMPLEMENTATION-CONTRACT §5):
 *   1. AGENT_HOME_DAEMON_URL
 *   2. `daemonPort` in ppeng-agent-core/.agent-state/dev-lab.ports.json
 *   3. http://127.0.0.1:27070 (dev-lab preferred)
 *   4. http://127.0.0.1:37070 (standalone daemon default)
 */

function portsFileCandidates(): string[] {
  const list = [
    process.env.AGENT_HOME_PORTS_FILE?.trim(),
    resolve(process.cwd(), '../../..', 'ppeng-agent-core/.agent-state/dev-lab.ports.json'),
    join(homedir(), 'apps/ppeng-agent-core/.agent-state/dev-lab.ports.json')
  ];
  return list.filter((p): p is string => Boolean(p));
}

export interface DaemonCandidate {
  url: string;
  source: 'env' | 'dev-lab.ports.json' | 'default:27070' | 'default:37070';
}

export interface DaemonProbe {
  online: boolean;
  url?: string;
  port?: number;
  source?: DaemonCandidate['source'];
  version?: string;
  adapter?: string;
  rttMs?: number;
  triedUrl: string;
  tried: string[];
  authConfigured: boolean;
}

const trim = (u: string) => u.trim().replace(/\/+$/, '');

export async function daemonCandidates(): Promise<DaemonCandidate[]> {
  const out: DaemonCandidate[] = [];
  const env = process.env.AGENT_HOME_DAEMON_URL?.trim();
  if (env) out.push({ url: trim(env), source: 'env' });
  for (const portsFile of portsFileCandidates()) {
    try {
      const raw = JSON.parse(await readFile(/*turbopackIgnore: true*/ portsFile, 'utf8')) as { daemonPort?: unknown; daemonUrl?: unknown };
      const port = Number(raw.daemonPort);
      if (Number.isInteger(port) && port > 0) {
        out.push({ url: `http://127.0.0.1:${port}`, source: 'dev-lab.ports.json' });
        break;
      }
    } catch {
      /* candidate not present on this machine */
    }
  }
  out.push({ url: 'http://127.0.0.1:27070', source: 'default:27070' }, { url: 'http://127.0.0.1:37070', source: 'default:37070' });
  const seen = new Set<string>();
  return out.filter((c) => (seen.has(c.url) ? false : (seen.add(c.url), true)));
}

export function daemonAuthHeader(): string | null {
  const token = String(process.env.RAW_AGENT_AUTH_TOKEN ?? '').trim();
  return token ? `Bearer ${token}` : null;
}

async function probeOne(c: DaemonCandidate, timeoutMs: number): Promise<{ ok: boolean; rttMs: number; version?: string; adapter?: string }> {
  const t0 = performance.now();
  const headers: Record<string, string> = { accept: 'application/json' };
  const auth = daemonAuthHeader();
  if (auth) headers.authorization = auth;
  try {
    const res = await fetch(`${c.url}/api/health`, { headers, signal: AbortSignal.timeout(timeoutMs), cache: 'no-store' });
    const rttMs = performance.now() - t0;
    if (!res.ok) return { ok: res.status === 401 || res.status === 403, rttMs };
    const j = (await res.json().catch(() => ({}))) as { ok?: boolean; version?: string; adapter?: string };
    return { ok: j.ok !== false, rttMs, version: j.version, adapter: j.adapter };
  } catch {
    return { ok: false, rttMs: performance.now() - t0 };
  }
}

let cache: { at: number; probe: DaemonProbe } | null = null;
const CACHE_MS = 5_000;

export async function resolveDaemon(force = false): Promise<DaemonProbe> {
  if (!force && cache && Date.now() - cache.at < CACHE_MS) return cache.probe;
  const cands = await daemonCandidates();
  const tried: string[] = [];
  let probe: DaemonProbe | null = null;
  for (const c of cands) {
    tried.push(c.url);
    const r = await probeOne(c, 1_200);
    if (r.ok) {
      probe = {
        online: true,
        url: c.url,
        port: Number(new URL(c.url).port) || undefined,
        source: c.source,
        version: r.version,
        adapter: r.adapter,
        rttMs: Math.round(r.rttMs),
        triedUrl: c.url,
        tried,
        authConfigured: daemonAuthHeader() !== null
      };
      break;
    }
  }
  probe ??= { online: false, triedUrl: tried.join(' | '), tried, authConfigured: daemonAuthHeader() !== null };
  cache = { at: Date.now(), probe };
  return probe;
}

/** Target base for proxying: the live daemon, else the highest-priority candidate (so errors are real). */
export async function proxyTarget(): Promise<{ base: string; online: boolean }> {
  const p = await resolveDaemon();
  if (p.online && p.url) return { base: p.url, online: true };
  const [first] = await daemonCandidates();
  return { base: first.url, online: false };
}

export function invalidateDaemonCache() {
  cache = null;
}
