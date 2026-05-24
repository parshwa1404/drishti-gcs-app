import asyncio
import json
import random
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from routers.logger import _state as logger_state
from services.session_loader import load_session as _do_load

router = APIRouter()

_DEOLALI_LAT = 19.9175
_DEOLALI_LON = 73.8278

# Single loaded session; None until POST /session/load or startup mock
_session: dict | None = None
_mock_tmpdir: str | None = None   # keep reference so tempdir persists


class LoadRequest(BaseModel):
    session_dir: str


# ─── NMEA helpers ────────────────────────────────────────────────────────────

def _nmea_cs(body: str) -> str:
    chk = 0
    for c in body:
        chk ^= ord(c)
    return f"{chk:02X}"


def _fmt_gprmc(ts_ms: int, lat: float, lon: float, heading: float) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    t = dt.strftime('%H%M%S')
    d = dt.strftime('%d%m%y')
    la_d, lo_d = int(abs(lat)), int(abs(lon))
    la_m = (abs(lat) - la_d) * 60
    lo_m = (abs(lon) - lo_d) * 60
    la_s = f"{la_d:02d}{la_m:09.6f}"
    lo_s = f"{lo_d:03d}{lo_m:09.6f}"
    la_h = 'N' if lat >= 0 else 'S'
    lo_h = 'E' if lon >= 0 else 'W'
    body = f"GPRMC,{t}.000,A,{la_s},{la_h},{lo_s},{lo_h},0.0,{heading:.1f},{d},,"
    return f"${body}*{_nmea_cs(body)}"


def _fmt_gpgga(ts_ms: int, lat: float, lon: float, hdop: float) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
    t = dt.strftime('%H%M%S')
    la_d, lo_d = int(abs(lat)), int(abs(lon))
    la_m = (abs(lat) - la_d) * 60
    lo_m = (abs(lon) - lo_d) * 60
    la_s = f"{la_d:02d}{la_m:09.6f}"
    lo_s = f"{lo_d:03d}{lo_m:09.6f}"
    la_h = 'N' if lat >= 0 else 'S'
    lo_h = 'E' if lon >= 0 else 'W'
    body = f"GPGGA,{t}.000,{la_s},{la_h},{lo_s},{lo_h},1,10,{hdop:.1f},100.0,M,,,,0000"
    return f"${body}*{_nmea_cs(body)}"


def _fmt_hchdg(heading: float) -> str:
    body = f"HCHDG,{heading:.1f},,,0.0,E"
    return f"${body}*{_nmea_cs(body)}"


# ─── Mock generator ──────────────────────────────────────────────────────────

def generate_mock_session() -> str:
    global _mock_tmpdir

    try:
        import numpy as np
        from PIL import Image
        has_imaging = True
    except ImportError:
        has_imaging = False

    tmpdir = tempfile.mkdtemp(prefix="drishti_mock_")
    _mock_tmpdir = tmpdir
    frames_dir = Path(tmpdir) / "frames"
    frames_dir.mkdir()

    n_frames = 100
    duration_ms = 30_000
    start_ms = int(time.time() * 1000) - duration_ms

    lat, lon = _DEOLALI_LAT, _DEOLALI_LON
    heading, hdir = 60.0, 1.0
    nmea_lines: list[str] = []

    for i in range(n_frames):
        ts_ms = start_ms + int(i * duration_ms / (n_frames - 1))

        lat += random.uniform(-0.00008, 0.00008)
        lon += random.uniform(-0.00008, 0.00008)
        hdop = round(random.uniform(0.6, 1.2), 1)

        heading += hdir * random.uniform(0.5, 2.0)
        if heading > 90.0:
            hdir = -1.0
        elif heading < 30.0:
            hdir = 1.0

        frame_path = frames_dir / f"{ts_ms}.jpg"
        if has_imaging:
            import numpy as np
            from PIL import Image
            arr = np.random.randint(30, 120, (60, 80, 3), dtype=np.uint8)
            arr[28:32, :, :] = 60   # faint horizontal band
            Image.fromarray(arr, 'RGB').save(str(frame_path), format='JPEG', quality=70)
        else:
            frame_path.write_bytes(_minimal_jpeg())

        # GPRMC first so parser can assign timestamp; GPGGA + HCHDG annotate it
        nmea_lines.append(_fmt_gprmc(ts_ms, lat, lon, heading))
        nmea_lines.append(_fmt_gpgga(ts_ms, lat, lon, hdop))
        nmea_lines.append(_fmt_hchdg(heading))

    (Path(tmpdir) / "gps.nmea").write_text('\n'.join(nmea_lines) + '\n')
    return tmpdir


def load_session_state(session_dir: str) -> None:
    global _session
    _session = _do_load(session_dir)


def _session_response() -> dict | None:
    if _session is None:
        return None
    return {k: v for k, v in _session.items() if k != 'frame_map'}


# ─── Minimal fallback JPEG (1×1 grey) ────────────────────────────────────────

def _minimal_jpeg() -> bytes:
    return bytes([
        0xFF,0xD8,0xFF,0xE0,0x00,0x10,0x4A,0x46,0x49,0x46,0x00,0x01,0x01,0x00,
        0x00,0x01,0x00,0x01,0x00,0x00,0xFF,0xDB,0x00,0x43,0x00,0x08,0x06,0x06,
        0x07,0x06,0x05,0x08,0x07,0x07,0x07,0x09,0x09,0x08,0x0A,0x0C,0x14,0x0D,
        0x0C,0x0B,0x0B,0x0C,0x19,0x12,0x13,0x0F,0x14,0x1D,0x1A,0x1F,0x1E,0x1D,
        0x1A,0x1C,0x1C,0x20,0x24,0x2E,0x27,0x20,0x22,0x2C,0x23,0x1C,0x1C,0x28,
        0x37,0x29,0x2C,0x30,0x31,0x34,0x34,0x34,0x1F,0x27,0x39,0x3D,0x38,0x32,
        0x3C,0x2E,0x33,0x34,0x32,0xFF,0xC0,0x00,0x0B,0x08,0x00,0x01,0x00,0x01,
        0x01,0x01,0x11,0x00,0xFF,0xC4,0x00,0x1F,0x00,0x00,0x01,0x05,0x01,0x01,
        0x01,0x01,0x01,0x01,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x01,0x02,
        0x03,0x04,0x05,0x06,0x07,0x08,0x09,0x0A,0x0B,0xFF,0xC4,0x00,0x35,0x10,
        0x00,0x02,0x01,0x03,0x03,0x02,0x04,0x03,0x05,0x05,0x04,0x04,0x00,0x00,
        0x01,0x7D,0x01,0x02,0x03,0x00,0x04,0x11,0x05,0x12,0x21,0x31,0x41,0xFF,
        0xDA,0x00,0x08,0x01,0x01,0x00,0x00,0x3F,0x00,0xF5,0x00,0xFF,0xD9,
    ])


# ─── Endpoints ───────────────────────────────────────────────────────────────

@router.get("/mock")
async def get_mock():
    """Return the pre-loaded mock session metadata."""
    data = _session_response()
    if data is None:
        raise HTTPException(status_code=503, detail="Mock session not yet initialized")
    return data


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
    """SSE stream of GPS fixes aliasing the logger mock random walk."""
    async def event_generator():
        while True:
            payload = {
                "lat": round(logger_state["lat"], 7),
                "lon": round(logger_state["lon"], 7),
                "hdop": round(random.uniform(0.6, 1.8), 2),
                "timestamp_ms": int(time.time() * 1000),
            }
            yield {"data": json.dumps(payload)}
            await asyncio.sleep(1.0)

    return EventSourceResponse(event_generator())
