'use client';

import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { QuantApiError, QUANT_API_URL, START_COMMANDS } from '@/lib/quant-api';
import { copyText, fmtMs } from '@/lib/format';
import { useWorkbench } from '@/lib/workbench';
import { Icon } from './icons';

export type Tone = 'pass' | 'warn' | 'fail' | 'info' | 'mute' | 'mem';

export function Tag({ tone = 'mute', children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span className={`tag ${tone}`} title={title}>
      {children}
    </span>
  );
}

export function Prd({ id }: { id: string }) {
  return <span className="prd">{id}</span>;
}

export function ViewHeader({ path, prds, title, sub, actions }: { path: string; prds: string[]; title: string; sub: ReactNode; actions?: ReactNode }) {
  return (
    <div className="vh">
      <div style={{ minWidth: 0 }}>
        <div className="crumb">
          <b>{path}</b>
          {prds.map((p) => (
            <Prd key={p} id={p} />
          ))}
        </div>
        <h1>{title}</h1>
        <p>{sub}</p>
      </div>
      {actions ? <div className="vh-act">{actions}</div> : null}
    </div>
  );
}

export function Panel({
  title,
  prd,
  sub,
  tools,
  flush,
  className,
  busy,
  children,
  style
}: {
  title: ReactNode;
  prd?: string;
  sub?: ReactNode;
  tools?: ReactNode;
  flush?: boolean;
  className?: string;
  busy?: boolean;
  children: ReactNode;
  style?: CSSProperties;
}) {
  return (
    <section className={`panel ${className ?? ''}`} style={style}>
      <div className="ph">
        <span className="pt">{title}</span>
        {prd ? <Prd id={prd} /> : null}
        {sub ? <span className="ps">{sub}</span> : null}
        <span className="tools">{tools}</span>
      </div>
      <div className={`pb ${flush ? 'flush' : ''}`}>{children}</div>
      {busy ? (
        <div className="busy-veil">
          <span className="solv">
            <i className="spin" />
            计算中
          </span>
        </div>
      ) : null}
    </section>
  );
}

/** KPI cell; flashes when its value string changes. */
export function Kpi({ label, value, sub, title }: { label: ReactNode; value: ReactNode; sub?: ReactNode; title?: string }) {
  const prev = useRef<string | null>(null);
  const [flash, setFlash] = useState(0);
  const key = typeof value === 'string' || typeof value === 'number' ? String(value) : null;
  useEffect(() => {
    if (key === null) return;
    if (prev.current !== null && prev.current !== key) setFlash((f) => f + 1);
    prev.current = key;
  }, [key]);
  return (
    <div className="kpi" title={title}>
      <div className="kl">{label}</div>
      <div key={flash} className={`kv ${flash ? 'chg' : ''}`}>
        {value}
      </div>
      {sub ? <div className="ks">{sub}</div> : <div className="ks">&nbsp;</div>}
    </div>
  );
}

export function Seg<T extends string>({ value, options, onChange, stretch }: { value: T; options: { key: T; label: ReactNode }[]; onChange: (v: T) => void; stretch?: boolean }) {
  return (
    <div className="seg" style={stretch ? { width: '100%', display: 'flex' } : undefined}>
      {options.map((o) => (
        <button key={o.key} type="button" className={o.key === value ? 'on' : ''} style={stretch ? { flex: 1, justifyContent: 'center' } : undefined} onClick={() => onChange(o.key)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function GateTag({ passed, labels = ['PASS', 'FAIL'] }: { passed: boolean | null | undefined; labels?: [string, string] }) {
  if (passed === null || passed === undefined) return <Tag tone="mute">N/A</Tag>;
  return passed ? <Tag tone="pass">{labels[0]}</Tag> : <Tag tone="fail">{labels[1]}</Tag>;
}

export function triTone(s: string | null | undefined): Tone {
  const v = (s ?? '').toLowerCase();
  if (['pass', 'ok', 'optimal', 'active', 'completed', 'healthy'].includes(v)) return 'pass';
  if (['warn', 'warning', 'degrading', 'alert', 'relaxed_optimal', 'optimal_inaccurate', 'inconclusive', 'hold', 'stale'].includes(v)) return 'warn';
  if (['fail', 'failed', 'reject', 'rejected', 'kill', 'kill-switch', 'infeasible', 'error', 'blocked'].includes(v)) return 'fail';
  return 'mute';
}

export function CopyCmd({ cmd }: { cmd: string }) {
  const [done, setDone] = useState(false);
  return (
    <div className="cmdline">
      <code>{cmd}</code>
      <button
        type="button"
        className="btn sm"
        onClick={async () => {
          if (await copyText(cmd)) {
            setDone(true);
            setTimeout(() => setDone(false), 1400);
          }
        }}
      >
        <Icon name={done ? 'check' : 'copy'} size={10} sw={2} />
        {done ? '已复制' : '复制'}
      </button>
    </div>
  );
}

/** Honest placeholder used inside chart panels when there is no real data to draw. */
export function EmptyState({ height = 200, title, detail, error }: { height?: number; title: string; detail?: ReactNode; error?: boolean }) {
  return (
    <div className={`empty ${error ? 'err' : ''}`} style={{ height }}>
      <b>{title}</b>
      {detail ? <span>{detail}</span> : null}
    </div>
  );
}

/** Placeholder for one panel body given the page-level request state. */
export function DataSlot({
  height,
  ready,
  loading,
  error,
  children
}: {
  height: number;
  ready: boolean;
  loading?: boolean;
  error?: Error;
  children: () => ReactNode;
}) {
  const { apiOnline, api } = useWorkbench();
  if (ready) return <>{children()}</>;
  if (!apiOnline && api.state !== 'checking') return <EmptyState height={height} title="API OFFLINE · 无真实数据" detail={`等待 quant-api ${QUANT_API_URL} 上线`} />;
  if (error) return <EmptyState height={height} title="REQUEST FAILED" detail={error.message} error />;
  if (loading || api.state === 'checking') return <EmptyState height={height} title="LOADING" detail="请求 quant-api 中…" />;
  return <EmptyState height={height} title="NO DATA" />;
}

/**
 * Page-level banner: API offline → start commands; HTTP error → message (+ seed hint).
 * Renders nothing when the API is online and the request succeeded.
 */
export function ApiNotice({ error, what }: { error?: Error; what?: string }) {
  const { api } = useWorkbench();
  if (api.state === 'offline') {
    return (
      <div className="notice">
        <Icon name="plug" size={14} style={{ color: 'var(--up)', flex: 'none', marginTop: 1 }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <b>API OFFLINE</b> · quant-api <code>{QUANT_API_URL}</code> 不可达，本页不展示任何伪造数值。请在仓库根目录启动后端并灌入金标数据集：
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginTop: 6 }}>
            <CopyCmd cmd={START_COMMANDS.seed} />
            <CopyCmd cmd={START_COMMANDS.api} />
          </div>
        </div>
      </div>
    );
  }
  if (!error) return null;
  const qe = error instanceof QuantApiError ? error : undefined;
  return (
    <div className={`notice ${qe?.kind === 'timeout' ? 'warn' : ''}`}>
      <Icon name="warn" size={14} style={{ color: 'var(--up)', flex: 'none', marginTop: 1 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <b>{what ?? 'quant-api'} 请求失败</b> · <span className="mono">{qe?.path ?? ''}</span> · {error.message}
        {qe?.needsSeed ? (
          <div style={{ marginTop: 6, maxWidth: 360 }}>
            数据湖可能尚无快照，请先执行：
            <CopyCmd cmd={START_COMMANDS.seed} />
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function SolveMeta({ loading, elapsedMs, label }: { loading: boolean; elapsedMs?: number; label?: string }) {
  return (
    <span className="solv">
      {loading ? <i className="spin" /> : null}
      {loading ? `${label ?? '请求中'}…` : elapsedMs !== undefined ? `${label ?? 'rtt'} ${fmtMs(elapsedMs)}` : null}
    </span>
  );
}

export function Legend({ items }: { items: { label: ReactNode; color?: string; box?: boolean; dash?: boolean; style?: CSSProperties }[] }) {
  return (
    <span className="lg">
      {items.map((it, i) => (
        <span key={i}>
          <i
            className={it.box ? 'bx' : undefined}
            style={
              it.style ??
              (it.dash ? { borderTop: `1.5px dashed ${it.color}`, height: 0, background: 'transparent' } : { background: it.color })
            }
          />
          {it.label}
        </span>
      ))}
    </span>
  );
}
