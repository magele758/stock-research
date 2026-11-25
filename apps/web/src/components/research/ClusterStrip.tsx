'use client';

import { fmtTs } from '@/lib/format';
import type { StreamState } from '@/lib/use-event-stream';
import type { ClusterNodeStatus } from '@/lib/types';
import { TermTip } from '../glossary/TermTip';
import { Icon } from '../icons';
import { EmptyState } from '../ui';

const STREAM: Record<StreamState, { label: string; cls: string }> = {
  idle: { label: 'SSE IDLE', cls: 'c-3' },
  connecting: { label: 'SSE CONNECTING', cls: 'c-warn' },
  live: { label: 'SSE LIVE', cls: 'c-pass' },
  reconnecting: { label: 'SSE RECONNECTING', cls: 'c-warn' },
  polling: { label: 'POLLING 2s', cls: 'c-warn' },
  closed: { label: 'SSE CLOSED', cls: 'c-3' }
};

export function StreamChip({ state, retries }: { state: StreamState; retries?: number }) {
  const s = STREAM[state];
  return (
    <span className={`st ${s.cls}`} title={retries ? `重连 ${retries} 次` : undefined}>
      <i style={{ background: 'currentColor' }} />
      {s.label}
    </span>
  );
}

/** engine_sandbox / ubuntu1_cluster / herdr_agent health (display names come from the backend). */
export function ClusterStrip({ clusters, error, loading, onRefresh }: { clusters: ClusterNodeStatus[] | undefined; error?: Error; loading?: boolean; onRefresh: () => void }) {
  if (!clusters?.length) {
    return (
      <EmptyState
        height={54}
        title={error ? 'CLUSTERS UNAVAILABLE' : loading ? 'LOADING CLUSTERS' : 'NO CLUSTERS'}
        detail={error ? `/research/clusters · ${error.message}` : '等待 /research/clusters'}
        error={Boolean(error)}
      />
    );
  }
  return (
    <div className="cstrip" style={{ gridTemplateColumns: `repeat(${Math.min(clusters.length, 4)}, minmax(0, 1fr))` }}>
      {clusters.map((c, i) => {
        const total = Math.max(0, c.slots_total);
        return (
          <div key={c.cluster_id} className={`cnode ${c.status}`} title={`${c.adapter} · ${c.agent_cli}${c.detail ? `\n${c.detail}` : ''}${c.capabilities.length ? `\n${c.capabilities.join(' / ')}` : ''}`}>
            <div className="ch">
              <Icon name={c.cluster_id === 'engine_sandbox' ? 'cluster' : 'hex'} size={11} />
              <span>{c.display_name || c.cluster_id}</span>
              <span className={`st ${c.status === 'online' ? 'c-pass' : c.status === 'degraded' ? 'c-warn' : 'c-up'}`}>
                <i style={{ background: 'currentColor' }} />
                {c.status.toUpperCase()}
              </span>
              {i === 0 ? (
                <button type="button" className="c-3" onClick={onRefresh} aria-label="刷新集群状态" style={{ display: 'grid', placeItems: 'center' }}>
                  {loading ? <i className="spin" /> : <Icon name="sync" size={10} sw={2} />}
                </button>
              ) : null}
            </div>
            <div className="cm">
              <span>
                <TermTip id="cluster_dispatch" value={c.slots_busy} display={`${c.slots_busy}/${total}`} context={{ cluster: c.cluster_id, queue: c.queue_depth }}>
                  槽位
                </TermTip>{' '}
                <b>
                  {c.slots_busy}/{total}
                </b>
              </span>
              <span>
                队列 <b>Q{c.queue_depth}</b>
              </span>
              <span style={{ marginLeft: 'auto' }}>{c.last_heartbeat ? fmtTs(c.last_heartbeat).slice(-8) : '—'}</span>
            </div>
            <div className="slots" aria-hidden>
              {Array.from({ length: Math.min(total, 16) }, (_, k) => (
                <i key={k} className={k < c.slots_busy ? 'on' : ''} />
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
