"""Atomic Qlib binary exporter with incremental content signatures and real adj_factor preservation."""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

EXPORTER_VERSION = "v2.0"
QLIB_FIELDS: tuple[str, ...] = ("open", "close", "high", "low", "vwap", "volume", "factor")


def to_qlib_symbol(symbol: str) -> str:
    """Normalize canonical ticker to Qlib convention (``600519.SH`` -> ``SH600519``)."""
    sym = symbol.strip().upper()
    if sym.endswith(".SH"):
        return f"SH{sym[:-3]}"
    if sym.endswith(".SZ"):
        return f"SZ{sym[:-3]}"
    if sym.endswith(".BJ"):
        return f"BJ{sym[:-3]}"
    return sym


def read_qlib_feature_bin(bin_path: Path) -> tuple[int, np.ndarray]:
    """Read a Qlib little-endian float32 ``.bin`` file -> ``(start_idx, values)``."""
    raw = np.fromfile(str(bin_path), dtype="<f4")
    if raw.size == 0:
        raise ValueError(f"Empty Qlib binary file: {bin_path}")
    start_idx = round(float(raw[0]))
    return start_idx, raw[1:]


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _compute_source_signature(
    *,
    snapshot_id: str,
    market: str,
    source_files: list[Path],
    symbol_stats: dict[str, dict[str, Any]],
) -> str:
    file_digests = [
        {
            "name": p.name,
            "size": p.stat().st_size,
            "sha256": _sha256_file(p),
        }
        for p in sorted(source_files, key=lambda x: x.as_posix())
        if p.exists()
    ]
    payload = {
        "exporter_version": EXPORTER_VERSION,
        "snapshot_id": snapshot_id,
        "market": market,
        "fields": list(QLIB_FIELDS),
        "files": file_digests,
        "symbol_stats": symbol_stats,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _structure_valid(target_dir: Path, expected_symbols: set[str], qlib_freq: str = "day") -> bool:
    if not target_dir.exists():
        return False
    cal_file = target_dir / "calendars" / f"{qlib_freq}.txt"
    inst_file = target_dir / "instruments" / "all.txt"
    features_dir = target_dir / "features"
    manifest_file = target_dir / "manifest.json"
    if not (cal_file.exists() and inst_file.exists() and features_dir.exists() and manifest_file.exists()):
        return False
    if not cal_file.read_text(encoding="utf-8").strip():
        return False

    listed_syms: set[str] = set()
    for line in inst_file.read_text(encoding="utf-8").splitlines():
        parts = line.strip().split("\t")
        if parts and parts[0]:
            listed_syms.add(parts[0].upper())
    if not expected_symbols.issubset(listed_syms):
        return False

    for sym in expected_symbols:
        sym_dir = features_dir / sym.lower()
        if not sym_dir.is_dir():
            return False
        for field in QLIB_FIELDS:
            fp = sym_dir / f"{field}.{qlib_freq}.bin"
            # At least start_index float32 (4B) + 1 value float32 (4B) = 8B
            if not fp.exists() or fp.stat().st_size < 8:
                return False
    return True


def export_snapshot_to_qlib(
    *,
    qlib_root: Path,
    snapshot_id: str,
    market: str,
    bars_df: pl.DataFrame,
    universe_df: pl.DataFrame | None,
    source_files: list[Path],
    force: bool = False,
) -> dict[str, Any]:
    """Export frozen snapshot bars for ``market`` into Qlib binary directory atomically.

    Key guarantees over legacy exporters:
    1. Preserves real raw OHLCV/VWAP and true canonical ``adj_factor`` (never hardcoded 1.0).
    2. Strong content-SHA256 + per-symbol coverage incremental cache check.
    3. Full build under ``_staging/`` followed by atomic directory rename swap.
    """
    market_norm = market.upper()
    qlib_freq = "day"
    market_bars = bars_df.filter(pl.col("market") == market_norm)
    if market_bars.is_empty():
        raise ValueError(f"No bars available for market={market_norm} in snapshot={snapshot_id}")

    # Normalize dtypes for binary serialization
    df = (
        market_bars.select(
            pl.col("symbol").cast(pl.Utf8),
            pl.col("trade_date").cast(pl.Date),
            pl.col("open").cast(pl.Float64),
            pl.col("close").cast(pl.Float64),
            pl.col("high").cast(pl.Float64),
            pl.col("low").cast(pl.Float64),
            pl.col("vwap").cast(pl.Float64),
            pl.col("volume_shares").cast(pl.Float64).alias("volume"),
            pl.col("adj_factor").cast(pl.Float64).alias("factor"),
        )
        .unique(subset=["symbol", "trade_date"], keep="last")
        .sort(["symbol", "trade_date"])
    )

    # Compute per-symbol stats for strong incremental signature verification
    symbol_stats: dict[str, dict[str, Any]] = {}
    stats_df = df.group_by("symbol").agg(
        pl.len().alias("rows"),
        pl.col("trade_date").min().alias("min_dt"),
        pl.col("trade_date").max().alias("max_dt"),
    )
    for row in stats_df.iter_rows(named=True):
        qsym = to_qlib_symbol(str(row["symbol"]))
        symbol_stats[qsym] = {
            "rows": int(row["rows"]),
            "min_dt": row["min_dt"].isoformat(),
            "max_dt": row["max_dt"].isoformat(),
        }

    source_signature = _compute_source_signature(
        snapshot_id=snapshot_id,
        market=market_norm,
        source_files=source_files,
        symbol_stats=symbol_stats,
    )

    target_dir = qlib_root / snapshot_id / EXPORTER_VERSION / market_norm / qlib_freq
    manifest_path = target_dir / "manifest.json"
    expected_symbols = set(symbol_stats.keys())

    # Check incremental cache hit
    if not force and manifest_path.exists():
        try:
            existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            existing_manifest = {}
        if (
            existing_manifest.get("source_signature") == source_signature
            and existing_manifest.get("snapshot_id") == snapshot_id
            and existing_manifest.get("market") == market_norm
            and existing_manifest.get("exporter_version") == EXPORTER_VERSION
            and _structure_valid(target_dir, expected_symbols, qlib_freq)
        ):
            return {
                "snapshot_id": snapshot_id,
                "market": market_norm,
                "exporter_version": EXPORTER_VERSION,
                "provider_uri": str(target_dir),
                "manifest_path": str(manifest_path),
                "cache_hit": True,
                "source_signature": source_signature,
                "symbol_count": len(symbol_stats),
                "calendar_days": int(existing_manifest.get("calendar_days", 0)),
                "fields": list(QLIB_FIELDS),
                "built_at": existing_manifest.get("built_at"),
            }

    # Build completely inside an isolated _staging directory first
    staging_root = qlib_root / "_staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    staging_dir = staging_root / f"{snapshot_id}_{market_norm}_{uuid.uuid4().hex[:8]}"
    (staging_dir / "calendars").mkdir(parents=True, exist_ok=True)
    (staging_dir / "instruments").mkdir(parents=True, exist_ok=True)
    (staging_dir / "features").mkdir(parents=True, exist_ok=True)

    calendars = sorted(df["trade_date"].unique().to_list())
    cal_lines = [d.isoformat() for d in calendars]
    (staging_dir / "calendars" / f"{qlib_freq}.txt").write_text(
        "\n".join(cal_lines) + "\n", encoding="utf-8"
    )
    cal_index = {d: idx for idx, d in enumerate(calendars)}

    instrument_lines: list[str] = []
    # Partition by symbol once (fast in Polars)
    for group in df.partition_by("symbol", maintain_order=True):
        raw_sym = str(group["symbol"][0])
        qsym = to_qlib_symbol(raw_sym)
        dates = group["trade_date"].to_list()
        start_d, end_d = dates[0], dates[-1]
        start_idx = cal_index[start_d]
        end_idx = cal_index[end_d]
        span = end_idx - start_idx + 1

        offsets = np.fromiter((cal_index[d] - start_idx for d in dates), dtype=np.int64, count=len(dates))

        feat_dir = staging_dir / "features" / qsym.lower()
        feat_dir.mkdir(parents=True, exist_ok=True)

        for field in QLIB_FIELDS:
            arr = np.full(span, np.nan, dtype="<f4")
            vals = group[field].to_numpy().astype("<f4", copy=False)
            arr[offsets] = vals
            payload = np.empty(span + 1, dtype="<f4")
            payload[0] = np.float32(start_idx)
            payload[1:] = arr
            payload.tofile(str(feat_dir / f"{field}.{qlib_freq}.bin"))

        instrument_lines.append(f"{qsym}\t{start_d.isoformat()}\t{end_d.isoformat()}")

    (staging_dir / "instruments" / "all.txt").write_text(
        "\n".join(sorted(instrument_lines)) + "\n", encoding="utf-8"
    )

    # Also write temporal universe instrument files matching this market's exported symbols
    if universe_df is not None and not universe_df.is_empty():
        market_raw_syms = set(df["symbol"].unique().to_list())
        uni_sub = universe_df.filter(pl.col("symbol").is_in(list(market_raw_syms)))
        if not uni_sub.is_empty():
            max_cal_date = calendars[-1]
            for u_group in uni_sub.partition_by("universe_id"):
                uid = str(u_group["universe_id"][0]).lower()
                # Collapse per-symbol effective spans across rebalance snapshots
                spans = u_group.group_by("symbol").agg(
                    pl.col("effective_from").min().alias("eff_start"),
                    pl.col("effective_to").max().alias("eff_end"),
                    pl.col("effective_to").is_null().any().alias("has_open"),
                )
                u_lines: list[str] = []
                for r in spans.iter_rows(named=True):
                    qsym = to_qlib_symbol(str(r["symbol"]))
                    s_str = r["eff_start"].isoformat()
                    e_d = max_cal_date if r["has_open"] or r["eff_end"] is None else r["eff_end"]
                    u_lines.append(f"{qsym}\t{s_str}\t{e_d.isoformat()}")
                (staging_dir / "instruments" / f"{uid}.txt").write_text(
                    "\n".join(sorted(u_lines)) + "\n", encoding="utf-8"
                )

    built_at = datetime.now(UTC).isoformat()
    manifest_data = {
        "snapshot_id": snapshot_id,
        "market": market_norm,
        "exporter_version": EXPORTER_VERSION,
        "qlib_freq": qlib_freq,
        "fields": list(QLIB_FIELDS),
        "source_signature": source_signature,
        "symbol_count": len(symbol_stats),
        "calendar_days": len(calendars),
        "symbol_stats": symbol_stats,
        "built_at": built_at,
    }
    (staging_dir / "manifest.json").write_text(
        json.dumps(manifest_data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    # Atomic directory swap: staging_dir -> target_dir
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    backup_dir: Path | None = None
    try:
        if target_dir.exists():
            backup_dir = staging_root / f"backup_{snapshot_id}_{market_norm}_{uuid.uuid4().hex[:8]}"
            target_dir.rename(backup_dir)
        staging_dir.rename(target_dir)
    except Exception:
        if backup_dir is not None and backup_dir.exists() and not target_dir.exists():
            backup_dir.rename(target_dir)
        if staging_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    finally:
        if backup_dir is not None and backup_dir.exists():
            shutil.rmtree(backup_dir, ignore_errors=True)

    return {
        "snapshot_id": snapshot_id,
        "market": market_norm,
        "exporter_version": EXPORTER_VERSION,
        "provider_uri": str(target_dir),
        "manifest_path": str(target_dir / "manifest.json"),
        "cache_hit": False,
        "source_signature": source_signature,
        "symbol_count": len(symbol_stats),
        "calendar_days": len(calendars),
        "fields": list(QLIB_FIELDS),
        "built_at": built_at,
    }
