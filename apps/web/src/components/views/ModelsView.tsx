'use client';

import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { quantApi, normalizeGateChecks } from '@/lib/quant-api';
import { useApi } from '@/lib/use-api';
import { usePublishMetrics, useWorkbench } from '@/lib/workbench';
import { universeMeta } from '@/lib/meta';
import { clamp, fmtMs, fx, isNum, mean, pct, ratioPct, spct } from '@/lib/format';
import { GATES, GATE_SPECS, judge, verdictTone, type Verdict } from '@/lib/thresholds';
import type { CpcvResult, ExperimentRecord, GateVerdict } from '@/lib/types';
import { neighborDropPct, normFdr, normParamSensitivity, type ParamCurve } from '@/lib/v3';
import { ApiNotice, DataSlot, EmptyState, Kpi, Panel, Seg, SolveMeta, Tag, ViewHeader, triTone, type Tone } from '@/components/ui';
import { Icon } from '@/components/icons';
import { CpcvHistogram } from '@/components/charts/CpcvHistogram';
import { XYChart } from '@/components/charts/XYChart';
import { normalizeFolds, WalkForward } from '@/components/charts/WalkForward';
import { TermTip } from '@/components/glossary/TermTip';

const PBO_MAX = GATES.cpcv_pbo_max;
const DSR_MIN = GATES.dsr_min;
const OOS_IS_MIN = GATES.oos_is_ratio_min;
const FDR_Q = GATES.bh_fdr_q_max;
const CLIFF_MAX = GATES.param_cliff_drop_max;

/** Worst neighbour drop (percent) across all returned parameter curves. */
function worstCliff(curves: ParamCurve[]): { param: string; drop: number } | null {
  let best: { param: string; drop: number } | null = null;
  for (const c of curves) {
    const d = neighborDropPct(c);
    if (d !== null && (!best || d > best.drop)) best = { param: c.param, drop: d };
  }
  return best;
}

function FdrCard({ c }: { c: CpcvResult | undefined }) {
  const rows = useMemo(() => normFdr(c?.fdr_items), [c]);
  const sorted = useMemo(() => rows.filter((r) => r.p !== null).sort((a, b) => (a.p as number) - (b.p as number)), [rows]);
  const m = sorted.length;
  const q = c?.fdr_q_value ?? null;
  const spa = c?.spa_p_value ?? null;
  const qv = judge(GATE_SPECS.bh_fdr, q);
  const sv = judge(GATE_SPECS.hansen_spa, spa);
  return (
    <Panel
      title={<TermTip id="bh_fdr">BH-FDR 多重检验校正</TermTip>}
      prd="MATH"
      sub={m ? `M = ${m} 个候选 · α = ${fx(FDR_Q)}` : undefined}
      tools={
        <>
          {q !== null ? (
            <TermTip id="bh_fdr" as="span" value={q}>
              <Tag tone={verdictTone(qv.verdict)}>q {fx(q, 3)}</Tag>
            </TermTip>
          ) : null}
          {spa !== null ? (
            <TermTip id="hansen_spa" as="span" value={spa}>
              <Tag tone={verdictTone(sv.verdict)}>SPA p {fx(spa, 3)}</Tag>
            </TermTip>
          ) : null}
        </>
      }
    >
      {!m && q === null ? (
        <EmptyState height={190} title="BH-FDR · 后端未返回" detail="需 V3 CPCV 返回 fdr_items / fdr_q_value（同批候选的 p 值与 BH 校正 q 值）" />
      ) : (
        <>
          {m ? (
            <XYChart
              height={120}
              series={[
                { name: 'p 值（升序）', color: 'var(--sig)', x: sorted.map((_, i) => i + 1), y: sorted.map((r) => r.p as number), scatter: true, fmt: (v) => fx(v, 4) },
                { name: 'BH 临界线 α·k/M', color: 'var(--warn)', x: sorted.map((_, i) => i + 1), y: sorted.map((_, i) => (FDR_Q * (i + 1)) / m), dash: true, fmt: (v) => fx(v, 4) }
              ]}
              xFmt={(v) => `#${fx(v, 0)}`}
              yFmt={(v) => fx(v, 3)}
              xLabel="rank k"
              yLabel="p"
            />
          ) : null}
          <table className="tbl dense compact" style={{ marginTop: 6 }}>
            <thead>
              <tr>
                <th className="n">k</th>
                <th>候选</th>
                <th className="n">p</th>
                <th className="n">BH q</th>
                <th>判定</th>
              </tr>
            </thead>
            <tbody>
              {sorted.slice(0, 8).map((r, i) => {
                const rej = r.rejected ?? (r.q !== null ? r.q <= FDR_Q : null);
                return (
                  <tr key={r.id}>
                    <td className="n c-3">{r.rank ?? i + 1}</td>
                    <td className="mono" title={r.label}>
                      {r.label}
                    </td>
                    <td className="n">{r.p !== null ? fx(r.p, 4) : '—'}</td>
                    <td className={`n ${rej === false ? 'c-up' : ''}`}>{r.q !== null ? fx(r.q, 4) : '—'}</td>
                    <td>{rej === null ? <Tag tone="mute">N/A</Tag> : rej ? <Tag tone="pass">显著</Tag> : <Tag tone="fail">不显著</Tag>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {m > 8 ? <div className="ci" style={{ marginTop: 4 }}>仅列 p 值最小的 8 个 · 共 {m}</div> : null}
        </>
      )}
    </Panel>
  );
}

function ParamCliffCard({ c }: { c: CpcvResult | undefined }) {
  const curves = useMemo(() => normParamSensitivity(c?.param_sensitivity), [c]);
  const [param, setParam] = useState<string | null>(null);
  const cur = curves.find((x) => x.param === param) ?? curves[0];
  const drop = cur ? neighborDropPct(cur) : null;
  const jv = judge(GATE_SPECS.param_cliff, drop !== null ? drop / 100 : null);
  return (
    <Panel
      title={<TermTip id="param_cliff">参数悬崖 · 邻点敏感性</TermTip>}
      prd="MATH"
      sub={cur && curves.length <= 1 ? `${cur.param} · ${cur.metricName}` : undefined}
      tools={
        <>
          {curves.length > 1 ? <Seg value={cur?.param ?? ''} options={curves.slice(0, 4).map((x) => ({ key: x.param, label: x.param }))} onChange={setParam} /> : null}
          {drop !== null ? (
            <TermTip id="param_cliff" as="span" value={drop / 100} display={`${fx(drop, 1)}%`}>
              <Tag tone={verdictTone(jv.verdict)}>邻点衰减 {fx(drop, 0)}%</Tag>
            </TermTip>
          ) : null}
        </>
      }
    >
      {!cur ? (
        <EmptyState height={190} title="PARAM SENSITIVITY · 后端未返回" detail="需 V3 CPCV 返回 param_sensitivity（参数网格与对应 OOS 指标）" />
      ) : (
        <>
          <XYChart
            height={170}
            series={[{ name: cur.metricName, color: 'var(--sig)', x: cur.values, y: cur.metric, fmt: (v) => fx(v, 3) }]}
            xFmt={(v) => fx(v, Math.abs(v) < 1 ? 2 : 0)}
            yFmt={(v) => fx(v, 2)}
            xLabel={cur.param}
            yLabel={cur.metricName}
            zeroLine
            marks={cur.selected !== null ? [{ x: cur.values[cur.selected], y: cur.metric[cur.selected], label: '选定参数', color: jv.verdict === 'FAIL' ? 'var(--up)' : 'var(--sig)' }] : []}
            hlines={cur.selected !== null && Number.isFinite(cur.metric[cur.selected]) ? [{ y: cur.metric[cur.selected] * (1 - CLIFF_MAX), label: `-${fx(CLIFF_MAX * 100, 0)}% 红线`, color: 'var(--warn)' }] : []}
          />
          <div className="ci" style={{ marginTop: 4 }}>
            {jv.verdict === 'FAIL' ? '选定点处于悬崖边缘：邻近参数指标骤降，结果疑似被调参挑选' : drop !== null ? '邻近参数表现平滑，未见悬崖' : '未标注选定参数，无法计算邻点衰减'}
          </div>
        </>
      )}
    </Panel>
  );
}

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

function VRow({ label, value, ok, verdict }: { label: ReactNode; value: ReactNode; ok?: boolean | null; verdict?: Verdict }) {
  const v: Verdict = verdict ?? (ok === null || ok === undefined ? 'N/A' : ok ? 'PASS' : 'FAIL');
  return (
    <div className="vrow">
      <span>{label}</span>
      <span style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        <b>{value}</b>
        <Tag tone={verdictTone(v)}>{v}</Tag>
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
  const extraDetails = details.filter((d) => !/^(pbo|dsr|oos\s*\/\s*is|bh[-_ ]?fdr.*|param.*cliff.*)$/i.test(d.name.trim()));
  const cliff = useMemo(() => worstCliff(normParamSensitivity(c?.param_sensitivity)), [c]);

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
        <Kpi label={<TermTip id="sharpe_is_oos" value={c ? c.is_sharpe : null}>IS Sharpe</TermTip>} value={c ? fx(c.is_sharpe) : '—'} sub={c ? <span>样本内 · 年化</span> : undefined} />
        <Kpi
          label={<TermTip id="sharpe_is_oos" value={c ? c.oos_median_sharpe : null}>OOS 中位夏普</TermTip>}
          value={c ? fx(c.oos_median_sharpe) : '—'}
          sub={
            c ? (
              <TermTip id="oos_is_ratio" as="span" value={c.oos_is_ratio}>
                <Tag tone={verdictTone(judge(GATE_SPECS.oos_is_ratio, c.oos_is_ratio).verdict)}>OOS/IS {fx(c.oos_is_ratio)}</Tag>
              </TermTip>
            ) : undefined
          }
        />
        <Kpi
          label={<TermTip id="pbo" value={c ? c.pbo : null}>CPCV PBO</TermTip>}
          value={c ? fx(c.pbo) : '—'}
          sub={c ? <><Tag tone={verdictTone(judge(GATE_SPECS.pbo, c.pbo).verdict)}>≤ {fx(PBO_MAX)}</Tag><span>过拟合概率</span></> : undefined}
        />
        <Kpi
          label={<TermTip id="dsr" value={c ? c.dsr : null} context={{ trial_count_m: selected?.trial_count_m ?? null }}>Deflated Sharpe</TermTip>}
          value={c ? fx(c.dsr) : '—'}
          sub={c ? <><Tag tone={verdictTone(judge(GATE_SPECS.dsr, c.dsr).verdict)}>≥ {fx(DSR_MIN)}</Tag><TermTip id="trial_count_m" as="span" value={selected?.trial_count_m ?? null}>M={selected?.trial_count_m ?? '—'}</TermTip></> : undefined}
        />
        <Kpi
          label={<TermTip id="purge" value={c ? c.purge_conflicts : null}>Purge 冲突</TermTip>}
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
          title={<TermTip id="cpcv" value={c ? c.n_oos_paths : null}>{`CPCV ${c?.n_oos_paths ?? '—'} 路径 OOS 夏普分布 · ${selId ?? '—'}`}</TermTip>}
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
                <TermTip id="pbo">CPCV PBO 过拟合概率</TermTip>
                <span className="c-up">阈值 {fx(PBO_MAX)}</span>
              </div>
              {exps.map((x) => (
                <GateRow key={`p${x.experiment_id}`} x={x} on={x.experiment_id === selected?.experiment_id} val={x.cpcv_pbo} max={pboMax} thr={PBO_MAX} bad={isNum(x.cpcv_pbo) && x.cpcv_pbo > PBO_MAX} />
              ))}
              <div className="gsec">
                <TermTip id="dsr">DEFLATED SHARPE RATIO</TermTip>
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
              <VRow label={<TermTip id="pbo" value={c.pbo}>{`CPCV PBO ≤ ${fx(PBO_MAX)}`}</TermTip>} value={fx(c.pbo)} ok={isNum(c.pbo) ? c.pbo <= PBO_MAX : null} />
              <VRow label={<TermTip id="dsr" value={c.dsr}>{`Deflated Sharpe ≥ ${fx(DSR_MIN)}`}</TermTip>} value={fx(c.dsr)} ok={isNum(c.dsr) ? c.dsr >= DSR_MIN : null} />
              <VRow label={<TermTip id="oos_is_ratio" value={c.oos_is_ratio}>{`OOS / IS 夏普 ≥ ${fx(OOS_IS_MIN)}`}</TermTip>} value={fx(c.oos_is_ratio)} ok={isNum(c.oos_is_ratio) ? c.oos_is_ratio >= OOS_IS_MIN : null} />
              <VRow
                label={<TermTip id="bh_fdr" value={c.fdr_q_value ?? null}>{`BH-FDR q ≤ ${fx(FDR_Q)}`}</TermTip>}
                value={isNum(c.fdr_q_value) ? fx(c.fdr_q_value, 3) : '未返回'}
                verdict={judge(GATE_SPECS.bh_fdr, c.fdr_q_value ?? null).verdict}
              />
              <VRow
                label={<TermTip id="param_cliff" value={cliff ? cliff.drop / 100 : null}>{`参数悬崖 ≤ ${fx(CLIFF_MAX * 100, 0)}%`}</TermTip>}
                value={cliff ? `${fx(cliff.drop, 1)}% · ${cliff.param}` : '未返回'}
                verdict={judge(GATE_SPECS.param_cliff, cliff ? cliff.drop / 100 : null).verdict}
              />
              {extraDetails.map((d, i) => (
                <VRow key={i} label={`${d.name}${d.threshold ? ` · ${d.threshold}` : ''}`} value={d.value ?? '—'} ok={d.passed} />
              ))}
              <div className="vrow">
                <TermTip id="negative_paths" value={c.n_oos_paths ? c.negative_path_count / c.n_oos_paths : null}>
                  OOS 负夏普路径
                </TermTip>
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

      <div className="g2">
        <FdrCard c={c} />
        <ParamCliffCard c={c} />
      </div>

      <Panel
        title={<TermTip id="walk_forward">Walk-Forward 样本外滚动</TermTip>}
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
