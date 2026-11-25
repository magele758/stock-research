'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { quantApi, QUANT_API_URL } from '@/lib/quant-api';
import { VIEWS } from '@/lib/meta';
import { fx } from '@/lib/format';
import { useApi } from '@/lib/use-api';
import { useWorkbench } from '@/lib/workbench';
import type { ExperimentRecord } from '@/lib/types';
import { Icon } from './icons';
import { Tag, triTone } from './ui';

export function verdictTag(e: Pick<ExperimentRecord, 'verdict' | 'status_badge'>) {
  const badge = (e.status_badge || e.verdict || '').toUpperCase();
  const tone = /PROMOTED/.test(badge) ? 'pass' : /BASELINE/.test(badge) ? 'mute' : /HOLD/.test(badge) ? 'warn' : triTone(e.verdict);
  return <Tag tone={tone === 'pass' && !/PROMOTED/.test(badge) ? 'info' : tone}>{badge || e.verdict.toUpperCase()}</Tag>;
}

export function SideNav() {
  const { view, apiOnline, api, experimentId, setExperimentId, universe } = useWorkbench();
  const router = useRouter();
  const exps = useApi(apiOnline ? `nav-exps:${api.health?.snapshot_id ?? ''}` : null, (s) => quantApi.experiments(20, s));
  return (
    <nav className="nav">
      <div className="nsec">
        <span>WORKSPACE</span>
        <span>06</span>
      </div>
      {VIEWS.map((v) => (
        <Link key={v.key} href={v.path} className={`ni ${view === v.key ? 'on' : ''}`}>
          <Icon name={v.icon} size={16} />
          <b>{v.label}</b>
          <small>{v.path}</small>
          <em>{v.prd}</em>
        </Link>
      ))}
      <div className="nsec" style={{ marginTop: 8, borderTop: '1px solid var(--line)', paddingTop: 10 }}>
        <span>EXPERIMENT REGISTRY</span>
        <span>{exps.data ? String(exps.data.length).padStart(2, '0') : '--'}</span>
      </div>
      <div className="xl">
        {!apiOnline ? (
          <div style={{ padding: '6px 12px', font: '10.5px/1.6 var(--f-mono)', color: 'var(--tx3)' }}>API OFFLINE · 实验注册表不可用</div>
        ) : exps.error ? (
          <div style={{ padding: '6px 12px', font: '10.5px/1.6 var(--f-mono)', color: 'var(--up)' }}>{exps.error.message}</div>
        ) : !exps.data ? (
          <div style={{ padding: '6px 12px' }} className="solv">
            <i className="spin" />
            加载实验…
          </div>
        ) : exps.data.length === 0 ? (
          <div style={{ padding: '6px 12px', font: '10.5px/1.6 var(--f-mono)', color: 'var(--tx3)' }}>暂无实验记录</div>
        ) : (
          exps.data.map((e) => (
            <button
              key={e.experiment_id}
              type="button"
              className={`xi ${view === 'models' && experimentId === e.experiment_id ? 'on' : ''}`}
              onClick={() => {
                setExperimentId(e.experiment_id);
                if (view !== 'models') router.push('/models');
              }}
            >
              <div>
                <span>{e.experiment_id}</span>
                {verdictTag(e)}
              </div>
              <small>
                {e.name} · OOS {fx(e.oos_sharpe)}
              </small>
            </button>
          ))
        )}
      </div>
      <div className="nfoot">
        quant-api <b className={apiOnline ? 'c-pass' : 'c-up'}>{apiOnline ? 'ONLINE' : 'OFFLINE'}</b> · {QUANT_API_URL ? QUANT_API_URL.replace(/^https?:\/\//, '') : 'same-origin'}
        <br />
        snapshot <b>{api.health?.snapshot_id ?? '—'}</b>
        <br />
        universe <b>{universe}</b> · quant-mcp <b>:{api.health?.mcp_port ?? 8101}</b>
      </div>
    </nav>
  );
}
