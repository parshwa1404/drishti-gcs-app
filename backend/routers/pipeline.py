import asyncio
import json
import math
import os
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

router = APIRouter()

_DEOLALI_LAT = 19.9175
_DEOLALI_LON = 73.8278

_BASELINE = {"median": 32.5, "p75": 48.2, "p90": 67.8}

_pipeline_state: dict = {
    "results": None,
    "session_name": None,
    "output_path": None,
}


class RunRequest(BaseModel):
    session_dir: str
    tile_index_dir: str = "~/datasets/faiss_index/deolali_z19"
    config_path: str = "configs/milestone_1b/deolali_tiledb.yaml"
    gate: int = 10


# ─── Stats helpers ────────────────────────────────────────────────────────────

def _pct(s: list[float], p: float) -> float:
    n = len(s)
    if n == 0:
        return 0.0
    idx = p / 100.0 * (n - 1)
    lo, hi = int(idx), min(int(idx) + 1, n - 1)
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)


def _cut_stats(errors: list[float], n_total: int, n_filtered: int) -> dict:
    n = len(errors)
    s = sorted(errors)
    return {
        "n_frames": n_total,
        "n_valid": n,
        "n_filtered": n_filtered,
        "median":  round(_pct(s, 50), 1) if n else None,
        "p75":     round(_pct(s, 75), 1) if n else None,
        "p90":     round(_pct(s, 90), 1) if n else None,
        "max":     round(max(errors), 1) if n else None,
        "le_25m":  sum(1 for e in errors if e <= 25),
        "le_50m":  sum(1 for e in errors if e <= 50),
        "le_100m": sum(1 for e in errors if e <= 100),
        "le_25m_pct":  round(sum(1 for e in errors if e <= 25)  / n * 100, 1) if n else 0,
        "le_50m_pct":  round(sum(1 for e in errors if e <= 50)  / n * 100, 1) if n else 0,
        "le_100m_pct": round(sum(1 for e in errors if e <= 100) / n * 100, 1) if n else 0,
        "filtered_pct": round(n_filtered / n_total * 100, 1) if n_total else 0,
    }


def compute_benchmark(frames: list[dict], gate: int = 10) -> dict:
    n_total = len(frames)
    n_filtered = sum(1 for f in frames if f.get("reject_reason") is not None)

    cut_a = [
        f["position_error_m"] for f in frames
        if f.get("reject_reason") is None and f.get("position_error_m") is not None
    ]
    cut_b_frames = [
        f for f in frames
        if f.get("reject_reason") is None
        and f.get("position_error_m") is not None
        and (f.get("inlier_count") or 0) >= gate
    ]
    cut_b = [f["position_error_m"] for f in cut_b_frames]
    cut_b_filtered = n_total - len(cut_b_frames) - n_filtered

    return {
        "gate": gate,
        "cut_a": _cut_stats(cut_a, n_total, n_filtered),
        "cut_b": _cut_stats(cut_b, n_total, cut_b_filtered),
        "baseline": _BASELINE,
    }


def compute_sslf(frames: list[dict], gate: int = 10) -> None:
    """Annotate frames in-place with seconds_since_last_fix."""
    last_fix_ts: int | None = None
    for f in frames:
        ts_ms = f["timestamp_ms"]
        if f.get("reject_reason") is not None:
            f["seconds_since_last_fix"] = None
        elif (f.get("inlier_count") or 0) >= gate:
            f["seconds_since_last_fix"] = 0.0
            last_fix_ts = ts_ms
        elif last_fix_ts is not None:
            f["seconds_since_last_fix"] = round((ts_ms - last_fix_ts) / 1000.0, 1)
        else:
            f["seconds_since_last_fix"] = None



@router.post("/run")
async def run_pipeline(body: RunRequest):
    async def stream():
        nav_path = os.path.expanduser(
            os.getenv("DRISHTI_NAV_PATH", "~/projects/drishti-nav-v3")
        )
        script = Path(nav_path) / "scripts" / "run_gcs_pipeline.py"

        if not script.exists():
            yield {"data": json.dumps({
                "type": "error",
                "message": f"Script not found: {script}  — set DRISHTI_NAV_PATH in backend/.env",
            })}
            return

        cmd = [
            "python", str(script),
            "--session-dir", body.session_dir,
            "--tile-index-dir", os.path.expanduser(body.tile_index_dir),
            "--config", body.config_path,
            "--gate", str(body.gate),
        ]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            async for raw in proc.stdout:
                yield {"data": json.dumps({"type": "log", "line": raw.decode("utf-8", errors="replace").rstrip()})}
            await proc.wait()
            if proc.returncode == 0:
                output_path = f"results/{Path(body.session_dir).name}"
                _pipeline_state["output_path"] = output_path
                yield {"data": json.dumps({"type": "done", "output_path": output_path})}
            else:
                yield {"data": json.dumps({"type": "error", "message": f"Process exited with code {proc.returncode}"})}
        except Exception as exc:
            yield {"data": json.dumps({"type": "error", "message": str(exc)})}

    return EventSourceResponse(stream())


@router.get("/results/{session_name}")
async def get_results(session_name: str):
    data = _pipeline_state["results"]
    if data is None or _pipeline_state["session_name"] != session_name:
        raise HTTPException(status_code=404, detail="Results not found for this session")
    return data


@router.get("/frame-pair/{session_name}/{timestamp_ms}")
async def get_frame_pair(session_name: str, timestamp_ms: int):
    results = _pipeline_state["results"]
    if results is None:
        raise HTTPException(status_code=404, detail="No results loaded")
    frame = next((f for f in results["frames"] if f["timestamp_ms"] == timestamp_ms), None)
    if frame is None:
        raise HTTPException(status_code=404, detail="Frame not found")
    return {
        "live_frame":              None,
        "matched_tile":            None,
        "retrieval_rank":          frame.get("retrieval_rank"),
        "inlier_count":            frame.get("inlier_count"),
        "position_error_m":        frame.get("position_error_m"),
        "camera_gsd_m_per_px":     frame.get("camera_gsd_m_per_px"),
        "altitude_m":              frame.get("altitude_m"),
        "reject_reason":           frame.get("reject_reason"),
        "confidence":              frame.get("confidence"),
        "solver_ms":               frame.get("solver_ms"),
        "seconds_since_last_fix":  frame.get("seconds_since_last_fix"),
    }


@router.get("/benchmark/{session_name}")
async def get_benchmark(session_name: str):
    data = _pipeline_state["results"]
    if data is None:
        raise HTTPException(status_code=404, detail="No results loaded")
    return data.get("benchmark")
