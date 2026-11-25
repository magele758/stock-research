"""QuantLake: PIT canonical Parquet lake + DuckDB temporal queries + SQLite WAL registry."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from quant_core.lake.qa_sentinel import run_all_qa_checks
from quant_core.lake.qlib_export import export_snapshot_to_qlib
from quant_core.lake.registry import QuantRegistry
from quant_core.schemas import (
    ArtifactRef,
    AsyncJobRecord,
    AvailabilityMode,
    DataHealthReport,
    DatasetSnapshot,
    ExperimentRecord,
    Market,
    UniverseMember,
)

CANONICAL_TABLES: tuple[str, ...] = (
    "bar_raw_1d",
    "corp_action",
    "financial_fact",
    "universe_member",
    "industry_membership",
    "instrument_lifecycle",
)

PIT_BAR_COLUMNS: tuple[str, ...] = (
    "listing_id",
    "symbol",
    "name",
    "market",
    "board",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "vwap",
    "volume_shares",
    "amount_ccy",
    "adj_factor",
    "up_limit",
    "down_limit",
    "is_suspended",
    "is_st",
    "float_market_cap",
    "industry_l1",
)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _sanitize_id(raw: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_-")
    if not cleaned:
        cleaned = "art"
    if not cleaned[0].isalnum():
        cleaned = f"a{cleaned}"
    return cleaned


def _normalize_as_of_utc(as_of: datetime | date) -> datetime:
    if isinstance(as_of, datetime):
        return as_of.astimezone(UTC) if as_of.tzinfo is not None else as_of.replace(tzinfo=UTC)
    # Date-only input defaults to end of UTC day for deterministic replay
    return datetime(as_of.year, as_of.month, as_of.day, 23, 59, 59, tzinfo=UTC)


def _default_db_path(root_dir: Path) -> Path:
    if root_dir.name == "quant-lake":
        return root_dir.parent / "state" / "quant_registry.db"
    return root_dir / "state" / "quant_registry.db"


def _duckdb_query_polars(sql: str, params: list[Any]) -> pl.DataFrame:
    con = duckdb.connect(":memory:")
    try:
        rel = con.sql(sql, params=params)
        if hasattr(rel, "pl"):
            return rel.pl()
        return pl.from_arrow(rel.to_arrow_table())
    finally:
        con.close()


class QuantLake:
    """PIT quantitative data lake backed by Zstd Parquet, DuckDB temporal SQL, and SQLite WAL."""

    def __init__(
        self,
        root_dir: Path | str = "data/quant-lake",
        db_path: Path | str | None = None,
    ) -> None:
        self.root_dir = Path(root_dir)
        resolved_db = Path(db_path) if db_path is not None else _default_db_path(self.root_dir)
        self.db_path = resolved_db
        self._ensure_layout()
        self.registry = QuantRegistry(db_path=self.db_path)

    def _ensure_layout(self) -> None:
        (self.root_dir / "raw").mkdir(parents=True, exist_ok=True)
        for tbl in CANONICAL_TABLES:
            (self.root_dir / "canonical" / tbl).mkdir(parents=True, exist_ok=True)
        (self.root_dir / "manifests" / "snapshots").mkdir(parents=True, exist_ok=True)
        (self.root_dir / "qlib").mkdir(parents=True, exist_ok=True)
        (self.root_dir / "quarantine").mkdir(parents=True, exist_ok=True)
        (self.root_dir / "artifacts").mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Snapshot Manifests & Publishing
    # ------------------------------------------------------------------

    def _manifest_path(self, snapshot_id: str) -> Path:
        return self.root_dir / "manifests" / "snapshots" / f"{snapshot_id}.json"

    def _read_manifest(self, snapshot_id: str) -> dict[str, Any]:
        mpath = self._manifest_path(snapshot_id)
        if not mpath.exists():
            raise FileNotFoundError(f"Snapshot manifest not found: {snapshot_id} ({mpath})")
        return json.loads(mpath.read_text(encoding="utf-8"))

    def _resolve_table_path(self, snapshot_id: str, table: str) -> Path:
        manifest = self._read_manifest(snapshot_id)
        rel = (manifest.get("tables") or {}).get(table, {}).get("path")
        if not rel:
            raise FileNotFoundError(f"Table '{table}' not listed in snapshot '{snapshot_id}' manifest")
        resolved = (self.root_dir / rel).resolve()
        root_resolved = self.root_dir.resolve()
        if not str(resolved).startswith(str(root_resolved)):
            raise ValueError(f"Manifest path escapes lake root: {rel}")
        if not resolved.exists():
            raise FileNotFoundError(f"Canonical parquet missing on disk: {resolved}")
        return resolved

    def _resolve_snapshot_id(self, snapshot_id: str | None) -> str:
        if snapshot_id:
            return snapshot_id
        return self.get_latest_snapshot().snapshot_id

    def _sync_manifests_from_disk(self) -> None:
        """Recover disk snapshot manifests into SQLite registry if registry is cold."""
        snap_dir = self.root_dir / "manifests" / "snapshots"
        for mpath in sorted(snap_dir.glob("*.json")):
            data = json.loads(mpath.read_text(encoding="utf-8"))
            snap_dict = data.get("snapshot")
            if snap_dict:
                snap = DatasetSnapshot.model_validate(snap_dict)
                if self.registry.get_snapshot(snap.snapshot_id) is None:
                    self.registry.save_snapshot(snap)

    def publish_snapshot(
        self,
        *,
        snapshot_id: str,
        as_of: datetime,
        tables: dict[str, pl.DataFrame],
        source_provenance: list[str] | None = None,
        markets: list[Market] | None = None,
        run_qa: bool = True,
        created_at: datetime | None = None,
    ) -> DatasetSnapshot:
        """Persist immutable Zstd canonical Parquet files, manifest JSON, and registry pointer."""
        as_of_utc = _normalize_as_of_utc(as_of)
        created_utc = _normalize_as_of_utc(created_at) if created_at else datetime.now(UTC)

        missing = [t for t in CANONICAL_TABLES if t not in tables]
        if missing:
            raise ValueError(f"publish_snapshot missing required canonical tables: {missing}")

        table_entries: dict[str, dict[str, Any]] = {}
        table_counts: dict[str, int] = {}
        table_files: dict[str, list[Path]] = {}

        for tbl in CANONICAL_TABLES:
            df = tables[tbl]
            tbl_dir = self.root_dir / "canonical" / tbl / f"snapshot={snapshot_id}"
            tbl_dir.mkdir(parents=True, exist_ok=True)
            tmp_path = tbl_dir / f".tmp-{uuid.uuid4().hex[:8]}.parquet"
            df.write_parquet(tmp_path, compression="zstd")
            file_sha = _sha256_file(tmp_path)
            final_path = tbl_dir / f"{file_sha[:16]}.parquet"
            tmp_path.replace(final_path)

            rel_path = final_path.relative_to(self.root_dir).as_posix()
            table_entries[tbl] = {
                "path": rel_path,
                "rows": df.height,
                "sha256": file_sha,
                "bytes": final_path.stat().st_size,
            }
            table_counts[tbl] = df.height
            table_files[tbl] = [final_path]

        inferred_markets = markets
        if not inferred_markets:
            bars = tables["bar_raw_1d"]
            m_vals = sorted(bars["market"].unique().to_list()) if not bars.is_empty() else [Market.CN_A.value]
            inferred_markets = [Market(m) for m in m_vals]

        life_df = tables["instrument_lifecycle"]
        delisted_count = (
            int(life_df.filter(pl.col("delist_date").is_not_null()).height)
            if not life_df.is_empty() and "delist_date" in life_df.columns
            else 0
        )

        manifest_core = {
            "snapshot_id": snapshot_id,
            "as_of": as_of_utc.isoformat(),
            "markets": [m.value for m in inferred_markets],
            "tables": table_entries,
        }
        manifest_sha256 = hashlib.sha256(
            json.dumps(manifest_core, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

        qa_status = "pass"
        qa_report: DataHealthReport | None = None
        if run_qa:
            qa_report = run_all_qa_checks(
                snapshot_id=snapshot_id,
                as_of=as_of_utc,
                tables=tables,
                table_files=table_files,
                manifest_sha256=manifest_sha256,
            )
            qa_status = qa_report.overall_status

        snapshot = DatasetSnapshot(
            snapshot_id=snapshot_id,
            as_of=as_of_utc,
            markets=inferred_markets,
            created_at=created_utc,
            manifest_sha256=manifest_sha256,
            table_counts=table_counts,
            qa_status=qa_status,
            delisted_count=delisted_count,
            source_provenance=source_provenance or [],
        )

        manifest_doc = {
            **manifest_core,
            "manifest_sha256": manifest_sha256,
            "snapshot": snapshot.model_dump(mode="json"),
        }
        mpath = self._manifest_path(snapshot_id)
        tmp_mpath = mpath.with_suffix(".json.tmp")
        tmp_mpath.write_text(
            json.dumps(manifest_doc, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp_mpath.replace(mpath)

        self.registry.save_snapshot(snapshot)
        if qa_report is not None:
            self.registry.save_qa_run(qa_report)
        return snapshot

    def quarantine_batch(
        self,
        dataset: str,
        batch_id: str,
        df: pl.DataFrame,
        reasons: list[str],
    ) -> Path:
        """Isolate rejected records under ``quarantine/<dataset>/<batch_id>/`` without advancing snapshots."""
        qdir = self.root_dir / "quarantine" / _sanitize_id(dataset) / _sanitize_id(batch_id)
        qdir.mkdir(parents=True, exist_ok=True)
        df.write_parquet(qdir / "rejected.parquet", compression="zstd")
        (qdir / "reason.json").write_text(
            json.dumps(
                {
                    "dataset": dataset,
                    "batch_id": batch_id,
                    "rows": df.height,
                    "reasons": reasons,
                    "quarantined_at": datetime.now(UTC).isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return qdir

    # ------------------------------------------------------------------
    # Contract §2.1 Methods
    # ------------------------------------------------------------------

    def get_latest_snapshot(self) -> DatasetSnapshot:
        snap = self.registry.get_latest_snapshot()
        if snap is None:
            self._sync_manifests_from_disk()
            snap = self.registry.get_latest_snapshot()
        if snap is None:
            raise FileNotFoundError(
                f"No snapshots published in QuantLake ({self.root_dir}). "
                "Run scripts/seed_golden_dataset.py or publish a snapshot first."
            )
        return snap

    def list_snapshots(self) -> list[DatasetSnapshot]:
        snaps = self.registry.list_snapshots()
        if not snaps:
            self._sync_manifests_from_disk()
            snaps = self.registry.list_snapshots()
        return snaps

    def load_bars(
        self,
        universe_id: str = "CSI500",
        start_date: date | None = None,
        end_date: date | None = None,
        snapshot_id: str | None = None,
    ) -> pl.DataFrame:
        """Load PIT daily bar panel joined with lifecycle, Shenwan industry, and dynamic universe membership."""
        sid = self._resolve_snapshot_id(snapshot_id)
        bar_path = self._resolve_table_path(sid, "bar_raw_1d")
        life_path = self._resolve_table_path(sid, "instrument_lifecycle")
        ind_path = self._resolve_table_path(sid, "industry_membership")
        uni_path = self._resolve_table_path(sid, "universe_member")

        where_clauses: list[str] = [
            "b.trade_date >= l.list_date",
            "(l.delist_date IS NULL OR b.trade_date <= l.delist_date)",
        ]
        params: list[Any] = [str(bar_path), str(life_path), str(ind_path)]

        uni_clean = (universe_id or "").strip()
        if uni_clean and uni_clean.upper() != "ALL":
            if uni_clean.upper() in (Market.CN_A.value, Market.US_EQ.value):
                where_clauses.append("b.market = ?")
                params.append(uni_clean.upper())
            else:
                where_clauses.append(
                    """
                    EXISTS (
                        SELECT 1
                        FROM read_parquet(?) AS um
                        WHERE um.universe_id = ?
                          AND um.listing_id = b.listing_id
                          AND b.trade_date >= um.effective_from
                          AND (um.effective_to IS NULL OR b.trade_date <= um.effective_to)
                    )
                    """
                )
                params.extend([str(uni_path), uni_clean])

        if start_date is not None:
            where_clauses.append("b.trade_date >= ?")
            params.append(start_date)
        if end_date is not None:
            where_clauses.append("b.trade_date <= ?")
            params.append(end_date)

        sql = f"""
        SELECT
            b.listing_id,
            b.symbol,
            l.name,
            b.market,
            l.board,
            b.trade_date,
            CAST(b.open AS DOUBLE) AS open,
            CAST(b.high AS DOUBLE) AS high,
            CAST(b.low AS DOUBLE) AS low,
            CAST(b.close AS DOUBLE) AS close,
            CAST(b.pre_close AS DOUBLE) AS pre_close,
            CAST(b.vwap AS DOUBLE) AS vwap,
            CAST(b.volume_shares AS BIGINT) AS volume_shares,
            CAST(b.amount_ccy AS DOUBLE) AS amount_ccy,
            CAST(b.adj_factor AS DOUBLE) AS adj_factor,
            CAST(b.up_limit AS DOUBLE) AS up_limit,
            CAST(b.down_limit AS DOUBLE) AS down_limit,
            CAST(b.is_suspended AS BOOLEAN) AS is_suspended,
            CAST(b.is_st AS BOOLEAN) AS is_st,
            CAST(b.float_market_cap AS DOUBLE) AS float_market_cap,
            COALESCE(im.l1_name, 'Unclassified') AS industry_l1
        FROM read_parquet(?) AS b
        INNER JOIN read_parquet(?) AS l
            ON b.listing_id = l.listing_id
        LEFT JOIN read_parquet(?) AS im
            ON b.listing_id = im.listing_id
           AND b.trade_date >= im.valid_from
           AND (im.valid_to IS NULL OR b.trade_date <= im.valid_to)
        WHERE {' AND '.join(where_clauses)}
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY b.listing_id, b.trade_date
            ORDER BY im.valid_from DESC NULLS LAST
        ) = 1
        ORDER BY b.trade_date ASC, b.listing_id ASC
        """
        df = _duckdb_query_polars(sql, params)
        return df.select(list(PIT_BAR_COLUMNS))

    def load_pit_financials(
        self,
        as_of: datetime | date,
        snapshot_id: str | None = None,
        availability_mode: AvailabilityMode | str = AvailabilityMode.STRICT_PUBLIC,
    ) -> pl.DataFrame:
        """Bi-temporal PIT financial replay: initial -> restatement -> withdrawal without future leakage.

        - Filters ``published_at <= as_of`` (and ``first_seen_at <= as_of`` in ``RECORDED_REPLAY`` mode).
        - For each ``(issuer_id, metric_code, report_period_end)``, selects the latest revision by
          ``revision_seq DESC, published_at DESC``.
        - If that latest effective revision has ``is_withdrawn = True``, excludes the fact completely
          (neither returning the withdrawn value nor silently reverting to a superseded revision).
        """
        sid = self._resolve_snapshot_id(snapshot_id)
        fin_path = self._resolve_table_path(sid, "financial_fact")
        as_of_utc = _normalize_as_of_utc(as_of)
        mode = (
            availability_mode
            if isinstance(availability_mode, AvailabilityMode)
            else AvailabilityMode(str(availability_mode))
        )

        sql = """
        WITH eligible AS (
            SELECT *
            FROM read_parquet(?)
            WHERE published_at <= ?
              AND (? = 'strict_public' OR first_seen_at <= ?)
        ),
        ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY issuer_id, metric_code, report_period_end
                       ORDER BY revision_seq DESC, published_at DESC, fact_id DESC
                   ) AS _rn
            FROM eligible
        )
        SELECT
            fact_id,
            issuer_id,
            symbol,
            market,
            metric_code,
            report_period_end,
            ann_date,
            published_at,
            first_seen_at,
            CAST(revision_seq AS INTEGER) AS revision_seq,
            CAST(is_restated AS BOOLEAN) AS is_restated,
            CAST(is_withdrawn AS BOOLEAN) AS is_withdrawn,
            CAST(value AS DOUBLE) AS value,
            currency,
            source_id,
            quality_state
        FROM ranked
        WHERE _rn = 1 AND NOT is_withdrawn
        ORDER BY issuer_id ASC, report_period_end ASC, metric_code ASC
        """
        return _duckdb_query_polars(
            sql,
            [str(fin_path), as_of_utc, mode.value, as_of_utc],
        )

    def load_corp_actions(
        self,
        snapshot_id: str | None = None,
        listing_id: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pl.DataFrame:
        sid = self._resolve_snapshot_id(snapshot_id)
        path = self._resolve_table_path(sid, "corp_action")
        clauses: list[str] = ["1=1"]
        params: list[Any] = [str(path)]
        if listing_id is not None:
            clauses.append("listing_id = ?")
            params.append(listing_id)
        if start_date is not None:
            clauses.append("ex_date >= ?")
            params.append(start_date)
        if end_date is not None:
            clauses.append("ex_date <= ?")
            params.append(end_date)

        sql = f"""
        SELECT
            action_id,
            listing_id,
            symbol,
            ex_date,
            record_date,
            pay_date,
            CAST(cash_dividend_per_share AS DOUBLE) AS cash_dividend_per_share,
            CAST(split_ratio AS DOUBLE) AS split_ratio,
            CAST(bonus_share_ratio AS DOUBLE) AS bonus_share_ratio,
            announced_at,
            source_id
        FROM read_parquet(?)
        WHERE {' AND '.join(clauses)}
        ORDER BY ex_date ASC, listing_id ASC
        """
        return _duckdb_query_polars(sql, params)

    def load_universe_constituents(
        self,
        universe_id: str,
        as_of_date: date,
        snapshot_id: str | None = None,
    ) -> list[UniverseMember]:
        sid = self._resolve_snapshot_id(snapshot_id)
        uni_path = self._resolve_table_path(sid, "universe_member")
        sql = """
        SELECT
            universe_id,
            as_of_date,
            listing_id,
            symbol,
            CAST(weight AS DOUBLE) AS weight,
            weight_quality,
            effective_from,
            effective_to
        FROM read_parquet(?)
        WHERE universe_id = ?
          AND as_of_date <= ?
          AND effective_from <= ?
          AND (effective_to IS NULL OR effective_to >= ?)
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY listing_id
            ORDER BY as_of_date DESC, effective_from DESC
        ) = 1
        ORDER BY weight DESC NULLS LAST, symbol ASC
        """
        df = _duckdb_query_polars(
            sql,
            [str(uni_path), universe_id, as_of_date, as_of_date, as_of_date],
        )
        return [UniverseMember.model_validate(row) for row in df.iter_rows(named=True)]

    def load_industry_memberships(
        self,
        snapshot_id: str | None = None,
        as_of_date: date | None = None,
    ) -> pl.DataFrame:
        sid = self._resolve_snapshot_id(snapshot_id)
        ind_path = self._resolve_table_path(sid, "industry_membership")
        df = pl.read_parquet(ind_path)
        if as_of_date is not None:
            df = df.filter(
                (pl.col("valid_from") <= as_of_date)
                & (pl.col("valid_to").is_null() | (pl.col("valid_to") >= as_of_date))
            )
        return df.sort(["listing_id", "valid_from"])

    def load_instrument_lifecycle(self, snapshot_id: str | None = None) -> pl.DataFrame:
        sid = self._resolve_snapshot_id(snapshot_id)
        life_path = self._resolve_table_path(sid, "instrument_lifecycle")
        return pl.read_parquet(life_path).sort("listing_id")

    def run_qa_sentinel(self, snapshot_id: str | None = None) -> DataHealthReport:
        """Execute all 7 hard end-of-day QA audits against a frozen snapshot and persist QA run."""
        sid = self._resolve_snapshot_id(snapshot_id)
        manifest = self._read_manifest(sid)
        snap = self.registry.get_snapshot(sid)
        as_of = (
            snap.as_of
            if snap is not None
            else datetime.fromisoformat(manifest["as_of"])
        )
        manifest_sha256 = str(manifest["manifest_sha256"])

        tables: dict[str, pl.DataFrame] = {}
        table_files: dict[str, list[Path]] = {}
        for tbl in CANONICAL_TABLES:
            p = self._resolve_table_path(sid, tbl)
            tables[tbl] = pl.read_parquet(p)
            table_files[tbl] = [p]

        report = run_all_qa_checks(
            snapshot_id=sid,
            as_of=as_of,
            tables=tables,
            table_files=table_files,
            manifest_sha256=manifest_sha256,
        )
        self.registry.save_qa_run(report)
        return report

    def export_qlib_bin(
        self,
        snapshot_id: str | None = None,
        market: str = "CN_A",
        force: bool = False,
    ) -> dict[str, Any]:
        """Export frozen snapshot canonical bars to Qlib binary format with atomic _staging switch."""
        sid = self._resolve_snapshot_id(snapshot_id)
        bar_path = self._resolve_table_path(sid, "bar_raw_1d")
        uni_path = self._resolve_table_path(sid, "universe_member")
        bars_df = pl.read_parquet(bar_path)
        uni_df = pl.read_parquet(uni_path)
        return export_snapshot_to_qlib(
            qlib_root=self.root_dir / "qlib",
            snapshot_id=sid,
            market=market,
            bars_df=bars_df,
            universe_df=uni_df,
            source_files=[bar_path, uni_path],
            force=force,
        )

    # ------------------------------------------------------------------
    # Experiment Registry, Artifacts & Jobs
    # ------------------------------------------------------------------

    def list_experiments(self, limit: int = 20) -> list[ExperimentRecord]:
        return self.registry.list_experiments(limit=limit)

    def save_experiment(self, rec: ExperimentRecord) -> None:
        self.registry.save_experiment(rec)

    def save_artifact(
        self,
        run_id: str,
        name: str,
        payload: dict[str, Any] | pl.DataFrame,
    ) -> ArtifactRef:
        safe_run = _sanitize_id(run_id)
        safe_name = _sanitize_id(name)
        run_dir = self.root_dir / "artifacts" / safe_run
        run_dir.mkdir(parents=True, exist_ok=True)

        if isinstance(payload, pl.DataFrame):
            fmt = "parquet"
            out_path = run_dir / f"{safe_name}.parquet"
            payload.write_parquet(out_path, compression="zstd")
            rows = payload.height
        elif isinstance(payload, dict):
            fmt = "json"
            out_path = run_dir / f"{safe_name}.json"
            out_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            rows = len(payload)
        else:
            raise TypeError(f"Unsupported artifact payload type: {type(payload)!r}")

        digest = _sha256_file(out_path)
        # Keep URI matching ^quant-artifact://[A-Za-z0-9][A-Za-z0-9_-]{0,127}$
        slug = f"{safe_run}_{safe_name}_{digest[:10]}"[:120]
        ref = ArtifactRef(
            uri=f"quant-artifact://{slug}",
            format=fmt,
            rows=rows,
            sha256=digest,
        )
        self.registry.save_artifact_meta(
            ref=ref,
            run_id=run_id,
            name=name,
            file_path=str(out_path),
        )
        return ref

    def load_artifact(self, uri: str) -> dict[str, Any] | pl.DataFrame:
        meta = self.registry.get_artifact_meta(uri)
        if meta is None:
            raise FileNotFoundError(f"Unknown artifact URI: {uri}")
        p = Path(meta["file_path"])
        if not p.exists():
            raise FileNotFoundError(f"Artifact file missing on disk: {p}")
        if meta["format"] == "parquet":
            return pl.read_parquet(p)
        return json.loads(p.read_text(encoding="utf-8"))

    def save_job(self, job: AsyncJobRecord) -> None:
        self.registry.save_job(job)

    def get_job(self, job_id: str) -> AsyncJobRecord | None:
        return self.registry.get_job(job_id)
