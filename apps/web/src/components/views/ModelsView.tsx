'use client';

import { useEffect, useMemo, type ReactNode } from 'react';
import { quantApi, normalizeGateChecks } from '@/lib/quant-api';
import { useApi } from '@/lib/use-api';
import { usePublishMetrics, useWorkbench } from '@/lib/workbench';
import { universeMeta } from '@/lib/meta';
import { clamp, fmtMs, fx, isNum, mean, pct, ratioPct, spct } from '@/lib/format';
import type { CpcvResult, ExperimentRecord, GateVerdict } from '@/lib/types';
import { ApiNotice, DataSlot, EmptyState, Kpi, Panel, SolveMeta, Tag, ViewHeader, triTone, type Tone } from '@/components/ui';
import { Icon } from '@/components/icons';
import { CpcvHistogram } from '@/components/charts/CpcvHistogram';
import { normalizeFolds, WalkForward } from '@/components/charts/WalkForward';

const PBO_MAX = 0.25;
const DSR_MIN = 0.95;
const OOS_IS_MIN = 0.5;

function badgeTone(badge: string | undefined, verdict: GateVerdict | undefined): Tone {
  const b = (badge ?? '').toUpperCase();
  if (b === 'PROMOTED') return 'info';
  if (b === 'BASELINE') return 'mute';
  const t = triTone(badge);
  return t !== 'mute' ? t : triTone(verdict);
}

const verdictColor = (v: GateVerdict | undefined) => (v === 'reject' ? 'var(--up)' : v === 'inconclusive' ? 'var(--warn)' : 'var(--sig)');
const verdictLabel = (v: GateVerdict | undefined) => (v === 'pass' ? 'PASS' : v === 'reject' ? 'REJECTED' : v === 'inconclusive' ? 'INCONCLUSIVE' : '—');

const num4 = (v: number | null | undefined) => (isNum(v) ? +v.toFixed(4) : null);

function GateRow({ x, on, val, max, thr, bad }: { x: ExperimentRecord; on: boolean; val: number | null | undefined; max: number; thr: number; bad: boolean }) {
  return (
    <div className={`gate-row ${on ? 'on' : ''}`} title={`${x.experiment_id} · ${x.name}`}>
      <span>{x.name || x.experiment_id}</span>
      <div className="gb">
        {isNum(val) ? <b style={{ width: `${clamp((val / max) * 100, 0, 100)}%`, background: bad ? 'var(--up)' : 'var(--sig)', opacity: on ? 1 : 0.55 }} /> : null}
        <i style={{ left: `${(thr / max) * 100}%` }} />
      </div>
      <em className={bad ? 'c-up' : ''}>{fx(val)}</em>
    </div>
  );
}

function VRow({ label, value, ok }: { label: ReactNode; value: ReactNode; ok: boolean | null }) {
  return (
    <div className="vrow">
      <span>{label}</span>
      <span style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        <b>{value}</b>
        {ok === null ? <Tag tone="mute">N/A</Tag> : ok ? <Tag tone="pass">PASS</Tag> : <Tag tone="fail">FAIL</Tag>}
      </span>
    </div>
  );
}

export function ModelsView() {
  const { universe, snapshotId, experimentId, setExperimentId, apiOnline, dock } = useWorkbench();
  const uni = universeMeta(universe);

  const ex = useApi<ExperimentRecord[]>(apiOnline ? 'experiments:20' : null, (s) => quantApi.experiments(20, s));
  const exps = apiOnline && ex.data && !(ex.error && !ex.loading) ? ex.data : undefined;
  const selected = exps?.find((e) => e.experiment_id === experimentId) ?? exps?.[0];

  useEffect(() => {
    if (exps?.length && !exps.some((e) => e.experiment_id === experimentId)) setExperimentId(exps[0].experiment_id);
  }, [exps, experimentId, setExperimentId]);

  const selId = selected?.experiment_id ?? (ex.error ? experimentId : null);
  const cpKey = apiOnline && selId ? `cpcv:${selId}:${snapshotId ?? 'latest'}` : null;
  const cp = useApi<CpcvResult>(cpKey, (s) => quantApi.cpcv({ experiment_id: selId as string, snapshot_id: snapshotId }, s));
  const c = apiOnline && cp.data && !(cp.error && cp.dataKey !== cpKey) ? cp.data : undefined;
  const cErr = cp.loading ? undefined : cp.error;
  const busy = cp.loading && !!c;

  const folds = useMemo(() => normalizeFolds(c?.walk_forward_folds), [c]);
  const irs = folds.map((f) => f.ir).filter(isNum);
  const irMean = irs.length ? mean(irs) : NaN;
  const details = useMemo(() => normalizeGateChecks(c?.gate_details), [c]);
  const extraDetails = details.filter((d) => !/^(pbo|dsr|oos\s*\/\s*is)$/i.test(d.name.trim()));

  usePublishMetrics(
    'models',
    c
      ? {
          experiment_id: c.experiment_id,
          pbo: num4(c.pbo),
          dsr: num4(c.dsr),
          is_sharpe: num4(c.is_sharpe),
          oos_median: num4(c.oos_median_sharpe),
          oos_is_ratio: num4(c.oos_is_ratio),
          negative_paths: `${c.negative_path_count}/${c.n_oos_paths}`,
          purge_conflicts: c.purge_conflicts,
          verdict: c.verdict
        }
      : null
  );

  const pboMax = Math.max(0.6, ...(exps ?? []).map((x) => (isNum(x.cpcv_pbo) ? x.cpcv_pbo * 1.1 : 0)));
  const selColor = verdictColor(c?.verdict ?? selected?.verdict);
  const cfgTag = c ? `N=${c.n_splits} 组 · k=${c.k_test} · ${c.n_oos_paths} 路径 · purge ${c.purge_days}D · embargo ${c.embargo_days}D` : 'CPCV 配置 —';

  return (
    <div className="stack">
      <ViewHeader
        path="/models"
        prds={['Q3']}
        title="多因子合成与过拟合防线"
        sub="Experiment Registry · CPCV 组合清除交叉验证 · Deflated Sharpe · Walk-Forward 样本外滚动 · 硬门禁自动拦截"
        actions={
          <>
            <Tag tone="mute">{cfgTag}</Tag>
            <SolveMeta loading={cp.loading} elapsedMs={c ? cp.elapsedMs : undefined} label="CPCV" />
            <button type="button" className="btn" onClick={cp.reload} disabled={!apiOnline || !selId || cp.loading}>
              <Icon name="play" size={10} />
              重算
            </button>
            <button type="button" className="btn pri" onClick={() => dock.runCommand('cpcv')}>
              <Icon name="shield" size={11} />
              CPCV 过拟合体检
            </button>
          </>
        }
      />
      <ApiNotice error={ex.error ?? cp.error} what={ex.error ? '实验注册表' : 'CPCV 验证'} />

      <div className="kpis k5">
        <Kpi label="IS Sharpe" value={c ? fx(c.is_sharpe) : '—'} sub={c ? <span>样本内 · 年化</span> : undefined} />
        <Kpi
          label="OOS 中位夏普"
          value={c ? fx(c.oos_median_sharpe) : '—'}
          sub={c ? <><Tag tone={c.oos_is_ratio >= OOS_IS_MIN ? 'pass' : 'fail'}>OOS/IS {fx(c.oos_is_ratio)}</Tag></> : undefined}
        />
        <Kpi label="CPCV PBO" value={c ? fx(c.pbo) : '—'} sub={c ? <><Tag tone={isNum(c.pbo) && c.pbo <= PBO_MAX ? 'pass' : 'fail'}>≤ {fx(PBO_MAX)}</Tag><span>过拟合概率</span></> : undefined} />
        <Kpi label="Deflated Sharpe" value={c ? fx(c.dsr) : '—'} sub={c ? <><Tag tone={isNum(c.dsr) && c.dsr >= DSR_MIN ? 'pass' : 'warn'}>≥ {fx(DSR_MIN)}</Tag><span>M={selected?.trial_count_m ?? '—'}</span></> : undefined} />
        <Kpi
          label="Purge 冲突"
          value={c ? String(c.purge_conflicts) : '—'}
          sub={c ? <><Tag tone={c.purge_conflicts === 0 ? 'pass' : 'fail'}>{c.purge_conflicts === 0 ? 'CLEAN' : 'LEAK'}</Tag><span>purge {c.purge_days}D · embargo {c.embargo_days}D</span></> : undefined}
        />
      </div>

      <Panel title="实验注册表 · Experiment Registry" prd="Q3.1" flush busy={ex.loading && !!exps} tools={<span className="ps">点击行切换诊断 · {uni.label}{exps ? ` · ${exps.length} 条` : ''}</span>}>
        <DataSlot height={140} ready={!!exps} loading={ex.loading} error={ex.loading ? undefined : ex.error}>
          {() =>
            exps!.length ? (
              <div style={{ overflowX: 'auto' }}>
                <table className="tbl dense compact">
                  <thead>
                    <tr>
                      <th>实验 ID</th>
                      <th>模型</th>
                      <th>特征集</th>
                      <th className="n">IS SR</th>
                      <th className="n">OOS SR</th>
                      <th className="n">OOS/IS</th>
                      <th className="n">PBO</th>
                      <th className="n">DSR</th>
                      <th className="n">OOS 超额</th>
                      <th className="n">M</th>
                      <th>门禁</th>
                    </tr>
                  </thead>
                  <tbody>
                    {exps!.map((x) => (
                      <tr key={x.experiment_id} className={`click ${x.experiment_id === selected?.experiment_id ? 'sel' : ''}`} onClick={() => setExperimentId(x.experiment_id)} title={x.notes || undefined}>
                        <td className="mono">{x.experiment_id}</td>
                        <td style={{ maxWidth: 165, overflow: 'hidden', textOverflow: 'ellipsis' }} title={`${x.name} ${x.model_type}`}>
                          {x.name} <span className="sub mono">{x.model_type}</span>
                        </td>
                        <td className="sub" style={{ maxWidth: 130, overflow: 'hidden', textOverflow: 'ellipsis' }} title={x.feature_summary}>
                          {x.feature_summary}
                        </td>
                        <td className="n">{fx(x.is_sharpe)}</td>
                        <td className="n">{fx(x.oos_sharpe)}</td>
                        <td className={`n ${isNum(x.oos_is_ratio) && x.oos_is_ratio < OOS_IS_MIN ? 'c-up' : ''}`}>{fx(x.oos_is_ratio)}</td>
                        <td className={`n ${isNum(x.cpcv_pbo) && x.cpcv_pbo > PBO_MAX ? 'c-up' : ''}`}>{fx(x.cpcv_pbo)}</td>
                        <td className={`n ${isNum(x.dsr) && x.dsr < DSR_MIN ? 'c-warn' : ''}`}>{fx(x.dsr)}</td>
                        <td className="n">{spct(ratioPct(x.oos_excess_ann), 1)}</td>
                        <td className="n">{x.trial_count_m}</td>
                        <td>
                          <Tag tone={badgeTone(x.status_badge, x.verdict)}>{x.status_badge || verdictLabel(x.verdict)}</Tag>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <EmptyState height={140} title="NO EXPERIMENTS" detail="实验注册表为空，请先 make seed" />
            )
          }
        </DataSlot>
      </Panel>

      <div className="mg">
        <Panel
          title={`CPCV ${c?.n_oos_paths ?? '—'} 路径 OOS 夏普分布 · ${selId ?? '—'}`}
          prd="Q3.2"
          busy={busy}
          tools={
            c ? (
              <>
                <span className="lg">
                  <span>
                    <i className="bx" style={{ background: selColor }} />
                    {c.experiment_id}
                  </span>
                  {c.baseline_path_sharpes.length ? (
                    <span>
                      <i className="bx" style={{ border: '1px dashed var(--tx2)', background: 'transparent' }} />
                      基线对照
                    </span>
                  ) : null}
                </span>
                <Tag tone={isNum(c.pbo) && c.pbo <= PBO_MAX ? 'pass' : 'fail'}>PBO {fx(c.pbo)}</Tag>
              </>
            ) : null
          }
        >
          <DataSlot height={318} ready={!!c} loading={cp.loading || ex.loading} error={cErr}>
            {() =>
              c!.path_sharpes.length ? (
                <CpcvHistogram
                  height={318}
                  paths={c!.path_sharpes}
                  baseline={c!.baseline_path_sharpes}
                  baselineLabel="基线对照"
                  isSharpe={c!.is_sharpe}
                  oosMedian={c!.oos_median_sharpe}
                  color={selColor}
                  label={c!.experiment_id}
                  nSplits={c!.n_splits}
                  kTest={c!.k_test}
                  nCombinations={c!.n_combinations}
                />
              ) : (
                <EmptyState height={318} title="NO CPCV PATHS" detail="path_sharpes 为空（候选收益矩阵不足）" />
              )
            }
          </DataSlot>
        </Panel>

        <Panel title="过拟合硬门禁 · PBO / DSR" prd="Q3.2" busy={busy}>
          {exps?.length ? (
            <>
              <div className="gsec" style={{ marginTop: 0 }}>
                <span>CPCV PBO 过拟合概率</span>
                <span className="c-up">阈值 {fx(PBO_MAX)}</span>
              </div>
              {exps.map((x) => (
                <GateRow key={`p${x.experiment_id}`} x={x} on={x.experiment_id === selected?.experiment_id} val={x.cpcv_pbo} max={pboMax} thr={PBO_MAX} bad={isNum(x.cpcv_pbo) && x.cpcv_pbo > PBO_MAX} />
              ))}
              <div className="gsec">
                <span>DEFLATED SHARPE RATIO</span>
                <span className="c-up">阈值 {fx(DSR_MIN)}</span>
              </div>
              {exps.map((x) => (
                <GateRow key={`d${x.experiment_id}`} x={x} on={x.experiment_id === selected?.experiment_id} val={x.dsr} max={1} thr={DSR_MIN} bad={isNum(x.dsr) && x.dsr < DSR_MIN} />
              ))}
            </>
          ) : (
            <DataSlot height={120} ready={false} loading={ex.loading} error={ex.loading ? undefined : ex.error}>
              {() => null}
            </DataSlot>
          )}
          {c ? (
            <div className="verdict">
              <div className="vrow">
                <b>{c.experiment_id} 门禁判定</b>
                <Tag tone={triTone(c.verdict)}>{verdictLabel(c.verdict)}</Tag>
              </div>
              <VRow label={`CPCV PBO ≤ ${fx(PBO_MAX)}`} value={fx(c.pbo)} ok={isNum(c.pbo) ? c.pbo <= PBO_MAX : null} />
              <VRow label={`Deflated Sharpe ≥ ${fx(DSR_MIN)}`} value={fx(c.dsr)} ok={isNum(c.dsr) ? c.dsr >= DSR_MIN : null} />
              <VRow label={`OOS / IS 夏普 ≥ ${fx(OOS_IS_MIN)}`} value={fx(c.oos_is_ratio)} ok={isNum(c.oos_is_ratio) ? c.oos_is_ratio >= OOS_IS_MIN : null} />
              {extraDetails.map((d, i) => (
                <VRow key={i} label={`${d.name}${d.threshold ? ` · ${d.threshold}` : ''}`} value={d.value ?? '—'} ok={d.passed} />
              ))}
              <div className="vrow">
                <span>OOS 负夏普路径</span>
                <span style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                  <b>
                    {c.negative_path_count}/{c.n_oos_paths}
                  </b>
                  <Tag tone={c.negative_path_count === 0 ? 'pass' : 'mute'}>{c.n_oos_paths ? pct((c.negative_path_count / c.n_oos_paths) * 100, 0) : '—'}</Tag>
                </span>
              </div>
              <div style={{ marginTop: 4, color: 'var(--tx3)', font: '10px var(--f-mono)', lineHeight: 1.5 }}>
                C({c.n_splits},{c.k_test}) = {c.n_combinations} 组合 · purge {c.purge_days}D · embargo {c.embargo_days}D · 冲突 {c.purge_conflicts}
                {cp.elapsedMs !== undefined ? ` · rtt ${fmtMs(cp.elapsedMs)}` : ''}
              </div>
            </div>
          ) : (
            <div className="verdict">
              <div className="vrow">
                <b>{selId ?? '—'} 门禁判定</b>
                <Tag tone="mute">{cp.loading ? 'RUNNING' : apiOnline ? (cErr ? 'ERROR' : '—') : 'OFFLINE'}</Tag>
              </div>
            </div>
          )}
        </Panel>
      </div>

      <Panel
        title="Walk-Forward 样本外滚动"
        prd="Q3.2"
        sub={c ? `${folds.length} folds · purge ${c.purge_days}D / embargo ${c.embargo_days}D` : undefined}
        busy={busy}
        tools={
          <>
            <span className="lg">
              <span>
                <i className="bx" style={{ background: 'var(--line2)' }} />
                训练
              </span>
              <span>
                <i className="bx" style={{ background: 'var(--tx3)' }} />
                验证
              </span>
              <span>
                <i className="bx" style={{ background: 'var(--warn)' }} />
                Purge+Embargo
              </span>
              <span>
                <i className="bx" style={{ background: 'var(--sig)' }} />
                OOS
              </span>
            </span>
            {isNum(irMean) ? <Tag tone={irMean >= 1 ? 'info' : irMean >= 0 ? 'warn' : 'fail'}>OOS IR 均值 {fx(irMean)}</Tag> : null}
          </>
        }
      >
        <DataSlot height={212} ready={!!c} loading={cp.loading || ex.loading} error={cErr}>
          {() =>
            folds.length ? (
              <WalkForward height={Math.max(212, folds.length * 22 + 24)} folds={folds} />
            ) : (
              <EmptyState height={212} title="NO WALK-FORWARD FOLDS" detail="walk_forward_folds 为空（样本期不足以切分训练/验证/测试）" />
            )
          }
        </DataSlot>
      </Panel>
    </div>
  );
}
