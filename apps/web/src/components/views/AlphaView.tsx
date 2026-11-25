'use client';

import { Fragment, useMemo, useRef, useState, type ReactNode } from 'react';
import { quantApi, normalizeGateChecks } from '@/lib/quant-api';
import { useApi } from '@/lib/use-api';
import { usePublishMetrics, useWorkbench } from '@/lib/workbench';
import { BUILTIN_FACTORS, PIPELINE, universeMeta } from '@/lib/meta';
import { DECILE_COLORS, esc, fmtMs, fx, isNum, pct, ratioPct, sg, spct } from '@/lib/format';
import type { FactorEvalResult, PreprocessConfig } from '@/lib/types';
import { ApiNotice, DataSlot, EmptyState, Kpi, Panel, Prd, Seg, Tag, ViewHeader } from '@/components/ui';
import { Icon } from '@/components/icons';
import { LineChart, type LineSeries } from '@/components/charts/LineChart';
import { BarChart } from '@/components/charts/BarChart';
import { Heatmap } from '@/components/charts/Heatmap';
import { REGIME_BANDS } from '@/components/charts/core';
import { TermTip } from '@/components/glossary/TermTip';
import { DimensionMatrix } from '@/components/research/report/DimensionMatrix';
import { openStudyDrawer } from '@/lib/glossary/store';
import { GATES, judge, UI_LIMITS, verdictTone, withGate, type GateSpecKey } from '@/lib/thresholds';
import { ci } from '@/lib/v3';

/** Contract thresholds (`lib/thresholds`); overridden by the matching `gate_checks[].threshold` when the backend reports one. */
const THRESHOLDS = {
  rank_ic: GATES.rank_ic_min,
  icir_ann: GATES.icir_ann_min,
  nw_t_stat: GATES.nw_t_stat_min,
  daily_turnover_oneway: GATES.daily_turnover_max,
  max_corr_in_library: GATES.max_corr_library
} as const;
type ThrKey = keyof typeof THRESHOLDS;
const TERM_OF: Record<ThrKey, GateSpecKey> = { rank_ic: 'rank_ic', icir_ann: 'icir', nw_t_stat: 'nw_t_stat', daily_turnover_oneway: 'turnover_oneway', max_corr_in_library: 'max_corr_library' };

const EXP_LIMIT = UI_LIMITS.residual_exposure_max;

function CiRow({ v, d = 3, pctScale = false }: { v: unknown; d?: number; pctScale?: boolean }) {
  const c = ci(v);
  if (!c) return null;
  const f = (x: number) => (pctScale ? `${fx(ratioPct(x) ?? NaN, 1)}%` : fx(x, d));
  return (
    <TermTip id="bootstrap_ci" as="span" className="ci" display={`[${f(c[0])}, ${f(c[1])}]`} context={{ excludes_zero: c[0] > 0 || c[1] < 0 }}>
      95% CI [{f(c[0])}, {f(c[1])}]
    </TermTip>
  );
}

// ------------------------------------------------------------------ syntax highlight (port of prototype `hl`)

const TOK_SRC = /("[^"]*"|'[^']*')|(\$\w+)|\b(def|return|lambda|for|in|if|else|elif|and|or|not|import|from|as|None|True|False)\b|\b(\d+(?:\.\d+)?)\b|([A-Za-z_]\w*)(?=\()/.source;

function hlLine(line: string): ReactNode[] {
  const ci = line.indexOf('#');
  const code = ci >= 0 ? line.slice(0, ci) : line;
  const out: ReactNode[] = [];
  const re = new RegExp(TOK_SRC, 'g');
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(code))) {
    if (m.index > last) out.push(code.slice(last, m.index));
    const cls = m[1] ? 'tk-s' : m[2] ? 'tk-v' : m[3] ? 'tk-k' : m[4] ? 'tk-n' : 'tk-f';
    out.push(
      <span key={out.length} className={cls}>
        {m[0]}
      </span>
    );
    last = m.index + m[0].length;
  }
  if (last < code.length) out.push(code.slice(last));
  if (ci >= 0)
    out.push(
      <span key={out.length} className="tk-c">
        {line.slice(ci)}
      </span>
    );
  return out;
}

function ExprEditor({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  const hlRef = useRef<HTMLDivElement>(null);
  const gutRef = useRef<HTMLDivElement>(null);
  const lines = value.split('\n');
  return (
    <div className="ed">
      <div className="ed-gut">
        <div ref={gutRef}>{lines.map((_, i) => i + 1).join('\n')}</div>
      </div>
      {/* div, not <pre>: the HTML parser drops a leading newline inside <pre>, breaking hydration when the first line is empty */}
      <div ref={hlRef} className="ed-hl" aria-hidden>
        {lines.map((l, i) => (
          <Fragment key={i}>
            {hlLine(l)}
            {'\n'}
          </Fragment>
        ))}
      </div>
      <textarea
        className="ed-ta"
        value={value}
        placeholder={placeholder}
        spellCheck={false}
        autoCapitalize="off"
        autoComplete="off"
        wrap="off"
        onChange={(e) => onChange(e.target.value)}
        onScroll={(e) => {
          const t = e.currentTarget;
          if (hlRef.current) {
            hlRef.current.scrollTop = t.scrollTop;
            hlRef.current.scrollLeft = t.scrollLeft;
          }
          if (gutRef.current) gutRef.current.style.transform = `translateY(${-t.scrollTop}px)`;
        }}
      />
    </div>
  );
}

// ------------------------------------------------------------------ helpers

function threshold(r: FactorEvalResult, key: ThrKey): number {
  const g = r.gate_checks.find((c) => c.key === key || c.key === (key === 'rank_ic' ? 'rank_ic_mean' : key));
  return isNum(g?.threshold) ? g.threshold : THRESHOLDS[key];
}

/** Contract gate (with backend threshold override) for a KPI; turnover is judged as a fraction. */
function gateOf(r: FactorEvalResult, key: ThrKey) {
  const t = threshold(r, key);
  return withGate(TERM_OF[key], key === 'daily_turnover_oneway' ? (ratioPct(t) ?? NaN) / 100 : t);
}
function kpiValue(r: FactorEvalResult, key: ThrKey): number | null {
  const v = key === 'rank_ic' ? r.rank_ic_mean : r[key];
  if (!isNum(v)) return null;
  return key === 'daily_turnover_oneway' ? (ratioPct(v) ?? NaN) / 100 : v;
}
function vt(r: FactorEvalResult, key: ThrKey) {
  const j = judge(gateOf(r, key), kpiValue(r, key));
  return <Tag tone={verdictTone(j.verdict)}>{j.verdict}</Tag>;
}

/** Align a series to the last `n` slots (pad front with NaN). */
function tail(a: number[] | undefined, n: number): number[] {
  if (!a) return [];
  return a.length >= n ? a.slice(a.length - n) : new Array<number>(n - a.length).fill(NaN).concat(a);
}

const num4 = (v: number | null | undefined) => (isNum(v) ? +v.toFixed(4) : null);

const shortLabel = (s: string) => {
  const t = s.replace(/^alpha_/, '');
  return t.length > 8 ? t.slice(0, 8) : t;
};

function findExposure(r: FactorEvalResult, re: RegExp): number | undefined {
  const hit = Object.entries(r.residual_exposures).find(([k]) => re.test(k));
  return hit && isNum(hit[1]) ? hit[1] : undefined;
}

// ------------------------------------------------------------------ view

export function AlphaView() {
  const { universe, snapshotId, factorId, setFactorId, preprocess, setPreprocess, apiOnline, dock } = useWorkbench();
  const uni = universeMeta(universe);

  const [draft, setDraft] = useState<string | null>(null);
  const [runExpr, setRunExpr] = useState<string | null>(null);
  const [alt, setAlt] = useState<'lag' | 'ind'>('lag');
  const [prevFactor, setPrevFactor] = useState(factorId);
  if (prevFactor !== factorId) {
    setPrevFactor(factorId);
    setDraft(null);
    setRunExpr(null);
  }

  const catalog = useApi(apiOnline ? 'factor-catalog' : null, (s) => quantApi.factorCatalog(s));
  const catItems = apiOnline && catalog.data?.length ? catalog.data : null;
  const tabs = (catItems ?? BUILTIN_FACTORS).map((it) => {
    const b = BUILTIN_FACTORS.find((x) => x.factor_id === it.factor_id);
    return { id: it.factor_id, name: it.name || b?.name || it.factor_id, lang: it.lang || b?.lang || '—' };
  });
  const catItem = catItems?.find((c) => c.factor_id === factorId);

  const key = apiOnline ? JSON.stringify({ f: factorId, u: universe, s: snapshotId, p: preprocess, e: runExpr }) : null;
  const ev = useApi<FactorEvalResult>(
    key,
    (s) => quantApi.evaluateFactor({ factor_id: factorId, expression: runExpr, universe_id: universe, snapshot_id: snapshotId, preprocess }, s),
    250
  );
  const r = apiOnline && ev.data && !(ev.error && ev.dataKey !== key) ? ev.data : undefined;
  const slotErr = ev.loading ? undefined : ev.error;
  const busy = ev.loading && !!r;

  const baseExpr = catItem?.expression ?? (r && r.factor_id === factorId && runExpr === null ? r.expression : '');
  const text = draft ?? baseExpr;
  const edited = draft !== null && draft !== (runExpr ?? baseExpr);
  const lang = runExpr !== null ? 'Custom Expr' : tabs.find((t) => t.id === factorId)?.lang ?? '—';

  const setPipe = (p: Partial<PreprocessConfig>) => setPreprocess({ ...preprocess, ...p });
  const pipeOn = PIPELINE.filter((p) => preprocess[p.key]).length;

  // ---------------------------------------------------------------- derived metrics
  const checks = useMemo(() => (r ? normalizeGateChecks(r.gate_checks) : []), [r]);
  const nPass = checks.filter((c) => c.passed).length;

  usePublishMetrics(
    'alpha',
    r
      ? {
          factor_id: r.factor_id,
          universe: r.universe_id,
          snapshot_id: r.snapshot_id,
          rank_ic: num4(r.rank_ic_mean),
          icir: num4(r.icir_ann),
          nw_t: num4(r.nw_t_stat),
          long_short_ann_pct: num4(ratioPct(r.long_short_ann)),
          turnover_pct: num4(ratioPct(r.daily_turnover_oneway)),
          max_corr: num4(r.max_corr_in_library),
          max_corr_peer: r.max_corr_peer || null,
          admission: `${nPass}/${checks.length}`,
          admission_passed: r.admission_passed
        }
      : null
  );

  const snapLabel = r?.snapshot_id ?? snapshotId ?? 'latest';
  const range = r?.dates.length ? `${r.dates[0]} → ${r.dates[r.dates.length - 1]}` : null;

  const submit = () => {
    const parts = [`提交 ${factorId} 入库门禁 @${universe}`, `snapshot ${snapLabel}`];
    if (r) {
      parts.push(`RankIC ${fx(r.rank_ic_mean, 4)}`, `ICIR ${fx(r.icir_ann, 2)}`, `NW t ${fx(r.nw_t_stat, 2)}`, `MaxCorr ${fx(r.max_corr_in_library, 2)}`, `门禁 ${nPass}/${checks.length}`);
    }
    if (runExpr !== null) parts.push('自定义表达式');
    dock.send(parts.join(' · '));
  };

  const deepDive = () =>
    openStudyDrawer({
      title: `${factorId} 因子深度研判`,
      hypothesis: r
        ? `${factorId} 在 ${uni.label} 的 RankIC ${fx(r.rank_ic_mean, 4)} / ICIR ${fx(r.icir_ann, 2)} / NW t ${fx(r.nw_t_stat, 2)}；需经形式化证明、CPCV 与跨维度切片确认其为稳定 Alpha 而非风格或单一 Regime 暴露。`
        : `${factorId} 因子深度研判：形式化证明 + CPCV + 跨维度切片`,
      kind: 'factor',
      factor_id: factorId,
      expression: (runExpr ?? baseExpr) || null,
      universe_id: universe,
      source: '/alpha'
    });

  // ---------------------------------------------------------------- gate strip
  const warnings: [tone: 'warn' | 'fail', title: string, detail: string][] = [];
  if (r) {
    checks
      .filter((c) => c.passed === false)
      .forEach((c) => warnings.push(['fail', c.name, `实际 ${c.value ?? '—'}${c.threshold ? ` · 要求 ${c.threshold}` : ''}`]));
    const size = findExposure(r, /size|cap/i);
    if (!preprocess.neutralize_size)
      warnings.push(['warn', 'SIZE 未中性化', `${isNum(size) ? `Size 残余暴露 ${sg(size)}σ · ` : ''}RankIC 可能含小盘溢价，属风格 Beta 而非 Alpha`]);
    if (!preprocess.filter_limit_st) warnings.push(['warn', 'ST/涨跌停未剔除', '含不可成交样本 · 多空收益可能虚高 · 回测不可落地']);
    if (!preprocess.neutralize_industry) warnings.push(['warn', '行业未中性', `ICIR ${fx(r.icir_ann)} 可能来自行业轮动`]);
    if (!preprocess.orthogonalize_barra) warnings.push(['warn', '未做风格正交', `库内 MaxCorr ${fx(r.max_corr_in_library)} · 冗余风险上升`]);
    if (!preprocess.winsorize_mad) warnings.push(['warn', '极值未截断', '分层离散度可能放大']);
    if (!r.ast_safe) warnings.push(['fail', 'AST UNSAFE', r.ast_warnings.join(' · ') || '表达式未通过安全校验']);
  }
  const allExposures = r ? Object.entries(r.residual_exposures).filter(([, v]) => isNum(v)) : [];
  const styleExposures = allExposures.filter(([k]) => k !== 'Intercept' && !k.startsWith('Industry:'));
  const industryExposures = allExposures.filter(([k]) => k.startsWith('Industry:'));
  const maxIndustryExposure = industryExposures.length ? Math.max(...industryExposures.map(([, v]) => Math.abs(v))) : null;
  const exposures = styleExposures.length ? styleExposures : allExposures.filter(([k]) => k !== 'Intercept').slice(0, 8);
  const leak = allExposures.filter(([k]) => k !== 'Intercept').some(([, v]) => Math.abs(v) > EXP_LIMIT);

  // ---------------------------------------------------------------- chart data
  const charts = useMemo(() => {
    if (!r) return null;
    const n = r.dates.length;
    const nav = new Map(Object.entries(r.decile_navs).map(([k, v]) => [k.toUpperCase().replace(/[^A-Z0-9]/g, ''), v]));
    const dec = Array.from({ length: 10 }, (_, j) => ({ j, data: nav.get(`Q${j + 1}`) })).filter((d): d is { j: number; data: number[] } => !!d.data?.length);
    const ls = nav.get('LS') ?? nav.get('LONGSHORT');
    const bench = nav.get('BENCH') ?? nav.get('BENCHMARK');
    const decS = dec.map((d) => ({ j: d.j, data: tail(d.data, n) }));
    const lsS = ls?.length ? tail(ls, n) : null;
    const benchS = bench?.length ? tail(bench, n) : null;

    const ric = r.rolling_60d_ic ?? [];
    const m = Math.min(ric.length, n);
    const icv = ric.slice(ric.length - m);
    const icx = r.dates.slice(n - m);
    const cum = tail(r.cum_ic, m);
    const finite = icv.filter(isNum);
    const posRatio = finite.length ? finite.filter((v) => v > 0).length / finite.length : NaN;

    const lag = Object.entries(r.ic_decay ?? {})
      .filter(([, v]) => isNum(v))
      .sort((a, b) => (parseFloat(a[0]) || 0) - (parseFloat(b[0]) || 0));
    const ind = Object.entries(r.industry_ic ?? {})
      .filter(([, v]) => isNum(v))
      .sort((a, b) => b[1] - a[1]);
    return { n, decS, lsS, benchS, icv, icx, cum, posRatio, lag, ind };
  }, [r]);

  const decSeries: LineSeries[] = charts
    ? [
        ...charts.decS.map((d) => ({
          name: `Q${d.j + 1}`,
          data: d.data,
          color: DECILE_COLORS[d.j],
          w: d.j === 0 || d.j === 9 ? 1.6 : 1,
          op: d.j === 0 || d.j === 9 ? 1 : 0.78,
          noHover: d.j > 0 && d.j < 9
        })),
        ...(charts.benchS ? [{ name: '等权基准', data: charts.benchS, color: 'var(--tx3)', w: 1, dash: '3 3', noHover: true }] : []),
        ...(charts.lsS ? [{ name: '多空 L/S', data: charts.lsS, color: 'var(--sig)', w: 2 }] : [])
      ]
    : [];
  const decTip = (i: number) => {
    if (!r || !charts) return '';
    const row = (name: string, color: string, v: number | undefined) =>
      `<div class="tr"><span><i style="background:${color}"></i> ${esc(name)}</span><b>${isNum(v) ? v.toFixed(3) : '—'}</b></div>`;
    return (
      `<div class="th">${esc(r.dates[i] ?? '')} · 累计净值</div>` +
      (charts.lsS ? row('多空 L/S', 'var(--sig)', charts.lsS[i]) : '') +
      (charts.benchS ? row('等权基准', 'var(--tx3)', charts.benchS[i]) : '') +
      `<div class="tg2">${charts.decS
        .slice()
        .reverse()
        .map((d) => row(`Q${d.j + 1}`, DECILE_COLORS[d.j], d.data[i]))
        .join('')}</div>`
    );
  };

  const annQ = r?.decile_ann_returns ?? [];
  const q10 = ratioPct(annQ[annQ.length - 1]);
  const q1 = ratioPct(annQ[0]);
  const lsPct = r ? ratioPct(r.long_short_ann) : null;
  const corrRisk = r ? isNum(r.max_corr_in_library) && r.max_corr_in_library >= threshold(r, 'max_corr_in_library') : false;
  const lag0 = charts?.lag[0]?.[1];

  const tabsEl = (
    <div className="ftabs">
      {tabs.map((t) => (
        <button key={t.id} type="button" className={`ftab ${t.id === factorId ? 'on' : ''}`} onClick={() => setFactorId(t.id)} title={t.id}>
          <span className="mono">{t.id}</span>
          <em>
            {t.name} · {t.lang}
          </em>
        </button>
      ))}
    </div>
  );

  return (
    <div className="stack">
      <ViewHeader
        path="/alpha"
        prds={['Q2']}
        title="Alpha 因子工坊"
        sub={`因子定义 → 截面预处理 → Tearsheet 诊断 → 入库门禁 · ${uni.label} · snapshot ${snapLabel}${range ? ` · ${range}` : ''}`}
        actions={
          <>
            <Tag tone="mute">
              {factorId} · {r ? (r.admission_passed ? 'ADMITTED' : 'DRAFT') : '—'}
            </Tag>
            <button type="button" className="btn" onClick={ev.reload} disabled={!apiOnline || ev.loading}>
              <Icon name="play" size={10} />
              重跑评估
            </button>
            <button type="button" className="btn" onClick={() => dock.runCommand('ic')}>
              Bot 解读
            </button>
            <button type="button" className="btn" onClick={deepDive} title="以当前因子与表达式预填新研究，派发到集群">
              <Icon name="research" size={11} />
              派发集群深度研判此因子
            </button>
            <button type="button" className="btn pri" onClick={submit}>
              <Icon name="shield" size={11} />
              提交入库门禁
            </button>
          </>
        }
      />
      <ApiNotice error={ev.error} what="因子评估" />

      <section className="panel">
        {tabsEl}
        <div className="defg">
          <div style={{ minWidth: 0 }}>
            <ExprEditor value={text} onChange={setDraft} placeholder={apiOnline ? '# 表达式待 quant-api 返回' : '# API OFFLINE · 无法加载因子表达式'} />
            <div className="edm">
              <span>{lang}</span>
              <TermTip id="point_in_time" as="span">
                PIT asof=ann_date
              </TermTip>
              {r ? (
                <TermTip id="ast_safe" as="span" display={r.ast_safe ? 'SAFE' : 'UNSAFE'} context={r.ast_warnings.length ? { warnings: r.ast_warnings.slice(0, 3).join(' · ') } : undefined}>
                  {r.ast_safe ? (
                    <Tag tone="pass">AST 无未来函数 · SAFE</Tag>
                  ) : (
                    <Tag tone="fail" title={r.ast_warnings.join('\n')}>
                      AST UNSAFE
                    </Tag>
                  )}
                </TermTip>
              ) : null}
              {r && typeof r.prefix_invariance_passed === 'boolean' ? (
                <TermTip id="prefix_invariance" as="span" value={r.max_prefix_drift ?? null} display={isNum(r.max_prefix_drift) ? r.max_prefix_drift.toExponential(1) : undefined}>
                  <Tag tone={r.prefix_invariance_passed ? 'pass' : 'fail'}>PREFIX {r.prefix_invariance_passed ? 'INVARIANT' : 'DRIFT'}</Tag>
                </TermTip>
              ) : null}
              {r && r.ast_warnings.length ? (
                <span className={r.ast_safe ? '' : 'c-up'} title={r.ast_warnings.join('\n')} style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 260 }}>
                  {r.ast_warnings.join(' · ')}
                </span>
              ) : null}
              {r ? (
                <TermTip id="coverage" as="span" value={isNum(r.coverage_pct) ? (ratioPct(r.coverage_pct) ?? NaN) / 100 : null} display={pct(r.coverage_pct, 1)}>
                  覆盖 {pct(r.coverage_pct, 1)}
                </TermTip>
              ) : null}
              {r?.dates.length ? <span>{r.dates.length} 日</span> : null}
              <span style={{ marginLeft: 'auto', display: 'flex', gap: 6, alignItems: 'center' }}>
                <Prd id="Q2.1" />
                {ev.loading ? (
                  <span className="solv">
                    <i className="spin" />
                    评估中…
                  </span>
                ) : r ? (
                  <span className="c-pass">eval {fmtMs(ev.elapsedMs)}</span>
                ) : null}
                {edited ? <Tag tone="warn">EDITED</Tag> : runExpr !== null ? <Tag tone="info">CUSTOM</Tag> : null}
                {draft !== null || runExpr !== null ? (
                  <button
                    type="button"
                    className="btn sm"
                    onClick={() => {
                      setDraft(null);
                      setRunExpr(null);
                    }}
                  >
                    恢复内置
                  </button>
                ) : null}
                <button type="button" className="btn sm pri" disabled={!apiOnline || !edited || !draft?.trim()} onClick={() => setRunExpr(draft)}>
                  <Icon name="play" size={9} />
                  运行编辑表达式
                </button>
              </span>
            </div>
          </div>
          <div className="pipe">
            <div className="ttl">
              截面预处理管线 <Prd id="Q2.2" />
              <span className="mono" style={{ marginLeft: 'auto', fontWeight: 400 }}>
                {pipeOn}/{PIPELINE.length} ON
              </span>
            </div>
            <div className="chips">
              {PIPELINE.map((p) => (
                <button key={p.key} type="button" className={`chip ${preprocess[p.key] ? 'on' : ''}`} onClick={() => setPipe({ [p.key]: !preprocess[p.key] })}>
                  <span className="cb" />
                  {p.label}
                </button>
              ))}
            </div>
            <div className="ttl" style={{ marginTop: 2 }}>
              <TermTip id="zscore_rank">标准化</TermTip>
              <span style={{ marginLeft: 'auto' }}>
                <Seg
                  value={preprocess.standardize}
                  options={[
                    { key: 'zscore', label: 'Z-Score' },
                    { key: 'rank', label: 'Rank' }
                  ]}
                  onChange={(v) => setPipe({ standardize: v })}
                />
              </span>
            </div>
            <div className="ttl" style={{ marginTop: 2 }}>
              <TermTip id="residual_exposure">残余风格暴露 (σ)</TermTip>
              <span style={{ marginLeft: 'auto', fontWeight: 400, letterSpacing: 0 }}>
                {maxIndustryExposure !== null ? `行业(${industryExposures.length}) ≤${fx(maxIndustryExposure)}σ · ` : ''}阈值 ±{fx(EXP_LIMIT)}σ
              </span>
            </div>
            <DataSlot height={92} ready={!!r} loading={ev.loading} error={slotErr}>
              {() =>
                exposures.length ? (
                  <Meters items={exposures} />
                ) : (
                  <EmptyState height={92} title="NO RESIDUAL EXPOSURE" detail="后端未返回 residual_exposures（未做风格中性化时为空）" />
                )
              }
            </DataSlot>
          </div>
        </div>
      </section>

      <div className="kpis k8">
        <Kpi
          label={<TermTip id="rank_ic" value={r ? r.rank_ic_mean : null} gate={r ? gateOf(r, 'rank_ic') : undefined} context={r ? { ic_std: r.ic_std } : undefined}>RankIC 均值</TermTip>}
          value={r ? fx(r.rank_ic_mean, 4) : '—'}
          sub={r ? <>{vt(r, 'rank_ic')}<span>σ {fx(r.ic_std, 3)}</span></> : undefined}
        />
        <Kpi
          label={<TermTip id="icir" value={r ? r.icir_ann : null} gate={r ? gateOf(r, 'icir_ann') : undefined}>ICIR 年化</TermTip>}
          value={r ? fx(r.icir_ann, 2) : '—'}
          sub={r ? <>{vt(r, 'icir_ann')}<TermTip id="ic_win_rate" as="span" value={isNum(r.ic_win_rate) ? (ratioPct(r.ic_win_rate) ?? NaN) / 100 : null}>胜率 {pct(ratioPct(r.ic_win_rate), 0)}</TermTip></> : undefined}
        />
        <Kpi
          label={<TermTip id="nw_t_stat" value={r ? r.nw_t_stat : null} gate={r ? gateOf(r, 'nw_t_stat') : undefined}>NW t-stat</TermTip>}
          value={r ? fx(r.nw_t_stat, 2) : '—'}
          sub={r ? <>{vt(r, 'nw_t_stat')}<TermTip id="newey_west_hac" as="span">Newey–West</TermTip></> : undefined}
        />
        <Kpi label={<TermTip id="long_short" value={lsPct} display={spct(lsPct, 1)}>多空年化收益</TermTip>} value={r ? spct(lsPct, 1) : '—'} sub={r ? <span>Q10−Q1</span> : undefined} />
        <Kpi label={<TermTip id="top_excess" value={r ? ratioPct(r.top_excess_ann) : null} display={r ? spct(ratioPct(r.top_excess_ann), 1) : undefined}>Top 组超额年化</TermTip>} value={r ? spct(ratioPct(r.top_excess_ann), 1) : '—'} sub={r ? <span>vs 等权</span> : undefined} />
        <Kpi
          label={<TermTip id="turnover_oneway" value={r ? kpiValue(r, 'daily_turnover_oneway') : null} gate={r ? gateOf(r, 'daily_turnover_oneway') : undefined}>单边日换手率</TermTip>}
          value={r ? pct(ratioPct(r.daily_turnover_oneway), 1) : '—'}
          sub={r ? <>{vt(r, 'daily_turnover_oneway')}<span>≤{pct(ratioPct(threshold(r, 'daily_turnover_oneway')), 0)}</span></> : undefined}
        />
        <Kpi label={<TermTip id="half_life" value={r ? r.half_life_days : null} display={r ? `${fx(r.half_life_days, 1)}D` : undefined}>IC 半衰期</TermTip>} value={r ? `${fx(r.half_life_days, 1)}D` : '—'} sub={r ? <span>指数衰减拟合</span> : undefined} />
        <Kpi
          label={<TermTip id="max_corr_library" value={r ? r.max_corr_in_library : null} gate={r ? gateOf(r, 'max_corr_in_library') : undefined} context={r ? { peer: r.max_corr_peer } : undefined}>库内 MaxCorr</TermTip>}
          value={r ? fx(r.max_corr_in_library, 2) : '—'}
          sub={r ? <>{vt(r, 'max_corr_in_library')}<span>{r.max_corr_peer || '—'}</span></> : undefined}
        />
      </div>
      {r && (ci(r.rank_ic_ci95) || ci(r.icir_ci95) || ci(r.long_short_ci95)) ? (
        <div
          style={{
            marginTop: 0,
            border: '1px solid var(--line)',
            borderTop: 0,
            background: 'var(--bg1)',
            padding: '5px 10px',
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            gap: '6px 16px',
            fontSize: 10.5
          }}
        >
          {ci(r.rank_ic_ci95) ? (
            <span className="mono" style={{ display: 'inline-flex', alignItems: 'center', gap: 5, color: 'var(--tx2)' }}>
              <span className="c-3">RankIC</span>
              <CiRow v={r.rank_ic_ci95} d={4} />
            </span>
          ) : null}
          {ci(r.icir_ci95) ? (
            <span className="mono" style={{ display: 'inline-flex', alignItems: 'center', gap: 5, color: 'var(--tx2)' }}>
              <span className="c-3">ICIR</span>
              <CiRow v={r.icir_ci95} d={2} />
            </span>
          ) : null}
          {ci(r.long_short_ci95) ? (
            <span className="mono" style={{ display: 'inline-flex', alignItems: 'center', gap: 5, color: 'var(--tx2)' }}>
              <span className="c-3">多空年化</span>
              <CiRow v={r.long_short_ci95} pctScale />
            </span>
          ) : null}
          <span className="ci" style={{ marginLeft: 'auto' }}>
            Stationary Bootstrap · 区间不含 0 方可视为显著
          </span>
        </div>
      ) : null}

      <div className="gstrip">
        {r ? (
          <>
            <div className={`gs ${r.admission_passed ? 'pass' : nPass >= checks.length - 1 ? 'warn' : 'fail'}`}>
              <b>
                入库门禁 {nPass}/{checks.length}
              </b>
              <span>{checks.map((c) => `${c.name}${c.threshold ? ` ${c.threshold}` : ''}`).join(' · ') || '后端未返回 gate_checks'}</span>
            </div>
            {warnings.length ? (
              warnings.map(([tone, t, d], i) => (
                <div key={i} className={`gs ${tone}`}>
                  <b>{t}</b>
                  <span>{d}</span>
                </div>
              ))
            ) : (
              <div className={`gs ${leak ? 'warn' : 'pass'}`}>
                <b>预处理 {pipeOn}/{PIPELINE.length}</b>
                <span>{leak ? `残余风格暴露超 ±${fx(EXP_LIMIT)}σ` : '无风格泄漏 · 可提交 Q3 合成'}</span>
              </div>
            )}
          </>
        ) : (
          <div className="gs">
            <b>入库门禁 —</b>
            <span>{apiOnline ? (ev.error ? '评估失败' : '等待评估结果') : 'API OFFLINE · 无真实数据'}</span>
          </div>
        )}
      </div>

      <div className="g2">
        <Panel
          title={<TermTip id="monotonicity">10 分层单调性净值 · Q1–Q10 + 多空</TermTip>}
          prd="Q2.3"
          busy={busy}
          tools={
            r ? (
              <>
                <TermTip id="monotonicity" as="span" value={r.monotonicity_spearman}>
                  <Tag tone={verdictTone(judge(withGate('monotonicity', undefined), r.monotonicity_spearman).verdict)}>单调性 ρ={fx(r.monotonicity_spearman)}</Tag>
                </TermTip>
                <Tag tone="mute">L/S {spct(lsPct, 1)}/yr</Tag>
              </>
            ) : null
          }
        >
          <DataSlot height={230} ready={!!charts} loading={ev.loading} error={slotErr}>
            {() =>
              decSeries.length && charts!.n ? (
                <>
                  <LineChart height={204} xs={r!.dates} series={decSeries} base={1} bands={REGIME_BANDS} ticks={5} yFmt={(v) => v.toFixed(2)} tipFn={decTip} />
                  <div className="lg" style={{ marginTop: 6 }}>
                    <span>
                      <i style={{ background: `linear-gradient(90deg,${DECILE_COLORS[0]},${DECILE_COLORS[4]},${DECILE_COLORS[9]})`, width: 46, height: 3 }} />
                      Q1 → Q10
                    </span>
                    {charts!.lsS ? (
                      <span>
                        <i style={{ background: 'var(--sig)', height: 2 }} />
                        多空 L/S
                      </span>
                    ) : null}
                    {charts!.benchS ? (
                      <span>
                        <i style={{ borderTop: '1px dashed var(--tx3)', height: 0, background: 'transparent' }} />
                        等权基准
                      </span>
                    ) : null}
                    {annQ.length ? (
                      <span style={{ marginLeft: 'auto' }}>
                        Q10 <b className="c-up">{spct(q10, 1)}</b> · Q1 <b className="c-dn">{spct(q1, 1)}</b> /yr
                      </span>
                    ) : null}
                  </div>
                </>
              ) : (
                <EmptyState height={230} title="NO DECILE DATA" detail="decile_navs 为空（样本不足或评估未完成）" />
              )
            }
          </DataSlot>
        </Panel>

        <Panel
          title={<TermTip id="rank_ic">滚动 60D RankIC · 累计 IC</TermTip>}
          prd="Q2.3"
          busy={busy}
          tools={
            <>
              <span className="lg">
                <span>
                  <i className="bx" style={{ background: 'var(--up)' }} />
                  IC&gt;0
                </span>
                <span>
                  <i className="bx" style={{ background: 'var(--dn)' }} />
                  IC&lt;0
                </span>
                <span>
                  <i style={{ background: 'var(--sig)' }} />
                  累计
                </span>
              </span>
              {charts && isNum(charts.posRatio) ? <Tag tone="mute">IC&gt;0 {fx(charts.posRatio * 100, 0)}%</Tag> : null}
            </>
          }
        >
          <DataSlot height={226} ready={!!charts} loading={ev.loading} error={slotErr}>
            {() =>
              charts!.icv.length ? (
                <BarChart
                  height={226}
                  values={charts!.icv}
                  dates={charts!.icx}
                  bands={REGIME_BANDS}
                  gap={1}
                  op={0.72}
                  line={charts!.cum.length ? { data: charts!.cum, color: 'var(--sig)' } : undefined}
                  yFmt={(v) => v.toFixed(2)}
                  rFmt={(v) => v.toFixed(1)}
                  tip={(v, i) => `${charts!.icx[i]} · 60D RankIC ${sg(v, 4)} · 累计 IC ${fx(charts!.cum[i], 2)}`}
                />
              ) : (
                <EmptyState height={226} title="NO ROLLING IC" detail="rolling_60d_ic 为空（有效样本 < 60 日）" />
              )
            }
          </DataSlot>
        </Panel>

        <Panel
          title={<TermTip id={alt === 'lag' ? 'ic_decay' : 'industry_neutral'}>{alt === 'lag' ? `IC 滞后衰减 · Lag ${charts?.lag.map(([k]) => k).join('/') || '—'}` : '分行业 RankIC'}</TermTip>}
          prd="Q2.3"
          busy={busy}
          tools={
            <Seg
              value={alt}
              options={[
                { key: 'lag', label: 'IC 衰减' },
                { key: 'ind', label: '行业 IC' }
              ]}
              onChange={setAlt}
            />
          }
        >
          <DataSlot height={226} ready={!!charts} loading={ev.loading} error={slotErr}>
            {() => {
              if (alt === 'lag') {
                const lag = charts!.lag;
                if (!lag.length) return <EmptyState height={226} title="NO IC DECAY" detail="ic_decay 为空" />;
                return (
                  <BarChart
                    height={226}
                    values={lag.map(([, v]) => v)}
                    labels={lag.map(([k]) => `Lag ${k}`)}
                    color={() => 'var(--sig)'}
                    showVal={(v) => fx(v, 4)}
                    yFmt={(v) => v.toFixed(2)}
                    gap={26}
                    hlines={isNum(lag0) && lag0 !== 0 ? [{ v: lag0 / 2, color: 'var(--warn)', label: `50% 半衰线 · HL ${fx(r!.half_life_days, 1)}D` }] : []}
                    tip={(v, i) => `Lag ${lag[i][0]} · RankIC ${fx(v, 4)}${isNum(lag0) && lag0 !== 0 ? ` · 保留 ${fx((v / lag0) * 100, 1)}%` : ''}`}
                  />
                );
              }
              const ind = charts!.ind;
              if (!ind.length) return <EmptyState height={226} title="NO INDUSTRY IC" detail="industry_ic 为空" />;
              const cut = ind.length > 14 ? 2 : 4;
              return (
                <BarChart
                  height={226}
                  values={ind.map(([, v]) => v)}
                  labels={ind.map(([k]) => k.slice(0, cut))}
                  yFmt={(v) => v.toFixed(2)}
                  gap={ind.length > 20 ? 2 : 8}
                  hlines={[{ v: r!.rank_ic_mean, color: 'var(--sig)', label: `全市场 ${fx(r!.rank_ic_mean, 3)}` }]}
                  tip={(v, i) => `${ind[i][0]} · RankIC ${sg(v, 4)}`}
                />
              );
            }}
          </DataSlot>
        </Panel>

        <Panel
          title={<TermTip id="max_corr_library">{`因子库相关性矩阵${r?.corr_matrix_labels.length ? ` · ${r.corr_matrix_labels.length}×${r.corr_matrix_labels.length}` : ''}`}</TermTip>}
          prd="Q2.3"
          busy={busy}
          tools={
            <>
              <span className="lg">
                <span>
                  <i className="bx" style={{ border: '1.5px solid var(--up)' }} />
                  |r|&gt;0.70
                </span>
              </span>
              {r ? <Tag tone={corrRisk ? 'fail' : 'pass'}>MaxCorr {fx(r.max_corr_in_library)}</Tag> : null}
            </>
          }
        >
          <DataSlot height={226} ready={!!r} loading={ev.loading} error={slotErr}>
            {() =>
              r!.corr_matrix_labels.length && r!.corr_matrix_values.length ? (
                <Heatmap height={226} labels={r!.corr_matrix_labels} short={r!.corr_matrix_labels.map(shortLabel)} matrix={r!.corr_matrix_values} riskAbs={threshold(r!, 'max_corr_in_library')} />
              ) : (
                <EmptyState height={226} title="NO CORRELATION MATRIX" detail="因子库同窗样本不足，后端未返回相关矩阵" />
              )
            }
          </DataSlot>
        </Panel>
      </div>

      <DimensionMatrix slices={r?.dimension_slices} title="维度切片稳定性矩阵" height={96} />
    </div>
  );
}

function Meters({ items }: { items: [string, number][] }) {
  const S = Math.max(0.8, ...items.map(([, v]) => Math.abs(v) * 1.1));
  const L = (EXP_LIMIT / S) * 50;
  return (
    <>
      {items.map(([k, v]) => {
        const w = Math.min(50, (Math.abs(v) / S) * 50);
        const bad = Math.abs(v) > EXP_LIMIT;
        return (
          <div key={k} className="mtr" title={`${k} 残余暴露 ${sg(v)}σ`}>
            <span>{k}</span>
            <div className="mtb">
              <i className="lim" style={{ left: `${50 - L}%` }} />
              <i className="lim" style={{ left: `${50 + L}%` }} />
              <i className="mz" />
              <b style={{ left: `${v < 0 ? 50 - w : 50}%`, width: `${w}%`, background: bad ? 'var(--warn)' : 'var(--sig)' }} />
            </div>
            <em className={bad ? 'c-warn' : ''}>{sg(v)}σ</em>
          </div>
        );
      })}
    </>
  );
}
