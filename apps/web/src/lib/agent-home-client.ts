/**
 * Browser-side Agent Home client. Every call goes through the same-origin proxy
 * `/api/agent-home/*` (the daemon bearer token is injected server-side only).
 */

export const AH_BASE = '/api/agent-home';

/** Port of `@ppeng/daemon-client` `feedSseBuffer()` (tolerates CRLF framing). */
export function feedSseBuffer(buf: string, chunk: Uint8Array, decoder: TextDecoder, onEvent: (event: string, payload: unknown) => void): string {
  const next = (buf + decoder.decode(chunk, { stream: true })).replace(/\r\n/g, '\n');
  const parts = next.split('\n\n');
  const tail = parts.pop() ?? '';
  for (const block of parts) {
    let eventName = 'message';
    const data: string[] = [];
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) eventName = line.slice(6).trim();
      else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
    }
    if (!data.length) continue;
    try {
      onEvent(eventName, JSON.parse(data.join('\n')));
    } catch {
      /* non-JSON keepalive */
    }
  }
  return tail;
}

export class AgentHomeError extends Error {
  readonly status: number;
  readonly body: unknown;
  constructor(status: number, message: string, body?: unknown) {
    super(message);
    this.name = 'AgentHomeError';
    this.status = status;
    this.body = body;
  }
}

async function ahJson<T>(path: string, init?: { method?: string; body?: unknown; signal?: AbortSignal }): Promise<T> {
  const res = await fetch(`${AH_BASE}${path}`, {
    method: init?.method ?? (init?.body === undefined ? 'GET' : 'POST'),
    headers: init?.body === undefined ? { accept: 'application/json' } : { accept: 'application/json', 'content-type': 'application/json' },
    body: init?.body === undefined ? undefined : JSON.stringify(init.body),
    signal: init?.signal,
    cache: 'no-store'
  });
  const text = await res.text();
  let j: unknown = undefined;
  try {
    j = text ? JSON.parse(text) : undefined;
  } catch {
    j = text;
  }
  if (!res.ok) {
    const o = (j && typeof j === 'object' ? j : {}) as { error?: unknown; message?: string };
    const msg = typeof o.error === 'string' ? o.error : o.error && typeof o.error === 'object' ? JSON.stringify(o.error) : o.message ?? (typeof j === 'string' ? j.slice(0, 200) : res.statusText);
    throw new AgentHomeError(res.status, `HTTP ${res.status} · ${msg}`, j);
  }
  return j as T;
}

export interface OpenBotResponse {
  bot?: { id: string; name?: string; displayName?: string };
  session?: { id: string; status?: string; title?: string };
  createdSession?: boolean;
}

export const agentHome = {
  openBot: (botId: string, signal?: AbortSignal) => ahJson<OpenBotResponse>(`/api/bots/${encodeURIComponent(botId)}/open`, { method: 'POST', body: {}, signal }),

  session: (sessionId: string, messageLimit = 30, signal?: AbortSignal) =>
    ahJson<Record<string, unknown>>(`/api/sessions/${encodeURIComponent(sessionId)}?messageLimit=${messageLimit}`, { signal }),

  steer: (sessionId: string, text: string) => ahJson<Record<string, unknown>>(`/api/sessions/${encodeURIComponent(sessionId)}/steer`, { method: 'POST', body: { text } }),

  cancel: (sessionId: string) => ahJson<Record<string, unknown>>(`/api/sessions/${encodeURIComponent(sessionId)}/cancel`, { method: 'POST', body: {} }),

  a2uiAction: (sessionId: string, body: { surfaceId: string; name: string; context: Record<string, unknown>; dataModel?: Record<string, unknown>; autoRun?: boolean }) =>
    ahJson<Record<string, unknown>>(`/api/sessions/${encodeURIComponent(sessionId)}/a2ui/action`, { method: 'POST', body }),

  /** POST /api/sessions/:id/stream → SSE (`ready`, `session`, `model`, `result`, `error`). */
  async stream(sessionId: string, body: Record<string, unknown>, onEvent: (event: string, payload: unknown) => void, signal?: AbortSignal): Promise<void> {
    const res = await fetch(`${AH_BASE}/api/sessions/${encodeURIComponent(sessionId)}/stream`, {
      method: 'POST',
      headers: { accept: 'text/event-stream', 'content-type': 'application/json' },
      body: JSON.stringify(body),
      signal,
      cache: 'no-store'
    });
    if (!res.ok || !res.body) {
      const text = await res.text().catch(() => '');
      let msg = text.slice(0, 240);
      try {
        const j = JSON.parse(text) as { error?: unknown };
        if (j.error) msg = typeof j.error === 'string' ? j.error : JSON.stringify(j.error);
      } catch {
        /* plain text */
      }
      throw new AgentHomeError(res.status, `HTTP ${res.status} · ${msg || res.statusText}`);
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (value) buf = feedSseBuffer(buf, value, decoder, onEvent);
    }
    if (buf.trim()) feedSseBuffer(buf, new TextEncoder().encode('\n\n'), decoder, onEvent);
  }
};

// ------------------------------------------------------------------ model stream chunk (daemon-client/types.ts)

export type ModelStreamChunk =
  | { type: 'text_delta'; text: string }
  | { type: 'reasoning_delta'; text: string }
  | { type: 'tool_call_start'; toolCallId: string; name: string }
  | { type: 'tool_call_delta'; toolCallId: string; argumentsFragment: string }
  | { type: 'a2ui_message'; surfaceId: string; envelope: unknown }
  | { type: 'stream_reset'; reason: 'retry' | 'fallback' }
  | { type: 'assistant_saved'; messageId: string }
  | { type: 'done'; stopReason: 'end' | 'tool_use' }
  | { type: string; [k: string]: unknown };

export const CANVAS_OPEN = '<quant_canvas_context>';
export const CANVAS_CLOSE = '</quant_canvas_context>';

/** Remove the injected canvas prefix when replaying user history. */
export function stripCanvasContext(text: string): { text: string; hadContext: boolean } {
  const a = text.indexOf(CANVAS_OPEN);
  const b = text.indexOf(CANVAS_CLOSE);
  if (a === 0 && b > a) return { text: text.slice(b + CANVAS_CLOSE.length).replace(/^\s+/, ''), hadContext: true };
  return { text, hadContext: false };
}
