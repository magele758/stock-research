'use client';

import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useGlossary } from '@/lib/glossary';
import { tipStore, useActiveTip } from '@/lib/glossary/store';
import { useFloating } from '@/lib/use-floating';
import { Icon } from '../icons';
import { TermCardBody } from './TermCard';

/** Single portal host for the TermCard (z 55; never covers the Bot Dock). */
export function TermPopoverHost() {
  const tip = useActiveTip();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  if (!mounted || !tip) return null;
  return createPortal(<Card key={tip.triggerId} />, document.body);
}

function Card() {
  const tip = useActiveTip();
  const { byId, terms } = useGlossary(true);
  const ref = useRef<HTMLDivElement>(null);
  const pos = useFloating(tip?.anchor ?? null, ref, [tip?.key, tip?.termId, terms.length]);

  useEffect(() => {
    if (!tip) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      const a = tip.anchor;
      tipStore.close();
      if (a.isConnected) a.focus({ preventScroll: true });
    };
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (ref.current?.contains(t) || tip.anchor.contains(t)) return;
      tipStore.close();
    };
    window.addEventListener('keydown', onKey);
    if (tip.pinned) window.addEventListener('mousedown', onDown, true);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('mousedown', onDown, true);
    };
  }, [tip]);

  if (!tip) return null;
  const term = byId.get(tip.termId);

  return (
    <div
      ref={ref}
      role="dialog"
      aria-label={term ? `术语卡片：${term.zh}` : '术语卡片'}
      className={`tcard ${tip.pinned ? 'pinned' : ''}`}
      style={{ left: pos.left, top: pos.top, maxHeight: pos.maxHeight, visibility: pos.ready ? 'visible' : 'hidden' }}
      onMouseEnter={tipStore.cardEnter}
      onMouseLeave={tipStore.cardLeave}
    >
      {term ? (
        <TermCardBody
          term={term}
          tv={tip.tv}
          onRelated={(id) => tipStore.switchTerm(id)}
          head={
            tip.pinned ? (
              <button type="button" className="tc-x" aria-label="关闭术语卡片" onClick={() => tipStore.close()}>
                <Icon name="x" size={11} sw={2} />
              </button>
            ) : null
          }
        />
      ) : (
        <div className="tc-sec">
          <div className="tc-l">{tip.termId}</div>
          <div className="tc-plain">{terms.length ? '术语库未收录该条目。' : '术语库加载中…'}</div>
        </div>
      )}
    </div>
  );
}
