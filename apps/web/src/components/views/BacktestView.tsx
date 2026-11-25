'use client';

import { useEffect, useState } from 'react';
import { quantApi } from '@/lib/quant-api';
import { useApi } from '@/lib/use-api';
import { usePublishMetrics, useWorkbench } from '@/lib/workbench';
import { universeMeta } from '@/lib/meta';
import { fmtMs, fx, isNum, ratioPct, sg, sum, thou } from '@/lib/format';
import type { AttributionResult, BacktestEngineMode, BacktestResult } from '@/lib/types';
import { ApiNotice, DataSlot, EmptyState, Kpi, Legend, Panel, Prd, Seg, SolveMeta, Tag, ViewHeader } from '../ui';
import { Icon } from '../icons';
import { LineChart } from '../charts/LineChart';
import { BarChart } from '../charts/BarChart';
import { REGIME_BANDS } from '../charts/core';
import { Waterfall, type WaterfallItem } from '../charts/Waterfall';
import { TermTip } from '../glossary/TermTip';
import { CapacitySection } from '../proof/CapacitySection';
import { InvariantStrip } from '../proof/InvariantStrip';
import { GATE_SPECS, judge, UI_LIMITS, verdictTone } from '@/lib/thresholds';
import { normInvariants } from '@/lib/v3';

const ENGINES: { key: BacktestEngineMode; label: string }[] = [
  { key: 'screening', label: '向量化快筛' },
  { key: 'event', label: '事件驱动 A 股微结构高保真' }
];

/** Drawdowns may arrive signed or as magnitudes; always rendered as −|v|. */
const negPct = (v: number | null | undefined, d = 2) => (isNum(v) ? `-${fx(Math.abs(v), d)}%` : '—');
const abs = (v: number) => (isNum(v) ? Math.abs(v) : 0);
const closureTag = (bp: number) => {
  const v = judge(GATE_SPECS.closure_error, bp).verdict;
  return <Tag tone={verdictTone(v)}>{v}</Tag>;
};

function AttrTable({ head, rows, closureBp }: { head: string; rows: [string, number, string, string?][]; closureBp: number }) {
  const total = sum(rows.map(([, v]) => (isNum(v) ? v : 0)));
  const absTot = sum(rows.map(([, v]) => abs(v)));
  return (
    <table className="tbl dense">
      <thead>
        <tr>
          <th>{head}</th>
          <th className="n">年化</th>
          <th className="n">占比</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([n, v, cl, term]) => {
          const sh = absTot > 0 ? (abs(v) / absTot) * 100 : 0;
          return (
            <tr key={n}>
              <td>
                {term ? (
                  <TermTip id={term} display={`${sg(v, 2)}%`}>
                    {n}
                  </TermTip>
                ) : (
                  n
                )}
              </td>
              <td className={`n ${v >= 0 ? 'c-up' : 'c-dn'}`}>{sg(v, 2)}%</td>
              <td className="n">
                {fx(sh, 1)}%<span className="bar-in" style={{ width: Math.max(1, sh * 0.4), background: cl }} />
              </td>
            </tr>
          );
        })}
        <tr>
          <td>
            <b>合计</b>
          </td>
          <td className="n">
            <b>{sg(total, 2)}%</b>
          </td>
          <td className="n c-3">100.0%</td>
        </tr>
        <tr>
          <td className="sub">
            <TermTip id="closure_error" value={closureBp} context={{ table: head }}>
              闭合误差
            </TermTip>
          </td>
          <td className="n">{fx(closureBp, 2)} bp</td>
          <td className="n">{closureTag(closureBp)}</td>
        </tr>
      </tbody>
    </table>
  );
}

function AttributionBody({ a }: { a: AttributionResult }) {
  const segs: [string, number, string][] = [
    ['纯 Alpha', a.barra_specific_alpha_pct, 'var(--sig)'],
    ['风格 Beta', a.barra_style_pct, 'var(--warn)'],
    ['行业', a.barra_industry_pct, 'var(--tx3)'],
    ['交易成本', a.barra_cost_pct, 'var(--dn)']
  ];
  const absTot = sum(segs.map(([, v]) => abs(v)));
  const total = sum(segs.map(([, v]) => (isNum(v) ? v : 0)));
  const detail = Object.entries(a.barra_style_detail ?? {}).filter(([, v]) => isNum(v));
  return (
    <>
      <div className="attr">
        {segs.map(([n, v, cl]) => {
          const sh = absTot > 0 ? (abs(v) / absTot) * 100 : 0;
          return (
            <div
              key={n}
              style={{ flex: Math.max(sh, 0.5).toFixed(3), background: cl }}
              data-tip={`${n} ${sg(v, 2)}%/年 · 占 |分量| ${fx(sh, 1)}%${total ? ` · 占合计 ${fx((v / total) * 100, 1)}%` : ''}`}
            >
              {sh > 9 ? `${n} ${fx(sh, 0)}%` : ''}
            </div>
          );
        })}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr) minmax(0, 1.15fr)', gap: 10, alignItems: 'start' }}>
        <div style={{ border: '1px solid var(--line)' }}>
          <AttrTable
            head="Brinson-Fachler"
            closureBp={a.brinson_closure_error_bp}
            rows={[
              ['配置效应 Allocation', a.brinson_allocation_pct, 'var(--tx3)', 'brinson_allocation'],
              ['选股效应 Selection', a.brinson_selection_pct, 'var(--sig)', 'brinson_selection'],
              ['交互效应 Interaction', a.brinson_interaction_pct, 'var(--line2)', 'brinson_interaction']
            ]}
          />
        </div>
        <div style={{ border: '1px solid var(--line)' }}>
          <AttrTable
            head="Barra 因子归因"
            closureBp={a.barra_closure_error_bp}
            rows={[
              ['风格 Beta', a.barra_style_pct, 'var(--warn)', 'style_exposure'],
              ['行业', a.barra_industry_pct, 'var(--tx3)'],
              ['特质 Alpha', a.barra_specific_alpha_pct, 'var(--sig)', 'barra_attribution'],
              ['交易成本', a.barra_cost_pct, 'var(--dn)', 'stamp_duty']
            ]}
          />
        </div>
        <div style={{ border: '1px solid var(--line)', padding: '6px 8px 0' }}>
          <div className="gsec" style={{ margin: '0 0 2px' }}>
            <span>风格 Beta 拆解 · 年化 %</span>
            <span>{detail.length} 因子</span>
          </div>
          {detail.length ? (
            <BarChart
              height={170}
              values={detail.map(([, v]) => v)}
              labels={detail.map(([k]) => (k.length > 6 ? k.slice(0, 6) : k))}
              gap={6}
              yFmt={(v) => fx(v, Math.abs(v) < 1 && v !== 0 ? 1 : 0) + '%'}
              tip={(v, i) => `${detail[i][0]} · ${sg(v, 3)}%/年`}
              pad={{ l: 34 }}
            />
          ) : (
            <EmptyState height={170} title="NO STYLE DETAIL" detail="barra_style_detail 为空" />
          )}
        </div>
      </div>
    </>
  );
}

export function BacktestView() {
  const { universe, snapshotId, experimentId, engine, setEngine, apiOnline, dock } = useWorkbench();
  const um = universeMeta(universe);

  const key = apiOnline ? JSON.stringify({ u: universe, s: snapshotId, e: experimentId, g: engine }) : null;
  const bt = useApi<BacktestResult>(key, (signal) =>
    quantApi.runBacktest({ experiment_id: experimentId, universe_id: universe, engine, snapshot_id: snapshotId }, signal)
  );

  // Live elapsed timer while a (possibly long) event-driven run is in flight.
  const [t0, setT0] = useState<number | null>(null);
  const [now, setNow] = useState(0);
  useEffect(() => {
    if (!bt.loading) {
      setT0(null);
      return;
    }
    const s = performance.now();
    setT0(s);
    setNow(s);
    const id = setInterval(() => setNow(performance.now()), 200);
    return () => clearInterval(id);
  }, [bt.loading]);

  const r = bt.data;
  const ready = !!r;
  const stale = bt.loading && ready;
  const ev = r?.engine === 'event';
  const f = r?.friction;

  usePublishMetrics(
    'backtest',
    r
      ? {
          engine: r.engine,
          ann: +fx(r.ann_return_pct, 2),
          excess: +fx(r.net_excess_ann_pct, 2),
          ir: +fx(r.info_ratio, 2),
          mdd: -Math.abs(+fx(r.max_drawdown_pct, 2)),
          friction_bps: +fx(r.friction.total_friction_bps, 1)
        }
      : null
  );

  const winPct = r ? ratioPct(r.daily_win_rate_pct) : null;
  const dateSpan = r?.dates.length ? `${r.dates[0]} → ${r.dates[r.dates.length - 1]}` : '';

  const kpis = (
    <div className="kpis k5 two">
      <Kpi label="年化收益" value={r ? sg(r.ann_return_pct, 2) + '%' : '—'} sub={dateSpan ? <span>{dateSpan}</span> : null} />
      <Kpi label={`基准收益 ${um.bench}`} value={r ? sg(r.bench_ann_return_pct, 2) + '%' : '—'} sub={<span>{um.label}</span>} />
      <Kpi
        label={<TermTip id="gross_vs_net" display={r ? sg(r.net_excess_ann_pct, 2) + '%' : undefined} context={{ engine: r?.engine ?? null }}>年化超额 Alpha</TermTip>}
        value={r ? sg(r.net_excess_ann_pct, 2) + '%' : '—'}
        sub={
          r ? (
            ev ? (
              <>
                <Tag tone="pass">NET</Tag>
                <span>扣全部微结构摩擦</span>
              </>
            ) : (
              <>
                <Tag tone="warn">GROSS</Tag>
                <span>向量化口径</span>
              </>
            )
          ) : null
        }
      />
      <Kpi label={<TermTip id="information_ratio" value={r ? r.info_ratio : null}>信息比率 IR</TermTip>} value={r ? fx(r.info_ratio, 2) : '—'} sub={<span>超额 / TE</span>} />
      <Kpi label={<TermTip id="max_drawdown" display={r ? negPct(r.max_drawdown_pct) : undefined}>最大回撤</TermTip>} value={r ? negPct(r.max_drawdown_pct) : '—'} sub={<span>策略净值</span>} />
      <Kpi label={<TermTip id="excess_drawdown" display={r ? negPct(r.excess_max_drawdown_pct) : undefined}>超额最大回撤</TermTip>} value={r ? negPct(r.excess_max_drawdown_pct) : '—'} sub={<span>相对基准</span>} />
      <Kpi label={<TermTip id="calmar" value={r ? r.calmar : null}>Calmar</TermTip>} value={r ? fx(r.calmar, 2) : '—'} sub={<span>年化 / |MDD|</span>} />
      <Kpi label="日胜率 / 盈亏比" value={r ? `${fx(winPct, 1)}% / ${fx(r.profit_loss_ratio, 2)}` : '—'} sub={<span>日频口径</span>} />
      <Kpi label="年化双边换手" value={r ? fx(r.ann_turnover_pct, 0) + '%' : '—'} sub={<span>双边 · 年化</span>} />
      <Kpi
        label={<TermTip id="sqrt_impact" display={f ? fx(f.total_friction_bps, 0) + ' bps/年' : undefined}>交易摩擦总损耗</TermTip>}
        value={f ? fx(f.total_friction_bps, 0) + ' bps/年' : '—'}
        sub={r ? <span>成交 {thou(r.total_fills)} · 拒单 {thou(r.total_rejections)}</span> : null}
      />
    </div>
  );

  // drawdown unit auto-detect: fractions (|min| ≤ 1) vs percent
  const ddAll = r ? [...r.strategy_dd, ...r.excess_dd].filter(isNum) : [];
  const ddFrac = ddAll.length ? Math.max(...ddAll.map(Math.abs)) <= 1 : true;
  const ddK = ddFrac ? 100 : 1;
  const ddFmt = (v: number) => fx(v * ddK, 2) + '%';
  const ddTick = (v: number) => fx(v * ddK, 0) + '%';
  const neg = (a: number[]) => a.map((v) => (isNum(v) ? -Math.abs(v) : NaN));

  const fall: WaterfallItem[] = f
    ? [
        { label: '理想无摩擦', value: f.gross_excess_pct, total: true, sub: 'GROSS' },
        ...([
          ['双边佣金', f.commission_pct],
          ['印花税', f.stamp_duty_pct],
          ['过户费', f.transfer_fee_pct],
          ['冲击滑点', f.impact_slippage_pct],
          ['涨跌停停牌', f.limit_suspend_pct]
        ] as [string, number][]).map(([label, v]) => ({ label, value: -abs(v), sub: fx(abs(v) * 100, 0) + 'bp' })),
        { label: '真实净超额', value: f.net_excess_pct, total: true, sub: 'NET' }
      ]
    : [];

  const rej = r?.recent_rejections ?? [];
  const rejRate = r && r.total_fills > 0 ? (r.total_rejections / r.total_fills) * 100 : null;

  const a = r?.attribution;
  const invariants = normInvariants(r?.invariants);
  const specShare = (() => {
    if (!a) return null;
    const tot = a.barra_specific_alpha_pct + a.barra_style_pct + a.barra_industry_pct + a.barra_cost_pct;
    return isNum(tot) && tot > 0 ? (a.barra_specific_alpha_pct / tot) * 100 : null;
  })();

  const running =
    bt.loading && t0 !== null ? (
      <span className="solv">
        <i className="spin" />
        {engine === 'event' ? 'event 引擎撮合中…' : 'screening 向量化计算中…'} {fmtMs(now - t0)}
      </span>
    ) : (
      <SolveMeta loading={false} elapsedMs={bt.elapsedMs} label={r ? `${r.engine} 耗时` : 'rtt'} />
    );

  const dim = { opacity: stale ? 0.55 : 1, transition: 'opacity .2s' };

  return (
    <div className="stack">
      <ViewHeader
        path="/backtest"
        prds={['Q5']}
        title="机构级回测与业绩归因"
        sub={`${um.label} · 实验 ${experimentId}${dateSpan ? ` · ${dateSpan}` : ''} · 快照 ${r?.snapshot_id ?? snapshotId ?? 'latest'}${r ? ` · run ${r.run_id}` : ''}`}
        actions={
          <>
            {running}
            <Prd id="Q5.1" />
            <Seg<BacktestEngineMode> value={engine} options={ENGINES} onChange={setEngine} />
            <button
              type="button"
              className="btn"
              onClick={() => dock.send('解读当前 ' + engine + ' 回测的 Brinson/Barra 归因与摩擦损耗', { bot: 'risk-pm' })}
            >
              <Icon name="send" size={11} />
              Bot 归因解读
            </button>
          </>
        }
      />
      <ApiNotice error={bt.error} what="回测 /backtest/run" />
      {r && r.engine !== engine ? (
        <div className="gs warn" style={{ width: '100%' }}>
          <b>STALE</b>
          <span>
            当前展示 {r.engine} 引擎结果 · {engine} 引擎{bt.loading ? '计算中' : '尚未返回'}
          </span>
        </div>
      ) : null}
      <div style={dim}>{kpis}</div>
      {r ? (
        <div style={dim}>
          <div className="gsec" style={{ marginTop: 0 }}>
            <TermTip id="conservation_law">回测不变量</TermTip>
            <span>{invariants.length ? `${invariants.filter((i) => i.passed).length}/${invariants.length} OK` : '现金守恒 · T+1 · 涨跌停 · POV · 归因闭合'}</span>
          </div>
          <InvariantStrip items={invariants} height={46} emptyDetail="需 V3 /backtest/run 返回 invariants[]（现金/持仓守恒、T+1、涨跌停、POV 上限、归因闭合）" />
        </div>
      ) : null}
      <Panel
        title="策略 / 基准 / 超额净值 · 水下回撤 (Underwater)"
        prd="Q5.3"
        busy={stale}
        tools={
          <Legend
            items={[
              { label: '策略', color: 'var(--tx)' },
              { label: '基准', color: 'var(--tx3)' },
              { label: '超额', color: 'var(--sig)' },
              { label: '策略回撤', box: true, style: { background: 'var(--dn)', opacity: 0.6 } },
              { label: '超额回撤', color: 'var(--warn)' }
            ]}
          />
        }
      >
        <DataSlot height={308} ready={ready && r!.dates.length > 0} loading={bt.loading} error={bt.error}>
          {() => (
            <div style={dim}>
              <LineChart
                height={212}
                xs={r!.dates}
                sync="bt"
                base={1}
                bands={REGIME_BANDS}
                ticks={4}
                yFmt={(v) => v.toFixed(2)}
                series={[
                  { name: '策略', data: r!.strategy_nav, color: 'var(--tx)', w: 1.5, fmt: (v) => fx(v, 4) },
                  { name: '基准 ' + um.bench, data: r!.bench_nav, color: 'var(--tx3)', w: 1.2, fmt: (v) => fx(v, 4) },
                  { name: '超额净值', data: r!.excess_nav, color: 'var(--sig)', w: 1.6, area: true, areaBase: 1, areaOp: 0.08, fmt: (v) => fx(v, 4) }
                ]}
              />
              <div style={{ marginTop: 4 }}>
                <LineChart
                  height={92}
                  xs={r!.dates}
                  sync="bt"
                  ticks={3}
                  yHi={0}
                  pad={{ t: 4 }}
                  yFmt={ddTick}
                  series={[
                    { name: '策略回撤', data: neg(r!.strategy_dd), color: 'var(--dn)', w: 1, area: true, areaOp: 0.25, fmt: ddFmt },
                    { name: '超额回撤', data: neg(r!.excess_dd), color: 'var(--warn)', w: 1.2, fmt: ddFmt }
                  ]}
                />
              </div>
            </div>
          )}
        </DataSlot>
      </Panel>
      <div className="bg2">
        <Panel
          title={<TermTip id="gross_vs_net">A 股微结构撮合损耗拆解 · 年化超额 (%)</TermTip>}
          prd="Q5.2"
          busy={stale}
          tools={f ? <Tag tone={ev ? 'warn' : 'mute'}>{ev ? `摩擦 ${fx(f.total_friction_bps, 0)} bps/年` : `向量化 · ${fx(f.total_friction_bps, 0)} bps/年`}</Tag> : null}
        >
          <DataSlot height={248} ready={ready} loading={bt.loading} error={bt.error}>
            {() => (
              <div style={dim}>
                <Waterfall items={fall} height={248} />
              </div>
            )}
          </DataSlot>
        </Panel>
        <Panel
          title={<TermTip id="price_limit_rules">{`撮合失败审计${rej.length ? ` · 最近 ${rej.length} 笔` : ''}`}</TermTip>}
          prd="Q5.2"
          flush
          busy={stale}
          tools={<span className="ps">{r ? `${r.engine} 引擎` : ''}</span>}
        >
          <DataSlot height={248} ready={ready} loading={bt.loading} error={bt.error}>
            {() => (
              <div style={dim}>
                {rej.length ? (
                  <table className="tbl dense">
                    <thead>
                      <tr>
                        <th>日期</th>
                        <th>标的</th>
                        <th>方向</th>
                        <th>失败原因</th>
                        <th className="n">损耗 bps</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rej.map((x, i) => (
                        <tr key={`${x.trade_date}-${x.symbol}-${i}`}>
                          <td className="mono sub" title={x.trade_date}>
                            {x.trade_date.length >= 10 ? x.trade_date.slice(5, 10) : x.trade_date}
                          </td>
                          <td>
                            <span className="mono" style={{ fontSize: 10.5 }}>
                              {x.symbol}
                            </span>{' '}
                            <span className="sub">{x.name}</span>
                          </td>
                          <td className={x.side === 'BUY' ? 'c-up' : 'c-dn'}>{x.side === 'BUY' ? '买' : '卖'}</td>
                          <td style={{ whiteSpace: 'normal' }}>{x.reason}</td>
                          <td className="n">{fx(x.loss_bps, 1)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <EmptyState
                    height={210}
                    title="NO REJECTIONS"
                    detail={r!.engine === 'screening' ? '向量化快筛不模拟逐笔撮合 · 切换 event 引擎查看' : '本次回测无撮合失败记录'}
                  />
                )}
                <div
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    padding: '7px 10px',
                    borderTop: '1px solid var(--line)',
                    font: '10.5px var(--f-mono)',
                    color: 'var(--tx3)'
                  }}
                >
                  <span>
                    撮合失败 {thou(r!.total_rejections)} / {thou(r!.total_fills)} 笔{rejRate !== null ? ` · ${fx(rejRate, 2)}%` : ''}
                  </span>
                  {f ? (
                    <span>
                      涨跌停/停牌损耗 <b className="c-warn">{fx(abs(f.limit_suspend_pct) * 100, 0)} bps/年</b>
                    </span>
                  ) : null}
                </div>
              </div>
            )}
          </DataSlot>
        </Panel>
      </div>
      <Panel
        title={<TermTip id="barra_attribution" value={specShare}>Brinson-Fachler & Barra 闭合归因 · 真假 Alpha 甄别</TermTip>}
        prd="Q5.3"
        busy={stale}
        tools={
          a ? (
            specShare !== null && specShare >= UI_LIMITS.specific_alpha_share_min ? (
              <Tag tone="pass">真 Alpha 主导 · {fx(specShare, 0)}%</Tag>
            ) : (
              <Tag tone="warn">风格 Beta 偏高 · Alpha {specShare !== null ? fx(specShare, 0) + '%' : '—'}</Tag>
            )
          ) : null
        }
      >
        <DataSlot height={230} ready={!!a} loading={bt.loading} error={bt.error}>
          {() => (
            <div style={dim}>
              <AttributionBody a={a!} />
            </div>
          )}
        </DataSlot>
      </Panel>
      <CapacitySection embedded={r?.capacity_frontier} experimentId={experimentId} universeId={universe} snapshotId={r?.snapshot_id ?? snapshotId} apiOnline={apiOnline} prd="Q5.4" />
    </div>
  );
}
