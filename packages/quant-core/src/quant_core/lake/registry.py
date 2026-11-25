"""SQLite WAL (PostgreSQL 18 schema-compatible) metadata, experiment & V3 research knowledge registry."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import sqlite3
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quant_core.schemas import (
    ArtifactRef,
    AsyncJobRecord,
    CapacityFrontierResult,
    DataHealthReport,
    DatasetSnapshot,
    DiagnosisItem,
    DimensionBaselineRecord,
    ExperimentRecord,
    FalsifiedTombstoneRecord,
    FormalProofResult,
    GateVerdict,
    Market,
    MathDerivationSpec,
    RegimeAnalysisResult,
    ResearchStudyDetail,
    ResearchStudySummary,
    StudyEventRecord,
)

# PostgreSQL 18 & SQLite WAL compatible DDL.
REGISTRY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id TEXT PRIMARY KEY,
    as_of TEXT NOT NULL,
    markets_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    table_counts_json TEXT NOT NULL,
    qa_status TEXT NOT NULL,
    delisted_count INTEGER NOT NULL DEFAULT 0,
    source_provenance_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS experiments (
    experiment_id TEXT PRIMARY KEY,
    family_id TEXT NOT NULL,
    name TEXT NOT NULL,
    model_type TEXT NOT NULL,
    feature_summary TEXT NOT NULL,
    universe_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    trial_count_m INTEGER NOT NULL,
    is_sharpe DOUBLE PRECISION NOT NULL,
    oos_sharpe DOUBLE PRECISION NOT NULL,
    oos_is_ratio DOUBLE PRECISION NOT NULL,
    cpcv_pbo DOUBLE PRECISION NOT NULL,
    dsr DOUBLE PRECISION NOT NULL,
    oos_excess_ann DOUBLE PRECISION NOT NULL,
    verdict TEXT NOT NULL,
    status_badge TEXT NOT NULL,
    created_at TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    progress_pct INTEGER NOT NULL DEFAULT 0,
    summary_json TEXT,
    artifact_uri TEXT,
    error TEXT
);

CREATE TABLE IF NOT EXISTS artifacts (
    uri TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    name TEXT NOT NULL,
    format TEXT NOT NULL,
    rows INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    file_path TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS qa_runs (
    qa_run_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    as_of TEXT NOT NULL,
    overall_status TEXT NOT NULL,
    freshness_sec INTEGER NOT NULL,
    coverage_pct DOUBLE PRECISION NOT NULL,
    fact_fingerprint TEXT NOT NULL,
    report_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingest_batches (
    batch_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    dataset TEXT NOT NULL,
    slice_key TEXT NOT NULL,
    status TEXT NOT NULL,
    row_count INTEGER NOT NULL DEFAULT 0,
    raw_sha256 TEXT,
    received_at TEXT NOT NULL,
    error TEXT
);

-- =========================================================================
-- QuantLab Pro V3.0 Persistent Research Knowledge Tables (SQLite WAL / PG18)
-- =========================================================================

CREATE TABLE IF NOT EXISTS research_studies (
    study_id TEXT PRIMARY KEY,
    parent_study_id TEXT REFERENCES research_studies(study_id),
    family_id TEXT NOT NULL,
    title TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'factor',
    topic_tags TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    verdict TEXT NOT NULL DEFAULT 'PENDING',
    stage TEXT NOT NULL DEFAULT 'HYPOTHESIS',
    dispatch_mode TEXT NOT NULL DEFAULT 'engine_sandbox',
    target_cluster TEXT NOT NULL DEFAULT 'engine_sandbox',
    snapshot_id TEXT NOT NULL,
    universe_id TEXT NOT NULL DEFAULT 'CSI500',
    job_id TEXT,
    idempotency_key TEXT,
    hypothesis_json TEXT NOT NULL DEFAULT '{}',
    math_derivation_json TEXT NOT NULL DEFAULT '{}',
    formal_proof_json TEXT NOT NULL DEFAULT '{}',
    metrics_summary_json TEXT NOT NULL DEFAULT '{}',
    dimension_analysis_json TEXT NOT NULL DEFAULT '{}',
    capacity_frontier_json TEXT NOT NULL DEFAULT '{}',
    diagnosis_json TEXT NOT NULL DEFAULT '[]',
    improvement_plan_json TEXT NOT NULL DEFAULT '[]',
    stages_json TEXT NOT NULL DEFAULT '[]',
    tombstone_warnings_json TEXT NOT NULL DEFAULT '[]',
    fact_fingerprint TEXT NOT NULL DEFAULT '',
    artifact_uri TEXT,
    created_by TEXT NOT NULL DEFAULT 'quant-lead',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS dimension_baselines (
    baseline_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    universe_id TEXT NOT NULL,
    dimension_type TEXT NOT NULL,
    period_granularity TEXT NOT NULL DEFAULT 'daily',
    date_range_start TEXT NOT NULL,
    date_range_end TEXT NOT NULL,
    current_state TEXT NOT NULL DEFAULT '',
    bucket_definitions_json TEXT NOT NULL DEFAULT '{}',
    bucket_stats_json TEXT NOT NULL DEFAULT '[]',
    date_bucket_map_uri TEXT,
    baseline_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(snapshot_id, universe_id, dimension_type)
);

CREATE TABLE IF NOT EXISTS falsified_tombstones (
    tombstone_id TEXT PRIMARY KEY,
    study_id TEXT REFERENCES research_studies(study_id),
    factor_id TEXT NOT NULL,
    expression TEXT NOT NULL,
    expr_hash TEXT NOT NULL,
    ast_fingerprint TEXT NOT NULL,
    universe_id TEXT NOT NULL,
    snapshot_id TEXT NOT NULL,
    failure_code TEXT NOT NULL,
    failure_metrics_json TEXT NOT NULL DEFAULT '{}',
    lesson_summary TEXT NOT NULL,
    keywords_json TEXT NOT NULL DEFAULT '[]',
    memory_line TEXT NOT NULL,
    created_by TEXT NOT NULL DEFAULT 'orchestrator',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS study_events (
    event_id TEXT PRIMARY KEY,
    study_id TEXT NOT NULL REFERENCES research_studies(study_id),
    seq INTEGER NOT NULL,
    stage TEXT NOT NULL,
    event_type TEXT NOT NULL,
    progress_pct INTEGER NOT NULL DEFAULT 0,
    message TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    actor TEXT NOT NULL DEFAULT 'orchestrator',
    created_at TEXT NOT NULL,
    UNIQUE(study_id, seq)
);

CREATE TABLE IF NOT EXISTS research_notes (
    note_id TEXT PRIMARY KEY,
    study_id TEXT NOT NULL REFERENCES research_studies(study_id),
    author TEXT NOT NULL,
    note_type TEXT NOT NULL DEFAULT 'review',
    content_md TEXT NOT NULL,
    linked_terms_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_created ON snapshots(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_experiments_created ON experiments(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_experiments_family ON experiments(family_id);
CREATE INDEX IF NOT EXISTS idx_jobs_updated ON jobs(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_qa_runs_snapshot ON qa_runs(snapshot_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ingest_batches_provider ON ingest_batches(provider, received_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS ux_research_studies_idem
    ON research_studies(idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_research_studies_parent
    ON research_studies(parent_study_id);
CREATE INDEX IF NOT EXISTS idx_research_studies_family
    ON research_studies(family_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_studies_snapshot_status
    ON research_studies(snapshot_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_research_studies_verdict
    ON research_studies(verdict, created_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS ux_dimension_baselines_key
    ON dimension_baselines(snapshot_id, universe_id, dimension_type);
CREATE INDEX IF NOT EXISTS idx_dimension_baselines_lookup
    ON dimension_baselines(snapshot_id, universe_id, dimension_type);

CREATE INDEX IF NOT EXISTS idx_tombstones_expr_hash
    ON falsified_tombstones(expr_hash);
CREATE INDEX IF NOT EXISTS idx_tombstones_ast_fp
    ON falsified_tombstones(ast_fingerprint);
CREATE INDEX IF NOT EXISTS idx_tombstones_study
    ON falsified_tombstones(study_id);
CREATE INDEX IF NOT EXISTS idx_tombstones_created
    ON falsified_tombstones(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_study_events_stream
    ON study_events(study_id, seq ASC);
CREATE INDEX IF NOT EXISTS idx_research_notes_study
    ON research_notes(study_id, created_at DESC);
"""

_AST_FUNC_ALIASES = {
    "ts_mean": "rolling_mean",
    "ts_std": "rolling_std",
    "ts_sum": "rolling_sum",
    "ts_min": "rolling_min",
    "ts_max": "rolling_max",
    "ts_corr": "rolling_corr",
    "lag": "shift",
    "cs_rank": "rank",
    "cs_mean": "mean",
    "cs_zscore": "zscore",
}
_AST_KWARG_ALIASES = {
    "window": "window_size",
    "min_periods": "min_samples",
}
_AST_FIELD_ALIASES = {
    "volume": "volume_shares",
    "amount": "amount_ccy",
}
_STOPWORDS = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "into",
        "over",
        "using",
        "factor",
        "study",
        "alpha",
        "close",
        "open",
        "high",
        "low",
        "vwap",
        "volume",
        "shares",
        "amount",
        "ccy",
        "adj",
        "pl",
        "polars",
        "rolling",
        "corr",
        "mean",
        "std",
        "sum",
        "min",
        "max",
        "median",
        "pct",
        "change",
        "diff",
        "shift",
        "log",
        "log1p",
        "exp",
        "sqrt",
        "abs",
        "clip",
        "rank",
        "zscore",
        "window",
        "size",
        "samples",
        "periods",
        "ts",
        "cs",
    }
)


def _dt_to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def _iso_to_dt(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _now_iso() -> str:
    return _dt_to_iso(datetime.now(UTC))


def _dump_json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        return json.dumps(value.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    if isinstance(value, list):
        items = [v.model_dump(mode="json") if hasattr(v, "model_dump") else v for v in value]
        return json.dumps(items, ensure_ascii=False, default=str)
    return json.dumps(value, ensure_ascii=False, default=str)


def compute_expr_hash(expression: str) -> str:
    """Whitespace-stripped SHA-256 prefix (16 hex chars), 100% compatible with V2 expr_hash."""
    norm = re.sub(r"\s+", "", expression or "")
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


class _AstNormalizer(ast.NodeTransformer):
    """Normalize numeric window/weight literals & operator aliases for structural AST fingerprinting."""

    def visit_Constant(self, node: ast.Constant) -> ast.AST:
        if type(node.value) in (int, float):
            return ast.copy_location(ast.Constant(value="__NUM__"), node)
        return node

    def visit_Name(self, node: ast.Name) -> ast.AST:
        mapped = _AST_FIELD_ALIASES.get(node.id, _AST_FUNC_ALIASES.get(node.id, node.id))
        if mapped != node.id:
            return ast.copy_location(ast.Name(id=mapped, ctx=node.ctx), node)
        return node

    def visit_Attribute(self, node: ast.Attribute) -> ast.AST:
        self.generic_visit(node)
        mapped = _AST_FUNC_ALIASES.get(node.attr, node.attr)
        if mapped != node.attr:
            node.attr = mapped
        return node

    def visit_Call(self, node: ast.Call) -> ast.AST:
        self.generic_visit(node)
        if node.keywords:
            for kw in node.keywords:
                if kw.arg in _AST_KWARG_ALIASES:
                    kw.arg = _AST_KWARG_ALIASES[kw.arg]
            node.keywords.sort(key=lambda kw: kw.arg or "")
        return node


def compute_ast_fingerprint(expression: str) -> str:
    """Compute a numeric-parameter-invariant structural AST fingerprint (24 hex chars).

    Catches parameter-tweaking duplicates such as changing ``window_size=20`` to ``window_size=21``.
    """
    text = (expression or "").strip()
    if not text:
        return hashlib.sha256(b"").hexdigest()[:24]
    try:
        tree = ast.parse(text, mode="eval")
        normalized = _AstNormalizer().visit(tree)
        canonical = ast.dump(normalized, include_attributes=False)
    except SyntaxError:
        canonical = re.sub(r"\b\d+(?:\.\d+)?(?:e[+-]?\d+)?\b", "__NUM__", re.sub(r"\s+", "", text.lower()))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def extract_keywords(*texts: str | None) -> list[str]:
    """Extract normalized English & Chinese keyword tokens for Jaccard similarity scanning."""
    combined = " ".join(t for t in texts if t)
    if not combined.strip():
        return []
    tokens: set[str] = set()
    # English / snake_case identifiers
    for tok in re.findall(r"[A-Za-z][A-Za-z0-9_]{1,}", combined):
        low = tok.lower()
        if low not in _STOPWORDS and len(low) >= 2:
            tokens.add(low)
        for part in low.split("_"):
            if part and len(part) >= 2 and part not in _STOPWORDS:
                tokens.add(part)
    # Chinese character segments -> overlapping bigrams + full segment (<= 6 chars)
    for seg in re.findall(r"[\u4e00-\u9fff]{2,}", combined):
        if len(seg) <= 6:
            tokens.add(seg)
        for idx in range(len(seg) - 1):
            tokens.add(seg[idx : idx + 2])
    return sorted(tokens)


def jaccard_similarity(left: Iterable[str], right: Iterable[str]) -> float:
    sa = {s.lower() for s in left if s}
    sb = {s.lower() for s in right if s}
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def format_tombstone_memory_line(
    *,
    factor_id: str,
    expr_hash: str,
    failure_code: str,
    failure_metrics: dict[str, Any] | None,
    snapshot_id: str,
    study_id: str | None,
    created_date: str | None = None,
) -> str:
    metrics = failure_metrics or {}

    def _fmt(key: str) -> str:
        val = metrics.get(key)
        if isinstance(val, (int, float)):
            return f"{val:.3f}"
        return "NA"

    date_str = created_date or datetime.now(UTC).date().isoformat()
    return (
        f"[FALSIFIED] {factor_id} | {expr_hash} | {failure_code} | "
        f"DSR={_fmt('dsr')} PBO={_fmt('pbo')} OOS/IS={_fmt('oos_is_ratio')} | "
        f"snap={snapshot_id} | {date_str} | study={study_id or 'MANUAL'}"
    )


def _extract_proof_flag(formal_proof_raw: Any) -> bool:
    if isinstance(formal_proof_raw, FormalProofResult):
        return bool(formal_proof_raw.passed)
    if isinstance(formal_proof_raw, dict):
        return bool(formal_proof_raw.get("passed") is True)
    if isinstance(formal_proof_raw, str) and formal_proof_raw.strip():
        try:
            parsed = json.loads(formal_proof_raw)
            return bool(isinstance(parsed, dict) and parsed.get("passed") is True)
        except json.JSONDecodeError:
            return False
    return False


class QuantRegistry:
    """Durable metadata, experiment & V3 research ledger backed by SQLite WAL (PG 18 compatible DDL).

    If ``QUANT_DATABASE_URL`` is set to a ``postgresql://`` DSN and ``psycopg`` is
    installed, callers can inspect ``self.backend``; local development defaults to
    SQLite WAL at ``data/state/quant_registry.db``.
    """

    def __init__(
        self,
        db_path: Path | str = "data/state/quant_registry.db",
        database_url: str | None = None,
    ) -> None:
        self.database_url = database_url or os.getenv("QUANT_DATABASE_URL")
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.backend = "sqlite-wal"
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=15.0,
            isolation_level=None,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute("PRAGMA busy_timeout=15000;")
            yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(REGISTRY_SCHEMA_SQL)

    # ------------------------------------------------------------------
    # Snapshots (V2 preserved 100%)
    # ------------------------------------------------------------------

    def save_snapshot(self, snapshot: DatasetSnapshot) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO snapshots (
                    snapshot_id, as_of, markets_json, created_at,
                    manifest_sha256, table_counts_json, qa_status,
                    delisted_count, source_provenance_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_id) DO UPDATE SET
                    as_of = excluded.as_of,
                    markets_json = excluded.markets_json,
                    created_at = excluded.created_at,
                    manifest_sha256 = excluded.manifest_sha256,
                    table_counts_json = excluded.table_counts_json,
                    qa_status = excluded.qa_status,
                    delisted_count = excluded.delisted_count,
                    source_provenance_json = excluded.source_provenance_json
                """,
                (
                    snapshot.snapshot_id,
                    _dt_to_iso(snapshot.as_of),
                    json.dumps([m.value for m in snapshot.markets]),
                    _dt_to_iso(snapshot.created_at),
                    snapshot.manifest_sha256,
                    json.dumps(snapshot.table_counts, sort_keys=True),
                    snapshot.qa_status,
                    snapshot.delisted_count,
                    json.dumps(snapshot.source_provenance, ensure_ascii=False),
                ),
            )

    @staticmethod
    def _row_to_snapshot(row: sqlite3.Row) -> DatasetSnapshot:
        return DatasetSnapshot(
            snapshot_id=row["snapshot_id"],
            as_of=_iso_to_dt(row["as_of"]),
            markets=[Market(m) for m in json.loads(row["markets_json"])],
            created_at=_iso_to_dt(row["created_at"]),
            manifest_sha256=row["manifest_sha256"],
            table_counts=json.loads(row["table_counts_json"]),
            qa_status=row["qa_status"],
            delisted_count=int(row["delisted_count"]),
            source_provenance=json.loads(row["source_provenance_json"]),
        )

    def get_snapshot(self, snapshot_id: str) -> DatasetSnapshot | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM snapshots WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
            return self._row_to_snapshot(row) if row is not None else None

    def get_latest_snapshot(self) -> DatasetSnapshot | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM snapshots ORDER BY created_at DESC, as_of DESC, snapshot_id DESC LIMIT 1"
            ).fetchone()
            return self._row_to_snapshot(row) if row is not None else None

    def list_snapshots(self) -> list[DatasetSnapshot]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM snapshots ORDER BY created_at DESC, as_of DESC, snapshot_id DESC"
            ).fetchall()
            return [self._row_to_snapshot(r) for r in rows]

    # ------------------------------------------------------------------
    # Experiments (V2 preserved 100%)
    # ------------------------------------------------------------------

    def save_experiment(self, rec: ExperimentRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO experiments (
                    experiment_id, family_id, name, model_type, feature_summary,
                    universe_id, snapshot_id, trial_count_m, is_sharpe, oos_sharpe,
                    oos_is_ratio, cpcv_pbo, dsr, oos_excess_ann, verdict,
                    status_badge, created_at, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(experiment_id) DO UPDATE SET
                    family_id = excluded.family_id,
                    name = excluded.name,
                    model_type = excluded.model_type,
                    feature_summary = excluded.feature_summary,
                    universe_id = excluded.universe_id,
                    snapshot_id = excluded.snapshot_id,
                    trial_count_m = excluded.trial_count_m,
                    is_sharpe = excluded.is_sharpe,
                    oos_sharpe = excluded.oos_sharpe,
                    oos_is_ratio = excluded.oos_is_ratio,
                    cpcv_pbo = excluded.cpcv_pbo,
                    dsr = excluded.dsr,
                    oos_excess_ann = excluded.oos_excess_ann,
                    verdict = excluded.verdict,
                    status_badge = excluded.status_badge,
                    created_at = excluded.created_at,
                    notes = excluded.notes
                """,
                (
                    rec.experiment_id,
                    rec.family_id,
                    rec.name,
                    rec.model_type,
                    rec.feature_summary,
                    rec.universe_id,
                    rec.snapshot_id,
                    rec.trial_count_m,
                    rec.is_sharpe,
                    rec.oos_sharpe,
                    rec.oos_is_ratio,
                    rec.cpcv_pbo,
                    rec.dsr,
                    rec.oos_excess_ann,
                    rec.verdict.value,
                    rec.status_badge,
                    _dt_to_iso(rec.created_at),
                    rec.notes,
                ),
            )

    @staticmethod
    def _row_to_experiment(row: sqlite3.Row) -> ExperimentRecord:
        return ExperimentRecord(
            experiment_id=row["experiment_id"],
            family_id=row["family_id"],
            name=row["name"],
            model_type=row["model_type"],
            feature_summary=row["feature_summary"],
            universe_id=row["universe_id"],
            snapshot_id=row["snapshot_id"],
            trial_count_m=int(row["trial_count_m"]),
            is_sharpe=float(row["is_sharpe"]),
            oos_sharpe=float(row["oos_sharpe"]),
            oos_is_ratio=float(row["oos_is_ratio"]),
            cpcv_pbo=float(row["cpcv_pbo"]),
            dsr=float(row["dsr"]),
            oos_excess_ann=float(row["oos_excess_ann"]),
            verdict=GateVerdict(row["verdict"]),
            status_badge=row["status_badge"],
            created_at=_iso_to_dt(row["created_at"]),
            notes=row["notes"] or "",
        )

    def get_experiment(self, experiment_id: str) -> ExperimentRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM experiments WHERE experiment_id = ?",
                (experiment_id,),
            ).fetchone()
            return self._row_to_experiment(row) if row is not None else None

    def list_experiments(
        self,
        limit: int = 20,
        family_id: str | None = None,
        snapshot_id: str | None = None,
    ) -> list[ExperimentRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if family_id:
            clauses.append("family_id = ?")
            params.append(family_id)
        if snapshot_id:
            clauses.append("snapshot_id = ?")
            params.append(snapshot_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM experiments {where} ORDER BY created_at DESC, experiment_id ASC LIMIT ?",
                params,
            ).fetchall()
            return [self._row_to_experiment(r) for r in rows]

    # ------------------------------------------------------------------
    # Async Jobs (V2 preserved 100%)
    # ------------------------------------------------------------------

    def save_job(self, job: AsyncJobRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    job_id, job_type, status, created_at, updated_at,
                    snapshot_id, progress_pct, summary_json, artifact_uri, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    job_type = excluded.job_type,
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    snapshot_id = excluded.snapshot_id,
                    progress_pct = excluded.progress_pct,
                    summary_json = excluded.summary_json,
                    artifact_uri = excluded.artifact_uri,
                    error = excluded.error
                """,
                (
                    job.job_id,
                    job.job_type,
                    job.status,
                    _dt_to_iso(job.created_at),
                    _dt_to_iso(job.updated_at),
                    job.snapshot_id,
                    job.progress_pct,
                    json.dumps(job.summary, ensure_ascii=False) if job.summary is not None else None,
                    job.artifact_uri,
                    job.error,
                ),
            )

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> AsyncJobRecord:
        return AsyncJobRecord(
            job_id=row["job_id"],
            job_type=row["job_type"],
            status=row["status"],
            created_at=_iso_to_dt(row["created_at"]),
            updated_at=_iso_to_dt(row["updated_at"]),
            snapshot_id=row["snapshot_id"],
            progress_pct=int(row["progress_pct"]),
            summary=json.loads(row["summary_json"]) if row["summary_json"] else None,
            artifact_uri=row["artifact_uri"],
            error=row["error"],
        )

    def get_job(self, job_id: str) -> AsyncJobRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE job_id = ?",
                (job_id,),
            ).fetchone()
            return self._row_to_job(row) if row is not None else None

    def list_jobs(self, limit: int = 50) -> list[AsyncJobRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY updated_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
            return [self._row_to_job(r) for r in rows]

    # ------------------------------------------------------------------
    # Artifacts (V2 preserved 100%)
    # ------------------------------------------------------------------

    def save_artifact_meta(
        self,
        ref: ArtifactRef,
        run_id: str,
        name: str,
        file_path: str,
    ) -> None:
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO artifacts (
                    uri, run_id, name, format, rows, sha256, file_path, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(uri) DO UPDATE SET
                    run_id = excluded.run_id,
                    name = excluded.name,
                    format = excluded.format,
                    rows = excluded.rows,
                    sha256 = excluded.sha256,
                    file_path = excluded.file_path,
                    created_at = excluded.created_at
                """,
                (
                    ref.uri,
                    run_id,
                    name,
                    ref.format,
                    ref.rows,
                    ref.sha256,
                    file_path,
                    now,
                ),
            )

    def get_artifact_meta(self, uri: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM artifacts WHERE uri = ?",
                (uri,),
            ).fetchone()
            return dict(row) if row is not None else None

    # ------------------------------------------------------------------
    # QA Runs (V2 preserved 100%)
    # ------------------------------------------------------------------

    def save_qa_run(self, report: DataHealthReport) -> str:
        qa_run_id = f"qa-{uuid.uuid4().hex[:12]}"
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO qa_runs (
                    qa_run_id, snapshot_id, as_of, overall_status,
                    freshness_sec, coverage_pct, fact_fingerprint,
                    report_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    qa_run_id,
                    report.snapshot_id,
                    _dt_to_iso(report.as_of),
                    report.overall_status,
                    report.freshness_sec,
                    report.coverage_pct,
                    report.fact_fingerprint,
                    report.model_dump_json(),
                    now,
                ),
            )
        return qa_run_id

    def get_latest_qa_run(self, snapshot_id: str) -> DataHealthReport | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT report_json FROM qa_runs
                WHERE snapshot_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (snapshot_id,),
            ).fetchone()
            if row is None:
                return None
            return DataHealthReport.model_validate_json(row["report_json"])

    # ------------------------------------------------------------------
    # Ingest Batches (V2 preserved 100%)
    # ------------------------------------------------------------------

    def record_ingest_batch(
        self,
        *,
        provider: str,
        dataset: str,
        slice_key: str,
        status: str,
        row_count: int = 0,
        raw_sha256: str | None = None,
        error: str | None = None,
        batch_id: str | None = None,
    ) -> str:
        bid = batch_id or f"batch-{uuid.uuid4().hex[:12]}"
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO ingest_batches (
                    batch_id, provider, dataset, slice_key, status,
                    row_count, raw_sha256, received_at, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(batch_id) DO UPDATE SET
                    status = excluded.status,
                    row_count = excluded.row_count,
                    raw_sha256 = excluded.raw_sha256,
                    received_at = excluded.received_at,
                    error = excluded.error
                """,
                (
                    bid,
                    provider,
                    dataset,
                    slice_key,
                    status,
                    int(row_count),
                    raw_sha256,
                    now,
                    error,
                ),
            )
        return bid

    def list_ingest_batches(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ingest_batches ORDER BY received_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
            return [dict(r) for r in rows]

    # ==================================================================
    # V3.0 Persistent Research Knowledge Base
    # ==================================================================

    @staticmethod
    def _parse_json_field(raw: str | None, default: Any) -> Any:
        if not raw:
            return default
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return default

    @classmethod
    def _row_to_study_summary(cls, row: sqlite3.Row) -> ResearchStudySummary:
        metrics = cls._parse_json_field(row["metrics_summary_json"], {})
        proof_raw = cls._parse_json_field(row["formal_proof_json"], {})
        tags = cls._parse_json_field(row["topic_tags"], [])

        def _opt_float(key: str) -> float | None:
            v = metrics.get(key)
            return float(v) if isinstance(v, (int, float)) else None

        proofs_passed = int(proof_raw.get("proofs_passed", 0)) if isinstance(proof_raw, dict) else 0
        proofs_total = int(proof_raw.get("proofs_total", 0)) if isinstance(proof_raw, dict) else 0
        return ResearchStudySummary(
            study_id=row["study_id"],
            parent_study_id=row["parent_study_id"],
            family_id=row["family_id"],
            title=row["title"],
            kind=row["kind"],
            topic_tags=tags if isinstance(tags, list) else [],
            status=row["status"],
            verdict=row["verdict"],
            stage=row["stage"],
            dispatch_mode=row["dispatch_mode"],
            target_cluster=row["target_cluster"],
            universe_id=row["universe_id"],
            snapshot_id=row["snapshot_id"],
            job_id=row["job_id"],
            oos_sharpe=_opt_float("oos_sharpe"),
            rank_ic_mean=_opt_float("rank_ic_mean"),
            dsr=_opt_float("dsr"),
            pbo=_opt_float("pbo"),
            net_excess_ann_pct=_opt_float("net_excess_ann_pct"),
            proofs_passed=proofs_passed,
            proofs_total=proofs_total,
            created_by=row["created_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
        )

    def _row_to_study_detail(
        self,
        row: sqlite3.Row,
        *,
        include_lineage: bool = True,
        include_events: bool = True,
        include_notes: bool = True,
    ) -> ResearchStudyDetail:
        summary = self._row_to_study_summary(row)
        hyp_obj = self._parse_json_field(row["hypothesis_json"], {})
        if not isinstance(hyp_obj, dict):
            hyp_obj = {"hypothesis": str(hyp_obj)}
        math_raw = self._parse_json_field(row["math_derivation_json"], {})
        proof_raw = self._parse_json_field(row["formal_proof_json"], {})
        metrics_raw = self._parse_json_field(row["metrics_summary_json"], {})
        dim_raw = self._parse_json_field(row["dimension_analysis_json"], {})
        cap_raw = self._parse_json_field(row["capacity_frontier_json"], {})
        diag_raw = self._parse_json_field(row["diagnosis_json"], [])
        imp_raw = self._parse_json_field(row["improvement_plan_json"], [])
        stages_raw = self._parse_json_field(row["stages_json"], [])
        tomb_raw = self._parse_json_field(row["tombstone_warnings_json"], [])

        math_spec = (
            MathDerivationSpec.model_validate(math_raw)
            if isinstance(math_raw, dict) and math_raw.get("economic_hypothesis")
            else None
        )
        formal_proof = (
            FormalProofResult.model_validate(proof_raw)
            if isinstance(proof_raw, dict) and proof_raw.get("verification_id")
            else None
        )
        dim_analysis = (
            RegimeAnalysisResult.model_validate(dim_raw)
            if isinstance(dim_raw, dict) and dim_raw.get("factor_id")
            else None
        )
        cap_frontier = (
            CapacityFrontierResult.model_validate(cap_raw)
            if isinstance(cap_raw, dict) and cap_raw.get("experiment_id")
            else None
        )
        diagnosis = [
            DiagnosisItem.model_validate(item) for item in diag_raw if isinstance(item, dict) and item.get("id")
        ]

        sid = row["study_id"]
        events = self.list_study_events(sid) if include_events else []
        lineage = self.get_study_lineage(sid) if include_lineage else []
        notes = self.list_research_notes(sid) if include_notes else []

        return ResearchStudyDetail(
            **summary.model_dump(),
            hypothesis=str(hyp_obj.get("hypothesis", "")),
            factor_id=hyp_obj.get("factor_id"),
            expression=hyp_obj.get("expression"),
            math_derivation=math_spec,
            formal_proof=formal_proof,
            metrics_summary=metrics_raw if isinstance(metrics_raw, dict) else {},
            dimension_analysis=dim_analysis,
            capacity_frontier=cap_frontier,
            diagnosis=diagnosis,
            improvement_plan=imp_raw if isinstance(imp_raw, list) else [],
            stages=stages_raw if isinstance(stages_raw, list) else [],
            events=events,
            lineage=lineage,
            notes=notes,
            tombstone_warnings=tomb_raw if isinstance(tomb_raw, list) else [],
            fact_fingerprint=row["fact_fingerprint"] or "",
            artifact_uri=row["artifact_uri"],
        )

    def _allocate_study_id(self, conn: sqlite3.Connection) -> str:
        rows = conn.execute("SELECT study_id FROM research_studies WHERE study_id LIKE 'STD-%'").fetchall()
        max_num = 0
        for r in rows:
            m = re.match(r"^STD-(\d+)$", r["study_id"])
            if m:
                max_num = max(max_num, int(m.group(1)))
        return f"STD-{max_num + 1:04d}"

    def create_study(
        self,
        *,
        title: str,
        snapshot_id: str,
        hypothesis: str = "",
        study_id: str | None = None,
        parent_study_id: str | None = None,
        family_id: str | None = None,
        kind: str = "factor",
        topic_tags: list[str] | None = None,
        status: str = "DRAFT",
        verdict: str = "PENDING",
        stage: str = "HYPOTHESIS",
        dispatch_mode: str = "engine_sandbox",
        target_cluster: str = "engine_sandbox",
        universe_id: str = "CSI500",
        job_id: str | None = None,
        idempotency_key: str | None = None,
        factor_id: str | None = None,
        expression: str | None = None,
        hypothesis_extra: dict[str, Any] | None = None,
        math_derivation: MathDerivationSpec | dict[str, Any] | None = None,
        stages: list[dict[str, Any]] | None = None,
        tombstone_warnings: list[dict[str, Any]] | None = None,
        created_by: str = "quant-lead",
    ) -> ResearchStudyDetail:
        if verdict == "VERIFIED":
            raise ValueError("ProofGateViolation: cannot mark VERIFIED when formal proof failed")

        now = _now_iso()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                if idempotency_key:
                    existing = conn.execute(
                        "SELECT study_id FROM research_studies WHERE idempotency_key = ?",
                        (idempotency_key,),
                    ).fetchone()
                    if existing is not None:
                        conn.execute("COMMIT;")
                        found = self.get_study(existing["study_id"])
                        assert found is not None
                        return found

                resolved_family = family_id
                if parent_study_id and not resolved_family:
                    parent = conn.execute(
                        "SELECT family_id FROM research_studies WHERE study_id = ?",
                        (parent_study_id,),
                    ).fetchone()
                    if parent is not None:
                        resolved_family = parent["family_id"]
                sid = study_id or self._allocate_study_id(conn)
                if not resolved_family:
                    resolved_family = f"FAM-{(factor_id or sid).upper()}"

                hyp_payload: dict[str, Any] = {
                    "hypothesis": hypothesis,
                    "factor_id": factor_id,
                    "expression": expression,
                    "expr_hash": compute_expr_hash(expression) if expression else "",
                    "ast_fingerprint": compute_ast_fingerprint(expression) if expression else "",
                }
                if hypothesis_extra:
                    hyp_payload.update(hypothesis_extra)

                conn.execute(
                    """
                    INSERT INTO research_studies (
                        study_id, parent_study_id, family_id, title, kind, topic_tags,
                        status, verdict, stage, dispatch_mode, target_cluster,
                        snapshot_id, universe_id, job_id, idempotency_key,
                        hypothesis_json, math_derivation_json, formal_proof_json,
                        metrics_summary_json, dimension_analysis_json, capacity_frontier_json,
                        diagnosis_json, improvement_plan_json, stages_json,
                        tombstone_warnings_json, fact_fingerprint, artifact_uri,
                        created_by, created_at, updated_at, completed_at
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}',
                        '{}', '{}', '{}', '[]', '[]', ?, ?, '', NULL, ?, ?, ?, NULL
                    )
                    """,
                    (
                        sid,
                        parent_study_id,
                        resolved_family,
                        title,
                        kind,
                        _dump_json(topic_tags or []),
                        status,
                        verdict,
                        stage,
                        dispatch_mode,
                        target_cluster,
                        snapshot_id,
                        universe_id,
                        job_id,
                        idempotency_key,
                        _dump_json(hyp_payload),
                        _dump_json(math_derivation) if math_derivation is not None else "{}",
                        _dump_json(stages or []),
                        _dump_json(tombstone_warnings or []),
                        created_by,
                        now,
                        now,
                    ),
                )
                conn.execute("COMMIT;")
            except Exception:
                conn.execute("ROLLBACK;")
                raise

        detail = self.get_study(sid)
        assert detail is not None
        return detail

    def get_study(
        self,
        study_id: str,
        *,
        include_lineage: bool = True,
        include_events: bool = True,
        include_notes: bool = True,
    ) -> ResearchStudyDetail | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM research_studies WHERE study_id = ?",
                (study_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_study_detail(
            row,
            include_lineage=include_lineage,
            include_events=include_events,
            include_notes=include_notes,
        )

    def get_study_by_idempotency_key(self, idempotency_key: str) -> ResearchStudyDetail | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT study_id FROM research_studies WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        if row is None:
            return None
        return self.get_study(row["study_id"])

    def list_studies(
        self,
        *,
        status: str | None = None,
        verdict: str | None = None,
        snapshot_id: str | None = None,
        family_id: str | None = None,
        parent_study_id: str | None = None,
        tag: str | None = None,
        q: str | None = None,
        limit: int = 50,
    ) -> list[ResearchStudySummary]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if verdict:
            clauses.append("verdict = ?")
            params.append(verdict)
        if snapshot_id:
            clauses.append("snapshot_id = ?")
            params.append(snapshot_id)
        if family_id:
            clauses.append("family_id = ?")
            params.append(family_id)
        if parent_study_id is not None:
            clauses.append("parent_study_id = ?")
            params.append(parent_study_id)
        if tag:
            clauses.append("topic_tags LIKE ?")
            params.append(f'%"{tag}"%')
        if q:
            like = f"%{q.strip()}%"
            clauses.append("(title LIKE ? OR study_id LIKE ? OR family_id LIKE ? OR hypothesis_json LIKE ?)")
            params.extend([like, like, like, like])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM research_studies {where} ORDER BY created_at DESC, study_id DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._row_to_study_summary(r) for r in rows]

    def update_study(self, study_id: str, **fields: Any) -> ResearchStudyDetail:
        """Update selected study fields while enforcing the ProofGate invariant."""
        column_map = {
            "status": "status",
            "verdict": "verdict",
            "stage": "stage",
            "target_cluster": "target_cluster",
            "job_id": "job_id",
            "fact_fingerprint": "fact_fingerprint",
            "artifact_uri": "artifact_uri",
            "completed_at": "completed_at",
            "topic_tags": "topic_tags",
            "hypothesis_json": "hypothesis_json",
            "math_derivation": "math_derivation_json",
            "math_derivation_json": "math_derivation_json",
            "formal_proof": "formal_proof_json",
            "formal_proof_json": "formal_proof_json",
            "metrics_summary": "metrics_summary_json",
            "metrics_summary_json": "metrics_summary_json",
            "dimension_analysis": "dimension_analysis_json",
            "dimension_analysis_json": "dimension_analysis_json",
            "capacity_frontier": "capacity_frontier_json",
            "capacity_frontier_json": "capacity_frontier_json",
            "diagnosis": "diagnosis_json",
            "diagnosis_json": "diagnosis_json",
            "improvement_plan": "improvement_plan_json",
            "improvement_plan_json": "improvement_plan_json",
            "stages": "stages_json",
            "stages_json": "stages_json",
            "tombstone_warnings": "tombstone_warnings_json",
            "tombstone_warnings_json": "tombstone_warnings_json",
        }
        json_cols = {
            "topic_tags",
            "hypothesis_json",
            "math_derivation_json",
            "formal_proof_json",
            "metrics_summary_json",
            "dimension_analysis_json",
            "capacity_frontier_json",
            "diagnosis_json",
            "improvement_plan_json",
            "stages_json",
            "tombstone_warnings_json",
        }

        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM research_studies WHERE study_id = ?",
                (study_id,),
            ).fetchone()
            if existing is None:
                raise KeyError(f"Research study not found: {study_id!r}")

            effective_verdict = fields.get("verdict", existing["verdict"])
            effective_proof = fields.get(
                "formal_proof",
                fields.get("formal_proof_json", existing["formal_proof_json"]),
            )
            if effective_verdict == "VERIFIED" and not _extract_proof_flag(effective_proof):
                raise ValueError("ProofGateViolation: cannot mark VERIFIED when formal proof failed")

            assignments: list[str] = []
            params: list[Any] = []
            for key, value in fields.items():
                if key not in column_map:
                    raise ValueError(f"Unsupported study update field: {key!r}")
                col = column_map[key]
                if col in json_cols:
                    serialized = value if isinstance(value, str) else _dump_json(value)
                    assignments.append(f"{col} = ?")
                    params.append(serialized)
                else:
                    assignments.append(f"{col} = ?")
                    params.append(value)

            assignments.append("updated_at = ?")
            params.append(_now_iso())
            params.append(study_id)
            conn.execute(
                f"UPDATE research_studies SET {', '.join(assignments)} WHERE study_id = ?",
                params,
            )

        updated = self.get_study(study_id)
        assert updated is not None
        return updated

    def finalize_study(
        self,
        study_id: str,
        *,
        status: str,
        verdict: str,
        formal_proof: FormalProofResult | dict[str, Any] | None = None,
        metrics_summary: dict[str, Any] | None = None,
        dimension_analysis: RegimeAnalysisResult | dict[str, Any] | None = None,
        capacity_frontier: CapacityFrontierResult | dict[str, Any] | None = None,
        diagnosis: list[DiagnosisItem | dict[str, Any]] | None = None,
        improvement_plan: list[dict[str, Any]] | None = None,
        stages: list[dict[str, Any]] | None = None,
        artifact_uri: str | None = None,
        fact_fingerprint: str | None = None,
        auto_tombstone: bool = True,
        failure_code: str | None = None,
        lesson_summary: str | None = None,
        created_by: str = "orchestrator",
    ) -> ResearchStudyDetail:
        """Finalize a study with an unbypassable ProofGate assertion and automatic tombstone archival."""
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM research_studies WHERE study_id = ?",
                (study_id,),
            ).fetchone()
            if existing is None:
                raise KeyError(f"Research study not found: {study_id!r}")

        effective_proof = formal_proof if formal_proof is not None else existing["formal_proof_json"]
        if verdict == "VERIFIED" and not _extract_proof_flag(effective_proof):
            raise ValueError("ProofGateViolation: cannot mark VERIFIED when formal proof failed")

        now = _now_iso()
        updates: dict[str, Any] = {
            "status": status,
            "verdict": verdict,
            "stage": "PUBLISHED",
            "completed_at": now,
        }
        if formal_proof is not None:
            updates["formal_proof"] = formal_proof
        if metrics_summary is not None:
            updates["metrics_summary"] = metrics_summary
        if dimension_analysis is not None:
            updates["dimension_analysis"] = dimension_analysis
        if capacity_frontier is not None:
            updates["capacity_frontier"] = capacity_frontier
        if diagnosis is not None:
            updates["diagnosis"] = diagnosis
        if improvement_plan is not None:
            updates["improvement_plan"] = improvement_plan
        if stages is not None:
            updates["stages"] = stages
        if artifact_uri is not None:
            updates["artifact_uri"] = artifact_uri
        if fact_fingerprint is not None:
            updates["fact_fingerprint"] = fact_fingerprint

        detail = self.update_study(study_id, **updates)

        if auto_tombstone and (verdict == "FALSIFIED" or status == "FALSIFIED") and not self._has_tombstone_for_study(study_id):
            metrics = detail.metrics_summary or {}
            inferred_code = failure_code or self._infer_failure_code(detail)
            inferred_lesson = lesson_summary or self._infer_lesson_summary(detail, inferred_code)
            self.save_tombstone(
                study_id=study_id,
                factor_id=detail.factor_id or detail.study_id,
                expression=detail.expression or detail.title,
                universe_id=detail.universe_id,
                snapshot_id=detail.snapshot_id,
                failure_code=inferred_code,
                failure_metrics={
                    k: metrics[k]
                    for k in (
                        "dsr",
                        "pbo",
                        "oos_is_ratio",
                        "rank_ic_mean",
                        "icir_ann",
                        "max_corr_in_library",
                        "net_excess_ann_pct",
                        "max_prefix_drift",
                    )
                    if k in metrics
                },
                lesson_summary=inferred_lesson,
                hypothesis_text=f"{detail.title} {detail.hypothesis}",
                created_by=created_by,
            )
        return detail

    def _has_tombstone_for_study(self, study_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM falsified_tombstones WHERE study_id = ? LIMIT 1",
                (study_id,),
            ).fetchone()
            return row is not None

    @staticmethod
    def _infer_failure_code(detail: ResearchStudyDetail) -> str:
        if detail.formal_proof and detail.formal_proof.blocking_failures:
            joined = " ".join(detail.formal_proof.blocking_failures).upper()
            if "PREFIX" in joined or "LOOKAHEAD" in joined:
                return "LOOKAHEAD_PREFIX_VIOLATION"
            if "AST" in joined:
                return "AST_UNSAFE"
            if "PBO" in joined:
                return "HIGH_PBO"
            if "DSR" in joined:
                return "LOW_DSR"
            if "CLIFF" in joined:
                return "PARAM_CLIFF"
        for item in detail.diagnosis:
            if item.root_cause:
                return item.root_cause.upper()
        metrics = detail.metrics_summary or {}
        if isinstance(metrics.get("net_excess_ann_pct"), (int, float)) and metrics["net_excess_ann_pct"] < 0:
            return "NEG_NET"
        return "FALSIFIED_GATE"

    @staticmethod
    def _infer_lesson_summary(detail: ResearchStudyDetail, failure_code: str) -> str:
        if detail.diagnosis:
            return detail.diagnosis[0].claim
        if detail.formal_proof and detail.formal_proof.blocking_failures:
            return f"形式化证明拦截（{failure_code}）：{'; '.join(detail.formal_proof.blocking_failures[:2])}"
        return f"课题 {detail.study_id}（{detail.title}）在 {detail.snapshot_id} 上被证伪（{failure_code}）。"

    # ------------------------------------------------------------------
    # Recursive Hypothesis Lineage & Cross-Study Comparison
    # ------------------------------------------------------------------

    def get_study_lineage(self, study_id: str) -> list[ResearchStudySummary]:
        """Return the full ancestor-to-descendant lineage tree for ``study_id`` via recursive CTE."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                WITH RECURSIVE ancestors(study_id, parent_study_id, depth) AS (
                    SELECT study_id, parent_study_id, 0
                    FROM research_studies
                    WHERE study_id = ?
                    UNION ALL
                    SELECT r.study_id, r.parent_study_id, a.depth + 1
                    FROM research_studies r
                    JOIN ancestors a ON r.study_id = a.parent_study_id
                    WHERE a.depth < 32
                ),
                roots(root_id) AS (
                    SELECT study_id FROM ancestors WHERE parent_study_id IS NULL
                    UNION
                    SELECT study_id FROM ancestors
                    WHERE depth = (SELECT MAX(depth) FROM ancestors)
                ),
                tree(study_id, level) AS (
                    SELECT root_id, 0 FROM roots
                    UNION ALL
                    SELECT r.study_id, t.level + 1
                    FROM research_studies r
                    JOIN tree t ON r.parent_study_id = t.study_id
                    WHERE t.level < 32
                )
                SELECT DISTINCT r.*, MIN(t.level) AS _level
                FROM tree t
                JOIN research_studies r ON r.study_id = t.study_id
                GROUP BY r.study_id
                ORDER BY _level ASC, r.created_at ASC, r.study_id ASC
                """,
                (study_id,),
            ).fetchall()
        return [self._row_to_study_summary(r) for r in rows]

    def compare_study_lineage(self, study_id: str) -> list[dict[str, Any]]:
        """Compute cross-study comparison deltas across a lineage tree."""
        lineage = self.get_study_lineage(study_id)
        by_id = {item.study_id: item for item in lineage}
        numeric_keys = ("rank_ic_mean", "oos_sharpe", "dsr", "pbo", "net_excess_ann_pct")
        rows: list[dict[str, Any]] = []
        for item in lineage:
            parent = by_id.get(item.parent_study_id) if item.parent_study_id else None
            deltas: dict[str, float | None] = {}
            for key in numeric_keys:
                cur = getattr(item, key)
                prev = getattr(parent, key) if parent is not None else None
                if isinstance(cur, (int, float)) and isinstance(prev, (int, float)):
                    deltas[key] = round(cur - prev, 6)
                else:
                    deltas[key] = None
            rows.append(
                {
                    "study_id": item.study_id,
                    "parent_study_id": item.parent_study_id,
                    "title": item.title,
                    "status": item.status,
                    "verdict": item.verdict,
                    "rank_ic_mean": item.rank_ic_mean,
                    "oos_sharpe": item.oos_sharpe,
                    "dsr": item.dsr,
                    "pbo": item.pbo,
                    "net_excess_ann_pct": item.net_excess_ann_pct,
                    "delta_vs_parent": deltas,
                }
            )
        return rows

    def count_family_trials(self, family_id: str, snapshot_id: str | None = None) -> int:
        """Count cumulative trials across a research family (including falsified studies)."""
        clauses = ["family_id = ?"]
        params: list[Any] = [family_id]
        if snapshot_id:
            clauses.append("snapshot_id = ?")
            params.append(snapshot_id)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS cnt FROM research_studies WHERE {' AND '.join(clauses)}",
                params,
            ).fetchone()
        return int(row["cnt"]) if row is not None else 0

    # ------------------------------------------------------------------
    # Dimension Baselines (5-dimension cached per snapshot + universe)
    # ------------------------------------------------------------------

    def save_dimension_baseline(
        self,
        record: DimensionBaselineRecord,
        *,
        period_granularity: str = "daily",
        date_bucket_map_uri: str | None = None,
    ) -> DimensionBaselineRecord:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO dimension_baselines (
                    baseline_id, snapshot_id, universe_id, dimension_type,
                    period_granularity, date_range_start, date_range_end,
                    current_state, bucket_definitions_json, bucket_stats_json,
                    date_bucket_map_uri, baseline_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_id, universe_id, dimension_type) DO UPDATE SET
                    baseline_id = excluded.baseline_id,
                    period_granularity = excluded.period_granularity,
                    date_range_start = excluded.date_range_start,
                    date_range_end = excluded.date_range_end,
                    current_state = excluded.current_state,
                    bucket_definitions_json = excluded.bucket_definitions_json,
                    bucket_stats_json = excluded.bucket_stats_json,
                    date_bucket_map_uri = excluded.date_bucket_map_uri,
                    baseline_sha256 = excluded.baseline_sha256,
                    created_at = excluded.created_at
                """,
                (
                    record.baseline_id,
                    record.snapshot_id,
                    record.universe_id,
                    record.dimension_type,
                    period_granularity,
                    record.date_range_start,
                    record.date_range_end,
                    record.current_state,
                    _dump_json(record.bucket_definitions),
                    _dump_json(record.bucket_stats),
                    date_bucket_map_uri,
                    record.baseline_sha256,
                    record.created_at,
                ),
            )
        return record

    @classmethod
    def _row_to_dimension_baseline(cls, row: sqlite3.Row) -> DimensionBaselineRecord:
        return DimensionBaselineRecord(
            baseline_id=row["baseline_id"],
            snapshot_id=row["snapshot_id"],
            universe_id=row["universe_id"],
            dimension_type=row["dimension_type"],
            date_range_start=row["date_range_start"],
            date_range_end=row["date_range_end"],
            current_state=row["current_state"] or "",
            bucket_definitions=cls._parse_json_field(row["bucket_definitions_json"], {}),
            bucket_stats=cls._parse_json_field(row["bucket_stats_json"], []),
            baseline_sha256=row["baseline_sha256"],
            created_at=row["created_at"],
        )

    def get_dimension_baseline(
        self,
        snapshot_id: str,
        universe_id: str,
        dimension_type: str,
    ) -> DimensionBaselineRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM dimension_baselines
                WHERE snapshot_id = ? AND universe_id = ? AND dimension_type = ?
                """,
                (snapshot_id, universe_id, dimension_type),
            ).fetchone()
        return self._row_to_dimension_baseline(row) if row is not None else None

    def list_dimension_baselines(
        self,
        *,
        snapshot_id: str | None = None,
        universe_id: str | None = None,
        dimension_type: str | None = None,
    ) -> list[DimensionBaselineRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if snapshot_id:
            clauses.append("snapshot_id = ?")
            params.append(snapshot_id)
        if universe_id:
            clauses.append("universe_id = ?")
            params.append(universe_id)
        if dimension_type:
            clauses.append("dimension_type = ?")
            params.append(dimension_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM dimension_baselines {where} ORDER BY dimension_type ASC",
                params,
            ).fetchall()
        return [self._row_to_dimension_baseline(r) for r in rows]

    # ------------------------------------------------------------------
    # Falsified Tombstones & 3-Layer Deduplication
    # ------------------------------------------------------------------

    def save_tombstone(
        self,
        *,
        factor_id: str,
        expression: str,
        universe_id: str,
        snapshot_id: str,
        failure_code: str,
        lesson_summary: str,
        study_id: str | None = None,
        failure_metrics: dict[str, Any] | None = None,
        hypothesis_text: str = "",
        keywords: list[str] | None = None,
        tombstone_id: str | None = None,
        created_by: str = "orchestrator",
    ) -> FalsifiedTombstoneRecord:
        eh = compute_expr_hash(expression)
        ast_fp = compute_ast_fingerprint(expression)
        now = _now_iso()
        tid = tombstone_id or f"TOMB-{hashlib.sha256(f'{eh}:{study_id or factor_id}:{failure_code}'.encode()).hexdigest()[:10]}"
        kw = (
            sorted(set(keywords))
            if keywords is not None
            else extract_keywords(factor_id, hypothesis_text or lesson_summary)
        )
        metrics = dict(failure_metrics or {})
        mem_line = format_tombstone_memory_line(
            factor_id=factor_id,
            expr_hash=eh,
            failure_code=failure_code,
            failure_metrics=metrics,
            snapshot_id=snapshot_id,
            study_id=study_id,
            created_date=now[:10],
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO falsified_tombstones (
                    tombstone_id, study_id, factor_id, expression, expr_hash,
                    ast_fingerprint, universe_id, snapshot_id, failure_code,
                    failure_metrics_json, lesson_summary, keywords_json,
                    memory_line, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tombstone_id) DO UPDATE SET
                    failure_code = excluded.failure_code,
                    failure_metrics_json = excluded.failure_metrics_json,
                    lesson_summary = excluded.lesson_summary,
                    keywords_json = excluded.keywords_json,
                    memory_line = excluded.memory_line
                """,
                (
                    tid,
                    study_id,
                    factor_id,
                    expression,
                    eh,
                    ast_fp,
                    universe_id,
                    snapshot_id,
                    failure_code,
                    _dump_json(metrics),
                    lesson_summary,
                    _dump_json(kw),
                    mem_line,
                    created_by,
                    now,
                ),
            )
        return FalsifiedTombstoneRecord(
            tombstone_id=tid,
            study_id=study_id,
            factor_id=factor_id,
            expression=expression,
            expr_hash=eh,
            ast_fingerprint=ast_fp,
            universe_id=universe_id,
            snapshot_id=snapshot_id,
            failure_code=failure_code,
            failure_metrics=metrics,
            lesson_summary=lesson_summary,
            memory_line=mem_line,
            created_by=created_by,
            created_at=now,
        )

    @classmethod
    def _row_to_tombstone(cls, row: sqlite3.Row) -> FalsifiedTombstoneRecord:
        return FalsifiedTombstoneRecord(
            tombstone_id=row["tombstone_id"],
            study_id=row["study_id"],
            factor_id=row["factor_id"],
            expression=row["expression"],
            expr_hash=row["expr_hash"],
            ast_fingerprint=row["ast_fingerprint"],
            universe_id=row["universe_id"],
            snapshot_id=row["snapshot_id"],
            failure_code=row["failure_code"],
            failure_metrics=cls._parse_json_field(row["failure_metrics_json"], {}),
            lesson_summary=row["lesson_summary"],
            memory_line=row["memory_line"],
            created_by=row["created_by"],
            created_at=row["created_at"],
        )

    def list_tombstones(
        self,
        *,
        expr_hash: str | None = None,
        failure_code: str | None = None,
        snapshot_id: str | None = None,
        q: str | None = None,
        limit: int = 50,
    ) -> list[FalsifiedTombstoneRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if expr_hash:
            clauses.append("expr_hash = ?")
            params.append(expr_hash)
        if failure_code:
            clauses.append("failure_code = ?")
            params.append(failure_code)
        if snapshot_id:
            clauses.append("snapshot_id = ?")
            params.append(snapshot_id)
        if q:
            like = f"%{q.strip()}%"
            clauses.append(
                "(factor_id LIKE ? OR expression LIKE ? OR lesson_summary LIKE ? OR keywords_json LIKE ? OR memory_line LIKE ?)"
            )
            params.extend([like, like, like, like, like])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM falsified_tombstones {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._row_to_tombstone(r) for r in rows]

    def check_tombstone_duplicates(
        self,
        *,
        expression: str | None = None,
        hypothesis: str = "",
        title: str = "",
        factor_id: str | None = None,
        jaccard_threshold: float = 0.45,
    ) -> dict[str, Any]:
        """Run 3-layer tombstone deduplication: exact expr_hash, AST fingerprint, and keyword Jaccard >= 0.45."""
        eh = compute_expr_hash(expression) if expression else ""
        ast_fp = compute_ast_fingerprint(expression) if expression else ""
        query_kw = extract_keywords(title, hypothesis, factor_id)

        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM falsified_tombstones ORDER BY created_at DESC").fetchall()

        matches_by_id: dict[str, dict[str, Any]] = {}
        exact_hit = False
        ast_hit = False
        keyword_hit = False

        for row in rows:
            tid = row["tombstone_id"]
            tier: str | None = None
            severity: str | None = None
            similarity = 0.0

            if eh and row["expr_hash"] == eh:
                exact_hit = True
                tier = "exact_expr_hash"
                severity = "blocking"
                similarity = 1.0
            elif ast_fp and row["ast_fingerprint"] == ast_fp:
                ast_hit = True
                tier = "ast_fingerprint"
                severity = "high"
                similarity = 0.90
            elif query_kw:
                stored_kw = self._parse_json_field(row["keywords_json"], [])
                if not stored_kw:
                    stored_kw = extract_keywords(row["factor_id"], row["expression"], row["lesson_summary"])
                sim = jaccard_similarity(query_kw, stored_kw)
                if sim >= jaccard_threshold:
                    keyword_hit = True
                    tier = "keyword_jaccard"
                    severity = "warn"
                    similarity = round(sim, 4)

            if tier is not None:
                matches_by_id[tid] = {
                    "tombstone_id": tid,
                    "study_id": row["study_id"],
                    "factor_id": row["factor_id"],
                    "expression": row["expression"],
                    "failure_code": row["failure_code"],
                    "lesson_summary": row["lesson_summary"],
                    "memory_line": row["memory_line"],
                    "tier": tier,
                    "severity": severity,
                    "similarity": similarity,
                }

        rank_order = {"blocking": 0, "high": 1, "warn": 2}
        matches = sorted(
            matches_by_id.values(),
            key=lambda m: (rank_order.get(m["severity"], 9), -float(m["similarity"])),
        )
        return {
            "exact_hit": exact_hit,
            "ast_hit": ast_hit,
            "keyword_hit": keyword_hit,
            "expr_hash": eh,
            "ast_fingerprint": ast_fp,
            "matches": matches,
        }

    # ------------------------------------------------------------------
    # Study Events (SSE streaming & Last-Event-ID replay)
    # ------------------------------------------------------------------

    def append_study_event(
        self,
        study_id: str,
        *,
        stage: str,
        event_type: str,
        message: str,
        progress_pct: int = 0,
        payload: dict[str, Any] | None = None,
        actor: str = "orchestrator",
    ) -> StudyEventRecord:
        eid = f"evt-{uuid.uuid4().hex[:12]}"
        now = _now_iso()
        pct = max(0, min(100, int(progress_pct)))
        payload_dict = dict(payload or {})
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                row = conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM study_events WHERE study_id = ?",
                    (study_id,),
                ).fetchone()
                next_seq = int(row["max_seq"]) + 1 if row is not None else 1
                conn.execute(
                    """
                    INSERT INTO study_events (
                        event_id, study_id, seq, stage, event_type,
                        progress_pct, message, payload_json, actor, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        eid,
                        study_id,
                        next_seq,
                        stage,
                        event_type,
                        pct,
                        message,
                        _dump_json(payload_dict),
                        actor,
                        now,
                    ),
                )
                conn.execute("COMMIT;")
            except Exception:
                conn.execute("ROLLBACK;")
                raise
        return StudyEventRecord(
            event_id=eid,
            study_id=study_id,
            seq=next_seq,
            stage=stage,
            event_type=event_type,
            progress_pct=pct,
            message=message,
            payload=payload_dict,
            actor=actor,
            created_at=now,
        )

    def list_study_events(
        self,
        study_id: str,
        *,
        after_seq: int = 0,
        limit: int = 500,
    ) -> list[StudyEventRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM study_events
                WHERE study_id = ? AND seq > ?
                ORDER BY seq ASC
                LIMIT ?
                """,
                (study_id, int(after_seq), int(limit)),
            ).fetchall()
        return [
            StudyEventRecord(
                event_id=r["event_id"],
                study_id=r["study_id"],
                seq=int(r["seq"]),
                stage=r["stage"],
                event_type=r["event_type"],
                progress_pct=int(r["progress_pct"]),
                message=r["message"],
                payload=self._parse_json_field(r["payload_json"], {}),
                actor=r["actor"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Research Notes
    # ------------------------------------------------------------------

    def add_research_note(
        self,
        study_id: str,
        *,
        author: str,
        content_md: str,
        note_type: str = "review",
        linked_terms: list[str] | None = None,
    ) -> dict[str, Any]:
        nid = f"note-{uuid.uuid4().hex[:12]}"
        now = _now_iso()
        terms = list(linked_terms or [])
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO research_notes (
                    note_id, study_id, author, note_type, content_md, linked_terms_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (nid, study_id, author, note_type, content_md, _dump_json(terms), now),
            )
        return {
            "note_id": nid,
            "study_id": study_id,
            "author": author,
            "note_type": note_type,
            "content_md": content_md,
            "linked_terms": terms,
            "created_at": now,
        }

    def list_research_notes(self, study_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM research_notes
                WHERE study_id = ?
                ORDER BY created_at DESC, note_id DESC
                """,
                (study_id,),
            ).fetchall()
        return [
            {
                "note_id": r["note_id"],
                "study_id": r["study_id"],
                "author": r["author"],
                "note_type": r["note_type"],
                "content_md": r["content_md"],
                "linked_terms": self._parse_json_field(r["linked_terms_json"], []),
                "created_at": r["created_at"],
            }
            for r in rows
        ]
