"""Comprehensive unit tests for Q1 PIT QuantLake, 7-check QA Sentinel, Qlib export & Providers."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import numpy as np
import polars as pl
import pytest

from quant_core.lake import (
    PIT_BAR_COLUMNS,
    ST_MAIN_RULE_CHANGE_DATE,
    QuantLake,
    get_expected_price_limit_pct,
    read_qlib_feature_bin,
    to_qlib_symbol,
)
from quant_core.providers import (
    BaoStockProvider,
    MassiveProvider,
    ProviderAuthError,
    ProviderError,
    ProviderPermissionError,
    SecEdgarProvider,
    TushareProvider,
)
from quant_core.schemas import (
    AsyncJobRecord,
    AvailabilityMode,
    BoardType,
    ExperimentRecord,
    GateVerdict,
    Market,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_seeder_module():
    spec = importlib.util.spec_from_file_location(
        "seed_golden_dataset",
        REPO_ROOT / "scripts" / "seed_golden_dataset.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def golden_lake(tmp_path_factory: pytest.TempPathFactory) -> tuple[QuantLake, dict]:
    base = tmp_path_factory.mktemp("quant_lake_golden")
    root_dir = base / "quant-lake"
    db_path = base / "state" / "quant_registry.db"
    seeder = _load_seeder_module()
    summary = seeder.seed_golden_lake(root_dir=root_dir, db_path=db_path, export_qlib=True)
    lake = QuantLake(root_dir=root_dir, db_path=db_path)
    return lake, summary


# ---------------------------------------------------------------------------
# 1. Golden Seed & 7-Check QA Sentinel Full-Green Verification
# ---------------------------------------------------------------------------


def test_golden_seed_full_green_and_schema_contract(golden_lake: tuple[QuantLake, dict]) -> None:
    lake, summary = golden_lake
    snap = lake.get_latest_snapshot()
    assert snap.snapshot_id == "snap-golden-20261125-v1"
    assert snap.qa_status == "pass"
    assert snap.delisted_count == 3
    assert set(snap.markets) == {Market.CN_A, Market.US_EQ}
    assert len(lake.list_snapshots()) == 1

    # Verify standard lake directory layout exists
    for sub in (
        "raw",
        "canonical/bar_raw_1d",
        "canonical/corp_action",
        "canonical/financial_fact",
        "canonical/universe_member",
        "canonical/industry_membership",
        "canonical/instrument_lifecycle",
        "manifests/snapshots",
        "qlib",
        "quarantine",
        "artifacts",
    ):
        assert (lake.root_dir / sub).is_dir()

    # Run QA sentinel explicitly and assert 7/7 checks pass with deterministic SHA-256 fingerprint
    report1 = lake.run_qa_sentinel(snap.snapshot_id)
    report2 = lake.run_qa_sentinel(snap.snapshot_id)
    assert report1.overall_status == "pass"
    assert len(report1.checks) == 7
    assert all(c.status == "pass" for c in report1.checks)
    assert report1.failed_checks == []
    assert len(report1.fact_fingerprint) == 64
    assert report1.fact_fingerprint == report2.fact_fingerprint
    assert summary["cn_trading_days"] >= 680

    # Verify PIT bar panel returns all contract §2.1 columns and >= 50 active stocks per day in CSI500
    csi500_bars = lake.load_bars("CSI500", date(2024, 1, 2), date(2026, 11, 25))
    assert tuple(csi500_bars.columns) == PIT_BAR_COLUMNS
    min_daily_names = csi500_bars.group_by("trade_date").len()["len"].min()
    assert min_daily_names >= 50

    # US_EQ SP500_SAMPLE loads cleanly with 12 names and None static price limits
    us_bars = lake.load_bars("SP500_SAMPLE", date(2024, 1, 2), date(2026, 11, 25))
    assert us_bars["symbol"].n_unique() == 12
    assert us_bars["up_limit"].null_count() == us_bars.height


# ---------------------------------------------------------------------------
# 2. Bi-Temporal PIT Replay: Initial -> Restatement -> Withdrawal Zero Leakage
# ---------------------------------------------------------------------------


def test_pit_financial_replay_initial_restated_withdrawn_zero_leakage(
    golden_lake: tuple[QuantLake, dict],
) -> None:
    lake, _ = golden_lake
    # Target 002463.SZ has v1=100M (2025-03-28), v2=78M restated (2025-05-15), v3=withdrawn (2025-07-10)
    # Target 600183.SH has v1=200M (2025-03-28), v2=156M restated (2025-05-15), no withdrawal
    # Target 000831.SZ has delayed feed ingestion: published 2025-03-28 10:00Z, first_seen 2025-03-31 10:00Z

    def _lookup(df: pl.DataFrame, sym: str) -> pl.DataFrame:
        return df.filter(
            (pl.col("symbol") == sym)
            & (pl.col("metric_code") == "net_profit_deducted")
            & (pl.col("report_period_end") == date(2024, 12, 31))
        )

    # Cutpoint T0: before initial filing -> absent
    f_t0 = lake.load_pit_financials(datetime(2025, 3, 27, 23, 59, tzinfo=UTC))
    assert _lookup(f_t0, "002463.SZ").height == 0

    # Cutpoint T1 (2025-03-29): MUST see ONLY v1 initial value, ZERO leakage of future v2 restatement
    f_t1_public = lake.load_pit_financials(
        datetime(2025, 3, 29, 12, 0, tzinfo=UTC),
        availability_mode=AvailabilityMode.STRICT_PUBLIC,
    )
    row_002463_t1 = _lookup(f_t1_public, "002463.SZ")
    assert row_002463_t1.height == 1
    assert row_002463_t1["revision_seq"][0] == 1
    assert row_002463_t1["is_restated"][0] is False
    assert row_002463_t1["value"][0] == pytest.approx(100_000_000.0)

    # STRICT_PUBLIC vs RECORDED_REPLAY on delayed feed ingestion (000831.SZ)
    assert _lookup(f_t1_public, "000831.SZ").height == 1
    f_t1_recorded = lake.load_pit_financials(
        datetime(2025, 3, 29, 12, 0, tzinfo=UTC),
        availability_mode=AvailabilityMode.RECORDED_REPLAY,
    )
    assert _lookup(f_t1_recorded, "000831.SZ").height == 0
    f_t1_after_ingest = lake.load_pit_financials(
        datetime(2025, 4, 1, 0, 0, tzinfo=UTC),
        availability_mode=AvailabilityMode.RECORDED_REPLAY,
    )
    assert _lookup(f_t1_after_ingest, "000831.SZ").height == 1

    # Cutpoint T2 (2025-05-20): MUST see v2 restated value (78M / 156M)
    f_t2 = lake.load_pit_financials(datetime(2025, 5, 20, 12, 0, tzinfo=UTC))
    row_002463_t2 = _lookup(f_t2, "002463.SZ")
    assert row_002463_t2.height == 1
    assert row_002463_t2["revision_seq"][0] == 2
    assert row_002463_t2["is_restated"][0] is True
    assert row_002463_t2["value"][0] == pytest.approx(78_000_000.0)

    row_600183_t2 = _lookup(f_t2, "600183.SH")
    assert row_600183_t2["value"][0] == pytest.approx(156_000_000.0)

    # Cutpoint T3 (2025-07-15): v3 withdrew 002463.SZ -> MUST be excluded (absent), NOT fall back to v1/v2!
    f_t3 = lake.load_pit_financials(datetime(2025, 7, 15, 12, 0, tzinfo=UTC))
    assert _lookup(f_t3, "002463.SZ").height == 0
    # Meanwhile 600183.SH (not withdrawn) stays at v2 restated value
    assert _lookup(f_t3, "600183.SH")["value"][0] == pytest.approx(156_000_000.0)


# ---------------------------------------------------------------------------
# 3. Corporate Action Continuity, Price Limit 2026-07-06 Boundary & QA Failure Detection
# ---------------------------------------------------------------------------


def test_corp_action_continuity_and_20260706_st_rule_boundary(
    golden_lake: tuple[QuantLake, dict],
) -> None:
    lake, _ = golden_lake

    # Verify 2-for-1 split on 002463.SZ (2025-05-22) & 10-for-1 split on NVDA (2024-06-10)
    ca_df = lake.load_corp_actions()
    assert ca_df.height >= 10

    bars_all = lake.load_bars("ALL")
    for sym, ex_d in [("002463.SZ", date(2025, 5, 22)), ("600519.SH", date(2025, 6, 19)), ("NVDA", date(2024, 6, 10))]:
        sub = bars_all.filter(pl.col("symbol") == sym).sort("trade_date")
        ex_rows = sub.filter(pl.col("trade_date") == ex_d)
        prev_rows = sub.filter(pl.col("trade_date") < ex_d).tail(1)
        assert ex_rows.height == 1 and prev_rows.height == 1

        ca_row = ca_df.filter((pl.col("symbol") == sym) & (pl.col("ex_date") == ex_d))
        cash_div = float(ca_row["cash_dividend_per_share"][0])
        share_mult = float(ca_row["split_ratio"][0]) + float(ca_row["bonus_share_ratio"][0])

        prev_close = float(prev_rows["close"][0])
        prev_adj = float(prev_rows["adj_factor"][0])
        ex_close = float(ex_rows["close"][0])
        ex_adj = float(ex_rows["adj_factor"][0])

        ex_ref = (prev_close - cash_div) / share_mult
        r_adj = (ex_close * ex_adj) / (prev_close * prev_adj) - 1.0
        r_true = ex_close / ex_ref - 1.0
        assert abs(r_adj - r_true) < 1e-5

    # Verify 2026-07-06 Shanghai/Shenzhen Main Board ST rule transition (5% -> 10%)
    assert get_expected_price_limit_pct(Market.CN_A, BoardType.MAIN, True, date(2026, 7, 3)) == 0.05
    assert get_expected_price_limit_pct(Market.CN_A, BoardType.MAIN, True, ST_MAIN_RULE_CHANGE_DATE) == 0.10
    assert get_expected_price_limit_pct(Market.CN_A, BoardType.CHINEXT, True, date(2026, 7, 3)) == 0.20
    assert get_expected_price_limit_pct(Market.CN_A, BoardType.BSE, False, date(2026, 7, 3)) == 0.30
    assert get_expected_price_limit_pct(Market.US_EQ, BoardType.NASDAQ, False, date(2026, 7, 6)) is None

    st_bars = bars_all.filter(pl.col("symbol") == "600157.SH").sort("trade_date")
    pre_rule = st_bars.filter(pl.col("trade_date") < ST_MAIN_RULE_CHANGE_DATE).tail(1)
    post_rule = st_bars.filter(pl.col("trade_date") >= ST_MAIN_RULE_CHANGE_DATE).head(1)
    assert pre_rule["is_st"][0] is True and post_rule["is_st"][0] is True
    pre_pct = (float(pre_rule["up_limit"][0]) / float(pre_rule["pre_close"][0])) - 1.0
    post_pct = (float(post_rule["up_limit"][0]) / float(post_rule["pre_close"][0])) - 1.0
    assert abs(pre_pct - 0.05) < 0.004
    assert abs(post_pct - 0.10) < 0.004


def test_qa_sentinel_catches_injected_anomalies(
    golden_lake: tuple[QuantLake, dict],
    tmp_path: Path,
) -> None:
    """Prove the 7 QA checks actively fail when real data corruption is introduced."""
    src_lake, _ = golden_lake
    sid = src_lake.get_latest_snapshot().snapshot_id
    manifest = src_lake._read_manifest(sid)
    tables = {
        tbl: pl.read_parquet(src_lake.root_dir / meta["path"])
        for tbl, meta in manifest["tables"].items()
    }

    # Corrupt 3 independent dimensions:
    # 1) OHLCV geometry breach (high < low)
    corrupt_bars = tables["bar_raw_1d"].with_columns(
        pl.when((pl.col("symbol") == "600519.SH") & (pl.col("trade_date") == date(2025, 1, 2)))
        .then(pl.col("low") - 10.0)
        .otherwise(pl.col("high"))
        .alias("high")
    )
    # 2) PIT timestamp inversion (ann_date < report_period_end)
    corrupt_fin = tables["financial_fact"].with_columns(
        pl.when(pl.col("symbol") == "600519.SH")
        .then(pl.col("report_period_end") - pl.duration(days=5))
        .otherwise(pl.col("ann_date"))
        .alias("ann_date")
    )
    # 3) Industry overlap (duplicate overlapping SW2021 row)
    overlap_row = tables["industry_membership"].head(1)
    corrupt_ind = pl.concat([tables["industry_membership"], overlap_row])

    bad_lake = QuantLake(root_dir=tmp_path / "bad-lake")
    bad_snap = bad_lake.publish_snapshot(
        snapshot_id="snap-corrupt-test",
        as_of=datetime(2026, 11, 25, 8, 30, tzinfo=UTC),
        tables={
            **tables,
            "bar_raw_1d": corrupt_bars,
            "financial_fact": corrupt_fin,
            "industry_membership": corrupt_ind,
        },
        run_qa=True,
    )
    assert bad_snap.qa_status == "fail"
    report = bad_lake.run_qa_sentinel("snap-corrupt-test")
    assert report.overall_status == "fail"
    assert "QA-01-OHLCV-GEOM" in report.failed_checks
    assert "QA-03-PIT-FIN-TS" in report.failed_checks
    assert "QA-06-INDUSTRY-OVERLAP" in report.failed_checks


# ---------------------------------------------------------------------------
# 4. Delisted Retention, Dynamic Constituents & Historical Shenwan Industry Switch
# ---------------------------------------------------------------------------


def test_delisted_retention_and_historical_shenwan_industry_switch(
    golden_lake: tuple[QuantLake, dict],
) -> None:
    lake, _ = golden_lake

    # 600677.SH delisted on 2024-11-15: present before, absent after
    members_before = lake.load_universe_constituents("CSI500", date(2024, 9, 1))
    members_after = lake.load_universe_constituents("CSI500", date(2025, 1, 15))
    syms_before = {m.symbol for m in members_before}
    syms_after = {m.symbol for m in members_after}
    assert "600677.SH" in syms_before
    assert "600677.SH" not in syms_after
    assert abs(sum(m.weight or 0.0 for m in members_before) - 1.0) <= 0.005

    bars_600677 = lake.load_bars("CSI500", date(2024, 1, 2), date(2026, 11, 25)).filter(
        pl.col("symbol") == "600677.SH"
    )
    assert bars_600677.height > 150
    assert bars_600677["trade_date"].max() <= date(2024, 11, 15)

    # 000988.SZ switched Shenwan L1 industry from 机械设备 -> 电子 on 2025-07-01
    bars_000988 = lake.load_bars("CSI500", date(2025, 6, 20), date(2025, 7, 10)).filter(
        pl.col("symbol") == "000988.SZ"
    )
    ind_before = set(bars_000988.filter(pl.col("trade_date") < date(2025, 7, 1))["industry_l1"].to_list())
    ind_after = set(bars_000988.filter(pl.col("trade_date") >= date(2025, 7, 1))["industry_l1"].to_list())
    assert ind_before == {"机械设备"}
    assert ind_after == {"电子"}


# ---------------------------------------------------------------------------
# 5. Qlib Binary Atomic Export, Incremental Cache Hit & Round-Trip Verification
# ---------------------------------------------------------------------------


def test_qlib_bin_atomic_export_incremental_cache_and_roundtrip(
    golden_lake: tuple[QuantLake, dict],
) -> None:
    lake, summary = golden_lake
    sid = lake.get_latest_snapshot().snapshot_id

    first_export = summary["qlib_exports"]["CN_A"]
    assert first_export["cache_hit"] is False
    assert first_export["symbol_count"] == 60

    # Immediate re-export with unchanged snapshot MUST hit incremental signature cache
    second_export = lake.export_qlib_bin(sid, market="CN_A")
    assert second_export["cache_hit"] is True
    assert second_export["source_signature"] == first_export["source_signature"]

    # Force rebuild uses _staging atomic swap and preserves identical signature
    forced_export = lake.export_qlib_bin(sid, market="CN_A", force=True)
    assert forced_export["cache_hit"] is False
    assert forced_export["source_signature"] == first_export["source_signature"]

    # Round-trip read binary files for 002463.SZ (which underwent 2-for-1 split -> adj_factor != 1.0!)
    provider_uri = Path(forced_export["provider_uri"])
    assert (provider_uri / "calendars" / "day.txt").exists()
    assert (provider_uri / "instruments" / "all.txt").exists()
    assert (provider_uri / "instruments" / "csi500.txt").exists()
    assert (provider_uri / "manifest.json").exists()

    qsym = to_qlib_symbol("002463.SZ")
    start_idx, factor_vals = read_qlib_feature_bin(provider_uri / "features" / qsym.lower() / "factor.day.bin")
    _, close_vals = read_qlib_feature_bin(provider_uri / "features" / qsym.lower() / "close.day.bin")
    assert start_idx == 0
    assert float(factor_vals[0]) == pytest.approx(1.0, rel=1e-5)
    # After 2-for-1 split on 2025-05-22, factor MUST be ~2.0 (NOT hardcoded 1.0!)
    assert float(factor_vals[-1]) == pytest.approx(2.0, rel=1e-4)

    # Verify raw close round-trip matches canonical Parquet
    canon_002463 = (
        lake.load_bars("CN_ALL")
        .filter(pl.col("symbol") == "002463.SZ")
        .sort("trade_date")
    )
    assert float(close_vals[-1]) == pytest.approx(float(canon_002463["close"][-1]), rel=1e-5)
    assert not np.isnan(close_vals[-1])


# ---------------------------------------------------------------------------
# 6. Providers: Explicit Auth/Permission Refusal & Zero Synthetic Fallback
# ---------------------------------------------------------------------------


def test_providers_refuse_missing_tokens_with_zero_synthetic_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.delenv("SEC_EDGAR_USER_AGENT", raising=False)
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)

    # Tushare missing token -> explicit ProviderAuthError (never fake data)
    ts = TushareProvider()
    with pytest.raises(ProviderAuthError, match="TUSHARE_TOKEN"):
        ts.fetch_daily_bars(trade_date=date(2026, 11, 25))

    # SEC EDGAR missing User-Agent -> explicit ProviderAuthError
    edgar = SecEdgarProvider()
    with pytest.raises(ProviderAuthError, match="SEC_EDGAR_USER_AGENT"):
        edgar.fetch_companyfacts("0001045810")

    # SEC EDGAR forbids rate > 10 req/s
    with pytest.raises(ValueError, match="10.0"):
        SecEdgarProvider(user_agent="ProQuant test@example.com", rate_per_sec=15.0)

    # Massive missing API key -> explicit ProviderAuthError
    massive = MassiveProvider()
    with pytest.raises(ProviderAuthError, match="MASSIVE_API_KEY"):
        massive.fetch_daily_bars(symbols=["NVDA"], start_date=date(2026, 11, 1), end_date=date(2026, 11, 25))

    # BaoStock missing SDK -> explicit ProviderError (never fake data)
    bs = BaoStockProvider()
    with pytest.raises(ProviderError):
        bs.fetch_daily_bars(symbols=["600519.SH"], start_date=date(2026, 11, 1), end_date=date(2026, 11, 25))


def test_providers_mock_http_parsing_and_permission_errors() -> None:
    # Mock Tushare HTTP transport: daily unit conversion + permission error mapping
    def _ts_handler(request: httpx.Request) -> httpx.Response:
        body = request.read().decode("utf-8")
        if "income_vip" in body:
            return httpx.Response(200, json={"code": 40203, "msg": "抱歉，您没有接口访问权限(需5000积分)"})
        return httpx.Response(
            200,
            json={
                "code": 0,
                "msg": "",
                "data": {
                    "fields": ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "vol", "amount"],
                    "items": [["600519.SH", "20261125", 1600.0, 1620.0, 1590.0, 1610.0, 1595.0, 25000.0, 4025000.0]],
                },
            },
        )

    ts_client = httpx.Client(transport=httpx.MockTransport(_ts_handler))
    ts = TushareProvider(token="test-token-123", http_client=ts_client)
    df = ts.fetch_daily_bars(trade_date=date(2026, 11, 25))
    assert df.height == 1
    assert df["volume_shares"][0] == 2_500_000  # 25,000 hands * 100 shares
    assert df["amount_ccy"][0] == pytest.approx(4_025_000_000.0)  # thousands * 1000

    with pytest.raises(ProviderPermissionError, match="积分"):
        ts.fetch_income_vip(period="20260930")

    # Mock SEC EDGAR XBRL parsing -> preserves original filed vs amended revision order
    def _edgar_handler(request: httpx.Request) -> httpx.Response:
        assert "ProQuantTest" in request.headers.get("User-Agent", "")
        return httpx.Response(
            200,
            json={
                "facts": {
                    "us-gaap": {
                        "Revenues": {
                            "units": {
                                "USD": [
                                    {"form": "10-Q", "end": "2026-06-30", "filed": "2026-08-01", "val": 1000, "accn": "0001"},
                                    {"form": "10-Q/A", "end": "2026-06-30", "filed": "2026-09-10", "val": 980, "accn": "0002"},
                                ]
                            }
                        }
                    }
                }
            },
        )

    edgar_client = httpx.Client(transport=httpx.MockTransport(_edgar_handler))
    edgar = SecEdgarProvider(user_agent="ProQuantTest qa@example.com", http_client=edgar_client)
    facts_df = edgar.extract_pit_financial_facts(cik="1045810", symbol="NVDA")
    assert facts_df.height == 2
    assert facts_df["revision_seq"].to_list() == [1, 2]
    assert facts_df["is_restated"].to_list() == [False, True]
    assert facts_df["value"].to_list() == [1000.0, 980.0]

    # Mock Massive HTTP transport -> verifies adjusted=false query param
    def _massive_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("adjusted") == "false"
        return httpx.Response(
            200,
            json={
                "results": [
                    {"t": 1795564800000, "o": 140.0, "h": 145.0, "l": 139.0, "c": 144.0, "vw": 142.5, "v": 1000000}
                ]
            },
        )

    massive_client = httpx.Client(transport=httpx.MockTransport(_massive_handler))
    massive = MassiveProvider(api_key="test-key", http_client=massive_client)
    m_df = massive.fetch_daily_bars(symbols=["NVDA"], start_date=date(2026, 11, 24), end_date=date(2026, 11, 25))
    assert m_df.height == 1
    assert m_df["close"][0] == pytest.approx(144.0)


# ---------------------------------------------------------------------------
# 7. Experiment Registry, Artifacts & Async Jobs Persistence
# ---------------------------------------------------------------------------


def test_experiment_registry_artifacts_and_jobs_roundtrip(golden_lake: tuple[QuantLake, dict]) -> None:
    lake, _ = golden_lake

    # 5 pre-seeded comparison experiments exist
    exps = lake.list_experiments(limit=20)
    exp_ids = {e.experiment_id for e in exps}
    assert {"EXP-0419", "EXP-0425", "EXP-0427", "EXP-0424", "RDA-0042"}.issubset(exp_ids)

    # Save & read back a new experiment
    new_exp = ExperimentRecord(
        experiment_id="EXP-0999",
        family_id="FAM-TEST",
        name="Test Quant Model",
        model_type="Ridge",
        feature_summary="12 因子 · 测试",
        universe_id="CSI500",
        snapshot_id=lake.get_latest_snapshot().snapshot_id,
        trial_count_m=5,
        is_sharpe=2.10,
        oos_sharpe=1.85,
        oos_is_ratio=0.881,
        cpcv_pbo=0.08,
        dsr=0.97,
        oos_excess_ann=11.5,
        verdict=GateVerdict.PASS,
        status_badge="PASS",
        created_at=datetime(2026, 11, 25, 10, 0, tzinfo=UTC),
    )
    lake.save_experiment(new_exp)
    loaded_ids = [e.experiment_id for e in lake.list_experiments(limit=20)]
    assert "EXP-0999" in loaded_ids

    # Save & reload both Parquet and JSON artifacts
    df_art = lake.save_artifact("run-test-01", "pit_panel", pl.DataFrame({"a": [1, 2], "b": [3.0, 4.0]}))
    assert df_art.uri.startswith("quant-artifact://")
    assert df_art.format == "parquet"
    assert df_art.rows == 2
    reloaded_df = lake.load_artifact(df_art.uri)
    assert isinstance(reloaded_df, pl.DataFrame) and reloaded_df.height == 2

    json_art = lake.save_artifact("run-test-01", "summary", {"ic": 0.048, "status": "ok"})
    assert json_art.format == "json"
    assert lake.load_artifact(json_art.uri) == {"ic": 0.048, "status": "ok"}

    # Save & retrieve async job
    now = datetime(2026, 11, 25, 10, 5, tzinfo=UTC)
    job = AsyncJobRecord(
        job_id="job-test-01",
        job_type="backtest",
        status="running",
        created_at=now,
        updated_at=now,
        snapshot_id=lake.get_latest_snapshot().snapshot_id,
        progress_pct=45,
        summary={"step": "matching"},
        artifact_uri=json_art.uri,
    )
    lake.save_job(job)
    fetched = lake.get_job("job-test-01")
    assert fetched is not None
    assert fetched.status == "running"
    assert fetched.progress_pct == 45
    assert fetched.artifact_uri == json_art.uri
