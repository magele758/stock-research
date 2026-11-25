'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { copyText, downloadBlob, fmtTs, shortHash } from '@/lib/format';
import { researchApi } from '@/lib/research-api';
import { buildMemo } from '@/lib/research-memo';
import type { ResearchStudyDetail } from '@/lib/types';
import { useWorkbench } from '@/lib/workbench';
import { TermTip } from '../../glossary/TermTip';
import { Icon } from '../../icons';
import { Panel } from '../../ui';

function Hash({ v }: { v: string | null | undefined }) {
  const [ok, setOk] = useState(false);
  if (!v) return <b>—</b>;
  return (
    <b style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }} title={v}>
      {shortHash(v, 8)}
      <button
        type="button"
        className="c-3"
        aria-label="复制"
        onClick={async () => {
          if (await copyText(v)) {
            setOk(true);
            setTimeout(() => setOk(false), 1200);
          }
        }}
      >
        <Icon name={ok ? 'check' : 'copy'} size={10} sw={2} />
      </button>
    </b>
  );
}

/** ⑥ Reproducibility fingerprint + IC Memo export + snapshot lock. */
export function ArtifactCard({ detail }: { detail: ResearchStudyDetail }) {
  const { setSnapshotLock, snapshotLock, setFactorId } = useWorkbench();
  const router = useRouter();
  const [state, setState] = useState<string | null>(null);
  const locked = snapshotLock.locked && snapshotLock.snapshotId === detail.snapshot_id;

  const exportMemo = async () => {
    setState('导出中…');
    const name = `IC-Memo-${detail.study_id}-${detail.snapshot_id}.md`;
    try {
      const md = await researchApi.memo(detail.study_id);
      downloadBlob(name, md, 'text/markdown;charset=utf-8');
      setState('已导出（后端 memo）');
    } catch {
      downloadBlob(name, buildMemo(detail), 'text/markdown;charset=utf-8');
      setState('后端 memo 不可用 · 已由报告对象生成');
    }
  };

  const rows: [string, React.ReactNode][] = [
    ['study_id', <b key="s">{detail.study_id}</b>],
    ['parent_study_id', <b key="p">{detail.parent_study_id ?? '—'}</b>],
    ['family_id', <b key="f">{detail.family_id}</b>],
    ['snapshot_id', <b key="sn">{detail.snapshot_id}</b>],
    [
      'fact_fingerprint',
      <Hash key="fp" v={detail.fact_fingerprint} />
    ],
    ['proof_sha256', <Hash key="ps" v={detail.formal_proof?.proof_sha256} />],
    ['job_id', <b key="j">{detail.job_id ?? '—'}</b>],
    ['cluster', <b key="c">{`${detail.dispatch_mode} · ${detail.target_cluster}`}</b>],
    ['artifact_uri', <b key="a">{detail.artifact_uri ?? '—'}</b>],
    ['created / completed', <b key="t">{`${fmtTs(detail.created_at)} / ${detail.completed_at ? fmtTs(detail.completed_at) : '—'}`}</b>]
  ];

  return (
    <Panel title={<TermTip id="fact_fingerprint">⑥ Artifact 与复现指纹</TermTip>} sub={state ?? undefined}>
      <div className="sigf">
        {rows.map(([k, v]) => (
          <div key={k}>
            <span>{k}</span>
            {v}
          </div>
        ))}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
        <button type="button" className="btn sm pri" onClick={() => void exportMemo()}>
          <Icon name="download" size={10} sw={2} />
          导出{' '}
          <TermTip id="ic_memo" as="span">
            IC Memo
          </TermTip>{' '}
          (Markdown)
        </button>
        <button type="button" className={`btn sm ${locked ? '' : ''}`} disabled={locked || !detail.snapshot_id} onClick={() => setSnapshotLock({ locked: true, snapshotId: detail.snapshot_id })}>
          <Icon name="lock" size={10} sw={2} />
          {locked ? '已锁定该快照' : '锁定该快照'}
        </button>
        {detail.factor_id ? (
          <button
            type="button"
            className="btn sm"
            onClick={() => {
              setFactorId(detail.factor_id as string);
              router.push('/alpha');
            }}
          >
            <Icon name="alpha" size={10} sw={2} />
            在 /alpha 打开因子
          </button>
        ) : null}
      </div>
    </Panel>
  );
}
