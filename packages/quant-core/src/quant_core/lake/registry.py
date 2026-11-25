"""SQLite WAL (PostgreSQL 18 schema-compatible) metadata & experiment registry."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quant_core.schemas import (
    ArtifactRef,
    AsyncJobRecord,
    DataHealthReport,
    DatasetSnapshot,
    ExperimentRecord,
    GateVerdict,
    Market,
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

CREATE INDEX IF NOT EXISTS idx_snapshots_created ON snapshots(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_experiments_created ON experiments(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_experiments_family ON experiments(family_id);
CREATE INDEX IF NOT EXISTS idx_jobs_updated ON jobs(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_qa_runs_snapshot ON qa_runs(snapshot_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ingest_batches_provider ON ingest_batches(provider, received_at DESC);
"""


def _dt_to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def _iso_to_dt(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


class QuantRegistry:
    """Durable metadata & experiment registry backed by SQLite WAL (PG 18 compatible DDL).

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
            conn.execute("PRAGMA busy_timeout=5000;")
            yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(REGISTRY_SCHEMA_SQL)

    # ------------------------------------------------------------------
    # Snapshots
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
    # Experiments
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
    # Async Jobs
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
    # Artifacts
    # ------------------------------------------------------------------

    def save_artifact_meta(
        self,
        ref: ArtifactRef,
        run_id: str,
        name: str,
        file_path: str,
    ) -> None:
        now = _dt_to_iso(datetime.now(UTC))
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
    # QA Runs
    # ------------------------------------------------------------------

    def save_qa_run(self, report: DataHealthReport) -> str:
        qa_run_id = f"qa-{uuid.uuid4().hex[:12]}"
        now = _dt_to_iso(datetime.now(UTC))
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
    # Ingest Batches
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
        now = _dt_to_iso(datetime.now(UTC))
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
