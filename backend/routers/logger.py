import asyncio
import json
import random
import time
from fastapi import APIRouter
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

router = APIRouter()

# Deolali Cantonment reference point
_DEOLALI_LAT = 19.9175
_DEOLALI_LON = 73.8278

class ConnectRequest(BaseModel):
    host: str
    user: str
    key_path: str


class StartRequest(BaseModel):
    altitude_m: float = 80.0
    session_name: str


# Module-level mock state shared across requests
_state = {
    "connected": False,
    "running": False,
    "host": None,
    # GPS random-walk state (always ticking so Live Map works without Start)
    "lat": _DEOLALI_LAT,
    "lon": _DEOLALI_LON,
}


@router.post("/connect")
async def connect(body: ConnectRequest):
    _state["connected"] = True
    _state["host"] = body.host
    return {"status": "connected", "host": body.host}


@router.post("/start")
async def start(body: StartRequest):
    if not _state["connected"]:
        return {"status": "error", "detail": "not connected"}
    _state["running"] = True
    return {"status": "started", "session_name": body.session_name, "altitude_m": body.altitude_m}


@router.post("/stop")
async def stop():
    _state["running"] = False
    return {"status": "stopped"}


@router.get("/preflight")
async def preflight(fail_demo: bool = False):
    if fail_demo:
        return {
            "gps_fix": {
                "pass": False,
                "hdop": 2.8,
                "satellites": 5,
                "message": "Poor fix, HDOP 2.8 (threshold: 2.0)",
            },
            "heading": {
                "pass": False,
                "heading_deg": 47.3,
                "variance_deg": 18.0,
                "message": "Unstable heading, variance 18.0° (threshold: 5°)",
            },
            "frame_counter": {"pass": True, "fps": 10.2, "message": "Camera streaming at 10.2 fps"},
            "tile_db": {"pass": True, "tile_count": 480, "index_path": "~/datasets/faiss_index/deolali_z19", "message": "480 tiles loaded"},
            "disk_space": {"pass": True, "free_gb": 12.4, "message": "12.4 GB free"},
            "gsd_norm": {"pass": True, "enabled": True, "message": "GSD normalisation enabled (target 0.28 m/px)"},
        }
    return {
        "gps_fix": {"pass": True, "hdop": 0.8, "satellites": 14, "message": "Fix acquired, HDOP 0.8"},
        "heading": {"pass": True, "heading_deg": 47.3, "variance_deg": 2.1, "message": "Stable at 47.3°"},
        "frame_counter": {"pass": True, "fps": 10.2, "message": "Camera streaming at 10.2 fps"},
        "tile_db": {"pass": True, "tile_count": 480, "index_path": "~/datasets/faiss_index/deolali_z19", "message": "480 tiles loaded"},
        "disk_space": {"pass": True, "free_gb": 12.4, "message": "12.4 GB free"},
        "gsd_norm": {"pass": True, "enabled": True, "message": "GSD normalisation enabled (target 0.28 m/px)"},
    }


@router.get("/status")
async def status():
    async def event_generator():
        frame_count = 0
        disk_mb = 48_000
        heading = 45.0

        while True:
            # GPS random walk — always active so LiveMapPanel animates in dev
            _state["lat"] += random.uniform(-0.00005, 0.00005)
            _state["lon"] += random.uniform(-0.00005, 0.00005)

            if _state["running"]:
                frame_count += random.randint(8, 12)
                disk_mb = max(0, disk_mb - random.randint(10, 30))
                heading = (heading + random.uniform(-5.0, 5.0)) % 360.0
                hdop = round(random.uniform(0.6, 1.4), 2)
                fix_count = random.randint(10, 14)
            else:
                hdop = round(random.uniform(1.8, 2.8), 2)
                fix_count = random.randint(4, 7)

            payload = {
                "frames_captured": frame_count,
                "gps_quality": hdop,
                "heading_deg": round(heading, 1),
                "disk_mb_remaining": disk_mb,
                "fix_count": fix_count,
                "lat": round(_state["lat"], 7),
                "lon": round(_state["lon"], 7),
                "timestamp_ms": int(time.time() * 1000),
            }
            yield {"data": json.dumps(payload)}
            await asyncio.sleep(1.0)

    return EventSourceResponse(event_generator())
