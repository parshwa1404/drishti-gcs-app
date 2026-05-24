import asyncio
import base64
import io
import json
import math
import os
import random
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


# ─── Mock data generators ─────────────────────────────────────────────────────

def generate_mock_results(session_name: str = "mock_session") -> dict:
    n = 100
    frames: list[dict] = []
    lat, lon = _DEOLALI_LAT, _DEOLALI_LON
    heading = 45.0
    start_ms = int(time.time() * 1000) - n * 300

    for i in range(n):
        ts_ms = start_ms + i * 300
        lat += random.uniform(-0.00008, 0.00008)
        lon += random.uniform(-0.00008, 0.00008)
        heading = (heading + random.uniform(-5, 5)) % 360

        rr = random.choices(
            [None, "blur", "uniform", "exposure"],
            weights=[70, 20, 7, 3], k=1
        )[0]

        if rr is not None:
            inliers = random.randint(2, 8)
            frames.append({
                "timestamp_ms": ts_ms,
                "lat": round(lat, 7), "lon": round(lon, 7),
                "est_lat": None, "est_lon": None,
                "position_error_m": None,
                "retrieval_rank": None,
                "inlier_count": inliers,
                "confidence": round(min(1.0, inliers / 30), 3),
                "camera_gsd_m_per_px": round(random.uniform(0.08, 0.12), 3),
                "compass_hdg_deg": round(heading, 1),
                "reject_reason": rr,
            })
        else:
            err = math.exp(random.gauss(math.log(22), 0.65))
            angle = random.uniform(0, 2 * math.pi)
            dlat = err / 111320.0
            dlon = err / (111320.0 * math.cos(math.radians(lat)))
            inliers = random.randint(5, 42)
            frames.append({
                "timestamp_ms": ts_ms,
                "lat": round(lat, 7), "lon": round(lon, 7),
                "est_lat": round(lat + dlat * math.cos(angle), 7),
                "est_lon": round(lon + dlon * math.sin(angle), 7),
                "position_error_m": round(err, 1),
                "retrieval_rank": random.choices([1, 2, 3, 4, 5], weights=[60, 20, 10, 6, 4], k=1)[0],
                "inlier_count": inliers,
                "confidence": round(min(1.0, inliers / 30), 3),
                "camera_gsd_m_per_px": round(random.uniform(0.08, 0.12), 3),
                "compass_hdg_deg": round(heading, 1),
                "reject_reason": None,
            })

    gps_track = [{"lat": f["lat"], "lon": f["lon"]} for f in frames]
    est_track = [
        {"lat": f["est_lat"], "lon": f["est_lon"]}
        for f in frames if f["est_lat"] is not None
    ]

    result = {
        "session_name": session_name,
        "frame_count": n,
        "frames": frames,
        "gps_track": gps_track,
        "est_track": est_track,
        "benchmark": compute_benchmark(frames),
    }
    _pipeline_state["results"] = result
    _pipeline_state["session_name"] = session_name
    return result


def _rand_jpeg_b64(w: int, h: int) -> str:
    import numpy as np
    from PIL import Image
    arr = np.random.randint(20, 130, (h, w, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr, "RGB").save(buf, format="JPEG", quality=60)
    return base64.b64encode(buf.getvalue()).decode()


# ─── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/mock_results")
async def get_mock_results():
    data = _pipeline_state["results"]
    return data if data is not None else generate_mock_results()


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
        "live_frame":    _rand_jpeg_b64(1280, 800),
        "matched_tile":  _rand_jpeg_b64(512, 512),
        "retrieval_rank":      frame.get("retrieval_rank"),
        "inlier_count":        frame.get("inlier_count"),
        "position_error_m":    frame.get("position_error_m"),
        "camera_gsd_m_per_px": frame.get("camera_gsd_m_per_px"),
        "reject_reason":       frame.get("reject_reason"),
    }


@router.get("/benchmark/{session_name}")
async def get_benchmark(session_name: str):
    data = _pipeline_state["results"]
    if data is None:
        raise HTTPException(status_code=404, detail="No results loaded")
    return data.get("benchmark")
