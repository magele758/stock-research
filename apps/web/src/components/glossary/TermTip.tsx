'use client';

import { useCallback, useEffect, useId, useMemo, useRef, useSyncExternalStore, type KeyboardEvent, type MouseEvent, type ReactNode } from 'react';
import { gateSpec, judge, type GateSpec } from '@/lib/thresholds';
import { getTerm, loadBuiltinTerms } from '@/lib/glossary';
import { pageStore, tipStore } from '@/lib/glossary/store';
import type { TermContext, TermId, TermValue } from '@/lib/glossary/types';
import { Icon } from '../icons';

interface Props {
  id: TermId;
  /** Live value judged against the gate (number or numeric string). */
  value?: number | string | null;
  /** Pre-formatted value for the card (defaults to `value`). */
  display?: string;
  /** Override gate (e.g. backend gate_checks threshold via `withGate`). */
  gate?: GateSpec;
  context?: TermContext;
  /** Info-icon variant (for tags / chips where a dotted underline would be noisy). */
  icon?: boolean;
  /** Render as `<span role="button">` when nested inside another button (tabs, chips). */
  as?: 'button' | 'span';
  className?: string;
  children?: ReactNode;
}

/**
 * Glossary trigger: dotted underline (or info icon). Hover 180 ms opens the TermCard,
 * leave 140 ms closes, click pins, Enter/Space toggles pin, Esc closes.
 * Registers its live value with the page store so the drawer can list "本页术语".
 */
export function TermTip({ id, value, display, gate, context, icon, as = 'button', className, children }: Props) {
  const uid = useId();
  const ref = useRef<HTMLElement | null>(null);
  const ctxKey = context ? JSON.stringify(context) : '';
  const gateKey = gate ? JSON.stringify(gate) : '';
  const tv = useMemo<TermValue>(
    () => ({ value, display: display ?? (value === null || value === undefined ? undefined : String(value)), gate, context }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [value, display, gateKey, ctxKey]
  );
  const on = useSyncExternalStore(
    tipStore.subscribe,
    () => tipStore.get()?.triggerId === uid,
    () => false
  );

  useEffect(() => {
    void loadBuiltinTerms();
  }, []);

  useEffect(() => {
    const verdict = judge(tv.gate ?? gateSpec(id), tv.value).verdict;
    pageStore.set(uid, { termId: id, ...tv, verdict });
    tipStore.updateValue(uid, tv);
  }, [uid, id, tv]);

  useEffect(
    () => () => {
      pageStore.remove(uid);
      tipStore.unmount(uid);
    },
    [uid]
  );

  const anchor = () => ref.current;
  const onEnter = () => ref.current && tipStore.hoverEnter(uid, ref.current, id, tv);
  const onLeave = () => tipStore.hoverLeave(uid);
  const onFocus = () => {
    const el = ref.current;
    if (el && el.matches(':focus-visible')) tipStore.focusOpen(uid, el, id, tv);
  };
  const toggle = useCallback(
    (e: MouseEvent | KeyboardEvent) => {
      e.preventDefault();
      e.stopPropagation();
      const el = anchor();
      if (el) tipStore.togglePin(uid, el, id, tv);
    },
    [uid, id, tv]
  );
  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'Escape' && on) {
      e.stopPropagation();
      tipStore.close();
    } else if (as === 'span' && (e.key === 'Enter' || e.key === ' ')) toggle(e);
  };

  const name = getTerm(id)?.zh ?? id;
  const common = {
    className: `${icon ? 'tt-i' : 'tt'} ${on ? 'on' : ''} ${className ?? ''}`,
    'aria-haspopup': 'dialog' as const,
    'aria-expanded': on,
    'aria-label': icon ? `术语：${name}` : undefined,
    'data-term': id,
    onMouseEnter: onEnter,
    onMouseLeave: onLeave,
    onFocus,
    onBlur: onLeave,
    onClick: toggle,
    onKeyDown: onKey
  };
  const body = icon ? <Icon name="info" size={11} sw={1.8} /> : children;

  if (as === 'span') {
    return (
      <span {...common} ref={(el) => void (ref.current = el)} role="button" tabIndex={0}>
        {body}
      </span>
    );
  }
  return (
    <button {...common} ref={(el) => void (ref.current = el)} type="button">
      {body}
    </button>
  );
}

/** Label + info-icon trigger, for chips / tags where the label itself must stay clickable. */
export function TermLabel({ id, children, ...rest }: Omit<Props, 'icon'>) {
  return (
    <span className="tt-w">
      {children}
      <TermTip id={id} icon {...rest} />
    </span>
  );
}
