'use client';

/**
 * Zero-dependency micro-TeX renderer (no CDN, no KaTeX). Supported subset:
 * `_{}` `^{}` groups, Greek letters, \sum \prod \int, \sqrt{}, \frac{}{}, \hat{} \bar{} \overline{},
 * \text{} \mathrm{} \operatorname{} \mathbb{}, relations (\le \ge \ne \approx \in \to \Rightarrow …),
 * \lfloor \rceil \mid, spacing (\, \; \quad), \left / \right (ignored). Unknown commands render as text.
 */
import { Fragment, memo, type ReactNode } from 'react';

const GREEK: Record<string, string> = {
  alpha: 'α', beta: 'β', gamma: 'γ', delta: 'δ', epsilon: 'ϵ', varepsilon: 'ε', zeta: 'ζ', eta: 'η', theta: 'θ', vartheta: 'ϑ',
  iota: 'ι', kappa: 'κ', lambda: 'λ', mu: 'μ', nu: 'ν', xi: 'ξ', pi: 'π', rho: 'ρ', sigma: 'σ', tau: 'τ', upsilon: 'υ',
  phi: 'ϕ', varphi: 'φ', chi: 'χ', psi: 'ψ', omega: 'ω', Gamma: 'Γ', Delta: 'Δ', Theta: 'Θ', Lambda: 'Λ', Xi: 'Ξ', Pi: 'Π',
  Sigma: 'Σ', Upsilon: 'Υ', Phi: 'Φ', Psi: 'Ψ', Omega: 'Ω', ell: 'ℓ'
};
const OPS: Record<string, string> = {
  le: '≤', leq: '≤', ge: '≥', geq: '≥', ne: '≠', neq: '≠', approx: '≈', sim: '∼', equiv: '≡', propto: '∝', cdot: '·', times: '×',
  pm: '±', mp: '∓', div: '÷', in: '∈', notin: '∉', subset: '⊂', subseteq: '⊆', cup: '∪', cap: '∩', to: '→', rightarrow: '→',
  leftarrow: '←', Rightarrow: '⇒', Leftarrow: '⇐', Leftrightarrow: '⇔', iff: '⇔', mapsto: '↦', forall: '∀', exists: '∃',
  neg: '¬', land: '∧', lor: '∨', perp: '⊥', mid: '|', circ: '∘', ast: '∗', star: '⋆', setminus: '∖', gg: '≫', ll: '≪'
};
const SYMS: Record<string, string> = {
  infty: '∞', partial: '∂', nabla: '∇', lfloor: '⌊', rfloor: '⌋', lceil: '⌈', rceil: '⌉', langle: '⟨', rangle: '⟩',
  ldots: '…', cdots: '⋯', dots: '…', emptyset: '∅', top: '⊤', prime: '′', '{': '{', '}': '}', '|': '‖', '%': '%', '#': '#', _: '_', '&': '&'
};
const BIG: Record<string, string> = { sum: '∑', prod: '∏', int: '∫', oint: '∮', bigcup: '⋃', bigcap: '⋂' };
const FN = new Set(['log', 'ln', 'exp', 'max', 'min', 'arg', 'argmax', 'argmin', 'sup', 'inf', 'lim', 'det', 'tr', 'var', 'Var', 'Cov', 'Corr', 'corr', 'Pr', 'sign', 'rank', 'sgn', 'diag', 'mean', 'median', 'std', 'erf']);
const SPACE: Record<string, string> = { ',': '\u2009', ';': '\u2005', ':': '\u2005', '!': '', ' ': ' ', quad: '\u2003', qquad: '\u2003\u2003', '\\': ' ' };
const IGNORE = new Set(['left', 'right', 'big', 'Big', 'bigg', 'Bigg', 'bigl', 'bigr', 'Bigl', 'Bigr', 'displaystyle', 'limits', 'nolimits']);
const BB: Record<string, string> = { E: '𝔼', R: 'ℝ', N: 'ℕ', P: 'ℙ', Z: 'ℤ', Q: 'ℚ', '1': '𝟙', I: '𝕀', V: '𝕍' };
const OPCH = new Set(['=', '+', '<', '>', '-', '*', '/']);
const has = (o: Record<string, string>, k: string) => Object.prototype.hasOwnProperty.call(o, k);

class Parser {
  i = 0;
  k = 0;
  constructor(private s: string) {}

  private key() {
    return this.k++;
  }

  private peek() {
    return this.s[this.i];
  }

  private readCmd(): string {
    this.i++;
    const m = /^[A-Za-z]+/.exec(this.s.slice(this.i));
    if (m) {
      this.i += m[0].length;
      return m[0];
    }
    const c = this.s[this.i] ?? '';
    this.i++;
    return c;
  }

  /** Raw brace content (for \text and friends). */
  private readRaw(): string {
    while (this.peek() === ' ') this.i++;
    if (this.peek() !== '{') {
      const c = this.s[this.i] ?? '';
      this.i++;
      return c;
    }
    let depth = 0;
    const start = this.i + 1;
    for (; this.i < this.s.length; this.i++) {
      const c = this.s[this.i];
      if (c === '\\') {
        this.i++;
        continue;
      }
      if (c === '{') depth++;
      else if (c === '}' && --depth === 0) {
        const raw = this.s.slice(start, this.i);
        this.i++;
        return raw;
      }
    }
    return this.s.slice(start);
  }

  /** `{...}` group or a single atom. */
  private arg(): ReactNode {
    while (this.peek() === ' ') this.i++;
    if (this.peek() === '{') {
      this.i++;
      return this.seq('}');
    }
    return this.atom();
  }

  seq(end?: string): ReactNode[] {
    const out: ReactNode[] = [];
    while (this.i < this.s.length) {
      const c = this.peek();
      if (end && c === end) {
        this.i++;
        break;
      }
      if (c === '_' && this.i >= 2 && /[A-Z]{2}$/.test(this.s.slice(0, this.i)) && /^[A-Z]{2}/.test(this.s.slice(this.i + 1))) {
        this.i++;
        out.push(<Fragment key={this.key()}>_</Fragment>);
        continue;
      }
      if (c === '_' || c === '^') {
        this.i++;
        const body = this.arg();
        out.push(c === '_' ? <sub key={this.key()}>{body}</sub> : <sup key={this.key()}>{body}</sup>);
        continue;
      }
      const a = this.atom();
      if (a !== null) out.push(<Fragment key={this.key()}>{a}</Fragment>);
    }
    return out;
  }

  private atom(): ReactNode {
    const c = this.peek();
    if (c === undefined) return null;
    if (c === '{') {
      this.i++;
      return this.seq('}');
    }
    if (c === '}') {
      this.i++;
      return null;
    }
    if (c === ' ' || c === '\t' || c === '&') {
      this.i++;
      return null;
    }
    if (c === '~') {
      this.i++;
      return '\u00a0';
    }
    if (c === '\\') return this.command(this.readCmd());
    this.i++;
    if (/[0-9.]/.test(c)) {
      let n = c;
      while (/[0-9.]/.test(this.peek() ?? '')) n += this.s[this.i++];
      return <span className="r">{n}</span>;
    }
    if (OPCH.has(c)) return <span className="op">{c === '-' ? '−' : c === '*' ? '∗' : c}</span>;
    if (c === "'") return '′';
    if (/[()[\],;:|!?]/.test(c)) return <span className="r">{c}</span>;
    if (/[A-Za-z]/.test(c)) return c;
    return <span className="r">{c}</span>;
  }

  private command(name: string): ReactNode {
    if (has(SPACE, name)) return SPACE[name] || null;
    if (IGNORE.has(name)) return null;
    if (has(GREEK, name)) return GREEK[name];
    if (has(OPS, name)) return <span className="op">{OPS[name]}</span>;
    if (has(SYMS, name)) return <span className="r">{SYMS[name]}</span>;
    if (has(BIG, name)) return <span className="big">{BIG[name]}</span>;
    if (FN.has(name)) return <span className="fn">{name === 'argmax' ? 'arg max' : name === 'argmin' ? 'arg min' : name}</span>;
    switch (name) {
      case 'operatorname':
      case 'mathrm':
        return <span className="fn">{this.readRaw()}</span>;
      case 'text':
      case 'textrm':
      case 'textit':
      case 'mbox':
        return <span className="tx">{this.readRaw()}</span>;
      case 'textbf':
      case 'mathbf':
      case 'boldsymbol':
        return <b>{this.arg()}</b>;
      case 'mathbb': {
        const raw = this.readRaw();
        return <span className="fx-bb">{[...raw].map((ch) => BB[ch] ?? ch).join('')}</span>;
      }
      case 'mathcal':
      case 'mathit':
        return this.arg();
      case 'sqrt': {
        let idx: ReactNode = null;
        if (this.peek() === '[') {
          const close = this.s.indexOf(']', this.i);
          if (close > this.i) {
            idx = <sup>{this.s.slice(this.i + 1, close)}</sup>;
            this.i = close + 1;
          }
        }
        return (
          <span className="fx-sq">
            {idx}
            <i>√</i>
            <span>{this.arg()}</span>
          </span>
        );
      }
      case 'frac':
      case 'dfrac':
      case 'tfrac': {
        const a = this.arg();
        const b = this.arg();
        return (
          <span className="fx-fr">
            <span>{a}</span>
            <span>{b}</span>
          </span>
        );
      }
      case 'hat':
      case 'widehat':
        return <span className="fx-hat">{this.arg()}</span>;
      case 'bar':
      case 'overline':
      case 'tilde':
      case 'widetilde':
      case 'vec':
        return <span className="fx-ov">{this.arg()}</span>;
      default:
        return <span className="fn">{name}</span>;
    }
  }
}

export function renderTex(src: string): ReactNode {
  try {
    return new Parser(src).seq();
  } catch {
    return <span className="r">{src}</span>;
  }
}

function splitSymbol(s: string): [string, string] {
  const m = /^(.+?)\s*[:：]\s*(.+)$/.exec(s);
  return m ? [m[1], m[2]] : [s, ''];
}

export const FormulaBlock = memo(function FormulaBlock({ lines, symbols }: { lines: string[]; symbols?: string[] }) {
  if (!lines.length) return null;
  return (
    <div className="fx" role="math" aria-label={lines.join('; ')}>
      {lines.map((l, i) => (
        <div className="fx-l" key={i}>
          {renderTex(l)}
        </div>
      ))}
      {symbols?.length ? (
        <dl className="fx-sym">
          {symbols.map((s, i) => {
            const [sym, desc] = splitSymbol(s);
            return (
              <Fragment key={i}>
                <dt>{renderTex(sym)}</dt>
                <dd>{desc}</dd>
              </Fragment>
            );
          })}
        </dl>
      ) : null}
    </div>
  );
});
