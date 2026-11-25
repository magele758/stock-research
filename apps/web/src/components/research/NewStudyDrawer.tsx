'use client';

import { useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { fx } from '@/lib/format';
import { drawerStore } from '@/lib/glossary/store';
import { UNIVERSES } from '@/lib/meta';
import { QuantApiError } from '@/lib/quant-api';
import { draftToRequest, isV3Missing, researchApi, STUDY_DIMENSIONS, type StudyDraft } from '@/lib/research-api';
import type { ResearchDispatchResponse, StudyKind, UniverseId } from '@/lib/types';
import { useApi } from '@/lib/use-api';
import { useWorkbench } from '@/lib/workbench';
import { Drawer } from '../glossary/Drawer';
import { TermTip } from '../glossary/TermTip';
import { Icon } from '../icons';
import { Seg } from '../ui';
import { jaccard, tokens } from './shared';

const KINDS: { key: StudyKind; label: string }[] = [
  { key: 'factor', label: '因子' },
  { key: 'model', label: '多因子模型' },
  { key: 'portfolio_method', label: '组合构建方法' },
  { key: 'execution_method', label: '执行方法' }
];

const DEFAULT_DIMS = ['market_regime', 'volatility_regime', 'market_cap_tier', 'sw_industry'];

export function NewStudyDrawer({ draft }: { draft: StudyDraft }) {
  const wb = useWorkbench();
  const router = useRouter();
  const [title, setTitle] = useState(draft.title ?? '');
  const [hypothesis, setHypothesis] = useState(draft.hypothesis ?? '');
  const [expression, setExpression] = useState(draft.expression ?? '');
  const [kind, setKind] = useState<StudyKind>(draft.kind ?? 'factor');
  const [parent, setParent] = useState<string>(draft.parent_study_id ?? '');
  const [cluster, setCluster] = useState(draft.target_cluster ?? 'engine_sandbox');
  const [universe, setUniverse] = useState<string>(draft.universe_id ?? wb.universe);
  const [dims, setDims] = useState<string[]>(draft.dimensions?.length ? draft.dimensions : DEFAULT_DIMS);
  const [m, setM] = useState(String(draft.trial_count_m ?? 20));
  const [bootB, setBootB] = useState(String(draft.bootstrap_b ?? 500));
  const [avoid] = useState<string[]>(draft.avoid ?? []);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [blocked, setBlocked] = useState<ResearchDispatchResponse | null>(null);
  const [override, setOverride] = useState('');

  const online = wb.apiOnline;
  const clusters = useApi(online ? 'drawer-clusters' : null, (s) => researchApi.clusters(s));
  const tombs = useApi(online ? 'drawer-tombs' : null, (s) => researchApi.tombstones(s));
  const routeMissing = wb.research.available === false && online;

  const similar = useMemo(() => {
    const mine = tokens(`${title} ${hypothesis} ${expression} ${draft.factor_id ?? ''}`);
    if (!mine.size || !tombs.data) return [];
    return tombs.data
      .map((t) => {
        const exact = !!expression.trim() && t.expression.replace(/\s+/g, '') === expression.replace(/\s+/g, '');
        return { t, sim: exact ? 1 : jaccard(mine, tokens(`${t.factor_id} ${t.expression} ${t.lesson_summary} ${t.memory_line}`)) };
      })
      .filter((x) => x.sim >= 0.3)
      .sort((a, b) => b.sim - a.sim)
      .slice(0, 3);
  }, [title, hypothesis, expression, tombs.data, draft.factor_id]);

  const parents = wb.research.studies;
  const nM = Number(m);
  const nB = Number(bootB);
  const valid = title.trim().length > 0 && title.trim().length <= 60 && hypothesis.trim().length > 0 && Number.isInteger(nM) && nM >= 1 && Number.isInteger(nB) && nB >= 50;
  const canSubmit = valid && online && !routeMissing && !busy;

  const currentDraft = (): StudyDraft => ({
    ...draft,
    title: title.trim(),
    hypothesis: hypothesis.trim(),
    expression: expression.trim() || null,
    kind,
    parent_study_id: parent || null,
    target_cluster: cluster,
    universe_id: universe,
    dimensions: dims,
    trial_count_m: nM,
    bootstrap_b: nB,
    avoid
  });

  const submit = async (force = false) => {
    if (!canSubmit) return;
    setBusy(true);
    setErr(null);
    try {
      const d = currentDraft();
      const req = draftToRequest({ ...d, title: d.title ?? '', hypothesis: d.hypothesis ?? '' }, wb.snapshotId, wb.preprocess);
      if (force) {
        req.allow_duplicate = true;
        req.override_rationale = override.trim() || null;
      }
      const res = await researchApi.dispatch(req);
      if (res.duplicate_blocked && !force) {
        setBlocked(res);
        return;
      }
      const id = res.study?.study_id;
      wb.refreshResearch();
      drawerStore.close();
      if (id) {
        wb.setStudyId(id);
        router.push(`/research?study=${encodeURIComponent(id)}`);
      }
    } catch (e) {
      setErr(isV3Missing(e) ? '研究派发接口未上线（/research/dispatch 返回 404）' : e instanceof QuantApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const askBot = () => {
    wb.dock.dispatchStudy(currentDraft());
    drawerStore.close();
  };

  return (
    <Drawer
      label="新建策略 / 方法研究"
      title="新建策略 / 方法研究"
      sub={draft.source ? `来自 ${draft.source}` : 'Q7.3 · 假设 → 推演 → 形式化验证 → 回测 → 诊断'}
      onClose={drawerStore.close}
      footer={
        <>
          <span className="ps" style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: !online || routeMissing ? 'var(--warn)' : undefined }}>
            {!online ? 'quant-api 离线 · 不可派发' : routeMissing ? '研究派发接口未上线' : `snapshot ${wb.snapshotId ?? wb.api.health?.snapshot_id ?? 'latest'}`}
          </span>
          <button type="button" className="btn sm" onClick={drawerStore.close}>
            取消
          </button>
          <button type="button" className="btn sm" onClick={askBot} disabled={!title.trim() && !hypothesis.trim()}>
            <Icon name="hex" size={10} sw={2} />
            交给 Quant-Lead 起草
          </button>
          <button type="button" className="btn sm pri" onClick={() => void submit(false)} disabled={!canSubmit}>
            {busy ? <i className="spin" /> : <Icon name="send" size={10} sw={2} />}
            提交并派发集群
          </button>
        </>
      }
    >
      <div style={{ padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 10 }}>
        {avoid.length ? (
          <div className="notice warn">
            <Icon name="tomb" size={13} style={{ color: 'var(--warn)', flex: 'none', marginTop: 1 }} />
            <div>
              <b>规避条件</b>（派发时自动写入假设）
              {avoid.map((a, i) => (
                <div key={i} style={{ marginTop: 2 }}>
                  {a}
                </div>
              ))}
            </div>
          </div>
        ) : null}
        <div className="fld-g">
          <label htmlFor="ns-title">研究标题 · 必填 ≤ 60 字</label>
          <input id="ns-title" className="inp" value={title} maxLength={60} onChange={(e) => setTitle(e.target.value)} placeholder="如：量价背离因子的小盘依赖修正 V2" />
        </div>
        <div className="fld-g">
          <span className="lab">研究类型</span>
          <Seg value={kind} options={KINDS} onChange={setKind} stretch />
        </div>
        <div className="fld-g">
          <label htmlFor="ns-hyp">经济假设 / 想法 · 必填</label>
          <textarea id="ns-hyp" className="inp" rows={4} value={hypothesis} onChange={(e) => setHypothesis(e.target.value)} placeholder="可证伪的经济机制：信号为何应当有效、在什么 Regime 下失效" />
        </div>
        <div className="fld-g">
          <label htmlFor="ns-expr">
            因子表达式 · 可选（经后端 AST 白名单 +{' '}
            <TermTip id="ast_causal_proof" as="span">
              因果证明
            </TermTip>
            ）
          </label>
          <textarea id="ns-expr" className="inp code" rows={3} value={expression} onChange={(e) => setExpression(e.target.value)} placeholder="rank(-corr(close, volume, 20))" spellCheck={false} />
        </div>
        <div className="fgrid">
          <div className="fld-g">
            <label htmlFor="ns-parent">
              父研究{' '}
              <TermTip id="study_lineage" as="span">
                parent_study_id
              </TermTip>
            </label>
            <select id="ns-parent" className="inp" value={parent} onChange={(e) => setParent(e.target.value)} disabled={Boolean(draft.lockParent && draft.parent_study_id)}>
              <option value="">（根课题）</option>
              {draft.parent_study_id && !parents.some((p) => p.study_id === draft.parent_study_id) ? <option value={draft.parent_study_id}>{draft.parent_study_id}</option> : null}
              {parents.map((p) => (
                <option key={p.study_id} value={p.study_id}>
                  {p.study_id} · {p.title.slice(0, 24)}
                </option>
              ))}
            </select>
          </div>
          <div className="fld-g">
            <label htmlFor="ns-uni">股票池</label>
            <select id="ns-uni" className="inp" value={universe} onChange={(e) => setUniverse(e.target.value as UniverseId)}>
              {UNIVERSES.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.label} ({u.id})
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="fld-g">
          <span className="lab">
            调度集群 ·{' '}
            <TermTip id="cluster_dispatch" as="span">
              dispatch
            </TermTip>
          </span>
          {clusters.data?.length ? (
            <div className="cstrip" style={{ gridTemplateColumns: `repeat(${Math.min(clusters.data.length, 3)}, minmax(0, 1fr))` }}>
              {clusters.data.map((c) => {
                const off = c.status === 'offline';
                const on = cluster === c.cluster_id;
                return (
                  <button
                    key={c.cluster_id}
                    type="button"
                    className={`cnode ${c.status}`}
                    disabled={off}
                    onClick={() => setCluster(c.cluster_id)}
                    aria-pressed={on}
                    style={{ textAlign: 'left', outline: on ? '1px solid var(--sig)' : undefined, background: on ? 'var(--sig-bg)' : undefined }}
                  >
                    <div className="ch">
                      <span>{c.display_name || c.cluster_id}</span>
                      <span className={`st ${off ? 'c-up' : c.status === 'degraded' ? 'c-warn' : 'c-pass'}`}>
                        <i style={{ background: 'currentColor' }} />
                        {c.status.toUpperCase()}
                      </span>
                    </div>
                    <div className="cm">
                      <span>
                        空闲 <b>{Math.max(0, c.slots_total - c.slots_busy)}</b>
                      </span>
                      <span>
                        队列 <b>Q{c.queue_depth}</b>
                      </span>
                    </div>
                  </button>
                );
              })}
            </div>
          ) : (
            <div className="empty" style={{ height: 44 }}>
              <span>{!online ? 'API OFFLINE' : clusters.error ? `集群接口不可用 · ${clusters.error.message}` : '加载集群…'}</span>
            </div>
          )}
        </div>
        <div className="fld-g">
          <span className="lab">
            对比维度基线 ·{' '}
            <TermTip id="dimension_dependence" as="span">
              单维依赖
            </TermTip>
          </span>
          <div className="chips">
            {STUDY_DIMENSIONS.map((d) => {
              const on = dims.includes(d.key);
              return (
                <button key={d.key} type="button" className={`chip ${on ? 'on' : ''}`} onClick={() => setDims((x) => (on ? x.filter((k) => k !== d.key) : [...x, d.key]))}>
                  <span className="cb" />
                  {d.label}
                </button>
              );
            })}
          </div>
        </div>
        <div className="fgrid">
          <div className="fld-g">
            <label htmlFor="ns-m">
              试验数{' '}
              <TermTip id="trial_count_m" as="span">
                M
              </TermTip>
            </label>
            <input id="ns-m" className="inp mono" inputMode="numeric" value={m} onChange={(e) => setM(e.target.value)} />
          </div>
          <div className="fld-g">
            <label htmlFor="ns-b">
              <TermTip id="bootstrap_ci" as="span">
                Bootstrap B
              </TermTip>{' '}
              · ≥ 50
            </label>
            <input id="ns-b" className="inp mono" inputMode="numeric" value={bootB} onChange={(e) => setBootB(e.target.value)} />
          </div>
        </div>
        {similar.length ? (
          <div className="notice warn">
            <Icon name="warn" size={13} style={{ color: 'var(--warn)', flex: 'none', marginTop: 1 }} />
            <div style={{ minWidth: 0 }}>
              <b>疑似重复已证伪课题</b>
              {similar.map(({ t, sim }) => (
                <div key={t.tombstone_id} style={{ marginTop: 2 }}>
                  与 <code>{t.factor_id || t.tombstone_id}</code>（{t.failure_code}）相似度 <b>{fx(sim, 2)}</b> · {t.lesson_summary.slice(0, 60)}
                </div>
              ))}
            </div>
          </div>
        ) : null}
        {blocked ? (
          <div className="notice">
            <Icon name="tomb" size={13} style={{ color: 'var(--up)', flex: 'none', marginTop: 1 }} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <b>后端墓碑查重拦截</b> · 命中 {blocked.tombstone_warnings.length} 条已证伪记录
              {blocked.tombstone_warnings.slice(0, 3).map((w, i) => (
                <div key={i} className="mono" style={{ fontSize: 10, marginTop: 2, overflowWrap: 'anywhere' }}>
                  {String(w.message ?? w.lesson_summary ?? w.tombstone_id ?? JSON.stringify(w)).slice(0, 160)}
                </div>
              ))}
              <input className="inp" style={{ marginTop: 6 }} placeholder="差异说明（override_rationale）：本次与墓碑课题的关键区别" value={override} onChange={(e) => setOverride(e.target.value)} />
              <button type="button" className="btn sm warnb" style={{ marginTop: 6 }} disabled={!override.trim() || busy} onClick={() => void submit(true)}>
                确认差异并强制派发
              </button>
            </div>
          </div>
        ) : null}
        {err ? (
          <div className="notice">
            <Icon name="warn" size={13} style={{ color: 'var(--up)', flex: 'none', marginTop: 1 }} />
            <div>{err}</div>
          </div>
        ) : null}
      </div>
    </Drawer>
  );
}
