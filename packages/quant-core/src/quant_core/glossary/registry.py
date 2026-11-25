"""Institutional quantitative glossary SSOT loader, search, and Bot prompt renderer."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from quant_core.schemas import (
    INSTITUTIONAL_GATES,
    GlossaryCategory,
    GlossaryListResponse,
    GlossaryTerm,
)

TERMS_PATH = Path(__file__).resolve().parent / "terms.json"


class _SafeFormatDict(dict[str, Any]):
    """Safe format mapping that never raises KeyError on missing placeholders."""

    def __missing__(self, key: str) -> str:
        return "未提供"


def _normalize_key(raw: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", raw.strip().lower()).strip("_")


class GlossaryRegistry:
    """In-memory SSOT registry backed by ``terms.json`` and aligned with ``INSTITUTIONAL_GATES``."""

    def __init__(self, terms_path: Path | str = TERMS_PATH) -> None:
        self.terms_path = Path(terms_path)
        raw_items = json.loads(self.terms_path.read_text(encoding="utf-8"))
        if not isinstance(raw_items, list):
            raise TypeError(f"Expected JSON array in {self.terms_path}")
        self._terms: list[GlossaryTerm] = [GlossaryTerm.model_validate(item) for item in raw_items]
        self._by_id: dict[str, GlossaryTerm] = {}
        self._by_alias: dict[str, GlossaryTerm] = {}
        for term in self._terms:
            tid = term.term_id.lower()
            if tid in self._by_id:
                raise ValueError(f"Duplicate glossary term_id: {term.term_id!r}")
            self._by_id[tid] = term
            self._by_alias[_normalize_key(term.term_id)] = term
            for alias in term.aliases:
                norm = _normalize_key(alias)
                if norm and norm not in self._by_alias:
                    self._by_alias[norm] = term

    @property
    def all_terms(self) -> list[GlossaryTerm]:
        return list(self._terms)

    @property
    def categories(self) -> list[str]:
        return [cat.value for cat in GlossaryCategory]

    def get_term(self, term_id: str) -> GlossaryTerm | None:
        """Resolve a term by canonical ``term_id`` or normalized alias."""
        if not term_id or not term_id.strip():
            return None
        key = term_id.strip().lower()
        if key in self._by_id:
            return self._by_id[key]
        norm = _normalize_key(term_id)
        return self._by_alias.get(norm)

    def list_terms(
        self,
        category: str | None = None,
        domain: str | None = None,
        q: str | None = None,
    ) -> GlossaryListResponse:
        """Filter and rank glossary terms by category, domain, and free-text query."""
        cat_filter = category.strip().lower() if category and category.strip() else None
        dom_filter = domain.strip().upper() if domain and domain.strip() else None
        query = q.strip().lower() if q and q.strip() else None
        query_norm = _normalize_key(query) if query else ""

        filtered: list[tuple[int, int, GlossaryTerm]] = []
        for idx, term in enumerate(self._terms):
            if cat_filter and term.category.value.lower() != cat_filter:
                continue
            if dom_filter and term.domain.upper() != dom_filter:
                continue
            if query:
                score = self._match_score(term, query, query_norm)
                if score <= 0:
                    continue
                filtered.append((-score, idx, term))
            else:
                filtered.append((0, idx, term))

        filtered.sort(key=lambda item: (item[0], item[1]))
        matched = [item[2] for item in filtered]
        return GlossaryListResponse(
            count=len(matched),
            categories=self.categories,
            terms=matched,
        )

    @staticmethod
    def _match_score(term: GlossaryTerm, query: str, query_norm: str) -> int:
        tid = term.term_id.lower()
        aliases_lower = [a.lower() for a in term.aliases]
        aliases_norm = [_normalize_key(a) for a in term.aliases]
        if query == tid or (query_norm and query_norm == _normalize_key(tid)):
            return 100
        if query in aliases_lower or (query_norm and query_norm in aliases_norm):
            return 90
        if tid.startswith(query) or term.term.lower().startswith(query) or term.en_name.lower().startswith(query):
            return 75
        en_initials = "".join(w[0] for w in re.findall(r"[A-Za-z]+", term.en_name)).lower()
        if len(query) >= 2 and en_initials.startswith(query):
            return 65
        haystack = " ".join(
            [
                tid,
                term.term.lower(),
                term.en_name.lower(),
                " ".join(aliases_lower),
                term.short_tip.lower(),
                term.institutional_threshold.lower(),
            ]
        )
        if query in haystack:
            return 50
        tokens = [t for t in re.split(r"[\s/_-]+", query) if t]
        if len(tokens) > 1 and all(tok in haystack for tok in tokens):
            return 40
        return 0

    def render_bot_prompt(
        self,
        term_id: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Render a contextual Bot prompt for ``term_id`` without mutating system prompts."""
        term = self.get_term(term_id)
        if term is None:
            raise KeyError(f"Unknown glossary term_id: {term_id!r}")

        ctx = dict(context or {})
        entity_id = (
            ctx.get("entity_id")
            or ctx.get("study_id")
            or ctx.get("experiment_id")
            or ctx.get("factor_id")
            or "当前研究对象"
        )
        mapping = _SafeFormatDict(
            {
                "term_id": term.term_id,
                "term": term.term,
                "en_name": term.en_name,
                "domain": term.domain,
                "category": term.category.value,
                "short_tip": term.short_tip,
                "formula_latex": term.formula_latex,
                "institutional_threshold": term.institutional_threshold or "参考具体策略口径",
                "snapshot_id": ctx.get("snapshot_id") or "当前快照",
                "universe_id": ctx.get("universe_id") or "CSI500",
                "entity_id": entity_id,
                "value": ctx.get("value") if ctx.get("value") is not None else "未提供",
                "trial_count_m": ctx.get("trial_count_m") if ctx.get("trial_count_m") is not None else "未申报",
                **{k: ("未提供" if v is None else v) for k, v in ctx.items()},
            }
        )
        template = term.bot_prompt_template or (
            "请结合当前快照 {snapshot_id}（股票池 {universe_id}，对象 {entity_id}）的真实数据，"
            "深入解读机构量化术语「{term}」（{en_name}）：当前观测值为 {value}，"
            "机构门槛参考为「{institutional_threshold}」。"
            "请说明统计含义、当前值是否触发风险或过拟合警示，并给出下一步验证与改进建议。"
        )
        rendered = template.format_map(mapping)
        extra_keys = [
            k
            for k in ctx
            if k not in {"snapshot_id", "universe_id", "entity_id", "value", "trial_count_m"}
            and f"{{{k}}}" not in template
            and ctx[k] is not None
        ]
        if extra_keys:
            extra_str = "，".join(f"{k}={ctx[k]}" for k in extra_keys)
            rendered = f"{rendered}（附上下文：{extra_str}）"
        return rendered


@lru_cache(maxsize=1)
def get_glossary_registry() -> GlossaryRegistry:
    """Return the cached singleton ``GlossaryRegistry``."""
    return GlossaryRegistry()


def list_terms(
    category: str | None = None,
    domain: str | None = None,
    q: str | None = None,
) -> GlossaryListResponse:
    return get_glossary_registry().list_terms(category=category, domain=domain, q=q)


def get_term(term_id: str) -> GlossaryTerm | None:
    return get_glossary_registry().get_term(term_id)


def render_bot_prompt(term_id: str, context: dict[str, Any] | None = None) -> str:
    return get_glossary_registry().render_bot_prompt(term_id, context=context)


__all__ = [
    "INSTITUTIONAL_GATES",
    "TERMS_PATH",
    "GlossaryRegistry",
    "get_glossary_registry",
    "get_term",
    "list_terms",
    "render_bot_prompt",
]
