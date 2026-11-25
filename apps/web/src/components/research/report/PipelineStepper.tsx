'use client';

import { useEffect, useRef, useState } from 'react';
import type { ResearchStudyDetail, StudyEventRecord } from '@/lib/types';
import type { StreamState } from '@/lib/use-event-stream';
import { TermTip } from '../../glossary/TermTip';
import { Icon } from '../../icons';
import { Panel } from '../../ui';
import { StreamChip } from '../ClusterStrip';

export const STAGES = [
  { key: 'HYPOTHESIS', code: 'HYP', label: '假设' },
  { key: 'MATH', code: 'MATH', label: '数学推演' },
  { key: 'FORMAL_VERIFY', code: 'PROOF', label: '形式化验证' },
  { key: 'BACKTEST_CPCV', code: 'CPCV', label: '回测 · CPCV' },
  { key: 'DIAGNOSIS', code: 'DIAG', label: '诊断' },
  { key: 'PUBLISHED', code: 'PUB', label: '发布' }
] as const;

type StepState = 'done' | 'run' | 'fail' | 'wait';

export function stageIndex(stage: string | undefined): number {
  const s = String(stage ?? '').toUpperCase();
  if (/PUBLISH|COMPLETE|MEMO|FINAL/.test(s)) return 5;
  if (/DIAGNOS/.test(s)) return 4;
  if (/BACKTEST|CPCV|EVAL|BARRA|CAPACITY|REGIME|PORTFOLIO/.test(s)) return 3;
  if (/FORMAL|VERIF|PROOF/.test(s)) return 2;
  if (/MATH|DEVELOP/.test(s)) return 1;
  return 0;
}

const mmss = (a?: unknown, b?: unknown) => {
  const t0 = typeof a === 'string' ? Date.parse(a) : NaN;
  const t1 = typeof b === 'string' ? Date.parse(b) : NaN;
  if (!Number.isFinite(t0) || !Number.isFinite(t1) || t1 < t0) return '';
  const s = Math.round((t1 - t0) / 1000);
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
};

function steps(d: ResearchStudyDetail, liveStage?: string, livePct?: number): { state: StepState; meta: string }[] {
  const status = String(d.status).toUpperCase();
  const explicit = new Map<number, Record<string, unknown>>();
  for (const st of d.stages ?? []) explicit.set(stageIndex(String(st.stage ?? st.name ?? '')), st);
  const cur = stageIndex(liveStage ?? d.stage);
  const terminalOk = status === 'COMPLETED';
  const terminalBad = status === 'FALSIFIED' || status === 'FAILED';
  return STAGES.map((_, i) => {
    const e = explicit.get(i);
    const es = String(e?.status ?? '').toLowerCase();
    const pct = typeof e?.progress_pct === 'number' ? e.progress_pct : undefined;
    let state: StepState;
    if (es === 'done' || es === 'completed' || es === 'passed') state = 'done';
    else if (es === 'failed' || es === 'fail' || es === 'falsified') state = 'fail';
    else if (es === 'running') state = terminalOk ? 'done' : terminalBad && i === cur ? 'fail' : 'run';
    else if (terminalOk) state = 'done';
    else if (i < cur) state = 'done';
    else if (i === cur) state = terminalBad ? 'fail' : status === 'DRAFT' ? 'wait' : 'run';
    else state = 'wait';
    const dur = mmss(e?.started_at, e?.ended_at ?? e?.finished_at);
    const meta = state === 'done' ? `DONE${dur ? ` ${dur}` : ''}` : state === 'run' ? `RUN${i === cur && livePct !== undefined ? ` ${livePct}%` : pct !== undefined ? ` ${pct}%` : ''}` : state === 'fail' ? (status === 'FALSIFIED' ? 'FALSIFIED' : 'FAILED') : 'WAIT';
    return { state, meta };
  });
}

const hms = (iso: string) => (iso && iso.length >= 19 ? iso.slice(11, 19) : iso);

/** ① 6-stage pipeline with live SSE progress and a tail-following event log. */
export function PipelineStepper({ detail, events, stream, retries }: { detail: ResearchStudyDetail; events: StudyEventRecord[]; stream: StreamState; retries: number }) {
  const [open, setOpen] = useState(false);
  const [follow, setFollow] = useState(true);
  const logRef = useRef<HTMLDivElement>(null);
  const last = events[events.length - 1];
  const s = steps(detail, last?.stage, last?.progress_pct);

  useEffect(() => {
    const el = logRef.current;
    if (el && follow) el.scrollTop = el.scrollHeight;
  }, [events.length, open, follow]);

  return (
    <Panel
      title={
        <TermTip id="study_lineage" context={{ study: detail.study_id, status: detail.status, stage: detail.stage }}>
          ① 执行流水线
        </TermTip>
      }
      sub={`${detail.dispatch_mode} · ${detail.target_cluster}${detail.job_id ? ` · ${detail.job_id}` : ''}`}
      tools={<StreamChip state={stream} retries={retries} />}
      flush
    >
      <div className="stp" role="list" aria-label="研究流水线">
        {STAGES.map((st, i) => (
          <div key={st.key} role="listitem" className={`stp-i ${s[i].state}`} title={st.key}>
            <div className="sn">
              <i>{s[i].state === 'done' ? <Icon name="check" size={9} sw={2.4} /> : s[i].state === 'fail' ? <Icon name="x" size={9} sw={2.4} /> : s[i].state === 'run' ? <span className="spin" style={{ width: 8, height: 8 }} /> : i + 1}</i>
              {st.code}
            </div>
            <b>{st.label}</b>
            <small>{s[i].meta}</small>
            <span className="sb" />
          </div>
        ))}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 9px', borderTop: '1px solid var(--line)', fontSize: 10.5 }}>
        <button type="button" className="btn sm" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          <Icon name={open ? 'down' : 'chev'} size={10} sw={2} />
          日志 {events.length}
        </button>
        <span className="mono c-3" style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {last ? `${hms(last.created_at)} · ${last.stage} · ${last.message}` : '暂无事件'}
        </span>
        {open && !follow ? (
          <button type="button" className="btn sm" onClick={() => setFollow(true)}>
            回到最新
          </button>
        ) : null}
      </div>
      {open ? (
        <div
          className="slog"
          ref={logRef}
          onScroll={(e) => {
            const el = e.currentTarget;
            setFollow(el.scrollHeight - el.scrollTop - el.clientHeight < 8);
          }}
        >
          {events.length ? (
            events.map((ev) => (
              <div key={`${ev.seq}-${ev.event_id}`} title={ev.message}>
                <span>{hms(ev.created_at)}</span>
                <span>
                  {ev.event_type} · {ev.progress_pct}%
                </span>
                <span>{ev.message}</span>
              </div>
            ))
          ) : (
            <div>
              <span>—</span>
              <span>—</span>
              <span>尚无事件</span>
            </div>
          )}
        </div>
      ) : null}
    </Panel>
  );
}
