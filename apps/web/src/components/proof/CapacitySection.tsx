'use client';

import { useState } from 'react';
import { isV3Missing, researchApi } from '@/lib/research-api';
import { useApi } from '@/lib/use-api';
import { normCapacity } from '@/lib/v3';
import { TermTip } from '../glossary/TermTip';
import { Icon } from '../icons';
import { EmptyState, Panel, Tag } from '../ui';
import { CapacityFrontier } from './CapacityFrontier';

/**
 * AUM capacity frontier panel. Uses `capacity_frontier` embedded in the result when present;
 * otherwise offers an explicit on-demand `POST /portfolio/capacity` (never synthesized client-side).
 */
export function CapacitySection({
  embedded,
  experimentId,
  universeId,
  snapshotId,
  apiOnline,
  prd,
  height = 196
}: {
  embedded: unknown;
  experimentId: string;
  universeId: string;
  snapshotId: string | null;
  apiOnline: boolean;
  prd?: string;
  height?: number;
}) {
  const inline = normCapacity(embedded);
  const [asked, setAsked] = useState<string | null>(null);
  const reqKey = `${experimentId}|${universeId}|${snapshotId ?? ''}`;
  const q = useApi(apiOnline && !inline && asked === reqKey ? `capacity:${reqKey}` : null, (s) =>
    researchApi.capacity({ experiment_id: experimentId, universe_id: universeId, snapshot_id: snapshotId }, s)
  );
  const fetched = q.dataKey === `capacity:${reqKey}` ? normCapacity(q.data) : null;
  const data = inline ?? fetched;
  return (
    <Panel
      title={<TermTip id="capacity_frontier">AUM 容量前沿</TermTip>}
      prd={prd}
      sub={data ? `${data.points.length} 档 AUM · ${data.experiment_id}` : undefined}
      tools={inline ? <Tag tone="mute">随结果返回</Tag> : fetched ? <Tag tone="info">/portfolio/capacity</Tag> : null}
      busy={q.loading && !!data}
    >
      {data ? (
        <CapacityFrontier data={data} height={height} />
      ) : q.error ? (
        <EmptyState
          height={height}
          title={isV3Missing(q.error) ? 'CAPACITY API · 未上线' : '容量前沿计算失败'}
          detail={isV3Missing(q.error) ? '后端未提供 POST /portfolio/capacity' : q.error.message}
          error={!isV3Missing(q.error)}
        />
      ) : (
        <div className="empty" style={{ height }}>
          <b>{q.loading ? '计算容量前沿…' : 'CAPACITY FRONTIER · 未随结果返回'}</b>
          <span>√冲击模型 · max POV 5% · 多档 AUM 下净超额与冲击成本</span>
          <button type="button" className="btn sm" style={{ marginTop: 6 }} disabled={!apiOnline || q.loading} onClick={() => setAsked(reqKey)}>
            <Icon name="play" size={9} />
            按需计算
          </button>
        </div>
      )}
    </Panel>
  );
}
