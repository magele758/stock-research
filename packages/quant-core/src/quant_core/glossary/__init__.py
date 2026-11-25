"""QuantLab Pro V3.0 authoritative institutional glossary SSOT."""

from __future__ import annotations

from quant_core.glossary.registry import (
    INSTITUTIONAL_GATES,
    TERMS_PATH,
    GlossaryRegistry,
    get_glossary_registry,
    get_term,
    list_terms,
    render_bot_prompt,
)

__all__ = [
    "INSTITUTIONAL_GATES",
    "TERMS_PATH",
    "GlossaryRegistry",
    "get_glossary_registry",
    "get_term",
    "list_terms",
    "render_bot_prompt",
]
