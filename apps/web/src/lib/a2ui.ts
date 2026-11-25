/**
 * Minimal A2UI v0.9 surface state machine (createSurface / updateComponents /
 * updateDataModel / deleteSurface). Accepts both the v0.9 flat component shape
 * (`{ id, component: 'Text', text }`) and the v0.8 wrapped shape
 * (`{ id, component: { Text: { ... } } }`).
 */

export const BASIC_CATALOG = 'https://a2ui.org/specification/v0_9/basic_catalog.json';
export const QUANT_CATALOG = 'https://quant.local/a2ui/v1';

export interface A2uiComponent {
  id: string;
  type: string;
  props: Record<string, unknown>;
}

export interface SurfaceState {
  surfaceId: string;
  catalogId?: string;
  components: Record<string, A2uiComponent>;
  order: string[];
  dataModel: Record<string, unknown>;
  deleted?: boolean;
}

export const emptySurface = (surfaceId: string): SurfaceState => ({ surfaceId, components: {}, order: [], dataModel: {} });

function normComponent(raw: unknown): A2uiComponent | null {
  if (!raw || typeof raw !== 'object') return null;
  const o = raw as Record<string, unknown>;
  const id = typeof o.id === 'string' ? o.id : null;
  if (!id) return null;
  const { id: _id, component, ...rest } = o;
  void _id;
  if (typeof component === 'string') return { id, type: component, props: rest };
  if (component && typeof component === 'object') {
    const [type, props] = Object.entries(component as Record<string, unknown>)[0] ?? ['Unknown', {}];
    return { id, type, props: { ...rest, ...((props && typeof props === 'object' ? props : {}) as Record<string, unknown>) } };
  }
  return { id, type: 'Unknown', props: rest };
}

export function getPath(model: Record<string, unknown>, path: string): unknown {
  const parts = path.split('/').filter(Boolean);
  let cur: unknown = model;
  for (const p of parts) {
    if (!cur || typeof cur !== 'object') return undefined;
    cur = (cur as Record<string, unknown>)[p];
  }
  return cur;
}

export function setPath(model: Record<string, unknown>, path: string, value: unknown): Record<string, unknown> {
  const parts = path.split('/').filter(Boolean);
  if (!parts.length) return value && typeof value === 'object' && !Array.isArray(value) ? { ...(value as Record<string, unknown>) } : model;
  const root: Record<string, unknown> = { ...model };
  let cur = root;
  parts.forEach((p, i) => {
    if (i === parts.length - 1) cur[p] = value;
    else {
      const nxt = cur[p] && typeof cur[p] === 'object' ? { ...(cur[p] as Record<string, unknown>) } : {};
      cur[p] = nxt;
      cur = nxt;
    }
  });
  return root;
}

/** Fold one envelope (or a `{ messages: [...] }` batch / surface_update part) into the surface map. */
export function applyA2ui(map: Record<string, SurfaceState>, env: unknown, fallbackSurfaceId?: string): Record<string, SurfaceState> {
  if (!env || typeof env !== 'object') return map;
  const o = env as Record<string, unknown>;
  if (Array.isArray(o.messages)) {
    let next = map;
    const sid = typeof o.surfaceId === 'string' ? o.surfaceId : fallbackSurfaceId;
    for (const m of o.messages) next = applyA2ui(next, m, sid);
    if (sid && typeof o.catalogId === 'string' && next[sid]) next = { ...next, [sid]: { ...next[sid], catalogId: next[sid].catalogId ?? o.catalogId } };
    return next;
  }
  const pick = (k: string) => (o[k] && typeof o[k] === 'object' ? (o[k] as Record<string, unknown>) : null);
  const create = pick('createSurface') ?? pick('beginRendering');
  const comps = pick('updateComponents') ?? pick('surfaceUpdate');
  const data = pick('updateDataModel') ?? pick('dataModelUpdate');
  const del = pick('deleteSurface');
  const body = create ?? comps ?? data ?? del;
  const sid = (body && typeof body.surfaceId === 'string' ? body.surfaceId : undefined) ?? fallbackSurfaceId;
  if (!sid) return map;
  let s = map[sid] ?? emptySurface(sid);
  if (create) {
    s = { ...s, catalogId: typeof create.catalogId === 'string' ? create.catalogId : s.catalogId, deleted: false };
    if (typeof create.root === 'string' && !s.order.includes(create.root)) s = { ...s, order: [create.root, ...s.order] };
  }
  if (comps && Array.isArray(comps.components)) {
    const components = { ...s.components };
    const order = [...s.order];
    for (const raw of comps.components) {
      const c = normComponent(raw);
      if (!c) continue;
      if (!components[c.id]) order.push(c.id);
      components[c.id] = c;
    }
    s = { ...s, components, order };
  }
  if (data) {
    const path = typeof data.path === 'string' ? data.path : '/';
    const value = 'value' in data ? data.value : 'contents' in data ? data.contents : undefined;
    s = { ...s, dataModel: setPath(s.dataModel, path, value) };
  }
  if (del) s = { ...s, deleted: true };
  return { ...map, [sid]: s };
}

/** Resolve v0.9 dynamic values: plain literal | { literalString } | { path }. */
export function resolveValue(v: unknown, model: Record<string, unknown>): unknown {
  if (v && typeof v === 'object' && !Array.isArray(v)) {
    const o = v as Record<string, unknown>;
    if (typeof o.path === 'string') return getPath(model, o.path);
    for (const k of ['literalString', 'literalNumber', 'literalBoolean', 'literal']) if (k in o) return o[k];
  }
  return v;
}

export function childIds(props: Record<string, unknown>): string[] {
  const c = props.children;
  if (Array.isArray(c)) return c.filter((x): x is string => typeof x === 'string');
  if (c && typeof c === 'object') {
    const ex = (c as Record<string, unknown>).explicitList;
    if (Array.isArray(ex)) return ex.filter((x): x is string => typeof x === 'string');
  }
  if (typeof props.child === 'string') return [props.child];
  return [];
}

export function rootId(s: SurfaceState): string | undefined {
  if (s.components.root) return 'root';
  const referenced = new Set<string>();
  Object.values(s.components).forEach((c) => childIds(c.props).forEach((id) => referenced.add(id)));
  return s.order.find((id) => !referenced.has(id) && s.components[id]);
}

/** Build a complete v0.9 surface from components + data model (used by local deterministic cards). */
export function buildSurfaceEnvelopes(surfaceId: string, catalogId: string, dataModel: Record<string, unknown>, components: Record<string, unknown>[]) {
  return {
    surfaceId,
    catalogId,
    messages: [
      { createSurface: { surfaceId, catalogId } },
      { updateDataModel: { surfaceId, path: '/', value: dataModel } },
      { updateComponents: { surfaceId, components } }
    ]
  };
}
