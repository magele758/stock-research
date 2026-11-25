'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { CapacityFrontier } from '@/components/proof/CapacityFrontier';
import { BaselinePanel } from '@/components/research/BaselinePanel';
import { ClusterStrip } from '@/components/research/ClusterStrip';
import { ArtifactCard } from '@/components/research/report/ArtifactCard';
import { DiagnosisCards } from '@/components/research/report/DiagnosisCards';
import { DimensionMatrix } from '@/components/research/report/DimensionMatrix';
import { MathCard } from '@/components/research/report/MathCard';
import { PipelineStepper } from '@/components/research/report/PipelineStepper';
import { ProofLedger } from '@/components/research/report/ProofLedger';
import { StudyBadge } from '@/components/research/shared';
import { StudyTree } from '@/components/research/StudyTree';
import { TombstoneList } from '@/components/research/TombstoneList';
import { TermTip } from '@/components/glossary/TermTip';
import { Icon } from '@/components/icons';
import { ApiNotice, EmptyState, Kpi, Panel, Seg, Tag, ViewHeader } from '@/components/ui';
import { fx } from '@/lib/format';
import { openStudyDrawer } from '@/lib/glossary/store';
import { isStudyRunning, isStudyTerminal, isV3Missing, researchApi } from '@/lib/research-api';
import type { ResearchStudyDetail, StudyEventRecord } from '@/lib/types';
import { useApi } from '@/lib/use-api';
import { useEventStream } from '@/lib/use-event-stream';
import { usePublishMetrics, useWorkbench } from '@/lib/workbench';

type LeftTab = 'tree' | 'tomb' | 'base';
const CLUSTER_POLL_MS = 10_000;

const n = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const f = (v: number | null, d = 2, suf = '') => (v === null ? '—' : `${fx(v, d)}${suf}`);

function isEvent(x: unknown, studyId: string): x is StudyEventRecord {
  if (!x || typeof x !== 'object') return false;
  const e = x as Partial<StudyEventRecord>;
  return typeof e.seq === 'number' && (e.study_id === undefined || e.study_id === studyId);
}

function mergeEvents(a: StudyEventRecord[], b: StudyEventRecord[]): StudyEventRecord[] {
  const m = new Map<number, StudyEventRecord>();
  for (const e of a) m.set(e.seq, e);
  for (const e of b) m.set(e.seq, e);
  return [...m.values()].sort((x, y) => x.seq - y.seq);
}

/** Headline KPIs of the selected study, read only from `metrics_summary` / summary fields. */
function StudyHero({ d }: { d: ResearchStudyDetail }) {
  const ms = d.metrics_summary ?? {};
  const pick = (k: string, fallback?: number | null) => n(ms[k]) ?? (fallback ?? null);
  const items: { id: string; label: string; v: number | null; text: string }[] = [
    { id: 'sharpe_is_oos', label: 'OOS Sharpe', v: pick('oos_sharpe', d.oos_sharpe), text: f(pick('oos_sharpe', d.oos_sharpe)) },
    { id: 'dsr', label: 'DSR', v: pick('dsr', d.dsr), text: f(pick('dsr', d.dsr), 3) },
    { id: 'pbo', label: 'PBO', v: pick('pbo', d.pbo), text: f(pick('pbo', d.pbo), 3) },
    { id: 'rank_ic', label: 'Rank IC', v: pick('rank_ic_mean', d.rank_ic_mean), text: f(pick('rank_ic_mean', d.rank_ic_mean), 4) },
    { id: 'icir', label: 'ICIR', v: pick('icir_ann'), text: f(pick('icir_ann')) },
    { id: 'gross_vs_net', label: '净超额(年化)', v: pick('net_excess_ann_pct', d.net_excess_ann_pct), text: f(pick('net_excess_ann_pct', d.net_excess_ann_pct), 1, '%') },
    { id: 'bh_fdr', label: 'BH-FDR q', v: pick('bh_fdr_q_value'), text: f(pick('bh_fdr_q_value'), 3) },
    { id: 'trial_count_m', label: '试验次数 M', v: pick('trial_count_m'), text: f(pick('trial_count_m'), 0) }
  ];
  return (
    <div className="kpis k4" style={{ gridAutoRows: 'auto' }}>
      {items.map((it) => (
        <Kpi key={it.id} label={<TermTip id={it.id} value={it.v} display={it.text}>{it.label}</TermTip>} value={it.text} />
      ))}
    </div>
  );
}

export function ResearchView() {
  const wb = useWorkbench();
  const { apiOnline, research, refreshResearch, studyId, setStudyId, universe, snapshotId, dock } = wb;
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const urlStudy = params.get('study');
  const [tab, setTab] = useState<LeftTab>('tree');
  const [collapsed, setCollapsed] = useState(false);
  const [proofFocus, setProofFocus] = useState<string | null>(null);

  // URL <-> workbench selection
  useEffect(() => {
    if (urlStudy && urlStudy !== studyId) setStudyId(urlStudy);
  }, [urlStudy, studyId, setStudyId]);
  const select = useCallback(
    (id: string) => {
      setStudyId(id);
      router.replace(`${pathname}?study=${encodeURIComponent(id)}`, { scroll: false });
    },
    [pathname, router, setStudyId]
  );
  const studies = research.studies;
  const selected = urlStudy ?? studyId ?? studies[0]?.study_id ?? null;

  // Cluster strip (10s)
  const [clusterTick, setClusterTick] = useState(0);
  useEffect(() => {
    if (!apiOnline) return;
    const t = setInterval(() => setClusterTick((x) => x + 1), CLUSTER_POLL_MS);
    return () => clearInterval(t);
  }, [apiOnline]);
  const clusters = useApi(apiOnline ? `clusters:${clusterTick}` : null, (s) => researchApi.clusters(s));
  const tombs = useApi(apiOnline ? 'tombstones' : null, (s) => researchApi.tombstones(s));
  const baselines = useApi(apiOnline ? `baselines:${universe}:${snapshotId ?? ''}` : null, (s) => researchApi.baselines(snapshotId, universe, s));

  // Selected study detail + live events
  const detailQ = useApi(apiOnline && selected ? `study:${selected}` : null, (s) => researchApi.study(selected as string, s));
  const detail = detailQ.dataKey === `study:${selected}` ? detailQ.data : undefined;
  const [live, setLive] = useState<StudyEventRecord[]>([]);
  useEffect(() => setLive([]), [selected]);
  const events = useMemo(() => mergeEvents(detail?.events ?? [], live), [detail?.events, live]);
  const reloadTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const scheduleReload = useCallback(() => {
    clearTimeout(reloadTimer.current);
    reloadTimer.current = setTimeout(() => detailQ.reload(), 600);
  }, [detailQ]);
  useEffect(() => () => clearTimeout(reloadTimer.current), []);
  const streaming = Boolean(detail && selected && !isStudyTerminal(detail.status));
  const stream = useEventStream({
    url: selected ? researchApi.eventsUrl(selected) : null,
    enabled: apiOnline && streaming,
    onEvent: (name, data) => {
      if (selected && isEvent(data, selected)) setLive((prev) => mergeEvents(prev, [data]));
      if (name === 'state_change' || name === 'proof_check' || name === 'metric_snapshot') scheduleReload();
      if (name === 'terminal' || name === 'completed' || name === 'failed' || name === 'falsified' || name === 'timeout') {
        scheduleReload();
        refreshResearch();
      }
    },
    poll: () => detailQ.reload(),
    pollMs: 3000
  });

  usePublishMetrics(
    'research',
    detail
      ? {
          study: detail.study_id,
          status: detail.status,
          verdict: detail.verdict,
          stage: detail.stage,
          proofs: `${detail.formal_proof?.proofs_passed ?? detail.proofs_passed}/${detail.formal_proof?.proofs_total ?? detail.proofs_total}`,
          oos_sharpe: n(detail.metrics_summary?.oos_sharpe) ?? detail.oos_sharpe ?? null,
          dsr: n(detail.metrics_summary?.dsr) ?? detail.dsr ?? null,
          pbo: n(detail.metrics_summary?.pbo) ?? detail.pbo ?? null,
          diagnosis: detail.diagnosis.length
        }
      : null
  );

  const missing = research.available === false || isV3Missing(detailQ.error);
  const running = studies.filter((s) => isStudyRunning(s.status)).length;
  const newStudy = () => openStudyDrawer({ universe_id: universe, source: '/research' });
  const botStart = () => dock.dispatchStudy({ universe_id: universe, source: '/research' });

  const left = (
    <div className="rcol" style={{ position: 'sticky', top: 0, gap: 6 }}>
      <div style={{ display: 'flex', gap: 6, alignItems: 'center', minWidth: 0 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
        <Seg
          value={tab}
          onChange={setTab}
          stretch
          options={[
            { key: 'tree', label: `课题 ${studies.length}` },
            { key: 'tomb', label: <TermTip id="falsified_tombstone" as="span">{`墓碑 ${tombs.data?.length ?? 0}`}</TermTip> },
            { key: 'base', label: '基线' }
          ]}
        />
        </div>
        <button type="button" className="btn sm" aria-label="收起左栏" title="收起左栏" onClick={() => setCollapsed(true)}>
          <Icon name="chev" size={10} sw={2} style={{ transform: 'rotate(180deg)' }} />
        </button>
      </div>
      <div style={{ maxHeight: 'calc(100vh - 230px)', overflow: 'auto', minHeight: 0 }}>
        {tab === 'tree' ? (
          studies.length ? (
            <StudyTree studies={studies} selected={selected} onSelect={select} />
          ) : (
            <EmptyState height={140} title={missing ? 'V3 研究接口未上线' : 'NO STUDIES'} detail={missing ? '后端未提供 /research/studies' : '点击「＋ 新建研究」派发第一个课题'} />
          )
        ) : tab === 'tomb' ? (
          tombs.data?.length ? <TombstoneList items={tombs.data} onOpen={select} /> : <EmptyState height={140} title={tombs.error ? '墓碑库不可用' : 'NO TOMBSTONES'} detail={tombs.error ? tombs.error.message : '尚无被证伪的研究'} error={Boolean(tombs.error) && !isV3Missing(tombs.error)} />
        ) : baselines.data?.length ? (
          <BaselinePanel items={baselines.data} />
        ) : (
          <EmptyState height={140} title={baselines.error ? '基线不可用' : 'NO BASELINES'} detail={baselines.error ? baselines.error.message : `${universe} · 无维度基线`} />
        )}
      </div>
    </div>
  );

  return (
    <>
      <ViewHeader
        path="/research"
        prds={['Q7.3', 'MATH']}
        title="研究工作台 · 集群派发"
        sub={
          <>
            <TermTip id="cluster_dispatch">集群派发</TermTip> · <TermTip id="ast_causal_proof">形式化证明</TermTip> · <TermTip id="study_lineage">课题血缘</TermTip>
            {running ? <> · <b className="c-sig">{running} RUN</b></> : null}
          </>
        }
        actions={
          <>
            <button type="button" className="btn" onClick={botStart}>
              <Icon name="hex" size={12} />
              让 Bot 发起
            </button>
            <button type="button" className="btn pri" onClick={newStudy} disabled={!apiOnline || missing}>
              <Icon name="research" size={12} />
              ＋ 新建研究
            </button>
          </>
        }
      />
      <ApiNotice error={detailQ.error && !isV3Missing(detailQ.error) ? detailQ.error : undefined} what="研究报告" />
      <div style={{ marginBottom: 10 }}>
        <ClusterStrip clusters={clusters.data} error={clusters.error} loading={clusters.loading} onRefresh={clusters.reload} />
      </div>
      <div className={`rsg ${collapsed ? 'collapsed' : ''}`}>
        {collapsed ? (
          <button type="button" className="rside-c" onClick={() => setCollapsed(false)} aria-label="展开左栏">
            <Icon name="tree" size={11} />
            课题 {studies.length} · 墓碑 {tombs.data?.length ?? 0}
          </button>
        ) : (
          left
        )}
        <div className="rcol">
          {!apiOnline ? (
            <Panel title="研究报告">
              <EmptyState height={220} title="QUANT API OFFLINE" detail="研究报告需要后端 /research/studies/{id}；离线时不展示任何推断数值" />
            </Panel>
          ) : missing ? (
            <Panel title="研究报告">
              <EmptyState height={220} title="V3 RESEARCH API · 未上线" detail="后端未提供 /research/* 路由（404/405/501）；部署 V3 quant-api 后自动启用" />
            </Panel>
          ) : !selected ? (
            <Panel title="研究报告">
              <EmptyState height={220} title="未选择课题" detail="从左侧课题树选择，或点击「＋ 新建研究」派发到集群" />
            </Panel>
          ) : !detail ? (
            <Panel title="研究报告">
              <EmptyState height={220} title={detailQ.error ? '报告加载失败' : '加载研究报告…'} detail={detailQ.error?.message ?? selected} error={Boolean(detailQ.error)} />
            </Panel>
          ) : (
            <>
              <div className="gs" style={{ flexWrap: 'wrap', gap: 8 }}>
                <StudyBadge s={detail} />
                <b style={{ font: '600 12.5px var(--f-sans)', color: 'var(--tx)' }}>{detail.title}</b>
                <span className="mono c-3">{detail.study_id}</span>
                {detail.parent_study_id ? (
                  <button type="button" className="tag" onClick={() => select(detail.parent_study_id as string)} title="打开父课题">
                    <Icon name="branch" size={9} sw={2} /> {detail.parent_study_id}
                  </button>
                ) : null}
                <span style={{ flex: 1 }} />
                {detail.tombstone_warnings.length ? <Tag tone="warn">{detail.tombstone_warnings.length} 墓碑相似告警</Tag> : null}
                <Tag tone="mute">{detail.universe_id}</Tag>
              </div>
              <StudyHero d={detail} />
              <PipelineStepper detail={detail} events={events} stream={streaming ? stream.state : 'closed'} retries={stream.retries} />
              <MathCard detail={detail} />
              <ProofLedger detail={detail} highlight={proofFocus} />
              <DimensionMatrix slices={detail.dimension_analysis?.slices} detail={detail} consistency={detail.dimension_analysis?.regime_consistency_score ?? null} />
              {detail.capacity_frontier ? (
                <Panel title={<TermTip id="capacity_frontier">容量前沿</TermTip>} sub={`${detail.capacity_frontier.experiment_id}`}>
                  <CapacityFrontier data={detail.capacity_frontier} />
                </Panel>
              ) : null}
              <DiagnosisCards
                detail={detail}
                onEvidence={(kind, ref) => {
                  if (kind !== 'proof' || !ref) return;
                  setProofFocus(ref);
                  document.getElementById(`proof-${ref}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
                }}
              />
              <ArtifactCard detail={detail} />
              {detail.lineage.length > 1 ? (
                <Panel title={<TermTip id="study_lineage">课题血缘</TermTip>} sub={`${detail.lineage.length} 节点`} flush>
                  <table className="tbl dense">
                    <tbody>
                      {detail.lineage.map((s) => (
                        <tr key={s.study_id} className={s.study_id === detail.study_id ? 'sel' : ''} onClick={() => select(s.study_id)} style={{ cursor: 'pointer' }}>
                          <td className="mono">{s.study_id}</td>
                          <td>{s.title}</td>
                          <td>
                            <StudyBadge s={s} />
                          </td>
                          <td className="n mono">{s.oos_sharpe !== null && s.oos_sharpe !== undefined ? fx(s.oos_sharpe, 2) : '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Panel>
              ) : null}
            </>
          )}
        </div>
      </div>
    </>
  );
}
