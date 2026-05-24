"""
CLI benchmark report for drishti pipeline results JSON.

Usage:
  python scripts/benchmark.py results.json
  cat results.json | python scripts/benchmark.py
"""

import json
import math
import sys


def _pct(s: list[float], p: float) -> float:
    n = len(s)
    if n == 0:
        return 0.0
    idx = p / 100.0 * (n - 1)
    lo, hi = int(idx), min(int(idx) + 1, n - 1)
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)


def compute_solver_stats(frames: list[dict]) -> dict | None:
    """Return solver timing stats, or None if solver_ms absent from all frames."""
    totals = [f["solver_ms"]["total"] for f in frames if f.get("solver_ms") is not None]
    if not totals:
        return None
    embeds      = [f["solver_ms"]["embed"]     for f in frames if f.get("solver_ms")]
    faisses     = [f["solver_ms"]["faiss"]     for f in frames if f.get("solver_ms")]
    lightgluees = [f["solver_ms"]["lightglue"] for f in frames if f.get("solver_ms")]
    totals_s    = sorted(totals)
    return {
        "embed_median":      round(_pct(sorted(embeds), 50), 1),
        "faiss_median":      round(_pct(sorted(faisses), 50), 1),
        "lightglue_median":  round(_pct(sorted(lightgluees), 50), 1),
        "total_median":      round(_pct(totals_s, 50), 1),
        "total_p90":         round(_pct(totals_s, 90), 1),
    }


def compute_fix_latency(frames: list[dict]) -> dict:
    gaps = [
        f["seconds_since_last_fix"]
        for f in frames
        if f.get("seconds_since_last_fix") is not None and f["seconds_since_last_fix"] > 0
    ]
    if not gaps:
        return {"median_gap_s": 0.0, "max_gap_s": 0.0, "gt5s_count": 0, "gt5s_pct": 0.0}
    s = sorted(gaps)
    n = len(s)
    gt5 = sum(1 for g in gaps if g > 5)
    return {
        "median_gap_s": round(_pct(s, 50), 1),
        "max_gap_s":    round(max(gaps), 1),
        "gt5s_count":   gt5,
        "gt5s_pct":     round(gt5 / n * 100, 1),
    }


def run_benchmark(data: dict) -> str:
    frames = data.get("frames", [])
    lines: list[str] = []

    solver = compute_solver_stats(frames)
    if solver is not None:
        lines.append("SOLVER TIMING")
        lines.append(f"  embed median    : {solver['embed_median']:.1f} ms")
        lines.append(f"  faiss median    : {solver['faiss_median']:.1f} ms")
        lines.append(f"  lightglue median: {solver['lightglue_median']:.1f} ms")
        lines.append(f"  total median    : {solver['total_median']:.1f} ms  (p90: {solver['total_p90']:.1f} ms)")
        lines.append("")

    fl = compute_fix_latency(frames)
    lines.append("FIX LATENCY")
    lines.append(f"  median gap between fixes: {fl['median_gap_s']:.1f} s")
    lines.append(f"  max gap between fixes   : {fl['max_gap_s']:.1f} s")
    lines.append(f"  frames with gap > 5s    : {fl['gt5s_count']} ({fl['gt5s_pct']:.1f}%)")

    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            data = json.load(f)
    else:
        data = json.load(sys.stdin)
    print(run_benchmark(data))
