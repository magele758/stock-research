"""Comprehensive tests for V3.0 Glossary SSOT and QuantRegistry persistent research ledger."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from quant_core.glossary import (
    GlossaryRegistry,
    get_glossary_registry,
    get_term,
    list_terms,
    render_bot_prompt,
)
from quant_core.lake.registry import (
    QuantRegistry,
    compute_ast_fingerprint,
    compute_expr_hash,
    extract_keywords,
    jaccard_similarity,
)
from quant_core.schemas import (
    INSTITUTIONAL_GATES,
    DatasetSnapshot,
    DimensionBaselineRecord,
    ExperimentRecord,
    FormalProofResult,
    GateVerdict,
    GlossaryCategory,
    Market,
    ProofCheckItem,
)

REQUIRED_TERM_IDS = (
    "rank_ic",
    "icir",
    "nw_t_stat",
    "ic_win_rate",
    "ic_decay",
    "half_life",
    "monotonicity",
    "long_short",
    "top_excess",
    "turnover_oneway",
    "max_corr_library",
    "mad_winsorize",
    "industry_neutral",
    "size_neutral",
    "barra_orthogonal",
    "zscore_rank",
    "residual_exposure",
    "ast_safe",
    "sharpe_is_oos",
    "oos_is_ratio",
    "cpcv",
    "purge",
    "embargo",
    "pbo",
    "dsr",
    "trial_count_m",
    "walk_forward",
    "negative_paths",
    "barra_cne6",
    "style_exposure",
    "industry_active_dev",
    "tracking_error",
    "information_ratio",
    "turnover_lambda",
    "enc",
    "ledoit_wolf",
    "psd",
    "risk_decomposition",
    "opt_objective",
    "solve_status",
    "capacity_frontier",
    "t_plus_1",
    "pov",
    "sqrt_impact",
    "stamp_duty",
    "gross_vs_net",
    "brinson_allocation",
    "brinson_selection",
    "brinson_interaction",
    "closure_error",
    "barra_attribution",
    "max_drawdown",
    "calmar",
    "excess_drawdown",
    "point_in_time",
    "survivorship_bias",
    "look_ahead_bias",
    "corp_action_adj",
    "ohlcv_geometry",
    "historical_constituents",
    "sw_industry_pit",
    "price_limit_rules",
    "coverage",
    "freshness",
    "fact_fingerprint",
    "implementation_shortfall",
    "perold_is",
    "open_gap",
    "liquidity_impact",
    "limit_queue",
    "fill_rate",
    "crowding_index",
    "hist_percentile",
    "kill_switch",
    "authority_gate",
    "regime_shift",
    "newey_west_hac",
    "bootstrap_ci",
    "bh_fdr",
    "prefix_invariance",
    "ast_causal_proof",
    "conservation_law",
    "param_cliff",
    "regime_classification",
    "dimension_dependence",
)


# ---------------------------------------------------------------------------
# 1. Glossary SSOT Registry Tests
# ---------------------------------------------------------------------------


def test_glossary_completeness_domains_and_zero_dangling_references() -> None:
    resp = list_terms()
    assert resp.count >= 64
    assert resp.count == len(resp.terms) == 85

    by_id = {t.term_id: t for t in resp.terms}
    for required in REQUIRED_TERM_IDS:
        assert required in by_id, f"Missing required glossary term_id: {required}"

    # All 7 domains and all 7 categories covered
    domains = {t.domain for t in resp.terms}
    categories = {t.category for t in resp.terms}
    assert domains == {"Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "MATH"}
    assert categories == set(GlossaryCategory)

    # Zero dangling related_terms and non-empty core fields
    for term in resp.terms:
        assert term.short_tip.strip()
        assert term.formula_latex.strip()
        assert term.formula_lines
        assert term.institutional_threshold.strip()
        assert len(term.common_pitfalls) >= 2
        assert term.bot_prompt_template.strip()
        for rel in term.related_terms:
            assert rel in by_id, f"Dangling related_term {rel!r} on {term.term_id!r}"


def test_glossary_alignment_with_institutional_gates() -> None:
    checks = {
        "rank_ic": f"{INSTITUTIONAL_GATES['rank_ic_min']:.2f}",
        "icir": f"{INSTITUTIONAL_GATES['icir_ann_min']:.2f}",
        "nw_t_stat": f"{INSTITUTIONAL_GATES['nw_t_stat_min']:.2f}",
        "turnover_oneway": f"{int(INSTITUTIONAL_GATES['daily_turnover_max'] * 100)}%",
        "max_corr_library": f"{INSTITUTIONAL_GATES['max_corr_library']:.2f}",
        "monotonicity": f"{INSTITUTIONAL_GATES['monotonicity_min']:.2f}",
        "coverage": f"{int(INSTITUTIONAL_GATES['min_coverage'] * 100)}%",
        "pbo": f"{INSTITUTIONAL_GATES['cpcv_pbo_max']:.2f}",
        "dsr": f"{INSTITUTIONAL_GATES['dsr_min']:.2f}",
        "oos_is_ratio": f"{INSTITUTIONAL_GATES['oos_is_ratio_min']:.2f}",
        "bh_fdr": f"{INSTITUTIONAL_GATES['bh_fdr_q_max']:.2f}",
        "param_cliff": f"{int(INSTITUTIONAL_GATES['param_cliff_drop_max'] * 100)}%",
        "prefix_invariance": "1e-10",
        "closure_error": f"{INSTITUTIONAL_GATES['closure_error_bp_max']:.1f}",
        "style_exposure": f"{INSTITUTIONAL_GATES['barra_style_hard_limit']:.2f}",
        "dimension_dependence": f"{int(INSTITUTIONAL_GATES['single_dim_dominance_max'] * 100)}%",
    }
    for term_id, expected_substr in checks.items():
        term = get_term(term_id)
        assert term is not None
        assert expected_substr in term.institutional_threshold, (
            f"Term {term_id} threshold {term.institutional_threshold!r} does not contain {expected_substr!r}"
        )


def test_glossary_lookup_filtering_aliases_and_bot_prompt_rendering() -> None:
    assert isinstance(get_glossary_registry(), GlossaryRegistry)
    # Alias resolution
    assert get_term("deflated_sharpe_ratio") == get_term("dsr")
    assert get_term("cpcv_pbo") == get_term("pbo")
    assert get_term("prefix_invariance_proof") == get_term("prefix_invariance")
    assert get_term("nonexistent_xyz_term") is None

    # Filtering by category, domain, and search query
    val_terms = list_terms(category="validation")
    assert val_terms.count == 10
    assert all(t.category == GlossaryCategory.VALIDATION for t in val_terms.terms)

    q1_terms = list_terms(domain="Q1")
    assert q1_terms.count == 11
    assert all(t.domain == "Q1" for t in q1_terms.terms)

    q_dsr = list_terms(q="DSR")
    assert q_dsr.count >= 1
    assert q_dsr.terms[0].term_id == "dsr"

    q_zh = list_terms(q="前视")
    assert q_zh.count >= 2

    # Bot prompt rendering: full context, partial context, and empty context
    full_prompt = render_bot_prompt(
        "dsr",
        context={
            "snapshot_id": "snap-test-01",
            "universe_id": "CSI500",
            "study_id": "STD-0001",
            "value": 0.91,
            "trial_count_m": 25,
            "verdict": "WARN",
        },
    )
    assert "snap-test-01" in full_prompt
    assert "0.91" in full_prompt
    assert "25" in full_prompt
    assert "verdict=WARN" in full_prompt

    minimal_prompt = render_bot_prompt("prefix_invariance")
    assert "前缀不变性" in minimal_prompt
    assert "{" not in minimal_prompt

    with pytest.raises(KeyError):
        render_bot_prompt("unknown_term_xyz")


# ---------------------------------------------------------------------------
# 2. QuantRegistry V3 Persistent Knowledge Base Tests
# ---------------------------------------------------------------------------


def _make_proof(passed: bool) -> FormalProofResult:
    return FormalProofResult(
        verification_id="FV-test-001",
        target_id="STD-0001",
        universe_id="CSI500",
        snapshot_id="snap-test-01",
        as_of="2026-11-25",
        passed=passed,
        verdict=GateVerdict.PASS if passed else GateVerdict.REJECT,
        proofs_passed=2 if passed else 0,
        proofs_total=2,
        max_prefix_drift=0.0 if passed else 0.12,
        blocking_failures=[] if passed else ["T1.PREFIX_INVARIANCE"],
        checks=[
            ProofCheckItem(
                check_id="T1.AST_CAUSAL",
                suite="T1",
                label="表达式因果性",
                status="pass" if passed else "fail",
                statistic="0" if passed else "1",
                threshold="0",
                error_metric="unsafe_nodes",
                reason_code="OK" if passed else "LOOKAHEAD_AST_VIOLATION",
                n_observed=1,
            )
        ],
        proof_sha256="a" * 64,
    )


def test_registry_v3_tables_coexist_with_v2_and_idempotent_init(tmp_path: Path) -> None:
    db_path = tmp_path / "state" / "quant_registry.db"
    reg1 = QuantRegistry(db_path=db_path)
    reg2 = QuantRegistry(db_path=db_path)  # idempotent re-init

    with sqlite3.connect(db_path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    expected = {
        "snapshots",
        "experiments",
        "jobs",
        "artifacts",
        "qa_runs",
        "ingest_batches",
        "research_studies",
        "dimension_baselines",
        "falsified_tombstones",
        "study_events",
        "research_notes",
    }
    assert expected <= tables

    # V2 roundtrip remains 100% intact
    now = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)
    snap = DatasetSnapshot(
        snapshot_id="snap-v2-compat",
        as_of=now,
        markets=[Market.CN_A],
        created_at=now,
        manifest_sha256="b" * 64,
        table_counts={"bar_raw_1d": 1000},
        qa_status="pass",
    )
    reg1.save_snapshot(snap)
    assert reg2.get_snapshot("snap-v2-compat") is not None

    exp = ExperimentRecord(
        experiment_id="EXP-COMPAT-1",
        family_id="FAM-1",
        name="Compat Exp",
        model_type="Ridge",
        feature_summary="alpha_vp_corr_20d",
        universe_id="CSI500",
        snapshot_id="snap-v2-compat",
        trial_count_m=10,
        is_sharpe=1.8,
        oos_sharpe=1.3,
        oos_is_ratio=0.72,
        cpcv_pbo=0.12,
        dsr=0.97,
        oos_excess_ann=8.5,
        verdict=GateVerdict.PASS,
        status_badge="PASS",
        created_at=now,
    )
    reg1.save_experiment(exp)
    assert reg2.get_experiment("EXP-COMPAT-1") is not None


def test_study_crud_recursive_lineage_and_cross_study_diff(tmp_path: Path) -> None:
    reg = QuantRegistry(db_path=tmp_path / "registry.db")

    # Root -> Child -> Grandchild + Sibling
    s1 = reg.create_study(
        study_id="STD-0001",
        title="量价背离根研究",
        snapshot_id="snap-1",
        hypothesis="20D 量价负相关反转效应",
        family_id="FAM-VP",
        topic_tags=["volume_price", "csi500"],
        factor_id="alpha_vp_corr_20d",
        expression="-pl.rolling_corr(close.pct_change(), volume_shares.log1p().diff(), window_size=20)",
        idempotency_key="idem-std-1",
    )
    assert s1.study_id == "STD-0001"
    # Idempotency returns identical study
    s1_dup = reg.create_study(
        title="Duplicate ignored",
        snapshot_id="snap-1",
        idempotency_key="idem-std-1",
    )
    assert s1_dup.study_id == "STD-0001"

    reg.finalize_study(
        "STD-0001",
        status="COMPLETED",
        verdict="VERIFIED",
        formal_proof=_make_proof(passed=True),
        metrics_summary={"rank_ic_mean": 0.035, "oos_sharpe": 1.25, "dsr": 0.96, "pbo": 0.14, "net_excess_ann_pct": 6.0},
    )

    s2 = reg.create_study(
        study_id="STD-0002",
        parent_study_id="STD-0001",
        title="量价背离 V2 降换手",
        snapshot_id="snap-1",
        hypothesis="延长窗口至 40D 降低换手",
        topic_tags=["volume_price", "low_turnover"],
        factor_id="alpha_vp_corr_40d",
        expression="-pl.rolling_corr(close.pct_change(), volume_shares.log1p().diff(), window_size=40)",
    )
    assert s2.family_id == "FAM-VP"  # inherited from parent
    reg.finalize_study(
        "STD-0002",
        status="COMPLETED",
        verdict="VERIFIED",
        formal_proof=_make_proof(passed=True),
        metrics_summary={"rank_ic_mean": 0.041, "oos_sharpe": 1.55, "dsr": 0.98, "pbo": 0.09, "net_excess_ann_pct": 8.2},
    )

    s3 = reg.create_study(
        study_id="STD-0003",
        parent_study_id="STD-0002",
        title="量价背离 V3 过拟合淘汰",
        snapshot_id="snap-1",
        factor_id="alpha_vp_v3",
        expression="close.shift(-1) / close - 1.0",
    )
    assert s3.family_id == "FAM-VP"
    reg.finalize_study(
        "STD-0003",
        status="FALSIFIED",
        verdict="FALSIFIED",
        formal_proof=_make_proof(passed=False),
        metrics_summary={"rank_ic_mean": 0.01, "oos_sharpe": -0.2, "dsr": 0.40, "pbo": 0.55},
        failure_code="LOOKAHEAD_PREFIX_VIOLATION",
    )

    # Recursive lineage from grandchild reaches full tree
    lineage = reg.get_study_lineage("STD-0003")
    assert [n.study_id for n in lineage] == ["STD-0001", "STD-0002", "STD-0003"]

    # Cross-study diff
    diffs = reg.compare_study_lineage("STD-0002")
    by_id = {d["study_id"]: d for d in diffs}
    assert by_id["STD-0002"]["delta_vs_parent"]["oos_sharpe"] == pytest.approx(0.30, abs=1e-5)
    assert by_id["STD-0002"]["delta_vs_parent"]["net_excess_ann_pct"] == pytest.approx(2.2, abs=1e-5)

    # Filter queries
    assert len(reg.list_studies(verdict="VERIFIED")) == 2
    assert len(reg.list_studies(tag="low_turnover")) == 1
    assert len(reg.list_studies(q="降换手")) == 1
    assert reg.count_family_trials("FAM-VP", "snap-1") == 3

    # Notes roundtrip
    note = reg.add_research_note(
        "STD-0002",
        author="quant-lead",
        content_md="V2 换手显著下降且 DSR 提升。",
        linked_terms=["dsr", "turnover_oneway"],
    )
    assert note["note_id"].startswith("note-")
    loaded_s2 = reg.get_study("STD-0002")
    assert loaded_s2 is not None
    assert len(loaded_s2.notes) == 1
    assert loaded_s2.notes[0]["linked_terms"] == ["dsr", "turnover_oneway"]


def test_finalize_study_hard_proof_gate_blocks_unverified_promotion(tmp_path: Path) -> None:
    reg = QuantRegistry(db_path=tmp_path / "registry.db")
    reg.create_study(
        study_id="STD-GATE",
        title="Proof Gate Test",
        snapshot_id="snap-1",
        factor_id="alpha_test",
        expression="close.pct_change()",
    )

    # 1) No formal proof at all -> MUST raise exact ValueError
    with pytest.raises(ValueError, match="ProofGateViolation: cannot mark VERIFIED when formal proof failed"):
        reg.finalize_study("STD-GATE", status="COMPLETED", verdict="VERIFIED")

    # 2) Failed formal proof -> MUST raise exact ValueError
    with pytest.raises(ValueError, match="ProofGateViolation: cannot mark VERIFIED when formal proof failed"):
        reg.finalize_study(
            "STD-GATE",
            status="COMPLETED",
            verdict="VERIFIED",
            formal_proof=_make_proof(passed=False),
        )

    # 3) Direct update_study bypass attempt -> ALSO blocked
    with pytest.raises(ValueError, match="ProofGateViolation: cannot mark VERIFIED when formal proof failed"):
        reg.update_study("STD-GATE", verdict="VERIFIED")

    # 4) Passing formal proof -> succeeds
    ok = reg.finalize_study(
        "STD-GATE",
        status="COMPLETED",
        verdict="VERIFIED",
        formal_proof=_make_proof(passed=True),
    )
    assert ok.verdict == "VERIFIED"
    assert ok.status == "COMPLETED"


def test_three_layer_tombstone_deduplication_and_memory_line(tmp_path: Path) -> None:
    reg = QuantRegistry(db_path=tmp_path / "registry.db")
    expr_orig = "-pl.rolling_corr(close.pct_change(), volume_shares.log1p().diff(), window_size=20)"
    tomb = reg.save_tombstone(
        factor_id="alpha_vp_corr_20d",
        expression=expr_orig,
        universe_id="CSI500",
        snapshot_id="snap-1",
        failure_code="HIGH_PBO",
        failure_metrics={"dsr": 0.61, "pbo": 0.44, "oos_is_ratio": 0.31},
        lesson_summary="20D 量价背离在微盘反转期换手过高导致 PBO=0.44 超阈",
        hypothesis_text="CSI500 量价背离 20D 微盘反转高换手失败",
    )
    assert tomb.tombstone_id.startswith("TOMB-")
    assert tomb.memory_line.startswith("[FALSIFIED] alpha_vp_corr_20d |")
    assert "HIGH_PBO" in tomb.memory_line
    assert "DSR=0.610 PBO=0.440 OOS/IS=0.310" in tomb.memory_line

    # Tier 1: exact expr_hash (ignoring whitespace differences)
    expr_spaces = "-pl.rolling_corr( close.pct_change() ,  volume_shares.log1p().diff() , window_size = 20 )"
    assert compute_expr_hash(expr_orig) == compute_expr_hash(expr_spaces)
    scan1 = reg.check_tombstone_duplicates(expression=expr_spaces)
    assert scan1["exact_hit"] is True
    assert scan1["matches"][0]["tier"] == "exact_expr_hash"
    assert scan1["matches"][0]["severity"] == "blocking"

    # Tier 2: window parameter tweak (20 -> 21, plus alias volume -> volume_shares, window -> window_size)
    expr_tweaked = "-pl.rolling_corr(close.pct_change(), volume.log1p().diff(), window=21)"
    assert compute_expr_hash(expr_orig) != compute_expr_hash(expr_tweaked)
    assert compute_ast_fingerprint(expr_orig) == compute_ast_fingerprint(expr_tweaked)
    scan2 = reg.check_tombstone_duplicates(expression=expr_tweaked)
    assert scan2["exact_hit"] is False
    assert scan2["ast_hit"] is True
    assert scan2["matches"][0]["tier"] == "ast_fingerprint"
    assert scan2["matches"][0]["severity"] == "high"

    # Tier 3: keyword Jaccard >= 0.45 on a structurally different expression
    kw_a = extract_keywords("CSI500 量价背离 微盘反转 高换手")
    kw_b = extract_keywords("CSI500 量价背离 20D 微盘反转高换手失败")
    assert jaccard_similarity(kw_a, kw_b) >= 0.45
    scan3 = reg.check_tombstone_duplicates(
        expression="close.rolling_mean(window_size=10)",
        title="CSI500 量价背离 微盘反转",
        hypothesis="测试 CSI500 量价背离微盘反转高换手效应",
        factor_id="alpha_vp_corr_20d",
    )
    assert scan3["exact_hit"] is False
    assert scan3["ast_hit"] is False
    assert scan3["keyword_hit"] is True
    assert scan3["matches"][0]["tier"] == "keyword_jaccard"
    assert scan3["matches"][0]["similarity"] >= 0.45


def test_dimension_baselines_unique_cache_and_concurrent_study_events(tmp_path: Path) -> None:
    reg = QuantRegistry(db_path=tmp_path / "registry.db")

    # 5-dimension baseline unique index upsert
    for dim in ("market_regime", "volatility_regime", "market_cap_tier", "sw_industry", "barra_style"):
        rec = DimensionBaselineRecord(
            baseline_id=f"bl_snap-1_CSI500_{dim}",
            snapshot_id="snap-1",
            universe_id="CSI500",
            dimension_type=dim,  # type: ignore[arg-type]
            date_range_start="2024-01-02",
            date_range_end="2026-11-25",
            current_state="BULL" if dim == "market_regime" else "MID",
            bucket_definitions={"rule": "causal"},
            bucket_stats=[{"bucket": "BULL", "n_days": 200, "ann_return_pct": 12.0}],
            baseline_sha256="c" * 64,
            created_at="2026-11-25T10:00:00+00:00",
        )
        reg.save_dimension_baseline(rec)
        # Re-save same (snapshot, universe, dim) updates cleanly without duplicate row
        updated = rec.model_copy(update={"current_state": "SIDEWAYS"})
        reg.save_dimension_baseline(updated)

    all_bl = reg.list_dimension_baselines(snapshot_id="snap-1", universe_id="CSI500")
    assert len(all_bl) == 5
    mr = reg.get_dimension_baseline("snap-1", "CSI500", "market_regime")
    assert mr is not None and mr.current_state == "SIDEWAYS"

    # Concurrent multi-threaded WAL writes to study_events -> monotonic unique seq 1..40
    reg.create_study(study_id="STD-CONC", title="Concurrency", snapshot_id="snap-1")

    def _writer(worker_idx: int) -> list[int]:
        seqs: list[int] = []
        for i in range(5):
            ev = reg.append_study_event(
                "STD-CONC",
                stage="VERIFYING",
                event_type="progress",
                progress_pct=i * 10,
                message=f"worker {worker_idx} step {i}",
            )
            seqs.append(ev.seq)
        return seqs

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_writer, range(8)))

    all_seqs = sorted(s for chunk in results for s in chunk)
    assert all_seqs == list(range(1, 41))
    replayed = reg.list_study_events("STD-CONC", after_seq=35)
    assert [e.seq for e in replayed] == [36, 37, 38, 39, 40]
