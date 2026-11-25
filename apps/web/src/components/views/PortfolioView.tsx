'use client';

import { Fragment, useEffect, useRef, useState, type CSSProperties } from 'react';
import { quantApi } from '@/lib/quant-api';
import { useApi } from '@/lib/use-api';
import { usePublishMetrics, useWorkbench } from '@/lib/workbench';
import { BARRA_HARD_LIMIT, DEFAULT_CONSTRAINTS, OBJECTIVES, universeMeta } from '@/lib/meta';
import { clamp, fmtMs, fx, isNum, mean, sg, sum } from '@/lib/format';
import type { BarraStyleExposure, OptConstraints, OptObjective, PortfolioOptResult, TargetWeightItem } from '@/lib/types';
import { ApiNotice, DataSlot, Kpi, Legend, Panel, Seg, SolveMeta, Tag, ViewHeader, type Tone } from '../ui';
import { Icon } from '../icons';
import { Tornado, type TornadoRow } from '../charts/Tornado';
import { BarChart } from '../charts/BarChart';
import { TermTip } from '../glossary/TermTip';
import { CapacitySection } from '../proof/CapacitySection';
import { InvariantStrip } from '../proof/InvariantStrip';
import { normInvariants } from '@/lib/v3';

type SliderKey = 'max_stock_weight' | 'max_industry_dev' | 'turnover_lambda' | 'target_te' | 'max_size_z';

const SLIDERS: { key: SliderKey; label: string; term: string; min: number; max: number; step: number; scale: number; fmt: (v: number) => string }[] = [
  { key: 'max_stock_weight', label: '单股权重上限', term: 'enc', min: 1, max: 15, step: 0.5, scale: 100, fmt: (v) => fx(v, 1) + '%' },
  { key: 'max_industry_dev', label: '行业最大偏离', term: 'industry_active_dev', min: 1, max: 5, step: 0.1, scale: 100, fmt: (v) => '±' + fx(v, 1) + '%' },
  { key: 'turnover_lambda', label: '换手率 L1 惩罚系数 λ', term: 'turnover_lambda', min: 0, max: 2, step: 0.05, scale: 1, fmt: (v) => fx(v, 2) },
  { key: 'target_te', label: '目标跟踪误差 TE', term: 'tracking_error', min: 3, max: 8, step: 0.1, scale: 100, fmt: (v) => fx(v, 1) + '%' },
  { key: 'max_size_z', label: 'Size 风格敞口限制', term: 'style_exposure', min: 0.2, max: 0.8, step: 0.02, scale: 1, fmt: (v) => '±' + fx(v, 2) + 'σ' }
];

const STYLE_LABELS: Record<string, string> = {
  size: 'Size 市值',
  beta: 'Beta',
  momentum: 'Momentum 动量',
  residualvolatility: 'ResVol 残差波动',
  resvol: 'ResVol 残差波动',
  volatility: 'Volatility 波动',
  nonlinearsize: 'NLSize 非线性市值',
  midcap: 'MidCap 中市值',
  booktoprice: 'BTOP 账面市值比',
  btop: 'BTOP 账面市值比',
  value: 'Value 价值',
  liquidity: 'Liquidity 流动性',
  earningsyield: 'EY 盈利收益率',
  earningsvariability: 'EarnVar 盈利波动',
  earningsquality: 'EarnQual 盈利质量',
  growth: 'Growth 成长',
  leverage: 'Leverage 杠杆',
  profitability: 'Profit 盈利能力',
  investmentquality: 'InvQual 投资质量',
  dividendyield: 'DY 股息率',
  longtermreversal: 'LTRev 长期反转',
  analystsentiment: 'Sentiment 分析师'
};

const normKey = (f: string) => f.toLowerCase().replace(/[^a-z]/g, '');
const styleLabel = (f: string) => STYLE_LABELS[normKey(f)] ?? f;
const isSize = (f: string) => normKey(f) === 'size';

const SOLVE_TAG: Record<PortfolioOptResult['solve_status'], [Tone, string]> = {
  optimal: ['pass', 'OPTIMAL'],
  relaxed_optimal: ['warn', 'RELAXED_OPTIMAL'],
  optimal_inaccurate: ['warn', 'OPTIMAL_INACCURATE'],
  infeasible: ['fail', 'INFEASIBLE']
};

const TRADABLE_TAG: Record<TargetWeightItem['tradable_status'], [Tone, string]> = {
  OK: ['pass', 'T+1 OK · 可成交'],
  'T+1_LOCK': ['warn', 'T+1 锁定'],
  LIMIT_CAP: ['warn', '涨跌停受限'],
  SUSPENDED: ['fail', '停牌']
};

const isBreach = (s: BarraStyleExposure) => s.breached || Math.abs(s.post_opt) > BARRA_HARD_LIMIT;

/** Renders the backend cvxpy snippet with numeric literals and `#` comments highlighted. */
function CvxpyCode({ snippet }: { snippet: string }) {
  const lines = snippet.replace(/\s+$/, '').split('\n');
  return (
    <div className="code">
      {lines.map((ln, li) => {
        const ci = ln.indexOf('#');
        const body = ci >= 0 ? ln.slice(0, ci) : ln;
        const parts = body.split(/(\b\d+(?:\.\d+)?(?:[eE]-?\d+)?\b)/g);
        return (
          <Fragment key={li}>
            {parts.map((p, pi) => (pi % 2 === 1 ? <span key={pi} className="hl">{p}</span> : p))}
            {ci >= 0 ? <span className="c-3">{ln.slice(ci)}</span> : null}
            {li < lines.length - 1 ? '\n' : null}
          </Fragment>
        );
      })}
    </div>
  );
}

export function PortfolioView() {
  const { universe, snapshotId, experimentId, constraints, setConstraints, constraintsRev, apiOnline, dock } = useWorkbench();
  const um = universeMeta(universe);

  const optKey = apiOnline ? JSON.stringify({ u: universe, s: snapshotId, e: experimentId, c: constraints }) : null;
  const opt = useApi<PortfolioOptResult>(
    optKey,
    (signal) => quantApi.optimizePortfolio({ experiment_id: experimentId, universe_id: universe, snapshot_id: snapshotId, constraints }, signal),
    350
  );
  const barraKey = apiOnline ? JSON.stringify({ u: universe, s: snapshotId, e: experimentId }) : null;
  const barra = useApi(barraKey, (signal) => quantApi.barraExposure({ experiment_id: experimentId, universe_id: universe, snapshot_id: snapshotId }, signal));

  // External (Bot Dock / A2UI) constraint patch → transient info banner.
  const rev0 = useRef(constraintsRev);
  const [flashRev, setFlashRev] = useState<number | null>(null);
  useEffect(() => {
    if (constraintsRev === rev0.current) return;
    rev0.current = constraintsRev;
    setFlashRev(constraintsRev);
    const t = setTimeout(() => setFlashRev(null), 4200);
    return () => clearTimeout(t);
  }, [constraintsRev]);

  const o = opt.data;
  const ready = !!o;
  const busy = opt.loading && ready;
  const styles = o?.style_exposures ?? [];
  const breaches = styles.filter(isBreach);
  const sizeRow = styles.find((s) => isSize(s.factor));
  const invariants = normInvariants(o?.invariants);
  const maxStyleZ = styles.reduce((m, s) => Math.max(m, Math.abs(s.post_opt)), 0);

  usePublishMetrics(
    'portfolio',
    o
      ? {
          solve_status: o.solve_status,
          net_excess: +fx(o.expected_net_excess_pct, 2),
          te: +fx(o.predicted_te_pct, 2),
          ir: +fx(o.expected_ir, 2),
          size_exposure_post: sizeRow ? +fx(sizeRow.post_opt, 3) : null,
          max_size_z: constraints.max_size_z,
          turnover: +fx(o.oneway_turnover_pct, 1)
        }
      : null
  );

  const setC = (patch: Partial<OptConstraints>) => setConstraints((prev) => ({ ...prev, ...patch }));

  // ------------------------------------------------------------ constraint panel
  const sliders = SLIDERS.map((s) => {
    const dv = constraints[s.key] * s.scale;
    const p = clamp(((dv - s.min) / (s.max - s.min)) * 100, 0, 100);
    return (
      <div className="sl" key={s.key}>
        <div className="slh">
          <TermTip id={s.term} display={s.fmt(dv)}>
            {s.label}
          </TermTip>
          <b>{s.fmt(dv)}</b>
        </div>
        <input
          type="range"
          min={s.min}
          max={s.max}
          step={s.step}
          value={clamp(dv, s.min, s.max)}
          style={{ '--p': `${p}%` } as CSSProperties}
          onChange={(e) => setC({ [s.key]: +(+e.target.value / s.scale).toFixed(6) } as Partial<OptConstraints>)}
        />
        <div className="sls mono">
          <span>{s.fmt(s.min)}</span>
          <span>{s.fmt(s.max)}</span>
        </div>
      </div>
    );
  });

  const solveLine = o ? (
    <div style={{ marginTop: 6 }}>
      <div className="solv" style={{ flexWrap: 'wrap' }}>
        <TermTip id="solve_status" as="span" display={o.solve_status} context={{ solver: o.solver }}>
          <Tag tone={SOLVE_TAG[o.solve_status]?.[0] ?? 'mute'}>{SOLVE_TAG[o.solve_status]?.[1] ?? String(o.solve_status).toUpperCase()}</Tag>
        </TermTip>
        <span>
          {o.solver} · {fmtMs(o.solve_ms)}
        </span>
        {isNum(o.max_constraint_violation) ? <span title="max_constraint_violation">viol {o.max_constraint_violation.toExponential(1)}</span> : null}
      </div>
      <div className="solv" style={{ marginTop: 3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={o.opt_run_id}>
        run {o.opt_run_id}
      </div>
      {o.relaxation_notes?.length ? (
        <div style={{ marginTop: 5, display: 'flex', flexDirection: 'column', gap: 3 }}>
          {o.relaxation_notes.map((n, i) => (
            <div key={i} className="gs warn" style={{ fontSize: 10.5 }}>
              <b>RELAX</b>
              <span style={{ whiteSpace: 'normal' }}>{n}</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  ) : null;

  // ------------------------------------------------------------ barra pre-opt risk decomposition
  const b = barra.data;
  const riskSegs = b
    ? ([
        ['风格', b.style_risk_share, 'var(--warn)'],
        ['行业', b.industry_risk_share, 'var(--tx3)'],
        ['特质', b.specific_risk_share, 'var(--sig)']
      ] as [string, number, string][])
    : [];
  const riskTot = sum(riskSegs.map(([, v]) => Math.abs(isNum(v) ? v : 0)));
  const psdOk = b ? b.psd_min_eigenvalue >= -1e-10 : false;

  // ------------------------------------------------------------ KPIs
  const kpis = (
    <div className="kpis k5">
      <Kpi
        label={<TermTip id="gross_vs_net" display={o ? sg(o.expected_net_excess_pct, 2) + '%' : undefined}>预计年化净超额</TermTip>}
        value={o ? sg(o.expected_net_excess_pct, 2) + '%' : '—'}
        sub={o ? <span>毛 {fx(o.expected_gross_excess_pct, 1)}% · 费 {fx(o.expected_cost_pct, 2)}%</span> : null}
      />
      <Kpi label={<TermTip id="tracking_error" display={o ? fx(o.predicted_te_pct, 2) + '%' : undefined} context={{ target_pct: +(constraints.target_te * 100).toFixed(2) }}>预期跟踪误差</TermTip>} value={o ? fx(o.predicted_te_pct, 2) + '%' : '—'} sub={<span>目标 ≤ {fx(constraints.target_te * 100, 1)}%</span>} />
      <Kpi label={<TermTip id="information_ratio" value={o ? o.expected_ir : null}>预期信息比率</TermTip>} value={o ? fx(o.expected_ir, 2) : '—'} sub={<span>net / TE</span>} />
      <Kpi label={<TermTip id="turnover_lambda" display={o ? fx(o.oneway_turnover_pct, 1) + '%' : undefined}>单次单边换手</TermTip>} value={o ? fx(o.oneway_turnover_pct, 1) + '%' : '—'} sub={o ? <span>年化 {fx(o.annualized_turnover, 1)}x</span> : null} />
      <Kpi label={<TermTip id="enc" value={o ? o.effective_n_stocks : null}>有效持股数 ENC</TermTip>} value={o ? fx(o.effective_n_stocks, 0) : '—'} sub={<span>单股上限 {fx(constraints.max_stock_weight * 100, 1)}%</span>} />
    </div>
  );

  const indDev = o?.industry_deviations ?? [];
  const absDevMax = indDev.length ? Math.max(...indDev.map((d) => Math.abs(d.active_weight_pct))) : NaN;
  const banner = !o ? null : o.solve_status === 'infeasible' || breaches.length ? (
    <div className="gs fail" style={{ width: '100%' }}>
      <b>风控门禁 BLOCK</b>
      <span>
        {o.solve_status === 'infeasible' ? 'cvxpy 求解不可行' : null}
        {o.solve_status === 'infeasible' && breaches.length ? ' · ' : null}
        {breaches.length
          ? breaches.map((s) => `${styleLabel(s.factor)} ${sg(s.post_opt, 2)}σ`).join(' / ') + ` 越过 ±${fx(BARRA_HARD_LIMIT, 1)}σ 硬边界或约束`
          : null}
      </span>
      <button type="button" className="btn sm warnb" style={{ marginLeft: 'auto' }} onClick={() => setC({ max_size_z: 0.3 })}>
        收紧 Size 至 ±0.30σ
      </button>
    </div>
  ) : (
    <div className="gs pass" style={{ width: '100%' }}>
      <b>风控门禁 PASS</b>
      <span>
        CNE6 {styles.length} 项风格暴露均在 ±{fx(BARRA_HARD_LIMIT, 1)}σ 内（max |z| {fx(maxStyleZ, 2)}σ）· 行业 |偏离| ≤ {fx(absDevMax, 2)}% · TE {fx(o.predicted_te_pct, 2)}%
      </span>
    </div>
  );

  // ------------------------------------------------------------ tornado
  const torRows: TornadoRow[] = styles.map((s) => ({
    key: s.factor,
    label: styleLabel(s.factor),
    pre: s.pre_opt,
    post: s.post_opt,
    limit: s.limit,
    breached: s.breached,
    constraint: isSize(s.factor) ? constraints.max_size_z : undefined
  }));

  // ------------------------------------------------------------ industries
  const limPcts = indDev.map((d) => d.limit_pct).filter((v) => isNum(v) && v > 0);
  const indLim = limPcts.length ? Math.max(...limPcts) : constraints.max_industry_dev * 100;
  const indVals = indDev.map((d) => d.active_weight_pct);
  const indLabels = indDev.map((d) => (indDev.length > 16 && d.industry.length > 2 ? d.industry.slice(0, 2) : d.industry));
  const indTouch = indVals.filter((v) => Math.abs(v) >= indLim - 1e-3).length;

  // ------------------------------------------------------------ holdings
  const hold = o?.top_holdings ?? [];
  const capPct = constraints.max_stock_weight * 100;
  const capped = hold.filter((h) => h.target_weight_pct >= capPct - 1e-3).length;
  const holdNote = hold.length
    ? `Top${hold.length} 合计 ${fx(sum(hold.map((h) => h.target_weight_pct)), 2)}% · 触顶 ${capped}/${hold.length} · 平均冲击 ${fx(mean(hold.map((h) => h.est_impact_bps)), 1)} bps`
    : '';

  return (
    <div className="stack">
      <ViewHeader
        path="/portfolio"
        prds={['Q4']}
        title="组合优化与 Barra CNE6 风险终端"
        sub={`cvxpy 凸优化 · ${um.label} · 基准 ${um.bench} · 风险模型 Barra CNE6 · 实验 ${experimentId} · 快照 ${o?.snapshot_id ?? snapshotId ?? 'latest'}`}
        actions={
          <>
            <SolveMeta loading={opt.loading} elapsedMs={opt.elapsedMs} label="cvxpy" />
            <button type="button" className="btn" onClick={() => setConstraints({ ...DEFAULT_CONSTRAINTS })}>
              恢复默认约束
            </button>
            <button type="button" className="btn pri" onClick={() => dock.runCommand('barra')}>
              <Icon name="shield" size={11} />
              Barra 风险诊断
            </button>
          </>
        }
      />
      <ApiNotice error={opt.error} what="组合优化 /portfolio/optimize" />
      {barra.error && barra.error.message !== opt.error?.message ? <ApiNotice error={barra.error} what="Barra 暴露 /risk/barra-exposure" /> : null}
      {flashRev !== null ? (
        <div key={flashRev} className="gs chg" style={{ width: '100%', borderLeftColor: 'var(--sig)' }}>
          <Icon name="sync" size={11} style={{ color: 'var(--sig)' }} />
          <b style={{ color: 'var(--sig)' }}>A2UI</b>
          <span>Agent Home A2UI 已应用约束 · 已触发 cvxpy 重算</span>
        </div>
      ) : null}
      <div className="pfg">
        <div className="stack" style={{ minWidth: 0 }}>
          <Panel title={<TermTip id="opt_objective">cvxpy 约束调节器</TermTip>} prd="Q4.2" tools={<span className="ps">拖动 350ms 防抖重算</span>}>
            <Seg<OptObjective>
              stretch
              value={constraints.objective}
              options={OBJECTIVES.map((x) => ({ key: x.key, label: x.label }))}
              onChange={(v) => setC({ objective: v })}
            />
            {sliders}
            {o ? (
              <CvxpyCode snippet={o.cvxpy_snippet} />
            ) : (
              <div style={{ marginTop: 8 }}>
                <DataSlot height={150} ready={false} loading={opt.loading} error={opt.error}>
                  {() => null}
                </DataSlot>
              </div>
            )}
            {solveLine}
          </Panel>
          <Panel
            title={<TermTip id="risk_decomposition">当前风险分解 · 优化前</TermTip>}
            prd="Q4.1"
            busy={barra.loading && !!b}
            tools={
              b ? (
                <TermTip id="psd" as="span" value={b.psd_min_eigenvalue} display={b.psd_min_eigenvalue.toExponential(2)}>
                  <Tag tone={psdOk ? 'pass' : 'fail'}>{psdOk ? 'PSD OK' : 'NOT PSD'}</Tag>
                </TermTip>
              ) : null
            }
          >
            <DataSlot height={96} ready={!!b} loading={barra.loading} error={barra.error}>
              {() => (
                <>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                    <span className="ps">预测跟踪误差</span>
                    <b className="mono" style={{ fontSize: 15 }}>
                      {fx(b!.predicted_te_pct, 2)}%
                    </b>
                  </div>
                  <div className="attr">
                    {riskSegs.map(([n, v, cl]) => {
                      const share = riskTot > 0 ? (Math.abs(v) / riskTot) * 100 : 0;
                      return (
                        <div key={n} style={{ flex: Math.max(share, 0.5).toFixed(3), background: cl }} data-tip={`${n}风险 占比 ${fx(share, 1)}%`}>
                          {share > 18 ? `${n} ${fx(share, 0)}%` : ''}
                        </div>
                      );
                    })}
                  </div>
                  <div className="solv" style={{ justifyContent: 'space-between' }}>
                    <Legend
                      items={riskSegs.map(([n, , cl]) => ({ label: n, color: cl, box: true }))}
                    />
                    <span title="协方差矩阵最小特征值">λmin {b!.psd_min_eigenvalue.toExponential(2)}</span>
                  </div>
                  <div className="solv" style={{ marginTop: 3 }}>
                    as_of {b!.as_of} · {b!.snapshot_id}
                  </div>
                </>
              )}
            </DataSlot>
          </Panel>
        </div>
        <div className="stack" style={{ minWidth: 0 }}>
          {kpis}
          {banner}
          {o ? (
            <div>
              <div className="gsec" style={{ marginTop: 0 }}>
                <TermTip id="conservation_law">优化不变量</TermTip>
                <span>{invariants.length ? `${invariants.filter((i) => i.passed).length}/${invariants.length} OK` : '权重和 · 约束违背 · PSD · 风格界 · 行业界'}</span>
              </div>
              <InvariantStrip items={invariants} height={46} emptyDetail="需 V3 /portfolio/optimize 返回 invariants[]（权重和 / 约束违背 / PSD / 风格硬界 / 行业偏离）" />
            </div>
          ) : null}
          <Panel
            title={<TermTip id="barra_cne6">Barra CNE6 主动风格暴露 · 龙卷风图 (σ)</TermTip>}
            prd="Q4.1"
            busy={busy}
            tools={
              <Legend
                items={[
                  { label: '优化前', box: true, style: { border: '1px dashed var(--tx2)', background: 'transparent' } },
                  { label: 'cvxpy 优化后', color: 'var(--sig)', box: true },
                  { label: '越界', color: 'var(--up)', box: true },
                  { label: `±${fx(BARRA_HARD_LIMIT, 1)}σ`, color: 'var(--up)', dash: true },
                  { label: '约束', color: 'var(--warn)' }
                ]}
              />
            }
          >
            <DataSlot height={300} ready={ready && torRows.length > 0} loading={opt.loading} error={opt.error}>
              {() => <Tornado rows={torRows} height={300} hardLimit={BARRA_HARD_LIMIT} />}
            </DataSlot>
          </Panel>
        </div>
      </div>
      <Panel
        title={<TermTip id="industry_active_dev">申万一级行业主动权重偏离 (%)</TermTip>}
        prd="Q4.2"
        busy={busy}
        tools={indDev.length ? <span className="ps">|偏离| 均值 {fx(mean(indVals.map(Math.abs)), 2)}% · 触限 {indTouch} 个行业</span> : null}
      >
        <DataSlot height={166} ready={ready && indDev.length > 0} loading={opt.loading} error={opt.error}>
          {() => (
            <BarChart
              height={166}
              values={indVals}
              labels={indLabels}
              gap={6}
              yFmt={(v) => fx(v, Math.abs(v) < 1 && v !== 0 ? 1 : 0) + '%'}
              hlines={[
                { v: indLim, color: 'var(--warn)', label: '+' + fx(indLim, 1) + '%' },
                { v: -indLim, color: 'var(--warn)', label: '-' + fx(indLim, 1) + '%' }
              ]}
              tip={(v, i) => `${indDev[i].industry} · 主动偏离 ${sg(v, 2)}% · 限额 ±${fx(indDev[i].limit_pct || indLim, 1)}%`}
              pad={{ l: 34 }}
            />
          )}
        </DataSlot>
      </Panel>
      <CapacitySection embedded={o?.capacity_frontier} experimentId={experimentId} universeId={universe} snapshotId={o?.snapshot_id ?? snapshotId} apiOnline={apiOnline} prd="Q4.3" />
      <Panel title={`目标调仓权重 · Top ${hold.length || ''} 持仓明细`} prd="Q4.2" flush busy={busy} tools={holdNote ? <span className="ps">{holdNote}</span> : null}>
        <DataSlot height={280} ready={ready && hold.length > 0} loading={opt.loading} error={opt.error}>
          {() => (
            <table className="tbl dense">
              <thead>
                <tr>
                  <th>代码</th>
                  <th>名称</th>
                  <th>行业</th>
                  <th className="n">目标权重</th>
                  <th className="n">基准权重</th>
                  <th className="n">主动偏离</th>
                  <th className="n">冲击 bps</th>
                  <th>流动性 / T+1 校验</th>
                </tr>
              </thead>
              <tbody>
                {hold.map((h) => {
                  const cap = h.target_weight_pct >= capPct - 1e-3;
                  const [tone, txt] = TRADABLE_TAG[h.tradable_status] ?? ['mute', h.tradable_status];
                  return (
                    <tr key={h.symbol}>
                      <td className="mono">{h.symbol}</td>
                      <td>{h.name}</td>
                      <td className="sub">{h.industry}</td>
                      <td className="n">
                        {fx(h.target_weight_pct, 2)}%
                        {cap ? (
                          <>
                            {' '}
                            <span className="tag mute" style={{ padding: '1px 3px' }}>
                              CAP
                            </span>
                          </>
                        ) : null}
                      </td>
                      <td className="n c-2">{fx(h.bench_weight_pct, 2)}%</td>
                      <td className={`n ${h.active_weight_pct >= 0 ? 'c-up' : 'c-dn'}`}>{sg(h.active_weight_pct, 2)}%</td>
                      <td className="n">{fx(h.est_impact_bps, 1)}</td>
                      <td>
                        <Tag tone={tone}>{txt}</Tag>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </DataSlot>
      </Panel>
    </div>
  );
}
