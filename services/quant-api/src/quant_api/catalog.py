"""Static catalog of the 21 `quant_*` MCP tools: 13 V2.0 + 8 V3.0 (single source for API + MCP)."""

from __future__ import annotations

MCP_TOOLS: list[dict[str, str]] = [
    {"name": "quant_data_health", "domain": "Q1", "mode": "sync", "approval": "never", "rest": "GET /api/v1/data/health"},
    {"name": "quant_query_pit_panel", "domain": "Q1", "mode": "sync", "approval": "never", "rest": "GET /api/v1/data/snapshots"},
    {"name": "quant_universe_constituents", "domain": "Q1", "mode": "sync", "approval": "never", "rest": "GET /api/v1/data/universe/{universe_id}"},
    {"name": "quant_eval_factor", "domain": "Q2", "mode": "sync", "approval": "never", "rest": "POST /api/v1/factors/evaluate"},
    {"name": "quant_factor_correlation", "domain": "Q2", "mode": "sync", "approval": "never", "rest": "POST /api/v1/factors/correlation"},
    {"name": "quant_submit_factor_hypothesis", "domain": "Q2", "mode": "sync|async", "approval": "always", "rest": "POST /api/v1/factors/hypothesis"},
    {"name": "quant_run_cpcv_gate", "domain": "Q3", "mode": "sync|async", "approval": "auto", "rest": "POST /api/v1/validation/cpcv"},
    {"name": "quant_list_experiments", "domain": "Q3", "mode": "sync", "approval": "never", "rest": "GET /api/v1/experiments"},
    {"name": "quant_barra_exposure", "domain": "Q4", "mode": "sync", "approval": "never", "rest": "POST /api/v1/risk/barra-exposure"},
    {"name": "quant_optimize_portfolio", "domain": "Q4", "mode": "sync", "approval": "auto", "rest": "POST /api/v1/portfolio/optimize"},
    {"name": "quant_run_backtest", "domain": "Q5", "mode": "sync|async", "approval": "auto", "rest": "POST /api/v1/backtest/run"},
    {"name": "quant_attribution_tearsheet", "domain": "Q5", "mode": "sync", "approval": "never", "rest": "GET /api/v1/backtest/{run_id}"},
    {"name": "quant_shadow_drift_check", "domain": "Q6", "mode": "sync", "approval": "never", "rest": "GET /api/v1/shadow/drift"},
    # --- V3.0 institutional additions (read-only; no trading surface) ---
    {"name": "quant_glossary_lookup", "domain": "Q8", "mode": "sync", "approval": "never", "rest": "GET /api/v1/glossary/{term_id}"},
    {"name": "quant_formal_verify", "domain": "Q3", "mode": "sync|async", "approval": "auto", "rest": "POST /api/v1/validation/formal-verify"},
    {"name": "quant_regime_analysis", "domain": "Q2", "mode": "sync", "approval": "never", "rest": "POST /api/v1/factors/regime-analysis"},
    {"name": "quant_capacity_analysis", "domain": "Q4", "mode": "sync", "approval": "never", "rest": "POST /api/v1/portfolio/capacity"},
    {"name": "quant_dispatch_research", "domain": "Q7", "mode": "sync|async", "approval": "always", "rest": "POST /api/v1/research/dispatch"},
    {"name": "quant_get_research_study", "domain": "Q7", "mode": "sync", "approval": "never", "rest": "GET /api/v1/research/studies/{study_id}"},
    {"name": "quant_list_research_studies", "domain": "Q7", "mode": "sync", "approval": "never", "rest": "GET /api/v1/research/studies"},
    {"name": "quant_publish_research_finding", "domain": "Q7", "mode": "sync", "approval": "always", "rest": "POST /api/v1/research/studies/{study_id}/publish"},
]

MCP_TOOL_NAMES: list[str] = [t["name"] for t in MCP_TOOLS]
