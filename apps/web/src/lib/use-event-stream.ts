'use client';

import { useEffect, useRef, useState } from 'react';

export type StreamState = 'idle' | 'connecting' | 'live' | 'reconnecting' | 'polling' | 'closed';

/** Event names the research / job SSE endpoints may emit (`event: <name>\ndata: <json>`). */
const EVENT_NAMES = [
  'message', 'state_change', 'progress', 'proof_check', 'metric_snapshot', 'warning', 'terminal', 'log', 'stage', 'artifact', 'note',
  'queued', 'running', 'completed', 'failed', 'falsified', 'cancelled', 'timeout', 'done', 'error'
];
const MAX_RETRIES = 3;

interface Opts {
  url: string | null;
  enabled: boolean;
  onEvent: (name: string, data: unknown) => void;
  /** Called every `pollMs` once SSE has failed `MAX_RETRIES` times (or EventSource is unavailable). */
  poll?: () => Promise<unknown> | void;
  pollMs?: number;
}

/**
 * EventSource with bounded exponential reconnect, then a polling fallback.
 * `enabled=false` (e.g. study reached a terminal status) closes everything.
 */
export function useEventStream({ url, enabled, onEvent, poll, pollMs = 2000 }: Opts): { state: StreamState; retries: number } {
  const [state, setState] = useState<StreamState>('idle');
  const [retries, setRetries] = useState(0);
  const onEventRef = useRef(onEvent);
  const pollRef = useRef(poll);
  onEventRef.current = onEvent;
  pollRef.current = poll;

  useEffect(() => {
    if (!enabled || !url) {
      setState((s) => (s === 'idle' ? s : 'closed'));
      return;
    }
    let es: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let interval: ReturnType<typeof setInterval> | undefined;
    let fails = 0;
    let disposed = false;

    const startPolling = () => {
      setState('polling');
      if (!pollRef.current) return;
      const tick = () => void Promise.resolve(pollRef.current?.()).catch(() => undefined);
      tick();
      interval = setInterval(tick, pollMs);
    };

    const connect = () => {
      if (disposed) return;
      if (typeof EventSource === 'undefined') return startPolling();
      setState(fails ? 'reconnecting' : 'connecting');
      es = new EventSource(url);
      es.onopen = () => {
        fails = 0;
        setRetries(0);
        setState('live');
      };
      const handler = (name: string) => (ev: MessageEvent) => {
        let data: unknown = ev.data;
        if (typeof ev.data === 'string' && ev.data) {
          try {
            data = JSON.parse(ev.data);
          } catch {
            /* keep text payload */
          }
        }
        onEventRef.current(name, data);
      };
      for (const n of EVENT_NAMES) if (n !== 'error') es.addEventListener(n, handler(n) as EventListener);
      es.addEventListener('error', ((ev: Event) => {
        if (ev instanceof MessageEvent && ev.data) {
          handler('error')(ev);
          return;
        }
        es?.close();
        es = null;
        if (disposed) return;
        fails += 1;
        setRetries(fails);
        if (fails >= MAX_RETRIES) return startPolling();
        setState('reconnecting');
        timer = setTimeout(connect, 800 * 2 ** fails);
      }) as EventListener);
    };

    connect();
    return () => {
      disposed = true;
      es?.close();
      if (timer) clearTimeout(timer);
      if (interval) clearInterval(interval);
    };
  }, [url, enabled, pollMs]);

  return { state, retries };
}
