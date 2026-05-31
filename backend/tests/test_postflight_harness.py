"""
Tests for the post-flight analysis harness (scripts/postflight_report.py).

All tests use the synthetic Deolali fixture in
tests/fixtures/synthetic_deolali_session/.  The fixture's pipeline_results.json
stands in for actual pipeline output so no GPU / tile-DB is required.
"""
import json
import sys
import time
from pathlib import Path

import pytest
import yaml

# Put the backend root on sys.path so the harness can import routers/services.
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.postflight_report import (
    altitude_correlation,
    build_comparison_json,
    check_gsd_normalisation,
    find_cruise,
    gps_validity_breakdown,
    heading_bin_breakdown,
    load_iitb_baseline,
    merge_frames,
    render_summary,
    run,
    validate_session,
)
from services.timestamps_csv import read_timestamps_csv

FIXTURE = Path(__file__).parent / "fixtures" / "synthetic_deolali_session"


# ── helpers ───────────────────────────────────────────────────────────────────

def _load_fixture_frames(fixture=FIXTURE):
    csv_recs = read_timestamps_csv(str(fixture / "timestamps.csv"))
    pipeline = json.loads((fixture / "pipeline_results.json").read_text())
    return merge_frames(csv_recs, pipeline["frames"])


# ── 1. Full run produces all three output files ───────────────────────────────

def test_full_run_produces_outputs(tmp_path):
    out = tmp_path / "out"
    run(
        session_dir=FIXTURE,
        tile_db=FIXTURE / "deolali_tiledb.yaml",
        output_dir=out,
        pipeline_json=FIXTURE / "pipeline_results.json",
    )
    assert (out / "summary.txt").exists()
    assert (out / "per_frame.json").exists()
    assert (out / "comparison.json").exists()


def test_per_frame_json_is_valid(tmp_path):
    out = tmp_path / "out"
    run(session_dir=FIXTURE, tile_db=FIXTURE/"deolali_tiledb.yaml", output_dir=out,
        pipeline_json=FIXTURE/"pipeline_results.json")
    frames = json.loads((out / "per_frame.json").read_text())
    assert len(frames) == 200
    assert "timestamp_ms" in frames[0]
    assert "inlier_count" in frames[0]
    assert "position_error_m" in frames[0]


def test_comparison_json_parseable(tmp_path):
    out = tmp_path / "out"
    run(session_dir=FIXTURE, tile_db=FIXTURE/"deolali_tiledb.yaml", output_dir=out,
        pipeline_json=FIXTURE/"pipeline_results.json")
    comp = json.loads((out / "comparison.json").read_text())
    assert "cut_b_full" in comp
    assert "cut_b_cruise" in comp
    assert "baseline_iitb" in comp
    assert comp["gate"] == 10


# ── 2. Pipeline skip: per_frame.json newer than timestamps.csv is reused ──────

def test_freshness_detection_skips_pipeline(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    # Pre-populate per_frame.json as if harness already ran
    frames = _load_fixture_frames()
    pf = out / "per_frame.json"
    pf.write_text(json.dumps(frames))
    # Touch it to ensure it's newer than timestamps.csv
    time.sleep(0.01)
    pf.touch()

    # Run without --pipeline-json; should load from out/per_frame.json
    run(session_dir=FIXTURE, tile_db=FIXTURE/"deolali_tiledb.yaml", output_dir=out)
    assert (out / "summary.txt").exists()


# ── 3. Cruise auto-detection ──────────────────────────────────────────────────

def test_cruise_detection_finds_correct_span():
    frames = _load_fixture_frames()
    start, end = find_cruise(frames)
    assert start is not None and end is not None
    # Fixture: climb rows 0–29, cruise 30–169, descent 170–199
    # With ±15m tolerance around median (~79m), cruise should start ≤ 35 and end ≥ 164
    assert start <= 35, f"Cruise start too late: {start}"
    assert end >= 164,  f"Cruise end too early: {end}"
    assert end > start
    # Cruise frames should all be near median altitude
    alts = [f["altitude_m"] for f in frames[start:end+1] if f.get("altitude_m") is not None]
    from scripts.postflight_report import _pct
    median_alt = _pct(sorted([f["altitude_m"] for f in frames if f.get("altitude_m") is not None]), 50)
    assert all(abs(a - median_alt) <= 15.0 for a in alts)


# ── 4. GSD normalisation check ────────────────────────────────────────────────

def test_gsd_normalisation_passes_when_enabled():
    ok, detail = check_gsd_normalisation(FIXTURE / "deolali_tiledb.yaml")
    assert ok is True
    assert "enabled" in detail.lower()


def test_gsd_normalisation_fails_when_disabled(tmp_path):
    cfg = tmp_path / "bad.yaml"
    cfg.write_text("embedder:\n  gsd_normalisation:\n    enabled: false\n")
    with pytest.raises(ValueError, match="FATAL"):
        check_gsd_normalisation(cfg)


# ── 5. Heading-bin breakdown has 8 bins ───────────────────────────────────────

def test_heading_bin_breakdown_has_8_bins():
    frames = _load_fixture_frames()
    bins = heading_bin_breakdown(frames)
    assert len(bins) == 8
    bin_labels = [b["bin"] for b in bins]
    assert any("210" in lb or "225" in lb for lb in bin_labels), "Expected a bin near 210°"


# ── 6. Old-format CSV (no hdop/sats/disk) ─────────────────────────────────────

def test_old_format_csv_completes_with_warning(tmp_path):
    sd = tmp_path / "old_session"
    sd.mkdir()
    (sd / "frames").mkdir()
    base_ms = 1_717_490_000_000
    lines = []
    for i in range(10):
        ms = base_ms + i * 200
        lines.append(f"{ms},frames/{ms}.jpg,19.91,73.82,82.4,210.5")
        (sd / "frames" / f"{ms}.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    (sd / "timestamps.csv").write_text("\n".join(lines) + "\n")

    pipeline_frames = [
        {"timestamp_ms": base_ms + i * 200, "inlier_count": 20,
         "position_error_m": 15.0, "reject_reason": None}
        for i in range(10)
    ]
    (sd / "pipeline_results.json").write_text(json.dumps({"frames": pipeline_frames}))

    out = tmp_path / "old_out"
    run(
        session_dir=sd,
        tile_db=FIXTURE / "deolali_tiledb.yaml",
        output_dir=out,
        pipeline_json=sd / "pipeline_results.json",
    )
    summary = (out / "summary.txt").read_text()
    assert "GPS-validity breakdown" in summary
    assert "unavailable" in summary.lower() or "missing" in summary.lower()


# ── 7. Comparison table with real phase4_cruise.json ─────────────────────────

def test_comparison_table_uses_iitb_baseline():
    iitb, is_fallback = load_iitb_baseline("~/projects/drishti-nav-v3")
    assert iitb["median_error_m"] == pytest.approx(19.9)
    assert iitb["p90_error_m"] == pytest.approx(29.7)
    assert not is_fallback


# ── 8. Comparison falls back to hardcoded baseline if JSON missing ────────────

def test_comparison_fallback_when_no_json(tmp_path, monkeypatch):
    import scripts.postflight_report as harness_mod
    # Patch os.path.expanduser so the hardcoded ~/projects/drishti-nav-v3 candidate
    # also resolves to a nonexistent path.
    nonexistent = str(tmp_path / "no_nav")
    original_expanduser = __import__("os").path.expanduser
    monkeypatch.setattr(
        "os.path.expanduser",
        lambda p: nonexistent if "drishti-nav-v3" in p else original_expanduser(p),
    )
    iitb, is_fallback = load_iitb_baseline(nonexistent)
    assert is_fallback is True
    assert iitb["median_error_m"] == pytest.approx(19.9)

    out = tmp_path / "out"
    run(session_dir=FIXTURE, tile_db=FIXTURE/"deolali_tiledb.yaml", output_dir=out,
        pipeline_json=FIXTURE/"pipeline_results.json",
        nav_path=nonexistent)
    summary = (out / "summary.txt").read_text()
    assert "HARDCODED FALLBACK" in summary or "hardcoded" in summary.lower()


# ── 9. Validate session: missing CSV raises ────────────────────────────────────

def test_validate_session_missing_csv(tmp_path):
    sd = tmp_path / "empty_session"
    sd.mkdir()
    with pytest.raises(FileNotFoundError, match="timestamps.csv"):
        validate_session(sd)


# ── 10. GPS-validity breakdown returns None when hdop absent ──────────────────

def test_gps_validity_none_when_no_hdop():
    frames = [{"inlier_count": 20, "position_error_m": 15.0, "reject_reason": None}]
    result = gps_validity_breakdown(frames)
    assert result is None
