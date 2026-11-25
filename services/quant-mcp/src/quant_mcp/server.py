"""Pro-Quant MCP server.

    python -m quant_mcp.server --transport streamable-http --host 127.0.0.1 --port 8101
    python -m quant_mcp.server --transport stdio

Uses MCP Python SDK v2 (`mcp.server.mcpserver.MCPServer`, formerly FastMCP).
Set `QUANT_MCP_BEARER_TOKEN` to require `Authorization: Bearer <token>` on the HTTP transport.
"""

from __future__ import annotations

import argparse
import functools
import hmac
import json
import os
import sys
from collections.abc import Callable
from typing import Any

import anyio
from mcp.server.mcpserver import MCPServer
from quant_api.catalog import MCP_TOOL_NAMES

from . import tools as T

INSTRUCTIONS = (
    "Pro-Quant V2.0 deterministic quant research tools (PIT data lake, factor lab, CPCV/DSR gate, Barra CNE6, "
    "cvxpy optimizer, A-share microstructure backtest, shadow drift). Every response is a McpEnvelope JSON <= 8 KiB; "
    "large tables are returned as quant-artifact:// references. Long jobs: pass async_mode=true and poll by calling "
    "the same tool with job_id. READ-ONLY: no broker connection or order placement exists. Never present outputs as "
    "investment advice."
)

# (tool name, implementation, description)
_REGISTRY: list[tuple[str, Callable[..., str], str]] = [
    ("quant_data_health", T.tool_data_health,
     "PIT data lake health: 7 QA Sentinel checks, per-dataset freshness/coverage, fact_fingerprint. Summary only; full report in artifact."),
    ("quant_query_pit_panel", T.tool_query_pit_panel,
     "Query the point-in-time daily bar panel. Returns column stats (count/null_pct/p05/p50/p95) + preview_limit<=20 rows; full panel is a parquet artifact."),
    ("quant_universe_constituents", T.tool_universe_constituents,
     "Historical universe constituents at as_of_date (count, weight quality, top weights, industry weights, changes vs 30d). include_symbols=true writes the full list to an artifact."),
    ("quant_eval_factor", T.tool_eval_factor,
     "Evaluate a builtin/registered/custom-expression factor: RankIC, ICIR, NW t, deciles, turnover, half-life, library correlation, admission gate. AST-guarded (no shift(-k))."),
    ("quant_factor_correlation", T.tool_factor_correlation,
     "Cross-sectional factor correlation: top high-correlation pairs and max |rho|; full matrix in artifact."),
    ("quant_submit_factor_hypothesis", T.tool_submit_factor_hypothesis,
     "Register a candidate factor hypothesis (AST check, expr_hash dedupe, trial count) and evaluate it. async_mode=true returns status=accepted + job_id in <1s; poll with job_id. Requires approval."),
    ("quant_run_cpcv_gate", T.tool_run_cpcv_gate,
     "CPCV + Deflated Sharpe + PBO overfitting gate for an experiment/hypothesis/factor id. Long-running: async_mode=true returns job_id in <1s; poll with job_id."),
    ("quant_list_experiments", T.tool_list_experiments,
     "Compact experiment registry table (limit<=100): DSR, PBO, OOS/IS, trial count m, verdict."),
    ("quant_barra_exposure", T.tool_barra_exposure,
     "Barra CNE6 style exposures vs limits, top industry deviations, predicted TE and risk shares."),
    ("quant_optimize_portfolio", T.tool_optimize_portfolio,
     "cvxpy portfolio optimization under Barra/industry/turnover constraints (pass `constraints` dict to retune). Returns solve status, expected net excess, TE, turnover, top-15 target weights. Proposal only; no orders."),
    ("quant_run_backtest", T.tool_run_backtest,
     "A-share microstructure backtest (event|screening): T+1, price limits, suspension, PoV impact, fees. async_mode=true returns job_id in <1s; poll with job_id."),
    ("quant_attribution_tearsheet", T.tool_attribution_tearsheet,
     "Brinson-Fachler and Barra attribution plus friction breakdown for a completed backtest (run_id or job_id)."),
    ("quant_shadow_drift_check", T.tool_shadow_drift_check,
     "Shadow portfolio drift: implementation shortfall, fill rate, factor decay/kill-switch, read-only authority manifest."),
]


def _threaded(fn: Callable[..., str]) -> Callable[..., Any]:
    """Run blocking compute in a worker thread so the event loop (and other MCP calls) stay responsive."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> str:
        return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))

    return wrapper


def build_server() -> MCPServer:
    server = MCPServer("pro-quant", instructions=INSTRUCTIONS, version="0.2.0")
    for name, fn, desc in _REGISTRY:
        server.add_tool(_threaded(fn), name=name, description=desc, structured_output=False)
    registered = [n for n, _, _ in _REGISTRY]
    assert registered == MCP_TOOL_NAMES, f"tool registry drift: {registered} != {MCP_TOOL_NAMES}"
    return server


class BearerAuthMiddleware:
    """Minimal ASGI guard: require `Authorization: Bearer <token>` on every HTTP request."""

    def __init__(self, app: Any, token: str):
        self.app = app
        self._token = token.encode()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v for k, v in scope.get("headers", [])}
        supplied = headers.get("authorization", b"").decode()
        ok = supplied.lower().startswith("bearer ") and hmac.compare_digest(supplied[7:].strip().encode(), self._token)
        if ok:
            await self.app(scope, receive, send)
            return
        body = json.dumps({"error": "unauthorized", "message": "missing or invalid bearer token"}).encode()
        await send({"type": "http.response.start", "status": 401, "headers": [
            (b"content-type", b"application/json"), (b"www-authenticate", b'Bearer realm="quant-mcp"'),
            (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def build_http_app(server: MCPServer, token: str | None = None, host: str = "127.0.0.1") -> Any:
    app = server.streamable_http_app(streamable_http_path="/mcp", host=host)
    token = token if token is not None else os.environ.get("QUANT_MCP_BEARER_TOKEN", "").strip()
    return BearerAuthMiddleware(app, token) if token else app


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="quant_mcp.server", description=__doc__)
    p.add_argument("--transport", choices=["streamable-http", "stdio"], default="streamable-http")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8101)
    args = p.parse_args(argv)
    server = build_server()
    if args.transport == "stdio":
        # stdout is the protocol channel: logs must go to stderr only.
        server.run("stdio")
        return
    import uvicorn

    os.environ.setdefault("QUANT_MCP_PORT", str(args.port))
    app = build_http_app(server, host=args.host)
    auth = "bearer auth ON" if os.environ.get("QUANT_MCP_BEARER_TOKEN") else "no auth (loopback only)"
    print(f"quant-mcp listening on http://{args.host}:{args.port}/mcp ({auth})", file=sys.stderr)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
