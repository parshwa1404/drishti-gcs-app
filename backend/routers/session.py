import asyncio
import json
import shlex
import tempfile
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from routers.logger import _state as logger_state
from services.session_loader import load_session as _do_load

router = APIRouter()

# Single loaded session; None until POST /session/load or fetch-remote
_session: dict | None = None


class LoadRequest(BaseModel):
    session_dir: str


class FetchRemoteRequest(BaseModel):
    remote_path: str


def load_session_state(session_dir: str) -> None:
    global _session
    _session = _do_load(session_dir)


def _session_response() -> dict | None:
    if _session is None:
        return None
    return {k: v for k, v in _session.items() if k != 'frame_map'}


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.post("/fetch-remote")
async def fetch_remote(body: FetchRemoteRequest):
    """Download a session from the connected Jetson/RPi and load it.

    If the remote directory contains a ROS2 .db3 bag, it extracts frames + GPS
    on the Jetson first (so we download only ~15 MB of JPEGs instead of gigabytes).
    Otherwise it does a plain SFTP recursive download.
    """
    from routers.logger import _state as logger_state
    from services.ros2_bag_extractor import is_ros2_bag, extract_and_download

    client = logger_state.get("client")
    if client is None or not client.is_connected:
        raise HTTPException(status_code=409, detail="SSH not connected — connect in the Logging panel first")

    bag_is_ros2 = await asyncio.to_thread(is_ros2_bag, client, body.remote_path)

    if not bag_is_ros2:
        # Also check for legacy DRISHTI session format (frames/ subdirectory).
        _, stdout, _ = client._client.exec_command(
            f"test -d {shlex.quote(body.remote_path + '/frames')} && echo 1 || echo 0"
        )
        has_frames_dir = stdout.read().decode().strip() == "1"
        if not has_frames_dir:
            raise HTTPException(
                status_code=422,
                detail=(
                    "No image data found in this bag — the .db3 file is missing. "
                    "The recording may have been interrupted before any data was written."
                ),
            )

    tmpdir = tempfile.mkdtemp(prefix="drishti_remote_")
    try:
        if bag_is_ros2:
            await asyncio.to_thread(extract_and_download, client, body.remote_path, tmpdir)
        else:
            await asyncio.to_thread(client.download_session_dir, body.remote_path, tmpdir)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    try:
        load_session_state(tmpdir)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return _session_response()



@router.post("/load")
async def load(body: LoadRequest):
    try:
        load_session_state(body.session_dir)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _session_response()


@router.get("/frame/{timestamp_ms}")
async def get_frame(timestamp_ms: int):
    if _session is None:
        raise HTTPException(status_code=404, detail="No session loaded")
    path = _session.get("frame_map", {}).get(timestamp_ms)
    if not path:
        raise HTTPException(status_code=404, detail="Frame not found")
    p = Path(path)
    if not p.exists():
        raise HTTPException(status_code=404, detail="Frame file missing on disk")
    return Response(content=p.read_bytes(), media_type="image/jpeg")


@router.get("/verify/{session_name}")
async def verify(session_name: str):
    if _session is None or _session.get("session_name") != session_name:
        raise HTTPException(status_code=404, detail="Session not loaded or name mismatch")

    frames = _session.get("frames", [])
    timestamps = [f["timestamp_ms"] for f in frames]
    frame_count = len(frames)
    duration_s = _session.get("duration_s", 0.0)

    gps_track_points = sum(1 for f in frames if f["lat"] is not None)

    # Recording gaps: consecutive frame pairs with gap > 1 s
    recording_gaps = []
    for i in range(1, len(timestamps)):
        gap_ms = timestamps[i] - timestamps[i - 1]
        if gap_ms > 1000:
            recording_gaps.append({"start_ms": timestamps[i - 1], "gap_s": round(gap_ms / 1000.0, 1)})

    # GPS fix quality
    hdops = sorted(f["hdop"] for f in frames if f["hdop"] is not None)
    no_fix_frames = sum(1 for f in frames if f["lat"] is None)
    hdop_median = _pct_list(hdops, 50) if hdops else None
    hdop_max    = round(max(hdops), 2) if hdops else None

    # Heading coverage (12 × 30° buckets)
    headings = [f["heading_deg"] for f in frames if f["heading_deg"] is not None]
    covered_buckets: set[int] = set()
    danger_zone_frames = 0
    for h in headings:
        covered_buckets.add(int(h // 30) % 12)
        if 210 <= h < 240:
            danger_zone_frames += 1

    # Verdict
    max_gap_s = max((g["gap_s"] for g in recording_gaps), default=0.0)
    verdict = "GOOD"
    refly_reasons: list[str] = []
    if max_gap_s > 5:
        verdict = "REFLY"
        refly_reasons.append(f"{len(recording_gaps)} recording gap(s) detected (max {max_gap_s} s)")
    if no_fix_frames > 10:
        verdict = "REFLY"
        refly_reasons.append(f"{no_fix_frames} frames without GPS fix")
    if frame_count < 100:
        verdict = "REFLY"
        refly_reasons.append(f"Only {frame_count} frames recorded (minimum 100 required)")

    return {
        "frame_count":       frame_count,
        "duration_s":        duration_s,
        "gps_track_points":  gps_track_points,
        "recording_gaps":    recording_gaps,
        "gps_fix_quality": {
            "hdop_median":    hdop_median,
            "hdop_max":       hdop_max,
            "no_fix_frames":  no_fix_frames,
        },
        "heading_coverage": {
            "buckets_covered":    len(covered_buckets),
            "danger_zone_frames": danger_zone_frames,
        },
        "verdict":        verdict,
        "refly_reasons":  refly_reasons,
    }


def _pct_list(s: list[float], p: float) -> float:
    n = len(s)
    if n == 0:
        return 0.0
    idx = p / 100.0 * (n - 1)
    lo, hi = int(idx), min(int(idx) + 1, n - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (idx - lo), 2)


@router.get("/live")
async def live():
    """SSE stream of real GPS fixes from the active logger tail."""
    async def event_generator():
        while True:
            lat = logger_state.get("lat")
            lon = logger_state.get("lon")
            payload = {
                "lat": round(lat, 7) if lat is not None else None,
                "lon": round(lon, 7) if lon is not None else None,
                "hdop": None,
                "timestamp_ms": int(time.time() * 1000),
            }
            yield {"data": json.dumps(payload)}
            await asyncio.sleep(1.0)

    return EventSourceResponse(event_generator())
