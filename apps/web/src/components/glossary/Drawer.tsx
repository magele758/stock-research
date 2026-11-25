'use client';

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { safeRight } from '@/lib/use-floating';
import { Icon } from '../icons';

/**
 * Left-anchored drawer sliding out of the Main edge (left: SideNav 200px).
 * Veil stops at the Bot Dock so the Dock stays interactive (z 44 / 45).
 */
export function Drawer({
  title,
  sub,
  tools,
  footer,
  width = 560,
  onClose,
  label,
  children
}: {
  title: ReactNode;
  sub?: ReactNode;
  tools?: ReactNode;
  footer?: ReactNode;
  width?: number;
  onClose: () => void;
  label: string;
  children: ReactNode;
}) {
  const [mounted, setMounted] = useState(false);
  const [right, setRight] = useState(360);
  const ref = useRef<HTMLDivElement>(null);
  const prevFocus = useRef<Element | null>(null);

  useEffect(() => {
    setMounted(true);
    prevFocus.current = document.activeElement;
    return () => {
      const p = prevFocus.current;
      if (p instanceof HTMLElement && p.isConnected) p.focus({ preventScroll: true });
    };
  }, []);

  useLayoutEffect(() => {
    if (!mounted) return;
    const m = () => setRight(Math.max(0, window.innerWidth - safeRight() - 8));
    m();
    window.addEventListener('resize', m);
    return () => window.removeEventListener('resize', m);
  }, [mounted]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !e.defaultPrevented && !document.querySelector('.tcard.pinned')) onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  if (!mounted) return null;
  return createPortal(
    <>
      <div className="drw-veil" style={{ right }} onMouseDown={onClose} aria-hidden />
      <div ref={ref} className="drw" role="dialog" aria-modal="false" aria-label={label} style={{ width }}>
        <div className="drw-h">
          <span className="pt">{title}</span>
          {sub ? <span className="ps">{sub}</span> : null}
          <span className="tools">
            {tools}
            <button type="button" className="ib" aria-label="关闭" onClick={onClose} style={{ width: 22, height: 22 }}>
              <Icon name="x" size={11} sw={2} />
            </button>
          </span>
        </div>
        <div className="drw-b" style={{ display: 'flex', flexDirection: 'column' }}>
          {children}
        </div>
        {footer ? <div className="drw-f">{footer}</div> : null}
      </div>
    </>,
    document.body
  );
}
