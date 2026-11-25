'use client';

import { useEffect, useRef, useState } from 'react';
import { quantApi, QUANT_API_URL, START_COMMANDS } from '@/lib/quant-api';
import { UNIVERSES, universeMeta } from '@/lib/meta';
import { fmtMs, fmtTs } from '@/lib/format';
import { useApi } from '@/lib/use-api';
import { useWorkbench } from '@/lib/workbench';
import { drawerStore, useDrawer } from '@/lib/glossary/store';
import type { UniverseId } from '@/lib/types';
import { BrandMark, Icon } from './icons';
import { CopyCmd, Prd, Tag, triTone } from './ui';
import { AGENT_HOME_START_CMD } from './dock/constants';

function useOutside(open: boolean, close: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) close();
    };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, [open, close]);
  return ref;
}

function SnapshotLock() {
  const { api, apiOnline, snapshotLock, setSnapshotLock } = useWorkbench();
  const [open, setOpen] = useState(false);
  const ref = useOutside(open, () => setOpen(false));
  const snaps = useApi(open && apiOnline ? 'snapshots' : null, (s) => quantApi.snapshots(s));
  const latest = api.health?.snapshot_id ?? null;
  const shown = snapshotLock.locked ? snapshotLock.snapshotId : latest;
  return (
    <div ref={ref} className={`fld ${snapshotLock.locked ? 'locked' : ''}`} style={{ cursor: 'pointer' }} onClick={() => setOpen((o) => !o)} title="PIT 数据快照锁：锁定后全部计算请求固定 snapshot_id，保证可复现">
      <span className="fl" style={{ gap: 4 }}>
        <Icon name={snapshotLock.locked ? 'lock' : 'unlock'} size={10} sw={2} />
        PIT
      </span>
      <b className="v" style={{ color: shown ? 'var(--tx)' : 'var(--tx3)', maxWidth: 185, overflow: 'hidden', textOverflow: 'ellipsis' }} title={shown ?? undefined}>
        {shown ?? (api.state === 'offline' ? 'NO SNAPSHOT' : '…')}
      </b>
      <span className="v" style={{ paddingLeft: 0, fontSize: 9.5, color: 'var(--tx3)' }}>{snapshotLock.locked ? 'LOCKED' : 'LATEST'}</span>
      {open ? (
        <div className="pop" onClick={(e) => e.stopPropagation()} style={{ left: 0, right: 'auto', minWidth: 420 }}>
          <h4>PIT SNAPSHOT LOCK</h4>
          <div style={{ display: 'flex', gap: 6, marginBottom: 8 }}>
            <button type="button" className={`btn sm ${!snapshotLock.locked ? 'pri' : ''}`} onClick={() => setSnapshotLock({ locked: false, snapshotId: null })}>
              跟随最新快照
            </button>
            <button type="button" className={`btn sm ${snapshotLock.locked ? 'pri' : ''}`} disabled={!latest} onClick={() => setSnapshotLock({ locked: true, snapshotId: snapshotLock.snapshotId ?? latest })}>
              <Icon name="lock" size={10} sw={2} />
              锁定当前
            </button>
          </div>
          {!apiOnline ? (
            <div>quant-api 离线，无法列出快照。</div>
          ) : snaps.error ? (
            <div className="c-up">{snaps.error.message}</div>
          ) : !snaps.data ? (
            <div className="solv">
              <i className="spin" />
              加载快照清单…
            </div>
          ) : snaps.data.length === 0 ? (
            <div>
              暂无快照，请先执行 <code className="mono">{START_COMMANDS.seed}</code>
            </div>
          ) : (
            <table className="tbl dense">
              <thead>
                <tr>
                  <th>snapshot_id</th>
                  <th>as_of</th>
                  <th>markets</th>
                  <th>QA</th>
                </tr>
              </thead>
              <tbody>
                {snaps.data.slice(0, 8).map((s) => (
                  <tr key={s.snapshot_id} className={`click ${snapshotLock.locked && snapshotLock.snapshotId === s.snapshot_id ? 'sel' : ''}`} onClick={() => setSnapshotLock({ locked: true, snapshotId: s.snapshot_id })}>
                    <td className="mono">{s.snapshot_id}</td>
                    <td className="mono sub">{fmtTs(s.as_of)}</td>
                    <td className="sub">{s.markets.join(' / ')}</td>
                    <td>
                      <Tag tone={triTone(s.qa_status)}>{s.qa_status.toUpperCase()}</Tag>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      ) : null}
    </div>
  );
}

function ApiChip() {
  const { api, recheckApi } = useWorkbench();
  const [open, setOpen] = useState(false);
  const ref = useOutside(open, () => setOpen(false));
  const cls = api.state === 'online' ? 'ok' : api.state === 'offline' ? 'bad' : 'wait';
  const apiLabel = QUANT_API_URL ? `API :${new URL(QUANT_API_URL).port || '80'}` : 'API PROXY';
  const apiDisplay = QUANT_API_URL || 'same-origin /api/v1';
  return (
    <div ref={ref} className={`hb ${cls}`} style={{ cursor: 'pointer' }} onClick={() => setOpen((o) => !o)} title={api.error ?? `quant-api ${apiDisplay}${api.rttMs !== undefined ? ` · ${fmtMs(api.rttMs)}` : ''}`}>
      <i className={`pulse ${api.state === 'online' ? '' : api.state === 'offline' ? 'off' : 'idle'}`} />
      {apiLabel}
      <b>{api.state === 'online' ? 'ONLINE' : api.state === 'offline' ? 'OFFLINE' : 'CHECKING'}</b>
      <Prd id="Q1.2" />
      {open ? (
        <div className="pop" onClick={(e) => e.stopPropagation()}>
          <h4>QUANT-API · {apiDisplay}</h4>
          {api.state === 'online' ? (
            <div className="sigf">
              <div>
                <span>status</span>
                <b className="c-pass">{api.health?.status ?? 'ok'}</b>
              </div>
              <div>
                <span>snapshot_id</span>
                <b>{api.health?.snapshot_id ?? '—（未 seed）'}</b>
              </div>
              <div>
                <span>markets</span>
                <b>{api.health?.markets?.join(' / ') ?? '—'}</b>
              </div>
              <div>
                <span>mcp_port</span>
                <b>{api.health?.mcp_port ?? '—'}</b>
              </div>
            </div>
          ) : (
            <>
              <div style={{ marginBottom: 6 }}>
                后端未启动或不可达，所有页面仅显示 <b className="c-up">API OFFLINE</b>，不会伪装成在线数据。在仓库根目录执行：
              </div>
              <CopyCmd cmd={START_COMMANDS.setup} />
              <CopyCmd cmd={START_COMMANDS.seed} />
              <CopyCmd cmd={START_COMMANDS.api} />
              {api.error ? <div className="mono c-3" style={{ marginTop: 6, fontSize: 10, wordBreak: 'break-all' }}>{api.error}</div> : null}
            </>
          )}
          <button type="button" className="btn sm" style={{ marginTop: 8 }} onClick={recheckApi}>
            <Icon name="sync" size={10} sw={2} />
            立即重新探测
          </button>
        </div>
      ) : null}
    </div>
  );
}

function AgentChip() {
  const { agent, recheckAgent } = useWorkbench();
  const [open, setOpen] = useState(false);
  const ref = useOutside(open, () => setOpen(false));
  const cls = agent.state === 'online' ? 'ok' : agent.state === 'offline' ? 'bad' : 'wait';
  return (
    <div ref={ref} className={`hb ${cls}`} style={{ cursor: 'pointer' }} onClick={() => setOpen((o) => !o)} title={agent.online ? `Agent Home Daemon ${agent.url} · rtt ${fmtMs(agent.rttMs)}` : `Agent Home Daemon 离线 · tried ${agent.triedUrl ?? ''}`}>
      <i className={`pulse ${agent.state === 'online' ? '' : agent.state === 'offline' ? 'off' : 'idle'}`} />
      Agent Home {agent.online && agent.port ? `:${agent.port}` : ''}
      <b>{agent.state === 'online' ? 'ONLINE' : agent.state === 'offline' ? 'DAEMON OFFLINE' : 'CHECKING'}</b>
      <Prd id="Q7.1" />
      {open ? (
        <div className="pop" onClick={(e) => e.stopPropagation()}>
          <h4>AGENT HOME DAEMON · ppeng-agent-core</h4>
          <div className="sigf">
            <div>
              <span>state</span>
              <b className={agent.online ? 'c-pass' : 'c-up'}>{agent.online ? 'ONLINE' : 'OFFLINE'}</b>
            </div>
            <div>
              <span>url</span>
              <b>{agent.url ?? agent.triedUrl ?? '—'}</b>
            </div>
            <div>
              <span>resolved via</span>
              <b>{agent.source ?? '—'}</b>
            </div>
            <div>
              <span>version / adapter</span>
              <b>
                {agent.version ?? '—'} / {agent.adapter ?? '—'}
              </b>
            </div>
            <div>
              <span>bearer (server-only)</span>
              <b>{agent.authConfigured ? 'RAW_AGENT_AUTH_TOKEN 已注入' : '未配置'}</b>
            </div>
          </div>
          {!agent.online ? (
            <>
              <div style={{ margin: '6px 0 2px' }}>探测顺序：AGENT_HOME_DAEMON_URL → dev-lab.ports.json → :27070 → :37070</div>
              {agent.tried?.length ? <div className="mono c-3" style={{ fontSize: 10 }}>{agent.tried.join('  ·  ')}</div> : null}
              <CopyCmd cmd={AGENT_HOME_START_CMD} />
            </>
          ) : null}
          <button type="button" className="btn sm" style={{ marginTop: 8 }} onClick={recheckAgent}>
            <Icon name="sync" size={10} sw={2} />
            重新探测
          </button>
        </div>
      ) : null}
    </div>
  );
}

function GlossaryButton() {
  const d = useDrawer();
  const on = d?.kind === 'glossary';
  return (
    <button type="button" className={`kbtn ${on ? 'on' : ''}`} onClick={() => drawerStore.toggleGlossary()} title="术语速查库（⌘K / Ctrl+K）" aria-expanded={on}>
      <Icon name="book" size={12} sw={1.8} />
      术语
      <kbd>⌘K</kbd>
    </button>
  );
}

export function TopBar() {
  const { universe, setUniverse, theme, setTheme, prd, setPrd, botOpen, setBotOpen } = useWorkbench();
  const u = universeMeta(universe);
  return (
    <header className="chrome">
      <div className="brand">
        <BrandMark />
        <b>QuantLab Pro</b>
        <span className="ver">V3.0 PRO-QUANT</span>
      </div>
      <div className="ctx">
        <label className="fld">
          <span className="fl">UNIVERSE</span>
          <select value={universe} onChange={(e) => setUniverse(e.target.value as UniverseId)}>
            {UNIVERSES.map((x) => (
              <option key={x.id} value={x.id}>
                {x.label} ({x.id})
              </option>
            ))}
          </select>
          <Icon name="down" size={12} sw={2} className="chv" />
        </label>
        <SnapshotLock />
        <div className="fld fld-bench">
          <span className="fl">BENCH</span>
          <b className="v">
            {u.bench} · {u.market}
          </b>
        </div>
        <Prd id="Q1.3" />
      </div>
      <div className="rt">
        <GlossaryButton />
        <ApiChip />
        <AgentChip />
        <button type="button" className={`tg ${prd ? 'on' : ''}`} onClick={() => setPrd(!prd)} title="切换 PRD 模块编号标注">
          <Icon name="tagp" size={12} sw={2} />
          PRD
        </button>
        <div className="seg">
          <button type="button" className={theme === 'dark' ? 'on' : ''} onClick={() => setTheme('dark')}>
            <Icon name="moon" size={11} sw={2} />
            深色
          </button>
          <button type="button" className={theme === 'light' ? 'on' : ''} onClick={() => setTheme('light')}>
            <Icon name="sun" size={11} sw={2} />
            浅色
          </button>
        </div>
        <button type="button" className={`ib ${botOpen ? 'on' : ''}`} title="折叠 / 展开 Agent Home Bot Dock" onClick={() => setBotOpen(!botOpen)}>
          <Icon name="panel" size={14} sw={1.8} />
        </button>
      </div>
    </header>
  );
}
