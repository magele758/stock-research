import { NextResponse, type NextRequest } from 'next/server';
import { daemonAuthHeader, invalidateDaemonCache, proxyTarget } from '@/lib/server/agent-home';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/**
 * Same-origin runtime proxy `/api/agent-home/*` → Agent Home daemon `/*`.
 * Mirrors ppeng-agent-core `apps/web-console/middleware.ts`:
 *  - Bearer `RAW_AGENT_AUTH_TOKEN` is injected here only; the browser never sees it.
 *  - Non-GET bodies are buffered (`arrayBuffer()`) before forwarding — undici turns a 401
 *    on a stream-bodied request into a network error that would surface as a fake 502.
 *  - `text/event-stream` responses are piped through unbuffered.
 */

const HOP_BY_HOP = new Set([
  'host',
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'proxy-connection',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
  'content-length',
  'accept-encoding',
  'cookie',
  'authorization',
  'origin',
  'referer'
]);

const STRIP_RESPONSE = new Set(['content-encoding', 'content-length', 'transfer-encoding', 'connection', 'keep-alive', 'set-cookie']);

type Ctx = { params: Promise<{ path: string[] }> };

/** Quant production sessions must never be switched to `permissionMode: 'bypass'` (03-agent-home-integration §5.2). */
function forbidsBypass(method: string, path: string, body: ArrayBuffer | undefined): boolean {
  if (!body || body.byteLength === 0 || !/^(PATCH|POST|PUT)$/.test(method)) return false;
  if (!/^api\/sessions\//.test(path) && !/^api\/bots\//.test(path)) return false;
  try {
    const text = new TextDecoder().decode(body);
    if (!text.includes('bypass')) return false;
    const j = JSON.parse(text) as Record<string, unknown>;
    const meta = (j.metadata ?? {}) as Record<string, unknown>;
    return j.permissionMode === 'bypass' || meta.permissionMode === 'bypass' || j.confirmBypass === true;
  } catch {
    return false;
  }
}

async function forward(request: NextRequest, ctx: Ctx): Promise<Response> {
  const { path } = await ctx.params;
  const subPath = (path ?? []).map(encodeURIComponent).join('/');
  const { base, online } = await proxyTarget();
  const target = `${base}/${subPath}${request.nextUrl.search}`;

  const headers = new Headers();
  request.headers.forEach((v, k) => {
    if (!HOP_BY_HOP.has(k.toLowerCase())) headers.set(k, v);
  });
  const auth = daemonAuthHeader();
  if (auth) headers.set('authorization', auth);
  headers.set('x-forwarded-host', request.nextUrl.host);
  headers.set('x-forwarded-proto', request.nextUrl.protocol.replace(/:$/, '') || 'http');
  headers.set('x-quantlab-proxy', '1');

  let body: ArrayBuffer | undefined;
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    body = await request.arrayBuffer();
    if (forbidsBypass(request.method, subPath, body)) {
      return NextResponse.json(
        { error: 'permissionMode=bypass is blocked by QuantLab proxy (Quant sessions require approvals)', code: 'FORBIDDEN' },
        { status: 403 }
      );
    }
  }

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: body && body.byteLength > 0 ? body : undefined,
      redirect: 'manual',
      cache: 'no-store',
      signal: request.signal
    });
  } catch (err) {
    invalidateDaemonCache();
    return NextResponse.json(
      {
        error: `agent-home daemon proxy failed: ${err instanceof Error ? err.message : String(err)}`,
        online,
        triedUrl: base
      },
      { status: 502 }
    );
  }

  const outHeaders = new Headers();
  upstream.headers.forEach((v, k) => {
    if (!STRIP_RESPONSE.has(k.toLowerCase())) outHeaders.set(k, v);
  });
  const isSse = (upstream.headers.get('content-type') ?? '').includes('text/event-stream');
  if (isSse) {
    outHeaders.set('content-type', 'text/event-stream; charset=utf-8');
    outHeaders.set('cache-control', 'no-cache, no-transform');
    outHeaders.set('x-accel-buffering', 'no');
  }
  return new Response(upstream.body, { status: upstream.status, statusText: upstream.statusText, headers: outHeaders });
}

export const GET = forward;
export const POST = forward;
export const PUT = forward;
export const PATCH = forward;
export const DELETE = forward;
