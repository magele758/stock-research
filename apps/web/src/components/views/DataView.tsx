'use client';

import { useState } from 'react';
import { Icon } from '@/components/icons';
import { TermTip } from '@/components/glossary/TermTip';
import { ApiNotice, DataSlot, Kpi, Panel, Seg, SolveMeta, Tag, ViewHeader, triTone, type Tone } from '@/components/ui';
import { fmtAge, fmtMb, fmtMs, fmtTs, isNum, pct, shortHash, sum, thou } from '@/lib/format';
import { quantApi, QuantApiError } from '@/lib/quant-api';
import type { DataHealthReport, DataSyncRequest, McpToolStatus, QualityCheckItem, SyncProvider } from '@/lib/types';
import { useApi } from '@/lib/use-api';
import { usePublishMetrics, useWorkbench } from '@/lib/workbench';

type SyncMode = DataSyncRequest['mode'];

const PROVIDERS: { key: SyncProvider; label: string }[] = [
  { key: 'tushare', label: 'Tushare' },
  { key: 'baostock', label: 'BaoStock' },
  { key: 'edgar', label: 'EDGAR' }
];
const MODES: { key: SyncMode; label: string }[] = [
  { key: 'incremental', label: '增量同步' },
  { key: 'check', label: '校验' }
];
const MCP_CONTRACT_TOOLS = 21;

interface SyncOutcome {
  provider: SyncProvider;
  mode: SyncMode;
  ms: number;
  body?: unknown;
  error?: Error;
}

const TRI_LABEL: Record<string, string> = { pass: 'PASS', warn: 'WARN', fail: 'FAIL' };
const triTag = (s: string | null | undefined) => <Tag tone={triTone(s)}>{s ? (TRI_LABEL[s] ?? s.toUpperCase()) : '—'}</Tag>;
const lightColor = (s: string) => (s === 'pass' ? 'var(--pass)' : s === 'warn' ? 'var(--warn)' : s === 'fail' ? 'var(--up)' : 'var(--tx3)');

const asObj = (v: unknown): Record<string, unknown> => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {});
const asStr = (v: unknown): string | undefined => (typeof v === 'string' && v ? v : typeof v === 'number' || typeof v === 'boolean' ? String(v) : undefined);

function syncTone(o: SyncOutcome): 'info' | 'warn' | '' {
  if (o.error) return o.error instanceof QuantApiError && o.error.kind === 'timeout' ? 'warn' : '';
  const b = asObj(o.body);
  const st = (asStr(b.status) ?? '').toLowerCase();
  if (b.ok === false || /fail|error/.test(st)) return '';
  if (['ok', 'success', 'done', 'completed'].includes(st) || b.ok === true) return 'info';
  return 'warn';
}

function SyncResult({ outcome, onClose }: { outcome: SyncOutcome; onClose: () => void }) {
  const tone = syncTone(outcome);
  const qe = outcome.error instanceof QuantApiError ? outcome.error : undefined;
  const b = asObj(outcome.body);
  const fields: [string, string | undefined][] = [
    ['status', asStr(b.status)],
    ['ok', asStr(b.ok)],
    ['provider', asStr(b.provider)],
    ['mode', asStr(b.mode)],
    ['message', asStr(b.message) ?? asStr(b.reason)],
    ['rows', isNum(b.rows) ? thou(b.rows) : asStr(b.rows)],
    ['snapshot_id', asStr(b.snapshot_id) ?? asStr(b.latest_snapshot_id)],
    ['snapshot_qa', asStr(b.latest_snapshot_qa)],
    ['synthetic_fallback', asStr(b.synthetic_fallback)],
    ['error', asStr(b.error)]
  ];
  return (
    <div className={`notice ${tone}`}>
      <Icon name={outcome.error ? 'warn' : 'sync'} size={14} style={{ color: tone === 'info' ? 'var(--sig)' : tone === 'warn' ? 'var(--warn)' : 'var(--up)', flex: 'none', marginTop: 1 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <b>
            POST /data/sync · {outcome.provider} · {outcome.mode}
          </b>
          <span className="mono c-3">{fmtMs(outcome.ms)}</span>
          <button type="button" className="btn sm" style={{ marginLeft: 'auto' }} onClick={onClose}>
            <Icon name="x" size={10} sw={2} />
            关闭
          </button>
        </div>
        {outcome.error ? (
          <div style={{ marginTop: 4 }}>
            <span className="mono">{qe?.status !== undefined ? `HTTP ${qe.status}` : (qe?.kind ?? 'error').toUpperCase()}</span> · {outcome.error.message}
            {qe?.detail !== undefined ? <pre className="code">{typeof qe.detail === 'string' ? qe.detail : JSON.stringify(qe.detail, null, 2)}</pre> : null}
          </div>
        ) : (
          <>
            <div className="mono" style={{ display: 'flex', flexWrap: 'wrap', gap: '2px 14px', marginTop: 4, fontSize: 10.5 }}>
              {fields
                .filter(([, v]) => v !== undefined)
                .map(([k, v]) => (
                  <span key={k}>
                    <span className="c-3">{k}</span> <span style={{ color: 'var(--tx)' }}>{v}</span>
                  </span>
                ))}
            </div>
            <details style={{ marginTop: 4 }}>
              <summary className="mono c-3" style={{ cursor: 'pointer', fontSize: 10 }}>
                raw JSON
              </summary>
              <pre className="code">{JSON.stringify(outcome.body, null, 2) ?? 'null'}</pre>
            </details>
          </>
        )}
      </div>
    </div>
  );
}

/** QA sentinel → glossary term (check ids from quant_core.lake.qa_sentinel). */
const QA_TERM: [RegExp, string][] = [
  [/QA-01|OHLCV|GEOM/i, 'ohlcv_geometry'],
  [/QA-02|CORP|ADJ/i, 'corp_action_adj'],
  [/QA-03|PIT|FIN/i, 'look_ahead_bias'],
  [/QA-04|DELIST|SURVIVOR/i, 'survivorship_bias'],
  [/QA-05|UNIVERSE|CONSTITUENT/i, 'historical_constituents'],
  [/QA-06|INDUSTRY/i, 'sw_industry_pit'],
  [/QA-07|LIMIT/i, 'price_limit_rules']
];
const qaTerm = (id: string) => QA_TERM.find(([re]) => re.test(id))?.[1] ?? 'point_in_time';

function GuardCard({ c }: { c: QualityCheckItem }) {
  const samples = c.sample_symbols ?? [];
  return (
    <div className={`gc ${c.status === 'pass' ? '' : c.status}`}>
      <div className="gh">
        <TermTip id={qaTerm(c.check_id)} display={`${c.status.toUpperCase()} · affected ${thou(c.affected_count)}`} context={{ check_id: c.check_id, affected: c.affected_count }} className="gh-t">
          {c.name}
        </TermTip>
        {triTag(c.status)}
      </div>
      <div className="gv" title="affected_count">
        {thou(c.affected_count)}
        <small className="c-3" style={{ fontSize: 10, fontWeight: 400, marginLeft: 4 }}>
          affected
        </small>
      </div>
      <p title={c.detail}>{c.detail}</p>
      {samples.length ? (
        <p className="mono" title={samples.join(', ')} style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {samples.slice(0, 4).join(' ')}
          {samples.length > 4 ? ` +${samples.length - 4}` : ''}
        </p>
      ) : null}
    </div>
  );
}

function GuardLights({ checks }: { checks: QualityCheckItem[] }) {
  const n = (s: string) => checks.filter((c) => c.status === s).length;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
      <span style={{ display: 'flex', gap: 3 }}>
        {checks.map((c) => (
          <i key={c.check_id} title={`${c.name} · ${c.status.toUpperCase()}`} style={{ width: 10, height: 10, display: 'inline-block', background: lightColor(c.status) }} />
        ))}
      </span>
      <span className="solv">
        <span className="c-pass">{n('pass')} PASS</span>
        <span className="c-warn">{n('warn')} WARN</span>
        <span className="c-up">{n('fail')} FAIL</span>
      </span>
    </div>
  );
}

function sideEffectTag(t: McpToolStatus, approval: string | undefined) {
  const se = (t.side_effect ?? '').toLowerCase();
  const ap = (approval ?? '').toLowerCase();
  const bg = /async|bg/.test(se);
  const title = `side_effect=${t.side_effect ?? '—'} · approval=${approval ?? '—'}`;
  if (ap === 'always' || ap === 'ask' || /ask/.test(se)) return <Tag tone="warn" title={title}>{bg ? 'ASK·BG' : 'ASK'}</Tag>;
  if (bg) return <Tag tone="info" title={title}>BG_RUN</Tag>;
  if (!se && !ap) return <span className="c-3">—</span>;
  return <Tag tone="mute" title={title}>READ</Tag>;
}

function mcpStatusTone(s: string | undefined): Tone {
  const v = (s ?? '').toLowerCase();
  if (['ready', 'enabled', 'ok', 'online', 'healthy'].includes(v)) return 'pass';
  if (['disabled', 'error', 'failed', 'offline'].includes(v)) return 'fail';
  return triTone(v);
}

function p95Bar(ms: number | undefined) {
  if (!isNum(ms) || ms <= 0) return null;
  const w = Math.max(2, 3 + (16 * Math.log10(Math.max(1, ms / 10))) / Math.log10(60000));
  return <span className="bar-in" style={{ width: w, background: ms > 60000 ? 'var(--warn)' : 'var(--sig)' }} />;
}

export function DataView() {
  const { snapshotId, apiOnline, dock } = useWorkbench();
  const [provider, setProvider] = useState<SyncProvider>('tushare');
  const [mode, setMode] = useState<SyncMode>('incremental');
  const [syncing, setSyncing] = useState(false);
  const [outcome, setOutcome] = useState<SyncOutcome | null>(null);

  const health = useApi<DataHealthReport>(apiOnline ? `data-health:${snapshotId ?? 'latest'}` : null, (s) => quantApi.dataHealth(snapshotId, s));
  const snaps = useApi(apiOnline ? 'data-snapshots' : null, (s) => quantApi.snapshots(s));
  const mcp = useApi(apiOnline ? 'mcp-catalog' : null, (s) => quantApi.mcpCatalog(s));

  const h = health.data;
  const checks = h?.checks ?? [];
  const datasets = h?.datasets ?? [];
  const stale = new Set(h?.stale_datasets ?? []);
  const passed = checks.filter((c) => c.status === 'pass').length;

  usePublishMetrics(
    'data',
    h
      ? {
          overall_status: h.overall_status,
          coverage_pct: h.coverage_pct,
          failed_checks: h.failed_checks?.length ?? checks.filter((c) => c.status === 'fail').length,
          snapshot: h.snapshot_id
        }
      : null
  );

  const runSync = async () => {
    setSyncing(true);
    const t0 = performance.now();
    try {
      const res = await quantApi.dataSync({ provider, mode });
      setOutcome({ provider, mode, ms: performance.now() - t0, body: res });
      health.reload();
      snaps.reload();
    } catch (err) {
      setOutcome({ provider, mode, ms: performance.now() - t0, error: err instanceof Error ? err : new Error(String(err)) });
    } finally {
      setSyncing(false);
    }
  };

  const shownSnapshot = snapshotId ?? h?.snapshot_id;

  // /mcp/catalog carries fields the shared normalizer does not map (server object, approval, avg_ms).
  const rawCat = asObj(mcp.data?.raw);
  const rawServer = asObj(rawCat.server);
  const rawTools = new Map<string, Record<string, unknown>>();
  (Array.isArray(rawCat.tools) ? rawCat.tools : []).forEach((t) => {
    const o = asObj(t);
    const name = asStr(o.name);
    if (name) rawTools.set(name, o);
  });
  const tools = mcp.data?.tools ?? [];
  const serverName = mcp.data?.server ?? asStr(rawServer.name) ?? 'quant-mcp';
  const transport = mcp.data?.transport ?? asStr(rawServer.transport);
  const hasAvg = tools.some((t) => isNum(rawTools.get(t.name)?.avg_ms));
  const calls24 = sum(tools.map((t) => (isNum(t.calls_24h) ? t.calls_24h : 0)));

  const totRows = sum(datasets.map((d) => (isNum(d.row_count) ? d.row_count : 0)));
  const totDisk = sum(datasets.map((d) => (isNum(d.disk_mb) ? d.disk_mb : 0)));

  return (
    <div className="stack">
      <ViewHeader
        path="/data"
        prds={['Q1']}
        title="PIT 量化数据底座与质量哨兵"
        sub="多源接入 · 主备切换 · DuckDB + Parquet · 零幸存者偏差 / PIT / 复权自动化审计 · 7 项质量哨兵"
        actions={
          <>
            <Tag tone={snapshotId ? 'info' : 'mute'} title={snapshotId ? '快照已锁定' : '跟随最新快照'}>
              {snapshotId ? <Icon name="lock" size={10} /> : null}
              {shownSnapshot ?? 'SNAPSHOT —'}
            </Tag>
            <Seg value={provider} options={PROVIDERS} onChange={setProvider} />
            <Seg value={mode} options={MODES} onChange={setMode} />
            <button type="button" className="btn pri" disabled={!apiOnline || syncing} onClick={runSync}>
              {syncing ? <i className="spin" /> : <Icon name="sync" size={11} />}
              触发增量同步 / 校验
            </button>
            <button type="button" className="btn" onClick={() => dock.send('体检当前 PIT 数据快照质量哨兵与 MCP 工具状态', { bot: 'quant-lead' })}>
              <Icon name="shield" size={11} />
              Bot 数据体检
            </button>
          </>
        }
      />

      <ApiNotice error={health.error} what="数据健康 /data/health" />
      {outcome ? <SyncResult outcome={outcome} onClose={() => setOutcome(null)} /> : null}

      <div className="kpis k5">
        <Kpi
          label="整体状态 overall_status"
          value={h ? triTag(h.overall_status) : '—'}
          sub={
            h ? (
              <TermTip id="fact_fingerprint" as="span" display={shortHash(h.fact_fingerprint, 6)}>
                fp {shortHash(h.fact_fingerprint, 6)}
              </TermTip>
            ) : undefined
          }
        />
        <Kpi
          label={<TermTip id="coverage" value={h && isNum(h.coverage_pct) ? (h.coverage_pct > 1 ? h.coverage_pct / 100 : h.coverage_pct) : null} display={h ? pct(h.coverage_pct, 2) : undefined}>覆盖率</TermTip>}
          value={h ? pct(h.coverage_pct, 2) : '—'}
          sub={h ? <span>as_of {fmtTs(h.as_of)}</span> : undefined}
        />
        <Kpi label={<TermTip id="freshness" display={h ? fmtAge(h.freshness_sec) : undefined}>数据新鲜度</TermTip>} value={h ? fmtAge(h.freshness_sec) : '—'} sub={h ? <span>freshness_sec {thou(h.freshness_sec)}</span> : undefined} />
        <Kpi
          label="质量哨兵"
          value={h ? `${passed} / ${checks.length}` : '—'}
          sub={
            h ? (
              <>
                {checks.some((c) => c.status === 'warn') ? <Tag tone="warn">{checks.filter((c) => c.status === 'warn').length} WARN</Tag> : null}
                {checks.some((c) => c.status === 'fail') ? <Tag tone="fail">{checks.filter((c) => c.status === 'fail').length} FAIL</Tag> : null}
                {passed === checks.length ? <Tag tone="pass">ALL PASS</Tag> : null}
              </>
            ) : undefined
          }
        />
        <Kpi
          label="数据集"
          value={h ? String(datasets.length) : '—'}
          sub={h ? stale.size ? <Tag tone="warn">{stale.size} STALE</Tag> : <Tag tone="pass">0 STALE</Tag> : undefined}
        />
      </div>

      <Panel
        title="数据集状态矩阵 · A 股 + 美股 US_EQ"
        prd="Q1.1"
        flush
        busy={health.loading && !!h}
        tools={<SolveMeta loading={health.loading} elapsedMs={health.elapsedMs} />}
      >
        <DataSlot height={200} ready={!!h} loading={health.loading} error={health.error}>
          {() =>
            datasets.length ? (
              <table className="tbl dense">
                <thead>
                  <tr>
                    <th>数据集</th>
                    <th>频率</th>
                    <th>主源 / 备源</th>
                    <th className="n">覆盖率</th>
                    <th>最新日期</th>
                    <th>PIT 对账</th>
                    <th className="n">行数</th>
                    <th className="n">磁盘占用</th>
                    <th>状态</th>
                  </tr>
                </thead>
                <tbody>
                  {datasets.map((d) => (
                    <tr key={d.dataset}>
                      <td className="mono">{d.dataset}</td>
                      <td className="mono sub">{d.freq}</td>
                      <td>
                        {d.primary_source}
                        <span className="sub"> / {d.backup_source || '—'}</span>
                      </td>
                      <td className={`n ${isNum(d.coverage_pct) && d.coverage_pct < 95 ? 'c-warn' : ''}`}>{pct(d.coverage_pct, 2)}</td>
                      <td className="mono sub">{d.latest_date || '—'}</td>
                      <td>{triTag(d.pit_status)}</td>
                      <td className="n">{thou(d.row_count)}</td>
                      <td className="n">{fmtMb(d.disk_mb)}</td>
                      <td>
                        {stale.has(d.dataset) ? (
                          <span className="st c-warn">
                            <i style={{ background: 'var(--warn)' }} />
                            STALE
                          </span>
                        ) : (
                          <span className="st c-pass">
                            <i style={{ background: 'var(--pass)' }} />
                            OK
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <td className="sub" colSpan={6} style={{ borderTop: '1px solid var(--line2)' }}>
                      合计 · {datasets.length} 个数据集 · snapshot {h?.snapshot_id ?? '—'}
                    </td>
                    <td className="n c-2" style={{ borderTop: '1px solid var(--line2)' }}>
                      {thou(totRows)}
                    </td>
                    <td className="n c-2" style={{ borderTop: '1px solid var(--line2)' }}>
                      {fmtMb(totDisk)}
                    </td>
                    <td style={{ borderTop: '1px solid var(--line2)' }} />
                  </tr>
                </tfoot>
              </table>
            ) : (
              <div className="solv" style={{ padding: 10 }}>
                datasets 为空 · 数据湖尚未登记任何数据集
              </div>
            )
          }
        </DataSlot>
      </Panel>

      <div className="g2" style={{ gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1.3fr)' }}>
        <Panel title={<TermTip id="point_in_time">7 项 PIT 质量哨兵 · Bias & Integrity Guard</TermTip>} prd="Q1.3" busy={health.loading && !!h}>
          <DataSlot height={260} ready={!!h} loading={health.loading} error={health.error}>
            {() => (
              <>
                <GuardLights checks={checks} />
                <div className="guard">
                  {checks.map((c) => (
                    <GuardCard key={c.check_id} c={c} />
                  ))}
                </div>
                <div className="solv" style={{ marginTop: 8 }}>
                  <Icon name="clock" size={11} />
                  as_of {fmtTs(h?.as_of)} · {h?.snapshot_id ?? '—'}
                  {h?.failed_checks?.length ? <span className="c-up"> · failed: {h.failed_checks.join(', ')}</span> : null}
                </div>
              </>
            )}
          </DataSlot>
        </Panel>

        <Panel
          title={<TermTip id="mcp_side_effect">{`Quant MCP · ${tools.length || 'N'} Tools 状态表`}</TermTip>}
          prd="Q7.1"
          flush
          busy={mcp.loading && !!mcp.data}
          tools={
            mcp.data ? (
              <>
                <span className="ps">
                  {serverName}
                  {mcp.data.version ? ` v${mcp.data.version}` : ''} · {transport ?? '—'} · {thou(calls24)} 次
                </span>
                <Tag tone={tools.length === MCP_CONTRACT_TOOLS ? 'pass' : 'warn'} title="契约约定 21 个 quant_* 工具">
                  {tools.length}/{MCP_CONTRACT_TOOLS} tools
                </Tag>
              </>
            ) : (
              <SolveMeta loading={mcp.loading} elapsedMs={mcp.elapsedMs} />
            )
          }
        >
          <DataSlot height={260} ready={!!mcp.data} loading={mcp.loading} error={mcp.error}>
            {() =>
              tools.length ? (
                <div style={{ overflowX: 'auto' }}>
                  <table className="tbl dense compact">
                  <thead>
                    <tr>
                      <th>Tool</th>
                      <th>域</th>
                      <th>副作用</th>
                      <th>状态</th>
                      <th className="n">24h</th>
                      {hasAvg ? <th className="n">AVG</th> : null}
                      <th className="n">P50</th>
                      <th className="n">P95</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tools.map((t) => {
                      const rt = rawTools.get(t.name) ?? {};
                      const avg = rt.avg_ms;
                      return (
                        <tr key={t.name} title={t.description ?? asStr(rt.rest)}>
                          <td className="mono" style={{ fontSize: 10 }}>
                            {t.name.startsWith('quant_') ? (
                              <>
                                <span className="c-3">quant_</span>
                                {t.name.slice(6)}
                              </>
                            ) : (
                              t.name
                            )}
                          </td>
                          <td className="mono sub">{t.layer ?? '—'}</td>
                          <td>{sideEffectTag(t, t.approval_mode ?? asStr(rt.approval))}</td>
                          <td>{t.status ? <Tag tone={mcpStatusTone(t.status)}>{t.status.toUpperCase()}</Tag> : <span className="c-3">—</span>}</td>
                          <td className="n">{thou(t.calls_24h)}</td>
                          {hasAvg ? <td className="n c-2">{fmtMs(isNum(avg) ? avg : undefined)}</td> : null}
                          <td className="n c-2">{fmtMs(t.p50_ms)}</td>
                          <td className="n">
                            {fmtMs(t.p95_ms)}
                            {p95Bar(t.p95_ms)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                  </table>
                </div>
              ) : (
                <div className="solv" style={{ padding: 10 }}>
                  /mcp/catalog 返回 0 个工具
                </div>
              )
            }
          </DataSlot>
        </Panel>
      </div>

      <Panel
        title={<TermTip id="pit_snapshot_lock">快照清单 · Snapshot Manifest</TermTip>}
        prd="Q1.2"
        flush
        busy={snaps.loading && !!snaps.data}
        tools={<SolveMeta loading={snaps.loading} elapsedMs={snaps.elapsedMs} />}
      >
        <DataSlot height={140} ready={!!snaps.data} loading={snaps.loading} error={snaps.error}>
          {() =>
            snaps.data && snaps.data.length ? (
              <table className="tbl dense">
                <thead>
                  <tr>
                    <th>snapshot_id</th>
                    <th>as_of</th>
                    <th>市场</th>
                    <th>created_at</th>
                    <th>manifest sha256</th>
                    <th className="n">总行数</th>
                    <th>QA</th>
                    <th className="n">退市股</th>
                    <th>数据来源</th>
                  </tr>
                </thead>
                <tbody>
                  {snaps.data.map((s) => {
                    const counts = s.table_counts ?? {};
                    const rows = sum(Object.values(counts).filter(isNum));
                    const sel = s.snapshot_id === shownSnapshot;
                    return (
                      <tr key={s.snapshot_id} className={sel ? 'sel' : ''}>
                        <td className="mono">{s.snapshot_id}</td>
                        <td className="mono sub">{s.as_of}</td>
                        <td className="mono sub">{(s.markets ?? []).join(' · ') || '—'}</td>
                        <td className="mono sub">{fmtTs(s.created_at)}</td>
                        <td className="mono" title={s.manifest_sha256}>
                          {shortHash(s.manifest_sha256)}
                        </td>
                        <td
                          className="n"
                          title={Object.entries(counts)
                            .map(([k, v]) => `${k}: ${thou(v)}`)
                            .join('\n')}
                        >
                          {thou(rows)}
                        </td>
                        <td>{triTag(s.qa_status)}</td>
                        <td className="n">{thou(s.delisted_count)}</td>
                        <td className="sub" title={(s.source_provenance ?? []).join(', ')} style={{ maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {(s.source_provenance ?? []).join(', ') || '—'}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            ) : (
              <div className="solv" style={{ padding: 10 }}>
                /data/snapshots 为空 · 尚未生成任何快照
              </div>
            )
          }
        </DataSlot>
      </Panel>
    </div>
  );
}
