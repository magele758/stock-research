import type { ReactNode } from 'react';

const P: Record<string, ReactNode> = {
  alpha: <path d="M18 5H6l6.5 7L6 19h12" />,
  models: (
    <>
      <path d="M12 3 3 7.5 12 12l9-4.5L12 3Z" />
      <path d="m3 12 9 4.5 9-4.5" />
      <path d="m3 16.5 9 4.5 9-4.5" />
    </>
  ),
  portfolio: (
    <>
      <circle cx="12" cy="12" r="8" />
      <path d="M12 2v6M12 16v6M2 12h6M16 12h6" />
    </>
  ),
  backtest: (
    <>
      <path d="M4 4v16h16" />
      <path d="m7 15 4-5 3 3 6-7" />
    </>
  ),
  data: (
    <>
      <ellipse cx="12" cy="6" rx="7" ry="2.6" />
      <path d="M5 6v12c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6V6" />
      <path d="M5 12c0 1.4 3.1 2.6 7 2.6s7-1.2 7-2.6" />
    </>
  ),
  shadow: <path d="M3 12h4l2.5-6 5 12 2.5-6H21" />,
  play: <path d="M7 4v16l13-8L7 4Z" />,
  send: <path d="M3 11 21 3l-8 18-2-8-8-2Z" />,
  chev: <path d="m9 6 6 6-6 6" />,
  down: <path d="m6 9 6 6 6-6" />,
  check: <path d="m5 12 5 5 9-10" />,
  x: <path d="M6 6l12 12M18 6 6 18" />,
  warn: (
    <>
      <path d="M12 3 2 20h20L12 3Z" />
      <path d="M12 10v4M12 17v.5" />
    </>
  ),
  bolt: <path d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z" />,
  clock: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  mem: (
    <>
      <path d="M4 5h16v14H4z" />
      <path d="M8 9h8M8 13h5M4 9H2M4 15H2M22 9h-2M22 15h-2" />
    </>
  ),
  shield: (
    <>
      <path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6l-8-3Z" />
      <path d="m8.5 12 2.5 2.5 4.5-5" />
    </>
  ),
  lock: (
    <>
      <rect x="5" y="11" width="14" height="10" />
      <path d="M8 11V7a4 4 0 0 1 8 0v4" />
    </>
  ),
  unlock: (
    <>
      <rect x="5" y="11" width="14" height="10" />
      <path d="M8 11V7a4 4 0 0 1 7.5-2" />
    </>
  ),
  hex: (
    <>
      <path d="M12 2.5 20.5 7.3v9.4L12 21.5 3.5 16.7V7.3L12 2.5Z" />
      <circle cx="12" cy="12" r="2.6" />
    </>
  ),
  dia: (
    <>
      <path d="M12 2.5 21.5 12 12 21.5 2.5 12 12 2.5Z" />
      <path d="M12 7.5v9M7.5 12h9" />
    </>
  ),
  sq: (
    <>
      <rect x="4" y="4" width="16" height="16" />
      <path d="M4 20 20 4" />
      <path d="M12 20l8-8" />
    </>
  ),
  term: (
    <>
      <rect x="3" y="4" width="18" height="16" />
      <path d="m7 9 3 3-3 3M12 15h5" />
    </>
  ),
  file: (
    <>
      <path d="M6 2h9l5 5v15H6V2Z" />
      <path d="M14 2v6h6" />
    </>
  ),
  route: (
    <>
      <circle cx="6" cy="6" r="2.5" />
      <circle cx="18" cy="18" r="2.5" />
      <path d="M8.5 6H15a3 3 0 0 1 0 6H9a3 3 0 0 0 0 6h6.5" />
    </>
  ),
  sync: (
    <>
      <path d="M4 12a8 8 0 0 1 14-5.3L20 9M20 4v5h-5" />
      <path d="M20 12a8 8 0 0 1-14 5.3L4 15M4 20v-5h5" />
    </>
  ),
  copy: (
    <>
      <rect x="8" y="8" width="12" height="12" />
      <path d="M16 8V4H4v12h4" />
    </>
  ),
  moon: <path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5Z" />,
  sun: (
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v3M12 19v3M2 12h3M19 12h3" />
    </>
  ),
  tagp: <path d="M3 3h8l10 10-8 8L3 11V3Z" />,
  panel: (
    <>
      <rect x="3" y="4" width="18" height="16" />
      <path d="M15 4v16" />
    </>
  ),
  plug: (
    <>
      <path d="M9 3v5M15 3v5M6 8h12v4a6 6 0 0 1-12 0V8Z" />
      <path d="M12 18v3" />
    </>
  ),
  stop: <rect x="6" y="6" width="12" height="12" />,
  download: (
    <>
      <path d="M12 3v12M7 10l5 5 5-5" />
      <path d="M4 20h16" />
    </>
  ),
  edit: (
    <>
      <path d="M4 20h4L19 9l-4-4L4 16v4Z" />
      <path d="m13 7 4 4" />
    </>
  )
};

export type IconName = keyof typeof P;

export function Icon({ name, size = 16, sw = 1.6, className, style }: { name: string; size?: number; sw?: number; className?: string; style?: React.CSSProperties }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={sw}
      strokeLinecap="square"
      strokeLinejoin="miter"
      className={className}
      style={style}
      aria-hidden
    >
      {P[name] ?? null}
    </svg>
  );
}

export function BrandMark() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none" aria-hidden>
      <rect x=".75" y=".75" width="16.5" height="16.5" stroke="var(--line2)" strokeWidth="1.2" />
      <path d="M4 13V10M7.5 13V7.5M11 13V9M14.5 13V4.5" stroke="var(--sig)" strokeWidth="1.6" />
    </svg>
  );
}
