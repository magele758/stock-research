'use client';

import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { childIds, getPath, resolveValue, rootId, setPath, type A2uiComponent, type SurfaceState } from '@/lib/a2ui';
import { Icon } from '../icons';
import { InlineMd } from './InlineMd';

export interface A2uiActionEvent {
  surfaceId: string;
  name: string;
  context: Record<string, unknown>;
  dataModel?: Record<string, unknown>;
}

interface Props {
  surface: SurfaceState;
  origin: 'daemon' | 'local';
  done?: string;
  busy?: boolean;
  onAction: (e: A2uiActionEvent) => void;
}

const asText = (v: unknown): string => (v === undefined || v === null ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v));

export function A2uiSurface({ surface, origin, done, busy, onAction }: Props) {
  const modelKey = JSON.stringify(surface.dataModel);
  const [model, setModel] = useState<Record<string, unknown>>(surface.dataModel);
  useEffect(() => {
    setModel(surface.dataModel);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelKey]);

  const root = useMemo(() => rootId(surface), [surface]);
  if (surface.deleted) return null;
  const locked = Boolean(done) || Boolean(busy);

  const fire = (c: A2uiComponent) => {
    const action = (c.props.action ?? {}) as Record<string, unknown>;
    const name = typeof action.name === 'string' ? action.name : typeof c.props.actionName === 'string' ? c.props.actionName : c.id;
    const ctxRaw = action.context;
    const context: Record<string, unknown> = {};
    if (Array.isArray(ctxRaw)) {
      ctxRaw.forEach((kv) => {
        if (kv && typeof kv === 'object' && typeof (kv as { key?: unknown }).key === 'string') {
          const o = kv as { key: string; value?: unknown };
          context[o.key] = resolveValue(o.value, model);
        }
      });
    } else if (ctxRaw && typeof ctxRaw === 'object') {
      Object.entries(ctxRaw as Record<string, unknown>).forEach(([k, v]) => (context[k] = resolveValue(v, model)));
    }
    const sendDataModel = action.sendDataModel !== false;
    onAction({ surfaceId: surface.surfaceId, name, context, dataModel: sendDataModel ? model : undefined });
  };

  const render = (id: string, depth: number, seen: Set<string>): ReactNode => {
    const c = surface.components[id];
    if (!c || seen.has(id) || depth > 12) return null;
    const nextSeen = new Set(seen).add(id);
    const kids = () => childIds(c.props).map((cid) => <FragmentKey key={cid}>{render(cid, depth + 1, nextSeen)}</FragmentKey>);
    const p = c.props;
    switch (c.type) {
      case 'Card':
      case 'Column':
      case 'List':
        return <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>{kids()}</div>;
      case 'Row':
        return <div className="a2row">{kids()}</div>;
      case 'Text': {
        const text = asText(resolveValue(p.text, model));
        const hint = String(p.usageHint ?? p.variant ?? '');
        const heading = /^h[1-4]$|title|heading/i.test(hint) || /title|heading/i.test(c.id);
        return <div className={heading ? 'a2t' : 'a2b'}>{heading ? text : <InlineMd text={text} />}</div>;
      }
      case 'Divider':
        return <div style={{ borderTop: '1px solid var(--line)' }} />;
      case 'TextField':
      case 'NumberField':
      case 'Slider': {
        const bind = p.value && typeof p.value === 'object' && typeof (p.value as { path?: unknown }).path === 'string' ? (p.value as { path: string }).path : null;
        const val = bind ? getPath(model, bind) : resolveValue(p.value, model);
        const label = asText(resolveValue(p.label, model)) || bind || c.id;
        const numeric = typeof val === 'number' || c.type !== 'TextField';
        return (
          <label className="a2f">
            <span>{label}</span>
            <input
              value={asText(val)}
              disabled={locked}
              inputMode={numeric ? 'decimal' : 'text'}
              onChange={(e) => {
                if (!bind) return;
                const raw = e.target.value;
                const n = Number(raw);
                setModel((m) => setPath(m, bind, numeric && raw.trim() !== '' && Number.isFinite(n) ? n : raw));
              }}
            />
          </label>
        );
      }
      case 'CheckBox': {
        const bind = p.value && typeof p.value === 'object' ? (p.value as { path?: string }).path : undefined;
        const checked = Boolean(bind ? getPath(model, bind) : resolveValue(p.value, model));
        return (
          <button className={`chip${checked ? ' on' : ''}`} disabled={locked} onClick={() => bind && setModel((m) => setPath(m, bind, !checked))}>
            <span className="cb" />
            {asText(resolveValue(p.label, model))}
          </button>
        );
      }
      case 'Button': {
        const childLabel = childIds(p)
          .map((cid) => surface.components[cid])
          .filter(Boolean)
          .map((cc) => asText(resolveValue(cc.props.text ?? cc.props.label, model)))
          .join(' ');
        const label = asText(resolveValue(p.label ?? p.text, model)) || childLabel || c.id;
        const primary = p.primary === true || p.variant === 'primary' || p.style === 'primary';
        return (
          <button className={`btn sm${primary ? ' pri' : ''}`} disabled={locked} onClick={() => fire(c)}>
            {primary && <Icon name="play" size={10} />}
            {label}
          </button>
        );
      }
      case 'QuantDiffGrid': {
        const rows = Array.isArray(p.rows) ? (p.rows as Record<string, unknown>[]) : [];
        return (
          <div className="a2g">
            {rows.map((r, i) => (
              <FragmentKey key={i}>
                <span>{asText(r.label)}</span>
                <b>{asText(resolveValue(r.from, model))}</b>
                <b className="to">{asText(resolveValue(r.to, model))}</b>
              </FragmentKey>
            ))}
          </div>
        );
      }
      case 'QuantStudyCard': {
        const v = (k: string) => asText(resolveValue(p[k], model));
        const metrics = Array.isArray(p.metrics) ? (p.metrics as Record<string, unknown>[]) : [];
        const warnings = Array.isArray(p.warnings) ? (p.warnings as unknown[]).map(asText).filter(Boolean) : [];
        const status = v('status') || 'DRAFT';
        const verdict = v('verdict');
        const tone = /VERIFIED|COMPLETED/.test(verdict || status) ? 'pass' : /FALSIFIED|FAILED|ERROR/.test(verdict || status) ? 'fail' : /PENDING|DRAFT/.test(verdict || status) ? 'mute' : 'warn';
        return (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
            <div className="a2row" style={{ flexWrap: 'nowrap' }}>
              <Icon name="research" size={12} style={{ color: 'var(--sig)', flex: 'none' }} />
              <span className="a2t" style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{v('title') || '未命名研究'}</span>
              <span className={`tag ${tone}`}>{verdict && verdict !== 'PENDING' ? verdict : status}</span>
            </div>
            <div className="a2g" style={{ gridTemplateColumns: 'auto 1fr', borderTop: 0, paddingTop: 0 }}>
              <span>study</span>
              <b style={{ textAlign: 'left' }}>{v('study_id') || '（未派发）'}</b>
              {v('parent_study_id') ? (
                <>
                  <span>parent</span>
                  <b style={{ textAlign: 'left' }}>{v('parent_study_id')}</b>
                </>
              ) : null}
              <span>kind · cluster</span>
              <b style={{ textAlign: 'left' }}>
                {v('kind') || 'factor'} · {v('target_cluster') || 'engine_sandbox'}
              </b>
              {v('proofs') ? (
                <>
                  <span>proofs</span>
                  <b style={{ textAlign: 'left' }}>{v('proofs')}</b>
                </>
              ) : null}
              {metrics.map((m, i) => (
                <FragmentKey key={i}>
                  <span>{asText(m.label)}</span>
                  <b style={{ textAlign: 'left' }}>{asText(resolveValue(m.value, model))}</b>
                </FragmentKey>
              ))}
            </div>
            {v('hypothesis') ? <div className="a2b">{v('hypothesis').slice(0, 240)}</div> : null}
            {warnings.map((w, i) => (
              <div key={i} className="a2b c-warn">
                {w}
              </div>
            ))}
          </div>
        );
      }
      default:
        return (
          <div className="a2x" title="Unregistered A2UI component — rendered as raw props">
            {`<${c.type}#${c.id}>\n${JSON.stringify(p, null, 1).slice(0, 600)}`}
          </div>
        );
    }
  };

  const others = surface.order.filter((id) => id !== root && !Object.values(surface.components).some((c) => childIds(c.props).includes(id)));

  return (
    <div className={`a2ui${done ? ' done' : ''}`}>
      <div className="a2h">
        <Icon name="route" size={11} />
        A2UI v0.9 · {origin === 'local' ? 'LOCAL DETERMINISTIC' : 'BOT SURFACE'}
        <span className="a2s">{surface.surfaceId}</span>
      </div>
      <div className="a2c">
        {root ? render(root, 0, new Set()) : <div className="a2b">（空 surface，等待 updateComponents）</div>}
        {others.map((id) => (
          <FragmentKey key={id}>{render(id, 0, new Set())}</FragmentKey>
        ))}
      </div>
      {done && (
        <div className="a2d">
          <Icon name="check" size={11} />
          {done}
        </div>
      )}
    </div>
  );
}

function FragmentKey({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
