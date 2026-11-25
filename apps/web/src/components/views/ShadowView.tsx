'use client';

import { useState } from 'react';
import { BarChart } from '@/components/charts/BarChart';
import { ShadowDriftChart } from '@/components/charts/ShadowDriftChart';
import { Sparkline } from '@/components/charts/Sparkline';
import { Icon } from '@/components/icons';
import { ApiNotice, DataSlot, Kpi, Legend, Panel, Seg, SolveMeta, Tag, ViewHeader, type Tone } from '@/components/ui';
import { downloadBlob, fmtTs, fx, isNum, pct, sg, shortHash, sum } from '@/lib/format';
import { DEFAULT_SHADOW_ID, universeMeta } from '@/lib/meta';
import { quantApi } from '@/lib/quant-api';
import type { AuthorityGateManifest, FactorDecayItem, ShadowDriftResult } from '@/lib/types';
import { useApi } from '@/lib/use-api';
import { usePublishMetrics, useWorkbench } from '@/lib/workbench';

type WindowKey = '20' | '60' | '120';
const WINDOWS: { key: WindowKey; label: string }[] = [
  { key: '20', label: '20D' },
  { key: '60', label: '60D' },
  { key: '120', label: '120D' }
];

const RISK: Record<ShadowDriftResult['risk_state'], { tone: Tone; label: string }> = {
  ok: { tone: 'pass', label: 'RISK OK' },
  alert: { tone: 'warn', label: 'RISK ALERT' },
  kill: { tone: 'fail', label: 'KILL' },
  unknown: { tone: 'mute', label: 'RISK UNKNOWN' }
};

const DECAY_TONE: Record<FactorDecayItem['status'], Tone> = { ACTIVE: 'pass', DEGRADING: 'warn', 'KILL-SWITCH': 'fail' };

const PEROLD_LABELS = ['开盘跳空', '流动性冲击', '涨跌停排队', '合计'];
const PEROLD_COLORS = ['var(--warn)', 'var(--sig)', 'var(--up)', 'var(--tx2)'];

const finSum = (a: number[] | undefined) => sum((a ?? []).filter(isNum));

function csvCell(v: unknown): string {
  const s = v === null || v === undefined ? '' : String(v);
  return /[",\n#]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function exportCsv(m: AuthorityGateManifest) {
  const lines = [
    `# manifest_id=${m.manifest_id},sha256=${m.sha256},execution_mode=${m.execution_mode},broker_connected=false`,
    'symbol,name,side,delta_weight_pct,est_shares,risk_check',
    ...m.signals.map((s) => [s.symbol, s.name, s.side, s.delta_weight_pct, s.est_shares, s.risk_check].map(csvCell).join(','))
  ];
  downloadBlob(`${m.manifest_id}.signals.csv`, lines.join('\n') + '\n', 'text/csv;charset=utf-8');
}

function exportJson(m: AuthorityGateManifest) {
  downloadBlob(`${m.manifest_id}.manifest.json`, JSON.stringify(m, null, 2), 'application/json');
}

function AuthorityGate({ m }: { m: AuthorityGateManifest }) {
  const isolated = m.broker_connected === false && m.execution_mode === 'READ_ONLY_SIGNAL_EXPORT_ONLY';
  const signals = m.signals ?? [];
  const n = (side: string) => signals.filter((s) => s.side === side).length;
  const blocked = signals.filter((s) => s.risk_check === 'BLOCKED').length;
  return (
    <>
      <div className={`gs ${isolated ? 'pass' : 'fail'}`} style={{ marginBottom: 8, flexWrap: 'wrap' }}>
        <b>{m.execution_mode}</b>
        <span>{isolated ? '物理无下单接口 · broker_connected=false' : `broker_connected=${String(m.broker_connected)}`}</span>
      </div>
      <div className="sigf">
        <div>
          <span>manifest_id</span>
          <b title={m.manifest_id}>{m.manifest_id}</b>
        </div>
        <div>
          <span>generated_at</span>
          <b>{fmtTs(m.generated_at)}</b>
        </div>
        <div>
          <span>snapshot_id</span>
          <b title={m.snapshot_id}>{m.snapshot_id}</b>
        </div>
        <div>
          <span>execution_mode</span>
          <b title={m.execution_mode}>{m.execution_mode}</b>
        </div>
        <div>
          <span>broker_connected</span>
          <b className={m.broker_connected === false ? 'c-pass' : 'c-up'}>{m.broker_connected === false ? 'false · 0 交易通道' : String(m.broker_connected)}</b>
        </div>
        <div>
          <span>risk_passed</span>
          <b>{m.risk_passed ? <Tag tone="pass">PASS</Tag> : <Tag tone="fail">FAIL</Tag>}</b>
        </div>
        <div>
          <span>sha256</span>
          <b title={m.sha256}>{shortHash(m.sha256)}</b>
        </div>
        <div>
          <span>signals</span>
          <b>
            {signals.length} · BUY {n('BUY')} / SELL {n('SELL')} / HOLD {n('HOLD')}
            {blocked ? <span className="c-up"> · BLOCKED {blocked}</span> : null}
          </b>
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginTop: 9 }}>
        <button type="button" className="btn" style={{ justifyContent: 'center' }} disabled={!signals.length} onClick={() => exportCsv(m)}>
          <Icon name="file" size={12} />
          导出 CSV
        </button>
        <button type="button" className="btn" style={{ justifyContent: 'center' }} onClick={() => exportJson(m)}>
          <Icon name="download" size={12} />
          导出 JSON
        </button>
      </div>
      <div className="solv" style={{ marginTop: 6 }}>
        <Icon name="lock" size={10} />
        仅导出信号文件，QuantLab 不持有任何券商下单凭据
      </div>
    </>
  );
}

export function ShadowView() {
  const { universe, snapshotId, apiOnline, dock } = useWorkbench();
  const [win, setWin] = useState<WindowKey>('60');
  const shadowId = universe === 'CSI500' ? DEFAULT_SHADOW_ID : `SHADOW-${universe}-MAIN`;
  const windowDays = Number(win);

  const res = useApi<ShadowDriftResult>(
    apiOnline ? `shadow:${universe}:${shadowId}:${windowDays}:${snapshotId ?? 'latest'}` : null,
    (s) => quantApi.shadowDrift({ universe_id: universe, shadow_portfolio_id: shadowId, window_days: windowDays, snapshot_id: snapshotId }, s)
  );
  const d = res.data;
  const dates = d?.dates ?? [];
  const decay = d?.factor_decay ?? [];
  const sGap = finSum(d?.gap_bps);
  const sLiq = finSum(d?.liquidity_bps);
  const sLim = finSum(d?.limit_queue_bps);
  const nLim = (d?.limit_queue_bps ?? []).filter((v) => isNum(v) && v !== 0).length;
  const risk = d ? (RISK[d.risk_state] ?? RISK.unknown) : null;

  usePublishMetrics(
    'shadow',
    d
      ? {
          risk_state: d.risk_state,
          total_is_bps: d.total_is_bps,
          fill_rate_pct: d.fill_rate_pct,
          degrading: decay.filter((f) => f.status === 'DEGRADING').length,
          kill_switch: decay.filter((f) => f.status === 'KILL-SWITCH').length
        }
      : null
  );

  const ctx = { universe, shadowId: d?.shadow_portfolio_id ?? shadowId, snapshotId: d?.snapshot_id ?? snapshotId ?? '' };
  const ready = !!d;
  const busy = res.loading && ready;

  return (
    <div className="stack">
      <ViewHeader
        path="/shadow"
        prds={['Q6']}
        title="影子实盘、滑点漂移与衰减监控"
        sub={`Shadow Portfolio 每日调仓模拟 · 理论 vs 影子 Implementation Shortfall 归因 · 因子 IC 衰减 / 拥挤度预警 · ${universeMeta(universe).label}`}
        actions={
          <>
            <Seg value={win} options={WINDOWS} onChange={setWin} />
            <Tag tone="info">{d?.shadow_portfolio_id ?? shadowId}</Tag>
            {risk ? <Tag tone={risk.tone}>{risk.label}</Tag> : null}
            <Tag tone="pass">
              <Icon name="lock" size={10} />
              SIGNAL-ONLY
            </Tag>
          </>
        }
      />

      <ApiNotice error={res.error} what="影子漂移 /shadow/drift" />

      <div className="kpis k5">
        <Kpi
          label={`${windowDays}D Implementation Shortfall`}
          value={d ? `${fx(d.total_is_bps, 0)} bps` : '—'}
          sub={d ? <span>中位 IS Δ {sg(d.median_is_delta_bps, 2)} bps</span> : undefined}
        />
        <Kpi label="成交率" value={d ? pct(d.fill_rate_pct, 1) : '—'} sub={d ? <span>影子成交 / 目标名义</span> : undefined} />
        <Kpi label="开盘跳空损耗" value={d ? `${sg(sGap, 0)} bps` : '—'} sub={d ? <span>sum(gap_bps) · {dates.length}D</span> : undefined} />
        <Kpi label="流动性冲击" value={d ? `${sg(sLiq, 0)} bps` : '—'} sub={d ? <span>sum(liquidity_bps)</span> : undefined} />
        <Kpi label="涨跌停排队" value={d ? `${sg(sLim, 0)} bps` : '—'} sub={d ? <span>{nLim} 日非零 / {dates.length}D</span> : undefined} />
      </div>

      <div className="sg2">
        <Panel
          title="理论 vs 影子超额漂移 (IS)"
          prd="Q6.1"
          busy={busy}
          tools={
            <>
              <Legend
                items={[
                  { label: '理论', color: 'var(--tx2)', dash: true },
                  { label: '影子', color: 'var(--sig)' },
                  { label: '跳空', color: 'var(--warn)', box: true },
                  { label: '流动性', box: true, style: { background: 'var(--sig)', opacity: 0.55 } },
                  { label: '涨跌停', color: 'var(--up)', box: true }
                ]}
              />
              <SolveMeta loading={res.loading} elapsedMs={res.elapsedMs} />
            </>
          }
        >
          <DataSlot height={262} ready={ready} loading={res.loading} error={res.error}>
            {() =>
              dates.length ? (
                <ShadowDriftChart
                  height={262}
                  dates={dates}
                  theoretical={d!.theoretical_excess_bps}
                  shadow={d!.shadow_excess_bps}
                  gap={d!.gap_bps}
                  liquidity={d!.liquidity_bps}
                  limitQueue={d!.limit_queue_bps}
                  totalIs={d!.total_is_bps}
                />
              ) : (
                <div className="empty" style={{ height: 262 }}>
                  <b>NO SHADOW HISTORY</b>
                  <span>{d!.shadow_portfolio_id} 在该窗口内无影子成交记录</span>
                </div>
              )
            }
          </DataSlot>
        </Panel>

        <div className="stack" style={{ minWidth: 0 }}>
          <Panel title="Perold IS 拆解" sub="bps" busy={busy}>
            <DataSlot height={130} ready={ready} loading={res.loading} error={res.error}>
              {() => (
                <BarChart
                  height={130}
                  values={[sGap, sLiq, sLim, sGap + sLiq + sLim]}
                  labels={PEROLD_LABELS}
                  color={(_, i) => PEROLD_COLORS[i]}
                  gap={14}
                  ticks={3}
                  yFmt={(v) => fx(v, 0)}
                  showVal={(v) => sg(v, 0)}
                  tip={(v, i) => `${PEROLD_LABELS[i]} ${sg(v, 1)} bps`}
                />
              )}
            </DataSlot>
          </Panel>
          <Panel title="Authority Gate · 研究/交易物理隔离" prd="Q6.3" busy={busy}>
            <DataSlot height={220} ready={ready} loading={res.loading} error={res.error}>
              {() => <AuthorityGate m={d!.authority_manifest} />}
            </DataSlot>
          </Panel>
        </div>
      </div>

      <Panel title="在产因子 · 60D IC 衰减与拥挤度预警" prd="Q6.2" flush busy={busy} sub={d ? `${decay.length} factors` : undefined}>
        <DataSlot height={180} ready={ready} loading={res.loading} error={res.error}>
          {() => (
            <>
              {decay.length ? (
                <table className="tbl dense">
                  <thead>
                    <tr>
                      <th>因子</th>
                      <th className="n">60D RankIC</th>
                      <th className="n">历史分位</th>
                      <th className="n">拥挤度</th>
                      <th>IC 位置 · [-0.05, 0.10]</th>
                      <th>健康状态</th>
                      <th>说明</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {decay.map((f) => {
                      const hp = f.hist_percentile;
                      const cr = f.crowding_index;
                      return (
                        <tr key={f.factor_id}>
                          <td>
                            <span className="mono" style={{ fontSize: 11 }}>
                              {f.factor_id}
                            </span>
                            {f.name && f.name !== f.factor_id ? <span className="sub"> · {f.name}</span> : null}
                          </td>
                          <td className={`n ${isNum(f.rank_ic_60d) && f.rank_ic_60d < 0 ? 'c-dn' : ''}`}>{fx(f.rank_ic_60d, 4)}</td>
                          <td className={`n ${isNum(hp) && hp < 20 ? 'c-warn' : ''}`}>
                            {pct(hp, 0)}
                            {isNum(hp) ? (
                              <span className="bar-in" style={{ width: Math.max(1, Math.min(100, hp) * 0.36), background: hp < 20 ? 'var(--warn)' : 'var(--sig)' }} />
                            ) : null}
                          </td>
                          <td className={`n ${isNum(cr) && cr > 0.8 ? 'c-up' : ''}`}>
                            {fx(cr, 2)}
                            {isNum(cr) ? (
                              <span className="bar-in" style={{ width: Math.max(1, Math.min(1, cr) * 36), background: cr > 0.8 ? 'var(--up)' : 'var(--tx3)' }} />
                            ) : null}
                          </td>
                          <td>
                            <Sparkline value={f.rank_ic_60d} status={f.status} />
                          </td>
                          <td>
                            <Tag tone={DECAY_TONE[f.status] ?? 'mute'}>{f.status}</Tag>
                          </td>
                          <td className="c-3" title={f.note} style={{ maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', fontSize: 10.5 }}>
                            {f.note || '—'}
                          </td>
                          <td>
                            <button type="button" className={`btn sm ${f.status !== 'ACTIVE' ? 'warnb' : ''}`} onClick={() => dock.diagnoseFactor(f, ctx)}>
                              <Icon name="route" size={11} />
                              派发 Bot 诊断
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              ) : (
                <div className="solv" style={{ padding: 10 }}>
                  factor_decay 为空 · 影子组合未登记在产因子
                </div>
              )}
              <div className="sigf" style={{ padding: '4px 10px 6px', borderTop: '1px solid var(--line)' }}>
                <div>
                  <span>fact_fingerprint</span>
                  <b title={d!.fact_fingerprint}>{d!.fact_fingerprint || '—'}</b>
                </div>
                <div style={{ borderBottom: 0 }}>
                  <span>snapshot_id · window</span>
                  <b>
                    {d!.snapshot_id} · {dates.length ? `${dates[0]} → ${dates[dates.length - 1]}` : '—'}
                  </b>
                </div>
              </div>
            </>
          )}
        </DataSlot>
      </Panel>
    </div>
  );
}
