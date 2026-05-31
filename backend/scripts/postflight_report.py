#!/usr/bin/env python3
"""
Post-flight analysis harness for DRISHTI-NAV Deolali sessions.

One-command interface:
  python scripts/postflight_report.py \\
    --session-dir ~/datasets/deolali_phase_b/sortie_1 \\
    --tile-db configs/deolali_tiledb.yaml \\
    --output-dir results/deolali_phase_b/sortie_1

Produces: summary.txt, per_frame.json, comparison.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow standalone invocation from repo root or scripts/
sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml

from routers.pipeline import compute_benchmark, _pct
from services.timestamps_csv import read_timestamps_csv

# ── IIT-B baseline fallback (from phase4_cruise.json / CLAUDE.md) ─────────────
_IITB_FALLBACK = {
    "median_error_m":      19.9,
    "p90_error_m":         29.7,
    "n_cruise_hi_conf":    440,   # gate=10
    "low_inlier_rate_pct": 40.8,
    "gate":                10,
    "source":              "hardcoded-fallback",
}

_EKF_GATE = 10
_CRUISE_TOL_M = 15.0   # ±15 m from median altitude defines cruise band


# ── Utilities ─────────────────────────────────────────────────────────────────

def _pearson_r(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mx) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - my) ** 2 for y in ys))
    if den_x == 0 or den_y == 0:
        return None
    return round(num / (den_x * den_y), 3)


def _median(vals: list[float]) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    return round(_pct(s, 50), 1)


# ── Session validation ─────────────────────────────────────────────────────────

def validate_session(session_dir: Path) -> tuple[bool, list[str]]:
    """Return (ok, warnings_list). Raises on hard failures."""
    csv_path = session_dir / "timestamps.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"timestamps.csv not found in {session_dir}")

    frames_dir = session_dir / "frames"
    jpegs = list(frames_dir.glob("*.jpg")) if frames_dir.exists() else []
    if not jpegs:
        raise ValueError(f"No JPEG frames found in {session_dir}/frames/")

    # Probe first valid CSV row to check column set
    warnings: list[str] = []
    sample = read_timestamps_csv(str(csv_path))
    if not sample:
        raise ValueError("timestamps.csv contains no valid rows")

    first = sample[0]
    missing_cols = [c for c in ("hdop", "satellite_count", "disk_free_gb") if first.get(c) is None]
    if len(missing_cols) == 3:
        warnings.append(
            "GPS-validity breakdown unavailable: required columns missing "
            "(hdop, satellite_count, disk_free_gb absent from timestamps.csv — "
            "old-format log). Update drishti-rpi-logger."
        )
    return True, warnings


# ── GSD normalisation check ────────────────────────────────────────────────────

def check_gsd_normalisation(tile_db_path: Path) -> tuple[bool, str]:
    """Return (enabled, detail_string). Raises ValueError if disabled."""
    if not tile_db_path.exists():
        raise FileNotFoundError(f"Tile-DB config not found: {tile_db_path}")

    cfg = yaml.safe_load(tile_db_path.read_text(encoding="utf-8"))
    enabled = (
        cfg.get("embedder", {})
           .get("gsd_normalisation", {})
           .get("enabled", False)
    )
    detail = f"gsd_normalisation.enabled = {enabled}  (config: {tile_db_path})"
    if not enabled:
        raise ValueError(
            f"FATAL: gsd_normalisation is OFF in {tile_db_path}.\n"
            "Set embedder.gsd_normalisation.enabled: true before re-running.\n"
            "All position fixes are suspect without GSD normalisation at Deolali altitudes."
        )
    return True, detail


# ── Pipeline output loading ────────────────────────────────────────────────────

def _load_pipeline_json(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("frames") or data.get("results") or (data if isinstance(data, list) else [])


def resolve_pipeline_output(
    session_dir: Path,
    output_dir: Path,
    pipeline_json: Path | None,
    tile_db: Path,
    nav_path: str | None,
    gate: int,
) -> list[dict]:
    """Return per-frame pipeline records, running the pipeline if needed."""
    # 1. Explicit --pipeline-json flag
    if pipeline_json is not None:
        return _load_pipeline_json(pipeline_json)

    per_frame_path = output_dir / "per_frame.json"
    csv_path = session_dir / "timestamps.csv"

    # 2. Existing per_frame.json that's newer than timestamps.csv → already done
    if per_frame_path.exists() and per_frame_path.stat().st_mtime >= csv_path.stat().st_mtime:
        print(f"[harness] Loading existing output from {per_frame_path}")
        return json.loads(per_frame_path.read_text(encoding="utf-8"))

    # 3. session-dir pipeline_results.json (saved from GCS panel)
    session_pipeline = session_dir / "pipeline_results.json"
    if session_pipeline.exists():
        print(f"[harness] Loading pipeline results from {session_pipeline}")
        return _load_pipeline_json(session_pipeline)

    # 4. Run the pipeline subprocess
    _run_pipeline(session_dir, output_dir, tile_db, nav_path, gate)
    # After pipeline run, look for pipeline_results.json the script may have written
    if session_pipeline.exists():
        return _load_pipeline_json(session_pipeline)
    raise RuntimeError(
        "Pipeline ran but no pipeline_results.json was found in the session dir.\n"
        "Check run_gcs_pipeline.py output above for errors."
    )


def _run_pipeline(session_dir: Path, output_dir: Path, tile_db: Path,
                  nav_path: str | None, gate: int) -> None:
    nav = Path(os.path.expanduser(nav_path or os.getenv("DRISHTI_NAV_PATH", "~/projects/drishti-nav-v3")))
    script = nav / "scripts" / "run_gcs_pipeline.py"
    if not script.exists():
        raise FileNotFoundError(
            f"run_gcs_pipeline.py not found at {script}.\n"
            "Set DRISHTI_NAV_PATH or pass --nav-path, or use --pipeline-json."
        )
    tile_index = output_dir.parent.parent / "datasets" / "faiss_index" / "deolali_z19"
    cmd = [
        sys.executable, str(script),
        "--session-dir", str(session_dir),
        "--tile-index-dir", os.path.expanduser(str(tile_index)),
        "--config", str(tile_db),
        "--gate", str(gate),
    ]
    print(f"[harness] Running pipeline: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)


# ── Frame merging ──────────────────────────────────────────────────────────────

def merge_frames(csv_records: list[dict], pipeline_records: list[dict]) -> list[dict]:
    """Merge CSV per-frame GPS data with pipeline position-fix records by unix_ms."""
    pipeline_by_ms = {r["timestamp_ms"]: r for r in pipeline_records if "timestamp_ms" in r}
    merged = []
    for i, csv_rec in enumerate(csv_records):
        ms = csv_rec["unix_ms"]
        pip = pipeline_by_ms.get(ms, {})
        merged.append({
            "row":              i,
            "timestamp_ms":    ms,
            "lat":              csv_rec.get("lat"),
            "lon":              csv_rec.get("lon"),
            "altitude_m":       csv_rec.get("altitude_m"),
            "heading_deg":      csv_rec.get("heading_deg"),
            "hdop":             csv_rec.get("hdop"),
            "satellite_count":  csv_rec.get("satellite_count"),
            "disk_free_gb":     csv_rec.get("disk_free_gb"),
            "inlier_count":     pip.get("inlier_count", 0),
            "position_error_m": pip.get("position_error_m"),
            "reject_reason":    pip.get("reject_reason"),
            "retrieval_rank":   pip.get("retrieval_rank"),
        })
    return merged


# ── Deolali-specific breakdowns ────────────────────────────────────────────────

def find_cruise(frames: list[dict]) -> tuple[int | None, int | None]:
    """Longest contiguous span with altitude_m within ±CRUISE_TOL_M of median."""
    alts = [f["altitude_m"] for f in frames if f.get("altitude_m") is not None]
    if not alts:
        return None, None
    median_alt = _pct(sorted(alts), 50)
    in_band = [
        f.get("altitude_m") is not None and abs(f["altitude_m"] - median_alt) <= _CRUISE_TOL_M
        for f in frames
    ]
    best_start = best_end = best_len = 0
    cur_start: int | None = None
    for i, v in enumerate(in_band):
        if v:
            if cur_start is None:
                cur_start = i
        else:
            if cur_start is not None:
                length = i - cur_start
                if length > best_len:
                    best_start, best_end, best_len = cur_start, i - 1, length
                cur_start = None
    if cur_start is not None:
        length = len(in_band) - cur_start
        if length > best_len:
            best_start, best_end, best_len = cur_start, len(in_band) - 1, length
    if best_len == 0:
        return None, None
    return best_start, best_end


def heading_bin_breakdown(frames: list[dict], n_bins: int = 8) -> list[dict]:
    """Per-heading-bin Cut B stats. 8 bins of 45° each."""
    bin_size = 360 / n_bins
    bins: list[list[dict]] = [[] for _ in range(n_bins)]
    for f in frames:
        h = f.get("heading_deg")
        if h is None:
            continue
        idx = int(h % 360 / bin_size) % n_bins
        bins[idx].append(f)
    result = []
    for idx, bin_frames in enumerate(bins):
        lo = int(idx * bin_size)
        hi = int((idx + 1) * bin_size)
        n = len(bin_frames)
        hi_conf = [f for f in bin_frames if (f.get("inlier_count") or 0) >= _EKF_GATE and f.get("reject_reason") is None]
        errs = [f["position_error_m"] for f in hi_conf if f.get("position_error_m") is not None]
        low_inlier = sum(1 for f in bin_frames if (f.get("inlier_count") or 0) < _EKF_GATE and f.get("reject_reason") is None)
        result.append({
            "bin":               f"{lo:3d}°–{hi:3d}°",
            "n_frames":          n,
            "n_hi_conf":         len(hi_conf),
            "low_inlier_count":  low_inlier,
            "low_inlier_pct":    round(low_inlier / n * 100, 1) if n else 0.0,
            "median_error_m":    _median(errs),
            "p90_error_m":       round(_pct(sorted(errs), 90), 1) if errs else None,
        })
    return result


def altitude_correlation(frames: list[dict]) -> dict:
    """Pearson r between altitude_m and inlier_count / position_error_m."""
    pairs_ic  = [(f["altitude_m"], f["inlier_count"])
                 for f in frames
                 if f.get("altitude_m") is not None and f.get("inlier_count") is not None]
    pairs_pe  = [(f["altitude_m"], f["position_error_m"])
                 for f in frames
                 if f.get("altitude_m") is not None and f.get("position_error_m") is not None
                 and f.get("reject_reason") is None]
    alts_all = [f["altitude_m"] for f in frames if f.get("altitude_m") is not None]
    med_alt = _pct(sorted(alts_all), 50) if alts_all else 0

    # Altitude bins: <80m, 80-120m, 120m+
    def alt_bin_stats(lo, hi):
        sub = [f for f in frames if f.get("altitude_m") is not None and lo <= f["altitude_m"] < hi]
        errs = [f["position_error_m"] for f in sub if f.get("position_error_m") is not None and f.get("reject_reason") is None]
        return {"n": len(sub), "n_with_fix": len(errs), "median_error_m": _median(errs)}

    return {
        "r_alt_vs_inliers":    _pearson_r([p[0] for p in pairs_ic], [p[1] for p in pairs_ic]),
        "r_alt_vs_pos_error":  _pearson_r([p[0] for p in pairs_pe], [p[1] for p in pairs_pe]),
        "median_altitude_m":   round(med_alt, 1),
        "bins": {
            "50_80m":   alt_bin_stats(50, 80),
            "80_120m":  alt_bin_stats(80, 120),
            "120m_plus": alt_bin_stats(120, 9999),
        },
    }


def gps_validity_breakdown(frames: list[dict], gate: int = _EKF_GATE) -> dict | None:
    """HDOP-based GPS validity breakdown. Returns None if hdop absent from all frames."""
    if all(f.get("hdop") is None for f in frames):
        return None
    good      = [f for f in frames if f.get("hdop") is not None and f["hdop"] < 2.0]
    degraded  = [f for f in frames if f.get("hdop") is not None and 2.0 <= f["hdop"] <= 5.0]
    poor      = [f for f in frames if f.get("hdop") is None or f["hdop"] > 5.0]

    def cut_b_median(subset):
        errs = [f["position_error_m"] for f in subset
                if (f.get("inlier_count") or 0) >= gate
                and f.get("reject_reason") is None
                and f.get("position_error_m") is not None]
        return _median(errs)

    return {
        "good_hdop_lt2":     {"n": len(good),     "cut_b_median_m": cut_b_median(good)},
        "degraded_hdop_2_5": {"n": len(degraded),  "cut_b_median_m": cut_b_median(degraded)},
        "poor_or_missing":   {"n": len(poor),      "cut_b_median_m": cut_b_median(poor)},
    }


# ── IIT-B baseline loader ─────────────────────────────────────────────────────

def load_iitb_baseline(nav_path: str | None) -> tuple[dict, bool]:
    """Return (baseline_dict, is_fallback)."""
    candidates = []
    if nav_path:
        candidates.append(Path(nav_path) / "results" / "phase4_cruise.json")
    candidates += [
        Path(os.path.expanduser("~/projects/drishti-nav-v3/results/phase4_cruise.json")),
        Path("results/phase4_cruise.json"),
    ]
    for path in candidates:
        if path.exists():
            try:
                d = json.loads(path.read_text(encoding="utf-8"))
                frames_all = d.get("frames", [])
                n_hi_conf = sum(1 for f in frames_all if (f.get("inlier_count") or 0) >= _EKF_GATE)
                n_total   = len(frames_all)
                n_low     = sum(1 for f in frames_all if (f.get("inlier_count") or 0) < _EKF_GATE)
                return {
                    "median_error_m":      d["cut_b"]["median_error_m"],
                    "p90_error_m":         d["cut_b"]["p90_error_m"],
                    "n_cruise_hi_conf":    n_hi_conf,
                    "low_inlier_rate_pct": round(n_low / n_total * 100, 1) if n_total else 0.0,
                    "gate":                _EKF_GATE,
                    "source":              str(path),
                }, False
            except Exception:
                continue
    return _IITB_FALLBACK, True


# ── Summary renderer ───────────────────────────────────────────────────────────

def _hr(char="-", width=72):
    return char * width


def render_summary(
    session_name: str,
    csv_records: list[dict],
    merged_frames: list[dict],
    cruise_frames: list[dict],
    gsd_detail: str,
    heading_bins: list[dict],
    alt_corr: dict,
    gps_valid: dict | None,
    gps_warnings: list[str],
    iitb: dict,
    iitb_fallback: bool,
    benchmark_full: dict,
    benchmark_cruise: dict,
    cruise_start: int | None,
    cruise_end: int | None,
    gate: int,
) -> str:
    n = len(merged_frames)
    nc = len(cruise_frames)
    t0_ms = merged_frames[0]["timestamp_ms"] if merged_frames else 0
    t1_ms = merged_frames[-1]["timestamp_ms"] if merged_frames else 0
    duration_s = (t1_ms - t0_ms) / 1000.0

    lines: list[str] = []
    lines.append(_hr("="))
    lines.append(f"DRISHTI-NAV POST-FLIGHT REPORT")
    lines.append(f"Session : {session_name}")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append(_hr("="))

    lines.append("")
    lines.append(f"GSD NORMALISATION CHECK")
    lines.append(f"  {gsd_detail}")
    lines.append("")

    for w in gps_warnings:
        lines.append(f"WARNING: {w}")

    lines.append(_hr())
    lines.append(f"SESSION OVERVIEW")
    lines.append(f"  Frames    : {n}")
    lines.append(f"  Duration  : {duration_s:.1f} s")
    alts = [f["altitude_m"] for f in merged_frames if f.get("altitude_m") is not None]
    if alts:
        lines.append(f"  Altitude  : min={min(alts):.1f} m  max={max(alts):.1f} m  median={_pct(sorted(alts),50):.1f} m")
    if cruise_start is not None:
        lines.append(f"  Cruise    : rows {cruise_start}–{cruise_end} ({nc} frames)")
        if merged_frames:
            ct0 = merged_frames[cruise_start]["timestamp_ms"]
            ct1 = merged_frames[cruise_end]["timestamp_ms"]
            lines.append(f"              t={ct0}ms – {ct1}ms")

    lines.append("")
    lines.append(_hr())
    lines.append(f"CUT A / CUT B  (EKF gate ≥ {gate} inliers)")
    for label, bm, subset_n in [("FULL SESSION", benchmark_full, n), ("CRUISE ONLY", benchmark_cruise, nc)]:
        lines.append(f"")
        lines.append(f"  [{label}  n={subset_n}]")
        ca = bm["cut_a"]
        cb = bm["cut_b"]
        lines.append(f"  Cut A (all frames with fix):")
        lines.append(f"    n={ca['n_valid']:4d}  median={ca['median'] or '—':>7}m  p90={ca['p90'] or '—':>7}m  ≤25m={ca['le_25m_pct']:.1f}%  ≤50m={ca['le_50m_pct']:.1f}%")
        lines.append(f"  Cut B (inliers ≥ {gate}):")
        lines.append(f"    n={cb['n_valid']:4d}  median={cb['median'] or '—':>7}m  p90={cb['p90'] or '—':>7}m  ≤25m={cb['le_25m_pct']:.1f}%  ≤50m={cb['le_50m_pct']:.1f}%")
        low_inlier_n = ca["n_filtered"] + (ca["n_frames"] - ca["n_valid"] - ca["n_filtered"])
        low_inlier_pct = round((ca["n_frames"] - cb["n_valid"]) / ca["n_frames"] * 100, 1) if ca["n_frames"] else 0.0
        lines.append(f"    Low-inlier rate: {low_inlier_pct:.1f}%")

    lines.append("")
    lines.append(_hr())
    lines.append("HEADING-BIN BREAKDOWN  (8 bins × 45°)")
    lines.append(f"  {'Bin':12s}  {'N':>5}  {'Hi-conf':>7}  {'Low-inl':>7}  {'Low%':>6}  {'Med-err':>8}  {'P90-err':>8}")
    for b in heading_bins:
        med = f"{b['median_error_m']:.1f}m" if b["median_error_m"] is not None else "  —  "
        p90 = f"{b['p90_error_m']:.1f}m"   if b["p90_error_m"]    is not None else "  —  "
        lines.append(f"  {b['bin']:12s}  {b['n_frames']:5d}  {b['n_hi_conf']:7d}  {b['low_inlier_count']:7d}  {b['low_inlier_pct']:5.1f}%  {med:>8}  {p90:>8}")

    lines.append("")
    lines.append(_hr())
    lines.append("ALTITUDE CORRELATION")
    lines.append(f"  Median altitude    : {alt_corr['median_altitude_m']} m")
    r_ic = alt_corr.get("r_alt_vs_inliers")
    r_pe = alt_corr.get("r_alt_vs_pos_error")
    lines.append(f"  Pearson r (alt vs inlier_count) : {r_ic if r_ic is not None else 'N/A'}")
    lines.append(f"  Pearson r (alt vs pos_error_m)  : {r_pe if r_pe is not None else 'N/A'}")
    for bin_name, bs in alt_corr.get("bins", {}).items():
        med = f"{bs['median_error_m']:.1f}m" if bs["median_error_m"] is not None else "—"
        lines.append(f"  {bin_name:12s}  n={bs['n']:4d}  fixes={bs['n_with_fix']:4d}  med_err={med}")

    lines.append("")
    lines.append(_hr())
    if gps_valid is None:
        lines.append("GPS-VALIDITY BREAKDOWN: unavailable (hdop column absent from timestamps.csv)")
    else:
        lines.append("GPS-VALIDITY BREAKDOWN  (HDOP-based)")
        for key, label in [
            ("good_hdop_lt2",     "Good    (HDOP < 2.0)"),
            ("degraded_hdop_2_5", "Degraded(HDOP 2–5)  "),
            ("poor_or_missing",   "Poor/N/A(HDOP > 5)  "),
        ]:
            v = gps_valid[key]
            med = f"{v['cut_b_median_m']:.1f}m" if v["cut_b_median_m"] is not None else "—"
            lines.append(f"  {label}: n={v['n']:4d}  Cut-B median={med}")

    lines.append("")
    lines.append(_hr())
    src = f"({iitb['source']})" if not iitb_fallback else "(HARDCODED FALLBACK — phase4_cruise.json not found)"
    lines.append(f"COMPARISON vs IIT-B BASELINE  {src}")
    lines.append(f"  {'Metric':<32}  {'IIT-B (Cut B)':>14}  {'Deolali (Cut B)':>15}")
    lines.append(f"  {_hr('-', 65)}")
    cb_full = benchmark_full["cut_b"]
    cb_cr   = benchmark_cruise["cut_b"]

    def _fmt(v): return f"{v:.1f} m" if v else "—"
    lines.append(f"  {'median error (full)':<32}  {_fmt(iitb['median_error_m']):>14}  {_fmt(cb_full['median']):>15}")
    lines.append(f"  {'p90 error (full)':<32}  {_fmt(iitb['p90_error_m']):>14}  {_fmt(cb_full['p90']):>15}")
    lines.append(f"  {'median error (cruise)':<32}  {_fmt(iitb['median_error_m']):>14}  {_fmt(cb_cr['median']):>15}")
    lines.append(f"  {'p90 error (cruise)':<32}  {_fmt(iitb['p90_error_m']):>14}  {_fmt(cb_cr['p90']):>15}")
    n_hi_conf_deolali = cb_cr["n_valid"]
    lines.append(f"  {'n frames (cruise, hi-conf)':<32}  {iitb['n_cruise_hi_conf']:>14}  {n_hi_conf_deolali:>15}")
    low_pct_deolali = round((nc - n_hi_conf_deolali) / nc * 100, 1) if nc else 0.0
    lines.append(f"  {'low-inlier rate (cruise)':<32}  {iitb['low_inlier_rate_pct']:>13.1f}%  {low_pct_deolali:>14.1f}%")
    if iitb_fallback:
        lines.append("")
        lines.append("  NOTE: IIT-B baseline loaded from hardcoded values; phase4_cruise.json not found.")
        lines.append("  Set DRISHTI_NAV_PATH or place phase4_cruise.json in results/.")

    lines.append("")
    lines.append(_hr("="))
    return "\n".join(lines)


# ── Comparison JSON ────────────────────────────────────────────────────────────

def build_comparison_json(
    session_name: str,
    benchmark_full: dict,
    benchmark_cruise: dict,
    cruise_start: int | None,
    cruise_end: int | None,
    iitb: dict,
    iitb_fallback: bool,
    n_full: int,
    n_cruise: int,
) -> dict:
    cb_full  = benchmark_full["cut_b"]
    cb_cr    = benchmark_cruise["cut_b"]
    ca_full  = benchmark_full["cut_a"]
    return {
        "session":        session_name,
        "generated_utc":  datetime.now(timezone.utc).isoformat(),
        "gate":           _EKF_GATE,
        "cruise": {
            "start_row":  cruise_start,
            "end_row":    cruise_end,
            "n_frames":   n_cruise,
        },
        "cut_b_full": {
            "n":         cb_full["n_valid"],
            "median_m":  cb_full["median"],
            "p90_m":     cb_full["p90"],
            "le_25m_pct": cb_full["le_25m_pct"],
            "le_50m_pct": cb_full["le_50m_pct"],
        },
        "cut_b_cruise": {
            "n":         cb_cr["n_valid"],
            "median_m":  cb_cr["median"],
            "p90_m":     cb_cr["p90"],
            "le_25m_pct": cb_cr["le_25m_pct"],
            "le_50m_pct": cb_cr["le_50m_pct"],
        },
        "cut_a_full": {
            "n":         ca_full["n_valid"],
            "median_m":  ca_full["median"],
            "p90_m":     ca_full["p90"],
            "low_inlier_rate_pct": round((n_full - ca_full["n_valid"]) / n_full * 100, 1) if n_full else 0.0,
        },
        "baseline_iitb": {
            "source":             iitb["source"],
            "is_fallback":        iitb_fallback,
            "median_m":           iitb["median_error_m"],
            "p90_m":              iitb["p90_error_m"],
            "n_cruise_hi_conf":   iitb["n_cruise_hi_conf"],
            "low_inlier_rate_pct": iitb["low_inlier_rate_pct"],
        },
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def run(
    session_dir: Path,
    tile_db: Path,
    output_dir: Path,
    gate: int = _EKF_GATE,
    pipeline_json: Path | None = None,
    nav_path: str | None = None,
) -> dict:
    """Full harness run. Returns dict with output paths."""
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Validate
    _, gps_warnings = validate_session(session_dir)

    # 2. GSD normalisation check
    _, gsd_detail = check_gsd_normalisation(tile_db)

    # 3. Pipeline output
    pipeline_records = resolve_pipeline_output(
        session_dir, output_dir, pipeline_json, tile_db, nav_path, gate
    )

    # 4. Load CSV + merge
    csv_records = read_timestamps_csv(str(session_dir / "timestamps.csv"))
    merged = merge_frames(csv_records, pipeline_records)

    # 5. Cruise detection
    cruise_start, cruise_end = find_cruise(merged)
    cruise_frames = merged[cruise_start:cruise_end + 1] if cruise_start is not None else merged

    # 6. Deolali-specific breakdowns
    heading_bins = heading_bin_breakdown(merged)
    alt_corr     = altitude_correlation(merged)
    gps_valid    = gps_validity_breakdown(merged, gate)

    # 7. Benchmarks
    bm_full   = compute_benchmark(merged, gate)
    bm_cruise = compute_benchmark(cruise_frames, gate)

    # 8. IIT-B baseline
    iitb, iitb_fallback = load_iitb_baseline(nav_path)

    # 9. Emit outputs
    summary = render_summary(
        session_name=session_dir.name,
        csv_records=csv_records,
        merged_frames=merged,
        cruise_frames=cruise_frames,
        gsd_detail=gsd_detail,
        heading_bins=heading_bins,
        alt_corr=alt_corr,
        gps_valid=gps_valid,
        gps_warnings=gps_warnings,
        iitb=iitb,
        iitb_fallback=iitb_fallback,
        benchmark_full=bm_full,
        benchmark_cruise=bm_cruise,
        cruise_start=cruise_start,
        cruise_end=cruise_end,
        gate=gate,
    )
    comparison = build_comparison_json(
        session_name=session_dir.name,
        benchmark_full=bm_full,
        benchmark_cruise=bm_cruise,
        cruise_start=cruise_start,
        cruise_end=cruise_end,
        iitb=iitb,
        iitb_fallback=iitb_fallback,
        n_full=len(merged),
        n_cruise=len(cruise_frames),
    )

    (output_dir / "summary.txt").write_text(summary, encoding="utf-8")
    (output_dir / "per_frame.json").write_text(
        json.dumps(merged, indent=2), encoding="utf-8"
    )
    (output_dir / "comparison.json").write_text(
        json.dumps(comparison, indent=2), encoding="utf-8"
    )

    print(summary)
    print(f"\n[harness] Outputs written to {output_dir}/")
    print(f"  summary.txt    — paste into PR")
    print(f"  per_frame.json — machine-readable frame data")
    print(f"  comparison.json — side-by-side comparison with IIT-B")
    return {
        "summary_path":    str(output_dir / "summary.txt"),
        "per_frame_path":  str(output_dir / "per_frame.json"),
        "comparison_path": str(output_dir / "comparison.json"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="DRISHTI post-flight analysis harness")
    parser.add_argument("--session-dir",  required=True, type=Path, help="Path to session directory")
    parser.add_argument("--tile-db",      required=True, type=Path, help="Path to Deolali tile-DB YAML config")
    parser.add_argument("--output-dir",   required=True, type=Path, help="Directory for output files")
    parser.add_argument("--gate",         type=int, default=_EKF_GATE, help=f"EKF inlier gate (default {_EKF_GATE})")
    parser.add_argument("--pipeline-json", type=Path, default=None, help="Existing pipeline results JSON (skips running pipeline)")
    parser.add_argument("--nav-path",      default=None, help="Path to drishti-nav-v3 repo (overrides DRISHTI_NAV_PATH)")
    args = parser.parse_args()
    run(
        session_dir=args.session_dir,
        tile_db=args.tile_db,
        output_dir=args.output_dir,
        gate=args.gate,
        pipeline_json=args.pipeline_json,
        nav_path=args.nav_path,
    )


if __name__ == "__main__":
    main()
