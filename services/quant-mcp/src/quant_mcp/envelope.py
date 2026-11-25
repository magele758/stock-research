"""McpEnvelope construction with a hard serialized-size budget (default 8 KiB)."""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from pathlib import Path
from typing import Any

from quant_api import deps
from quant_core.schemas import (
    ArtifactRef,
    McpEnvelope,
    McpErrorCode,
    McpErrorDetail,
    ProvenanceMeta,
)

MAX_BYTES = 8192
# Leave headroom for JSON escaping differences / transport framing.
BUDGET = 7800
PREVIEW_MAX_ROWS = 20
_MAX_STR = 240
_MAX_LIST = 20
_MAX_DEPTH = 6


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:16]}"


def compact(value: Any, depth: int = 0) -> Any:
    """Round floats, clip long strings/lists/dicts so summaries stay small and JSON-safe."""
    if depth > _MAX_DEPTH:
        return "..."
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, 6)
    if isinstance(value, str):
        return value if len(value) <= _MAX_STR else value[: _MAX_STR - 1] + "…"
    if isinstance(value, dict):
        items = list(value.items())
        out = {str(k): compact(v, depth + 1) for k, v in items[:40]}
        if len(items) > 40:
            out["_truncated_keys"] = len(items) - 40
        return out
    if isinstance(value, list | tuple):
        out_list = [compact(v, depth + 1) for v in value[:_MAX_LIST]]
        if len(value) > _MAX_LIST:
            out_list.append({"_truncated_items": len(value) - _MAX_LIST})
        return out_list
    if value is None or isinstance(value, bool | int):
        return value
    return str(value)


def spill_json(run_id: str, name: str, payload: dict[str, Any]) -> ArtifactRef:
    """Persist a large payload via QuantLake (preferred) or a local file; never inline it."""
    try:
        return deps.get_lake().save_artifact(run_id, name, payload)
    except Exception:  # noqa: BLE001 - lake may be absent: fall back to a local artifact dir
        root = deps.state_dir() / "mcp-artifacts"
        root.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in f"{run_id}_{name}")[:100]
        path = Path(root) / f"{safe}.json"
        data = json.dumps(payload, ensure_ascii=False, default=str).encode()
        path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        return ArtifactRef(uri=f"quant-artifact://local_{safe}_{digest[:8]}", format="json", rows=len(payload), sha256=digest)


def _size(env: McpEnvelope) -> int:
    return len(env.model_dump_json(exclude_none=True).encode("utf-8"))


def fit(env: McpEnvelope, full_payload: dict[str, Any] | None = None) -> McpEnvelope:
    """Shrink `env` until its serialized JSON is within BUDGET bytes.

    Order: compact summary -> halve preview -> spill full payload to an artifact and keep headline keys ->
    minimal envelope. The result is always valid JSON <= MAX_BYTES.
    """
    if env.summary is not None:
        env = env.model_copy(update={"summary": compact(env.summary)})
    if env.preview is not None:
        env = env.model_copy(update={"preview": [compact(r) for r in env.preview[:PREVIEW_MAX_ROWS]]})
    if _size(env) <= BUDGET:
        return env
    # 1) halve preview
    while env.preview and len(env.preview) > 1 and _size(env) > BUDGET:
        env = env.model_copy(update={"preview": env.preview[: max(1, len(env.preview) // 2)]})
    if _size(env) <= BUDGET:
        return env
    # 2) drop preview, spill everything, keep headline scalars only
    spilled = full_payload if full_payload is not None else {"summary": env.summary, "preview": env.preview}
    artifact = env.artifact or spill_json(env.request_id, "mcp_full_response", spilled)
    head: dict[str, Any] = {}
    for k, v in (env.summary or {}).items():
        if isinstance(v, str | int | float | bool) or v is None:
            head[k] = v
        if len(json.dumps(head, default=str)) > 2500:
            break
    head["_note"] = "response trimmed to fit 8 KiB; full payload in artifact.uri"
    env = env.model_copy(update={"summary": compact(head), "preview": None, "artifact": artifact})
    if _size(env) <= BUDGET:
        return env
    # 3) minimal
    prov = env.provenance
    if prov is not None:
        env = env.model_copy(update={"provenance": prov.model_copy(update={"warnings": prov.warnings[:2]})})
    env = env.model_copy(update={"summary": {"_note": "trimmed; see artifact.uri"}})
    return env


def to_text(env: McpEnvelope, full_payload: dict[str, Any] | None = None) -> str:
    fitted = fit(env, full_payload)
    text = fitted.model_dump_json(exclude_none=True)
    if len(text.encode("utf-8")) > MAX_BYTES:  # pragma: no cover - defensive final guard
        fitted = McpEnvelope(
            request_id=fitted.request_id,
            status="error",
            error=McpErrorDetail(code=McpErrorCode.INVALID_ARGUMENT, message="response too large"),
        )
        text = fitted.model_dump_json(exclude_none=True)
    return text


def ok_envelope(
    request_id: str,
    *,
    snapshot_id: str | None,
    as_of: str | None,
    summary: dict[str, Any] | None = None,
    preview: list[dict[str, Any]] | None = None,
    artifact: ArtifactRef | None = None,
    warnings: list[str] | None = None,
    status: str = "ok",
    coverage_pct: float = 100.0,
    job_id: str | None = None,
    job_state: str | None = None,
) -> McpEnvelope:
    prov = None
    if snapshot_id:
        prov = ProvenanceMeta(
            snapshot_id=snapshot_id, as_of=as_of or "", coverage_pct=coverage_pct, warnings=warnings or []
        )
    return McpEnvelope(
        request_id=request_id,
        status=status,  # type: ignore[arg-type]
        snapshot_id=snapshot_id,
        as_of=as_of,
        summary=summary,
        preview=preview,
        artifact=artifact,
        provenance=prov,
        job_id=job_id,
        job_state=job_state,  # type: ignore[arg-type]
    )


def error_envelope(request_id: str, code: McpErrorCode, message: str, retryable: bool = False) -> McpEnvelope:
    return McpEnvelope(
        request_id=request_id,
        status="error",
        error=McpErrorDetail(code=code, retryable=retryable, message=message[:600]),
    )
