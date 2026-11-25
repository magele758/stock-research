import { NextResponse } from 'next/server';
import { resolveDaemon } from '@/lib/server/agent-home';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

/** Liveness of the local Agent Home daemon. Never 500s: offline → `{ online: false, triedUrl }`. */
export async function GET(request: Request) {
  const force = new URL(request.url).searchParams.has('force');
  try {
    const probe = await resolveDaemon(force);
    return NextResponse.json(probe, { headers: { 'cache-control': 'no-store' } });
  } catch (err) {
    return NextResponse.json(
      { online: false, triedUrl: 'unresolved', tried: [], authConfigured: false, error: err instanceof Error ? err.message : String(err) },
      { headers: { 'cache-control': 'no-store' } }
    );
  }
}
