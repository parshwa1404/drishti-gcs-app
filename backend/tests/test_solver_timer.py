import pytest
from drishti.perception.solver_timer import SolverTiming
from routers.pipeline import compute_sslf
from scripts.benchmark import compute_solver_stats, run_benchmark


# ─── SolverTiming dataclass ───────────────────────────────────────────────────

def test_solver_timing_fields_present():
    t = SolverTiming(t_embed_ms=15.0, t_faiss_ms=5.0, t_lightglue_ms=100.0, t_total_ms=120.0)
    assert hasattr(t, "t_embed_ms")
    assert hasattr(t, "t_faiss_ms")
    assert hasattr(t, "t_lightglue_ms")
    assert hasattr(t, "t_total_ms")


def test_solver_timing_fields_positive():
    t = SolverTiming(t_embed_ms=15.0, t_faiss_ms=5.0, t_lightglue_ms=100.0, t_total_ms=120.0)
    assert t.t_embed_ms > 0
    assert t.t_faiss_ms > 0
    assert t.t_lightglue_ms > 0
    assert t.t_total_ms > 0


# ─── seconds_since_last_fix logic ────────────────────────────────────────────

def _make_frame(ts_ms, inliers, rejected=False):
    return {
        "timestamp_ms": ts_ms,
        "inlier_count": inliers,
        "reject_reason": "blur" if rejected else None,
        "seconds_since_last_fix": None,
    }


def test_sslf_high_confidence_frame_is_zero():
    frames = [_make_frame(1000, inliers=15)]
    compute_sslf(frames, gate=10)
    assert frames[0]["seconds_since_last_fix"] == 0.0


def test_sslf_gap_after_fix():
    frames = [
        _make_frame(1000, inliers=15),   # fix
        _make_frame(2000, inliers=5),    # low inlier — 1.0 s gap
    ]
    compute_sslf(frames, gate=10)
    assert frames[0]["seconds_since_last_fix"] == 0.0
    assert frames[1]["seconds_since_last_fix"] == pytest.approx(1.0)


def test_sslf_none_if_no_prior_fix():
    frames = [_make_frame(1000, inliers=5)]   # low inlier, no prior fix
    compute_sslf(frames, gate=10)
    assert frames[0]["seconds_since_last_fix"] is None


def test_sslf_rejected_frame_is_none():
    frames = [
        _make_frame(1000, inliers=15),            # fix
        _make_frame(2000, inliers=15, rejected=True),  # rejected — should be None
    ]
    compute_sslf(frames, gate=10)
    assert frames[1]["seconds_since_last_fix"] is None


# ─── Benchmark backward compat ────────────────────────────────────────────────

def test_benchmark_no_solver_ms_returns_none():
    frames = [
        {"timestamp_ms": 1000, "position_error_m": 25.0, "inlier_count": 12, "reject_reason": None}
    ]
    result = compute_solver_stats(frames)
    assert result is None


def test_benchmark_run_without_solver_ms_does_not_raise():
    data = {
        "frames": [
            {"timestamp_ms": 1000, "position_error_m": 25.0, "inlier_count": 12,
             "reject_reason": None, "seconds_since_last_fix": 0.0}
        ]
    }
    # Must not raise; solver section should just be omitted
    output = run_benchmark(data)
    assert "FIX LATENCY" in output
    assert "SOLVER TIMING" not in output
